"""录制节点运行时依赖装配与命令消费线程生命周期。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
import inspect
import logging
import os
import shutil
import socket
from threading import Event, Thread
from typing import Any
from urllib.parse import urlsplit

from media_platform.application import (
    NodeHeartbeatReporter,
    NodeHeartbeatReporterConfig,
)
from media_platform.infrastructure.messaging import (
    PikaRecorderCommandConsumer,
    RabbitMQCommandConsumerConfig,
    TaskPermanentError,
)
from services.recorder_node.commands import (
    RecorderCommandRegistry,
    RecordingCommandHandler,
    RecordingCommandHandlerConfig,
    UnsupportedRecorderCommandError,
)
from services.recorder_node.application.task_recovery_service import (
    RecordingTaskRecoveryService,
)
from services.recorder_node.application.post_processing_recovery_service import (
    RecordingPostProcessingRecoveryService,
)
from services.recorder_node.application.dependency_readiness import (
    RecorderDependencyReadinessProbe,
)


LOGGER = logging.getLogger(__name__)
DEFAULT_CONTROL_CENTER_URL = "http://127.0.0.1:8008"


def _environment_int(name: str, default: int, *, minimum: int = 1) -> int:
    """读取正整数环境变量，并在启动阶段给出清晰配置错误。"""

    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} 必须大于等于{minimum}")
    return value


def _environment_float(name: str, default: float) -> float:
    """读取正浮点环境变量。"""

    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} 必须大于0")
    return value


def _environment_bool(name: str, default: bool = False) -> bool:
    """读取布尔环境变量，拒绝含义不明确的配置值。"""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是true或false")


def _environment_optional_str(name: str) -> str | None:
    """读取可选字符串环境变量，空字符串按未配置处理。"""

    value = os.getenv(name, "").strip()
    return value or None


def _environment_port(name: str, default: int) -> str:
    """读取端口环境变量并标准化为 RTC 实体使用的字符串。"""

    port = _environment_int(name, default)
    if port > 65535:
        raise ValueError(f"{name} 必须小于等于65535")
    return str(port)


def _recorder_node_id() -> str:
    """返回当前录制节点编号；生产环境应显式配置稳定 ID。"""

    configured = (
        os.getenv("RECORDER_NODE_ID")
        or os.getenv("MEDIA_NODE_ID")
        or os.getenv("NODE_ID")
        or ""
    ).strip()
    if configured:
        return configured
    return socket.gethostname()


def _recorder_server_id(node_id: str) -> str:
    """返回录制单元稳定编号；本地未配置时复用 recorder-node 编号。"""

    return _environment_optional_str("RECORDER_SERVER_ID") or node_id


@dataclass
class RecorderNodeRuntime:
    """持有录制节点命令消费者、注册表和后台消费线程。"""

    consumer: PikaRecorderCommandConsumer
    registry: RecorderCommandRegistry
    node_id: str
    command_consumer_enabled: bool = True
    reconnect_delay_seconds: float = 5.0
    managed_resources: Iterable[Any] = field(default_factory=tuple)
    heartbeat_reporter: NodeHeartbeatReporter | None = None
    post_processing_manager: Any | None = None
    post_processing_max_workers: int = 3
    post_processing_media_concurrency: int = 2
    post_processing_idle_strategy_enabled: bool = False
    post_processing_busy_recording_threshold: int = 50
    post_processing_busy_media_concurrency: int = 1
    post_processing_busy_media_percent: int = 50
    post_processing_max_recordings: int = 100
    post_processing_idle_strategy_poll_interval_seconds: float = 1.0
    post_processing_db_save_max_attempts: int = 5
    post_processing_db_save_retry_delay_seconds: float = 5.0
    post_processing_failed_auto_retry_enabled: bool = True
    post_processing_failed_auto_retry_max_attempts: int = 3
    post_processing_failed_auto_retry_initial_delay_seconds: float = 60.0
    post_processing_failed_auto_retry_max_delay_seconds: float = 900.0
    post_processing_failed_auto_retry_scan_interval_seconds: float = 30.0
    post_processing_active_recordings_provider: Any | None = None
    recording_recovery_service: RecordingTaskRecoveryService | None = None
    post_processing_recovery_service: RecordingPostProcessingRecoveryService | None = None
    record_file_cleaner: Any | None = None
    _stop_event: Event = field(default_factory=Event, init=False, repr=False)
    _consumer_thread: Thread | None = field(default=None, init=False, repr=False)
    _post_processing_retry_task: asyncio.Task | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def handle_command(self, message) -> None:
        """执行命令注册表分发，并把未注册命令转为永久失败。"""

        try:
            self.registry.handle(message)
        except UnsupportedRecorderCommandError as exc:
            raise TaskPermanentError(str(exc)) from exc

    def _consume_loop(self) -> None:
        """消费断线后按固定间隔重连，直到生命周期请求停止。"""

        while not self._stop_event.is_set():
            try:
                self.consumer.start_consuming()
            except Exception:
                if not self._stop_event.is_set():
                    LOGGER.exception(
                        "录制节点命令消费连接异常，%s秒后重连",
                        self.reconnect_delay_seconds,
                    )
            finally:
                self.consumer.close()

            if not self._stop_event.is_set():
                self._stop_event.wait(self.reconnect_delay_seconds)

    async def start(self) -> None:
        """幂等启动心跳、录制命令消费线程和本机后处理 Worker 池。"""

        self._stop_event.clear()
        if self.heartbeat_reporter is not None:
            self.heartbeat_reporter.start()
        if self.post_processing_manager is not None:
            start = self.post_processing_manager.start
            parameters = inspect.signature(start).parameters
            accepts_extra_kwargs = any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
            kwargs = {}
            if accepts_extra_kwargs or "media_concurrency" in parameters:
                kwargs["media_concurrency"] = self.post_processing_media_concurrency
            if accepts_extra_kwargs or "idle_strategy_enabled" in parameters:
                kwargs["idle_strategy_enabled"] = (
                    self.post_processing_idle_strategy_enabled
                )
            if accepts_extra_kwargs or "busy_recording_threshold" in parameters:
                kwargs["busy_recording_threshold"] = (
                    self.post_processing_busy_recording_threshold
                )
            if accepts_extra_kwargs or "busy_media_concurrency" in parameters:
                kwargs["busy_media_concurrency"] = (
                    self.post_processing_busy_media_concurrency
                )
            if accepts_extra_kwargs or "busy_media_percent" in parameters:
                kwargs["busy_media_percent"] = (
                    self.post_processing_busy_media_percent
                )
            if accepts_extra_kwargs or "max_recordings" in parameters:
                kwargs["max_recordings"] = self.post_processing_max_recordings
            if (
                accepts_extra_kwargs
                or "idle_strategy_poll_interval_seconds" in parameters
            ):
                kwargs["idle_strategy_poll_interval_seconds"] = (
                    self.post_processing_idle_strategy_poll_interval_seconds
                )
            if accepts_extra_kwargs or "db_save_max_attempts" in parameters:
                kwargs["db_save_max_attempts"] = (
                    self.post_processing_db_save_max_attempts
                )
            if (
                accepts_extra_kwargs
                or "db_save_retry_delay_seconds" in parameters
            ):
                kwargs["db_save_retry_delay_seconds"] = (
                    self.post_processing_db_save_retry_delay_seconds
                )
            if accepts_extra_kwargs or "failed_auto_retry_enabled" in parameters:
                kwargs["failed_auto_retry_enabled"] = (
                    self.post_processing_failed_auto_retry_enabled
                )
            if (
                accepts_extra_kwargs
                or "failed_auto_retry_max_attempts" in parameters
            ):
                kwargs["failed_auto_retry_max_attempts"] = (
                    self.post_processing_failed_auto_retry_max_attempts
                )
            if (
                accepts_extra_kwargs
                or "failed_auto_retry_initial_delay_seconds" in parameters
            ):
                kwargs["failed_auto_retry_initial_delay_seconds"] = (
                    self.post_processing_failed_auto_retry_initial_delay_seconds
                )
            if (
                accepts_extra_kwargs
                or "failed_auto_retry_max_delay_seconds" in parameters
            ):
                kwargs["failed_auto_retry_max_delay_seconds"] = (
                    self.post_processing_failed_auto_retry_max_delay_seconds
                )
            if accepts_extra_kwargs or "active_recordings_provider" in parameters:
                kwargs["active_recordings_provider"] = (
                    self.post_processing_active_recordings_provider
                )
            await start(self.post_processing_max_workers, **kwargs)
            LOGGER.info(
                "录制节点后处理队列已启动: node_id=%s, max_workers=%s, "
                "media_concurrency=%s, idle_strategy_enabled=%s, "
                "busy_recording_threshold=%s, busy_media_percent=%s, "
                "busy_media_concurrency=%s, max_recordings=%s, "
                "db_save_max_attempts=%s",
                self.node_id,
                self.post_processing_max_workers,
                self.post_processing_media_concurrency,
                self.post_processing_idle_strategy_enabled,
                self.post_processing_busy_recording_threshold,
                self.post_processing_busy_media_percent,
                self.post_processing_busy_media_concurrency,
                self.post_processing_max_recordings,
                self.post_processing_db_save_max_attempts,
            )
        if self.post_processing_recovery_service is not None:
            try:
                recovered_count = await self.post_processing_recovery_service.recover_pending_post_processing()
                LOGGER.info(
                    "录制节点后处理恢复已执行: node_id=%s, recovered_count=%s",
                    self.node_id,
                    recovered_count,
                )
            except Exception:
                LOGGER.exception(
                    "录制节点后处理恢复异常，继续启动命令消费者: node_id=%s",
                    self.node_id,
                )
        if self.recording_recovery_service is not None:
            try:
                recovered_count = await asyncio.to_thread(
                    self.recording_recovery_service.recover_active_recordings
                )
                LOGGER.info(
                    "录制节点启动恢复已执行: node_id=%s, recovered_count=%s",
                    self.node_id,
                    recovered_count,
                )
            except Exception:
                LOGGER.exception(
                    "录制节点启动恢复异常，继续启动命令消费者: node_id=%s",
                    self.node_id,
                )
        if self.record_file_cleaner is not None:
            await self.record_file_cleaner.start()
        if (
            self.post_processing_failed_auto_retry_enabled
            and self.post_processing_recovery_service is not None
            and (
                self._post_processing_retry_task is None
                or self._post_processing_retry_task.done()
            )
        ):
            self._post_processing_retry_task = asyncio.create_task(
                self._post_processing_retry_loop(),
                name=f"post-processing-auto-retry-{self.node_id}",
            )
        if not self.command_consumer_enabled:
            LOGGER.warning("录制节点命令消费者已被显式禁用: node_id=%s", self.node_id)
            return
        if self._consumer_thread is not None and self._consumer_thread.is_alive():
            return
        self._consumer_thread = Thread(
            target=self._consume_loop,
            name=f"recorder-node-command-consumer-{self.node_id}",
            daemon=True,
        )
        self._consumer_thread.start()
        LOGGER.info(
            "录制节点运行时已启动: node_id=%s, commands=%s",
            self.node_id,
            self.registry.commands,
        )

    async def close(self) -> None:
        """停止拉取新命令并等待当前 RabbitMQ 消费线程退出。"""

        self._stop_event.set()
        self.consumer.stop()
        thread = self._consumer_thread
        if thread is not None and thread.is_alive():
            await asyncio.to_thread(thread.join, 10)
        self._consumer_thread = None
        self.consumer.close()
        retry_task = self._post_processing_retry_task
        self._post_processing_retry_task = None
        if retry_task is not None and not retry_task.done():
            retry_task.cancel()
            await asyncio.gather(retry_task, return_exceptions=True)
        if self.record_file_cleaner is not None:
            await self.record_file_cleaner.stop()
        if self.post_processing_manager is not None:
            await self.post_processing_manager.stop()
            LOGGER.info("录制节点后处理队列已停止: node_id=%s", self.node_id)
        for resource in self.managed_resources:
            close = getattr(resource, "close", None)
            if callable(close):
                close()
        if self.heartbeat_reporter is not None:
            self.heartbeat_reporter.close()
        LOGGER.info("录制节点运行时已停止: node_id=%s", self.node_id)

    async def _post_processing_retry_loop(self) -> None:
        """周期扫描 MySQL 中到期的后处理重试等待任务并自动重新入队。"""

        interval = max(
            0.1,
            float(self.post_processing_failed_auto_retry_scan_interval_seconds),
        )
        LOGGER.info(
            "录制后处理自动重试扫描已启动: node_id=%s, interval=%ss",
            self.node_id,
            interval,
        )
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(interval)
                if self._stop_event.is_set():
                    break
                recovered_count = await (
                    self.post_processing_recovery_service
                    .recover_pending_post_processing()
                )
                if recovered_count:
                    LOGGER.info(
                        "录制后处理自动重试扫描已重新入队任务: "
                        "node_id=%s, recovered_count=%s",
                        self.node_id,
                        recovered_count,
                    )
            except asyncio.CancelledError:
                break
            except Exception:
                LOGGER.exception(
                    "录制后处理自动重试扫描异常，稍后继续: node_id=%s",
                    self.node_id,
                )
        LOGGER.info("录制后处理自动重试扫描已停止: node_id=%s", self.node_id)


def build_recorder_node_runtime() -> RecorderNodeRuntime:
    """根据环境变量装配录制节点生产运行时，但不立即连接外部服务。"""

    from media_platform.common.config import get_settings

    settings = get_settings()
    rabbitmq = settings.rabbitmq
    node_id = _recorder_node_id()
    max_recordings = _environment_int("RECORDER_NODE_MAX_RECORDINGS", 100)
    registry = RecorderCommandRegistry()
    recording_handler = RecordingCommandHandler(
        recorder_factory=lambda: _build_stream_recorder(
            settings,
            max_recordings=max_recordings,
        ),
        config=RecordingCommandHandlerConfig(
            accept_timeout_seconds=_environment_float(
                "RECORDER_NODE_COMMAND_ACCEPT_TIMEOUT_SECONDS",
                60.0,
            ),
        ),
    )
    registry.register("record.start", recording_handler)
    registry.register("record.stop", recording_handler)
    runtime: RecorderNodeRuntime | None = None

    def handle(message) -> None:
        if runtime is None:
            raise RuntimeError("录制节点运行时尚未初始化")
        runtime.handle_command(message)

    consumer = PikaRecorderCommandConsumer(
        RabbitMQCommandConsumerConfig(
            host=rabbitmq.host,
            port=rabbitmq.port,
            username=rabbitmq.username,
            password=rabbitmq.password,
            virtual_host=rabbitmq.vhost,
            heartbeat=rabbitmq.heartbeat,
            blocked_connection_timeout=rabbitmq.blocked_connection_timeout,
            node_id=node_id,
            prefetch_count=_environment_int(
                "RECORDER_NODE_COMMAND_PREFETCH_COUNT",
                1,
            ),
            retry_delay_milliseconds=_environment_int(
                "RECORDER_NODE_COMMAND_RETRY_DELAY_MILLISECONDS",
                5000,
            ),
            max_attempts=_environment_int(
                "RECORDER_NODE_COMMAND_MAX_ATTEMPTS",
                3,
            ),
        ),
        handle,
    )
    command_consumer_enabled = _environment_bool(
        "RECORDER_NODE_COMMAND_CONSUMER_ENABLED",
        True,
    )
    from services.recorder_node.postprocess import (
        RecordFileCleaner,
        get_post_processing_manager,
    )
    from media_platform.infrastructure.database.session import SessionLocal
    recording_recovery_service = RecordingTaskRecoveryService(
        session_factory=SessionLocal,
        node_id=node_id,
        recording_starter=recording_handler.recover_recording,
    )
    recording_handler.set_recording_recovery_loader(
        recording_recovery_service.load_recovery_params
    )

    post_processing_manager = get_post_processing_manager()
    post_processing_recovery_service = RecordingPostProcessingRecoveryService(
        session_factory=SessionLocal,
        node_id=node_id,
        post_processing_recoverer=recording_handler.recover_post_processing,
        post_processing_tracker=recording_handler.is_post_processing_tracked,
    )
    record_file_cleaner = RecordFileCleaner()
    record_file_cleaner.configure(
        session_factory=SessionLocal,
        node_id=node_id,
    )

    runtime = RecorderNodeRuntime(
        consumer=consumer,
        registry=registry,
        node_id=node_id,
        command_consumer_enabled=command_consumer_enabled,
        reconnect_delay_seconds=_environment_float(
            "RECORDER_NODE_COMMAND_RECONNECT_DELAY_SECONDS",
            5.0,
        ),
        managed_resources=(recording_handler,),
        heartbeat_reporter=_build_recorder_node_heartbeat_reporter(
            node_id=node_id,
            recording_handler=recording_handler,
            command_consumer_enabled=command_consumer_enabled,
            settings=settings,
            max_recordings=max_recordings,
        ),
        post_processing_manager=post_processing_manager,
        post_processing_max_workers=getattr(
            settings.post_processing,
            "max_workers",
            _environment_int("POST_PROCESSING_MAX_WORKERS", 3),
        ),
        post_processing_media_concurrency=getattr(
            settings.post_processing,
            "media_concurrency",
            _environment_int("POST_PROCESSING_MEDIA_CONCURRENCY", 2),
        ),
        post_processing_idle_strategy_enabled=getattr(
            settings.post_processing,
            "idle_strategy_enabled",
            _environment_bool("POST_PROCESSING_IDLE_STRATEGY_ENABLED", False),
        ),
        post_processing_busy_recording_threshold=getattr(
            settings.post_processing,
            "busy_recording_threshold",
            _environment_int("POST_PROCESSING_BUSY_RECORDING_THRESHOLD", 50),
        ),
        post_processing_busy_media_concurrency=getattr(
            settings.post_processing,
            "busy_media_concurrency",
            _environment_int(
                "POST_PROCESSING_BUSY_MEDIA_CONCURRENCY", 1, minimum=0
            ),
        ),
        post_processing_busy_media_percent=getattr(
            settings.post_processing,
            "busy_media_percent",
            _environment_int("POST_PROCESSING_BUSY_MEDIA_PERCENT", 50),
        ),
        post_processing_max_recordings=max_recordings,
        post_processing_idle_strategy_poll_interval_seconds=getattr(
            settings.post_processing,
            "idle_strategy_poll_interval_seconds",
            _environment_float(
                "POST_PROCESSING_IDLE_STRATEGY_POLL_INTERVAL_SECONDS", 1.0
            ),
        ),
        post_processing_db_save_max_attempts=getattr(
            settings.post_processing,
            "db_save_max_attempts",
            _environment_int("POST_PROCESSING_DB_SAVE_MAX_ATTEMPTS", 5),
        ),
        post_processing_db_save_retry_delay_seconds=getattr(
            settings.post_processing,
            "db_save_retry_delay_seconds",
            float(os.getenv("POST_PROCESSING_DB_SAVE_RETRY_DELAY_SECONDS", "5")),
        ),
        post_processing_failed_auto_retry_enabled=getattr(
            settings.post_processing,
            "failed_auto_retry_enabled",
            _environment_bool("POST_PROCESSING_FAILED_AUTO_RETRY_ENABLED", True),
        ),
        post_processing_failed_auto_retry_max_attempts=getattr(
            settings.post_processing,
            "failed_auto_retry_max_attempts",
            _environment_int(
                "POST_PROCESSING_FAILED_AUTO_RETRY_MAX_ATTEMPTS",
                3,
                minimum=0,
            ),
        ),
        post_processing_failed_auto_retry_initial_delay_seconds=getattr(
            settings.post_processing,
            "failed_auto_retry_initial_delay_seconds",
            float(
                os.getenv(
                    "POST_PROCESSING_FAILED_AUTO_RETRY_INITIAL_DELAY_SECONDS",
                    "60",
                )
            ),
        ),
        post_processing_failed_auto_retry_max_delay_seconds=getattr(
            settings.post_processing,
            "failed_auto_retry_max_delay_seconds",
            float(
                os.getenv(
                    "POST_PROCESSING_FAILED_AUTO_RETRY_MAX_DELAY_SECONDS",
                    "900",
                )
            ),
        ),
        post_processing_failed_auto_retry_scan_interval_seconds=getattr(
            settings.post_processing,
            "failed_auto_retry_scan_interval_seconds",
            _environment_float(
                "POST_PROCESSING_FAILED_AUTO_RETRY_SCAN_INTERVAL_SECONDS",
                30.0,
            ),
        ),
        post_processing_active_recordings_provider=(
            recording_handler.active_recording_count
        ),
        recording_recovery_service=recording_recovery_service,
        post_processing_recovery_service=post_processing_recovery_service,
        record_file_cleaner=record_file_cleaner,
    )
    return runtime


def _safe_disk_usage_percent(record_root: str | None) -> float | None:
    """返回录像根目录所在磁盘使用率；目录不可用时返回 None。"""

    if not record_root:
        return None
    try:
        usage = shutil.disk_usage(record_root)
    except OSError:
        return None
    if usage.total <= 0:
        return None
    return round((usage.used / usage.total) * 100, 2)


def _build_recorder_node_heartbeat_reporter(
    *,
    node_id: str,
    recording_handler: RecordingCommandHandler,
    command_consumer_enabled: bool,
    settings,
    max_recordings: int = 100,
) -> NodeHeartbeatReporter:
    """创建录制节点心跳上报器。"""

    node_name = _environment_optional_str("MEDIA_NODE_NAME") or node_id
    control_center_url = (
        _environment_optional_str("MEDIA_NODE_HEARTBEAT_CONTROL_CENTER_URL")
        or _environment_optional_str("CONTROL_CENTER_URL")
        or DEFAULT_CONTROL_CENTER_URL
    )
    # 录像目录只认服务进程可见的 ZLM_RECORD_LOCAL_ROOT。
    # ZLM_RECORD_PATH 是 Docker 宿主机挂载源，只能由 Compose volume 使用，不能
    # 作为容器内路径回传或参与就绪检查，否则 Windows/Linux 和容器路径会混用。
    record_root = getattr(settings.zlm, "record_local_root", None)
    server_id = _recorder_server_id(node_id)
    zlm_server_id = _environment_optional_str("ZLM_SERVER_ID") or server_id
    zlm_api_url = str(getattr(settings.zlm, "api_url", "") or "").rstrip("/")
    parsed_zlm_api_url = urlsplit(zlm_api_url)
    play_protocol = (
        _environment_optional_str("ZLM_PLAY_PROTOCOL")
        or (
            parsed_zlm_api_url.scheme
            if parsed_zlm_api_url.scheme in {"http", "https"}
            else "http"
        )
    ).lower()
    if play_protocol not in {"http", "https"}:
        raise ValueError("ZLM_PLAY_PROTOCOL 必须是http或https")
    play_host = (
        _environment_optional_str("ZLM_PLAY_HOST")
        or parsed_zlm_api_url.hostname
        or "127.0.0.1"
    ).strip().rstrip("/")
    play_port = _environment_port(
        "ZLM_PLAY_PORT",
        443 if play_protocol == "https" else 80,
    )
    rtmp_port = _environment_port("ZLM_RTMP_PORT", 1935)
    rtsp_port = _environment_port("ZLM_RTSP_PORT", 554)
    readiness_probe = RecorderDependencyReadinessProbe(
        zlm_api_url=zlm_api_url,
        zlm_secret=getattr(settings.zlm, "secret", ""),
        record_root=record_root,
        min_free_disk_gb=float(
            os.getenv("RECORDER_NODE_MIN_FREE_DISK_GB", "10")
        ),
        timeout_seconds=_environment_float(
            "RECORDER_NODE_READINESS_TIMEOUT_SECONDS",
            3.0,
        ),
    )

    def payload() -> dict:
        from services.recorder_node.postprocess import get_post_processing_manager

        post_manager = get_post_processing_manager()
        post_stats = dict(getattr(post_manager, "stats", {}) or {})
        post_stats["queue_size"] = post_manager.queue.qsize()
        post_stats["worker_count"] = len(post_manager.workers)
        post_stats["running"] = post_manager.running
        readiness = readiness_probe.check()
        return {
            "node_code": node_id,
            "node_name": node_name,
            "node_type": "RECORDER",
            "server_code": server_id,
            "server_name": _environment_optional_str("RECORDER_SERVER_NAME")
            or server_id,
            "agent_url": _environment_optional_str("MEDIA_NODE_AGENT_URL"),
            "zlm_api_url": zlm_api_url,
            "zlm_server_id": zlm_server_id,
            "play_host": play_host,
            "play_port": play_port,
            "play_protocol": play_protocol,
            "rtmp_port": rtmp_port,
            "rtsp_port": rtsp_port,
            "record_root": record_root,
            "weight": _environment_int("MEDIA_NODE_WEIGHT", 100, minimum=0),
            "capabilities": ["record.start", "record.stop"],
            "capacity": {
                "command_consumer_enabled": command_consumer_enabled,
                "current_recordings": recording_handler.active_recording_count(),
                "max_recordings": max_recordings,
                "max_bindings": _environment_int(
                    "RECORDER_NODE_MAX_BINDINGS",
                    300,
                ),
                "postprocess_queue_size": post_stats.get("queue_size", 0),
                "postprocess_current_processing": post_stats.get(
                    "current_processing",
                    0,
                ),
                "postprocess_effective_media_concurrency": post_stats.get(
                    "effective_media_concurrency",
                    getattr(settings.post_processing, "media_concurrency", 2),
                ),
                "postprocess_max_workers": getattr(
                    settings.post_processing,
                    "max_workers",
                    _environment_int("POST_PROCESSING_MAX_WORKERS", 3),
                ),
                "disk_usage_percent": _safe_disk_usage_percent(record_root),
            },
            "readiness": "READY" if readiness.ready else "NOT_READY",
            "readiness_details": readiness.details,
            "updated_by": "recorder-node",
        }

    return NodeHeartbeatReporter(
        NodeHeartbeatReporterConfig(
            control_center_base_url=control_center_url,
            api_key=getattr(settings, "API_KEY", ""),
            interval_seconds=_environment_float(
                "MEDIA_NODE_HEARTBEAT_INTERVAL_SECONDS", 30.0
            ),
            timeout_seconds=_environment_float(
                "MEDIA_NODE_HEARTBEAT_TIMEOUT_SECONDS", 5.0
            ),
            enabled=True,
        ),
        payload,
    )


def _build_stream_recorder(settings, *, max_recordings: int):
    """懒加载旧录制器实现，供 recorder-node 命令处理器复用。"""

    from services.recorder_node.recorder import StreamRecorder

    return StreamRecorder(
        settings.model_dump(),
        max_recordings=max_recordings,
    )


__all__ = ["RecorderNodeRuntime", "build_recorder_node_runtime"]
