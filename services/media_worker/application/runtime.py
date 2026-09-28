"""通用媒体 Worker 运行时依赖装配与消费线程生命周期。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import os
import socket
from threading import Event, Thread

from media_platform.application import (
    NodeHeartbeatReporter,
    NodeHeartbeatReporterConfig,
)
from media_platform.infrastructure.messaging import (
    PikaTaskConsumer,
    RabbitMQConsumerConfig,
)
from services.media_worker.application.task_execution_service import (
    TaskExecutionService,
)
from services.media_worker.processors.audio import VideoAudioExtractProcessor
from services.media_worker.processors.object_detection import (
    ObjectDetectImageProcessor,
    ObjectTrackVideoProcessor,
)
from services.media_worker.processors.recognition import (
    SpeechOfflineRecognizeProcessor,
)
from services.media_worker.processors.stream import (
    LiveCoverExtractProcessor,
    StreamAudioChunkProcessor,
)
from services.media_worker.processors.video import (
    VideoClipExtractProcessor,
    VideoCoverExtractProcessor,
    VideoFrameExtractProcessor,
    VideoWatermarkProcessor,
)
from services.media_worker.registry import TaskProcessorRegistry


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


def _worker_instance_id() -> str:
    """返回当前 Worker 运行实例编号。

    竞争消费者不使用跨重启稳定身份。正常部署自动使用 ``主机名-进程号``，只用于
    心跳、日志、任务执行租约和排障，不参与 RabbitMQ 路由。两个旧环境变量只保留
    兼容读取，新的部署模板不再要求人工配置。
    """

    configured = (
        os.getenv("MEDIA_WORKER_ID")
        or os.getenv("MEDIA_WORKER_INSTANCE_ID")
        or ""
    ).strip()
    if configured:
        return configured
    return f"{socket.gethostname()}-{os.getpid()}"


def _asr_preload_enabled(settings) -> bool:
    """返回当前 ASR 提供方对应的模型预热开关。

    FunASR 和 GLM 使用不同的模型与资源配置，不能始终读取
    ``FUNASR_PRELOAD``；否则切换到 GLM 后，``GLM_ASR_PRELOAD`` 即使开启也
    不会生效。
    """

    if settings.asr.provider == "glm":
        return bool(settings.glm_asr.preload)
    return bool(settings.funasr.preload)


def register_default_processors(registry: TaskProcessorRegistry) -> None:
    """注册当前新 Worker 已迁移的通用媒体处理器。"""

    processors = (
        VideoCoverExtractProcessor(),
        VideoFrameExtractProcessor(),
        LiveCoverExtractProcessor(),
        VideoAudioExtractProcessor(),
        VideoClipExtractProcessor(),
        VideoWatermarkProcessor(),
        StreamAudioChunkProcessor(),
        ObjectDetectImageProcessor(),
        ObjectTrackVideoProcessor(),
        SpeechOfflineRecognizeProcessor(),
    )
    for processor in processors:
        for task_type in processor.task_types:
            registry.register(task_type, processor)


@dataclass
class MediaWorkerRuntime:
    """持有 Worker 进程中的消费者、执行服务和后台消费线程。"""

    consumer: PikaTaskConsumer
    execution_service: TaskExecutionService
    registry: TaskProcessorRegistry
    worker_id: str
    consumer_enabled: bool = True
    reconnect_delay_seconds: float = 5.0
    heartbeat_reporter: NodeHeartbeatReporter | None = None
    asr_preload_enabled: bool = False
    asr_provider: str = "funasr"
    _stop_event: Event = field(default_factory=Event, init=False, repr=False)
    _consumer_thread: Thread | None = field(default=None, init=False, repr=False)
    _asr_preload_completed: bool = field(default=False, init=False, repr=False)

    async def _preload_asr_model(self) -> None:
        """按配置在 Worker 对外就绪前预热默认 ASR 模型。

        模型下载、权重加载和设备初始化可能持续几十秒甚至更久，不能把这段时间
        隐藏在第一条 RabbitMQ 任务里，否则任务会长时间显示 ``processing=0``，
        也容易被误判为消费线程或 RabbitMQ 卡死。预热失败直接阻止 Worker 就绪，
        让部署平台重启并重试，而不是让一个已在线但永远无法识别的节点继续抢任务。
        """

        if not self.asr_preload_enabled or self._asr_preload_completed:
            return

        LOGGER.info(
            "开始预热离线 ASR 模型: provider=%s, worker_id=%s",
            self.asr_provider,
            self.worker_id,
        )
        try:
            from services.media_worker.processors.recognition.media_recog import (
                MediaRecog,
            )

            await asyncio.to_thread(
                MediaRecog.preload_default_model,
                self.asr_provider,
            )
        except Exception:
            LOGGER.exception(
                "离线 ASR 模型预热失败，Worker 不进入就绪状态: provider=%s, worker_id=%s",
                self.asr_provider,
                self.worker_id,
            )
            raise

        self._asr_preload_completed = True
        LOGGER.info(
            "离线 ASR 模型预热完成: provider=%s, worker_id=%s",
            self.asr_provider,
            self.worker_id,
        )

    def _consume_loop(self) -> None:
        """消费断线后按固定间隔重连，直到服务生命周期请求停止。"""

        while not self._stop_event.is_set():
            try:
                self.consumer.start_consuming()
            except Exception:
                if not self._stop_event.is_set():
                    LOGGER.exception(
                        "媒体Worker消费连接异常，%s秒后重连",
                        self.reconnect_delay_seconds,
                    )
            finally:
                self.consumer.close()

            if not self._stop_event.is_set():
                self._stop_event.wait(self.reconnect_delay_seconds)

    async def start(self) -> None:
        """幂等启动 RabbitMQ 消费线程。"""

        await self._preload_asr_model()
        if self.heartbeat_reporter is not None:
            self.heartbeat_reporter.start()
        if not self.consumer_enabled:
            LOGGER.warning("媒体Worker消费者已被显式禁用: worker_id=%s", self.worker_id)
            return
        if self._consumer_thread is not None and self._consumer_thread.is_alive():
            return
        self._stop_event.clear()
        self._consumer_thread = Thread(
            target=self._consume_loop,
            name=f"media-worker-consumer-{self.worker_id}",
            daemon=True,
        )
        self._consumer_thread.start()
        LOGGER.info(
            "媒体Worker运行时已启动: worker_id=%s, task_types=%s",
            self.worker_id,
            self.registry.task_types,
        )

    async def close(self) -> None:
        """停止拉取新消息并等待当前 RabbitMQ 消费线程退出。"""

        self._stop_event.set()
        self.consumer.stop()
        thread = self._consumer_thread
        if thread is not None and thread.is_alive():
            await asyncio.to_thread(thread.join, 10)
        self._consumer_thread = None
        self.consumer.close()
        if self.heartbeat_reporter is not None:
            self.heartbeat_reporter.close()
        LOGGER.info("媒体Worker运行时已停止: worker_id=%s", self.worker_id)


def build_media_worker_runtime() -> MediaWorkerRuntime:
    """根据环境变量装配生产 Worker 运行时，但不立即连接外部服务。"""

    from media_platform.common.config import get_settings
    from media_platform.infrastructure.database.session import SessionLocal

    settings = get_settings()
    rabbitmq = settings.rabbitmq
    worker_id = _worker_instance_id()

    registry = TaskProcessorRegistry()
    register_default_processors(registry)

    execution_service = TaskExecutionService(
        SessionLocal,
        registry,
        worker_id,
        execution_timeout=timedelta(
            seconds=_environment_int(
                "MEDIA_WORKER_EXECUTION_TIMEOUT_SECONDS", 1800
            )
        ),
        lease_timeout=timedelta(
            seconds=_environment_int(
                "MEDIA_WORKER_EXECUTION_LEASE_SECONDS", 1800
            )
        ),
        lease_renew_interval=timedelta(
            seconds=_environment_int(
                "MEDIA_WORKER_EXECUTION_LEASE_RENEW_SECONDS", 600
            )
        ),
        callback_timeout_seconds=_environment_float(
            "MEDIA_WORKER_CALLBACK_TIMEOUT_SECONDS",
            10.0,
        ),
        callback_max_retries=_environment_int(
            "MEDIA_WORKER_CALLBACK_MAX_RETRIES",
            3,
        ),
        callback_retry_interval_seconds=_environment_float(
            "MEDIA_WORKER_CALLBACK_RETRY_INTERVAL_SECONDS",
            5.0,
        ),
    )
    routing_keys = tuple(
        item.strip()
        for item in os.getenv("MEDIA_WORKER_ROUTING_KEYS", "#").split(",")
        if item.strip()
    )
    consumer = PikaTaskConsumer(
        RabbitMQConsumerConfig(
            host=rabbitmq.host,
            port=rabbitmq.port,
            username=rabbitmq.username,
            password=rabbitmq.password,
            virtual_host=rabbitmq.vhost,
            heartbeat=rabbitmq.heartbeat,
            blocked_connection_timeout=rabbitmq.blocked_connection_timeout,
            queue_name=os.getenv(
                "MEDIA_WORKER_QUEUE_NAME", "media-worker.tasks"
            ).strip(),
            routing_keys=routing_keys,
            prefetch_count=_environment_int(
                "MEDIA_WORKER_PREFETCH_COUNT", 1
            ),
            retry_delay_milliseconds=_environment_int(
                "MEDIA_WORKER_RETRY_DELAY_MILLISECONDS", 5000
            ),
        ),
        execution_service.handle,
    )
    consumer_enabled = _environment_bool("MEDIA_WORKER_CONSUMER_ENABLED", True)
    prefetch_count = _environment_int("MEDIA_WORKER_PREFETCH_COUNT", 1)
    heartbeat_reporter = _build_media_worker_heartbeat_reporter(
        worker_id=worker_id,
        registry=registry,
        execution_service=execution_service,
        consumer_enabled=consumer_enabled,
        prefetch_count=prefetch_count,
        settings=settings,
    )
    asr_provider = settings.asr.provider
    return MediaWorkerRuntime(
        consumer=consumer,
        execution_service=execution_service,
        registry=registry,
        worker_id=worker_id,
        consumer_enabled=consumer_enabled,
        reconnect_delay_seconds=_environment_float(
            "MEDIA_WORKER_RECONNECT_DELAY_SECONDS", 5.0
        ),
        heartbeat_reporter=heartbeat_reporter,
        asr_preload_enabled=_asr_preload_enabled(settings),
        asr_provider=asr_provider,
    )


def _build_media_worker_heartbeat_reporter(
    *,
    worker_id: str,
    registry: TaskProcessorRegistry,
    execution_service: TaskExecutionService,
    consumer_enabled: bool,
    prefetch_count: int,
    settings,
) -> NodeHeartbeatReporter:
    """创建媒体 Worker 心跳上报器。"""

    node_name = _environment_optional_str("MEDIA_NODE_NAME") or worker_id
    control_center_url = (
        _environment_optional_str("MEDIA_NODE_HEARTBEAT_CONTROL_CENTER_URL")
        or _environment_optional_str("CONTROL_CENTER_URL")
        or DEFAULT_CONTROL_CENTER_URL
    )

    def payload() -> dict:
        return {
            "node_code": worker_id,
            "node_name": node_name,
            "node_type": "WORKER",
            "status": "ONLINE",
            "agent_url": _environment_optional_str("MEDIA_NODE_AGENT_URL"),
            "weight": _environment_int("MEDIA_NODE_WEIGHT", 100, minimum=0),
            "capabilities": list(registry.task_types),
            "capacity": {
                "worker_prefetch": prefetch_count,
                "consumer_enabled": consumer_enabled,
                "supported_task_types": list(registry.task_types),
                "processing_tasks": execution_service.processing_count,
                "asr_provider": os.getenv("ASR_PROVIDER", "funasr"),
                "media_recog_concurrency": getattr(
                    settings.media,
                    "recog_concurrency",
                    _environment_int("MEDIA_RECOG_CONCURRENCY", 2),
                ),
                "funasr_gpu_concurrency": getattr(
                    settings.funasr,
                    "gpu_concurrency",
                    _environment_int("FUNASR_GPU_CONCURRENCY", 1),
                ),
                "funasr_cpu_concurrency": getattr(
                    settings.funasr,
                    "cpu_concurrency",
                    _environment_int("FUNASR_CPU_CONCURRENCY", 2),
                ),
            },
            "readiness": "READY",
            "readiness_details": {
                "consumer_ready": consumer_enabled,
            },
            "updated_by": "media-worker",
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


__all__ = [
    "MediaWorkerRuntime",
    "build_media_worker_runtime",
    "register_default_processors",
]
