"""调用中心运行时依赖装配。

本模块只在 FastAPI lifespan 启动阶段创建数据库会话工厂、RabbitMQ 发布器和
后台轮询器。模块导入本身不连接数据库或 RabbitMQ，便于测试和命令行工具复用。
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import timedelta
import asyncio
import logging
import os
import socket
from threading import Event, Thread

from media_platform.application import (
    MediaNodeHeartbeatService,
    MediaNodeSelectionService,
    MediaStreamBindingService,
    RecorderCommandDispatchService,
    TaskDispatchService,
    TaskQueryService,
)
from media_platform.application.content_prompt_service import PromptManagementService
from media_platform.contracts.task import TaskDeliveryChannel
from media_platform.contracts.topology import (
    CONTENT_ANALYSIS_TASK_EXCHANGE,
    CONTROL_CENTER_EVENT_QUEUE,
    MEDIA_TASK_EXCHANGE,
)
from media_platform.infrastructure.messaging import (
    ChannelTaskPublisher,
    PikaMediaEventConsumer,
    PikaRecorderCommandPublisher,
    PikaTaskPublisher,
    RabbitMQEventConsumerConfig,
    RabbitMQPublisherConfig,
)
from services.control_center.consumers import MediaEventHandler
from services.control_center.application.recording_task_state_service import (
    RecordingTaskStateService,
)
from services.control_center.application.recording_command_dispatch_service import (
    DurableRecordingCommandDispatchService,
)
from services.control_center.application.recording_server_service import (
    RecordingServerService,
)
from services.control_center.application.admin_service import (
    AdminService,
    RabbitQueueInspector,
)
from services.control_center.application.content_evaluation_query_service import (
    ContentEvaluationQueryService,
)
from services.control_center.publishers import (
    TaskDispatchLoop,
    TaskDispatchLoopConfig,
)


LOGGER = logging.getLogger(__name__)


@dataclass
class ControlCenterRuntime:
    """调用中心进程持有的长生命周期资源。"""

    task_service: TaskDispatchService
    node_heartbeat_service: MediaNodeHeartbeatService
    node_selection_service: MediaNodeSelectionService
    stream_binding_service: MediaStreamBindingService
    recorder_command_service: RecorderCommandDispatchService
    recording_task_state_service: RecordingTaskStateService
    recording_server_service: RecordingServerService
    task_query_service: TaskQueryService
    dispatch_loop: TaskDispatchLoop
    recording_command_dispatch_loop: TaskDispatchLoop
    publisher: ChannelTaskPublisher
    command_publisher: PikaRecorderCommandPublisher
    api_key: str
    content_prompt_service: PromptManagementService | None = None
    content_evaluation_query_service: ContentEvaluationQueryService | None = None
    admin_service: AdminService | None = None
    event_consumer: PikaMediaEventConsumer | None = None
    event_consumer_enabled: bool = False
    event_reconnect_delay_seconds: float = 5.0
    node_health_check_interval_seconds: float = 30.0
    node_heartbeat_timeout_seconds: float = 60.0
    _stop_event: Event = field(default_factory=Event, init=False, repr=False)
    _event_consumer_thread: Thread | None = field(default=None, init=False, repr=False)
    _node_health_task: asyncio.Task | None = field(default=None, init=False, repr=False)

    def _consume_events_loop(self) -> None:
        """事件消费断线后按固定间隔重连，直到服务生命周期停止。"""

        while not self._stop_event.is_set():
            if self.event_consumer is None:
                return
            try:
                self.event_consumer.start_consuming()
            except Exception:
                if not self._stop_event.is_set():
                    LOGGER.exception(
                        "调用中心事件消费连接异常，%s秒后重连",
                        self.event_reconnect_delay_seconds,
                    )
            finally:
                self.event_consumer.close()

            if not self._stop_event.is_set():
                self._stop_event.wait(self.event_reconnect_delay_seconds)

    async def _node_health_loop(self) -> None:
        """周期性把停止心跳的节点标记为离线。"""

        heartbeat_timeout = timedelta(seconds=self.node_heartbeat_timeout_seconds)
        while not self._stop_event.is_set():
            try:
                await asyncio.to_thread(
                    self.node_heartbeat_service.mark_stale_offline,
                    heartbeat_timeout=heartbeat_timeout,
                )
            except Exception:
                LOGGER.exception(
                    "媒体节点健康巡检异常，%s秒后重试",
                    self.node_health_check_interval_seconds,
                )
            await asyncio.sleep(self.node_health_check_interval_seconds)

    def _start_node_health_loop(self) -> None:
        """启动节点心跳超时巡检。"""

        if (
            self._node_health_task is not None
            and not self._node_health_task.done()
        ):
            return
        self._node_health_task = asyncio.create_task(
            self._node_health_loop(),
            name="control-center-node-health",
        )
        LOGGER.info(
            "媒体节点健康巡检已启动: heartbeat_timeout=%ss, interval=%ss",
            self.node_heartbeat_timeout_seconds,
            self.node_health_check_interval_seconds,
        )

    async def start(self) -> None:
        """启动任务发布循环、节点健康巡检和事件消费线程。"""

        await self.dispatch_loop.start()
        await self.recording_command_dispatch_loop.start()
        self._start_node_health_loop()
        if not self.event_consumer_enabled:
            LOGGER.warning("调用中心事件消费者未启用")
            return
        if self.event_consumer is None:
            LOGGER.warning("调用中心事件消费者未装配")
            return
        if (
            self._event_consumer_thread is not None
            and self._event_consumer_thread.is_alive()
        ):
            return
        self._stop_event.clear()
        self._event_consumer_thread = Thread(
            target=self._consume_events_loop,
            name="control-center-media-events",
            daemon=True,
        )
        self._event_consumer_thread.start()

    async def close(self) -> None:
        """先停止领取新任务，再关闭 RabbitMQ 发布连接。"""

        self._stop_event.set()
        if self._node_health_task is not None:
            self._node_health_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._node_health_task
            self._node_health_task = None
        if self.event_consumer is not None:
            self.event_consumer.stop()
        thread = self._event_consumer_thread
        if thread is not None and thread.is_alive():
            await asyncio.to_thread(thread.join, 10)
        self._event_consumer_thread = None
        if self.event_consumer is not None:
            self.event_consumer.close()
        await self.dispatch_loop.stop()
        await self.recording_command_dispatch_loop.stop()
        self.command_publisher.close()
        self.publisher.close()


def _default_instance_id() -> str:
    """生成进程级调用中心实例标识，用于数据库任务锁所有权。"""

    configured = os.getenv("CONTROL_CENTER_INSTANCE_ID", "").strip()
    if configured:
        return configured
    return f"{socket.gethostname()}-{os.getpid()}"


def _environment_int(name: str, default: int, *, minimum: int = 1) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} 必须大于等于{minimum}")
    return value


def _environment_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} 必须大于0")
    return value


def _environment_bool(name: str, default: bool = True) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是true或false")


def build_control_center_runtime() -> ControlCenterRuntime:
    """根据项目配置创建生产运行时。

    Returns:
        已完成依赖装配但尚未启动后台循环的运行时对象。

    Notes:
        这里延迟导入 `SessionLocal`，避免 `services.control_center.main` 被测试导入时
        立即创建数据库 Engine。RabbitMQ 连接同样由发布器按需创建。
    """

    from media_platform.common.config import get_settings
    from media_platform.infrastructure.database.init import init_database

    settings = get_settings()
    init_database(settings)

    from media_platform.infrastructure.database.session import SessionLocal

    rabbitmq = settings.rabbitmq
    media_task_publisher = PikaTaskPublisher(
        RabbitMQPublisherConfig(
            host=rabbitmq.host,
            exchange=MEDIA_TASK_EXCHANGE,
            port=rabbitmq.port,
            username=rabbitmq.username,
            password=rabbitmq.password,
            virtual_host=rabbitmq.vhost,
            heartbeat=rabbitmq.heartbeat,
            blocked_connection_timeout=rabbitmq.blocked_connection_timeout,
        )
    )
    content_task_publisher = PikaTaskPublisher(
        RabbitMQPublisherConfig(
            host=rabbitmq.host,
            exchange=CONTENT_ANALYSIS_TASK_EXCHANGE,
            port=rabbitmq.port,
            username=rabbitmq.username,
            password=rabbitmq.password,
            virtual_host=rabbitmq.vhost,
            heartbeat=rabbitmq.heartbeat,
            blocked_connection_timeout=rabbitmq.blocked_connection_timeout,
        )
    )
    publisher = ChannelTaskPublisher(
        {
            TaskDeliveryChannel.MEDIA: media_task_publisher,
            TaskDeliveryChannel.CONTENT_ANALYSIS: content_task_publisher,
        }
    )
    command_publisher = PikaRecorderCommandPublisher(
        RabbitMQPublisherConfig(
            host=rabbitmq.host,
            port=rabbitmq.port,
            username=rabbitmq.username,
            password=rabbitmq.password,
            virtual_host=rabbitmq.vhost,
            heartbeat=rabbitmq.heartbeat,
            blocked_connection_timeout=rabbitmq.blocked_connection_timeout,
        )
    )
    task_service = TaskDispatchService(SessionLocal, publisher)
    node_heartbeat_service = MediaNodeHeartbeatService(SessionLocal)
    node_selection_service = MediaNodeSelectionService(SessionLocal)
    stream_binding_service = MediaStreamBindingService(
        SessionLocal,
        heartbeat_timeout_seconds=_environment_float(
            "NODE_HEARTBEAT_TIMEOUT",
            60.0,
        ),
    )
    recorder_command_service = RecorderCommandDispatchService(command_publisher)
    recording_task_state_service = RecordingTaskStateService(
        SessionLocal,
        reservation_margin_seconds=_environment_int(
            "RECORDING_RESERVATION_MARGIN_SECONDS",
            5,
            minimum=0,
        ),
    )
    recording_command_dispatch_service = DurableRecordingCommandDispatchService(
        recording_task_state_service,
        recorder_command_service,
    )
    recording_server_service = RecordingServerService(SessionLocal)
    task_query_service = TaskQueryService(SessionLocal)
    content_evaluation_query_service = ContentEvaluationQueryService(SessionLocal)
    instance_id = _default_instance_id()
    dispatch_loop = TaskDispatchLoop(
        task_service,
        TaskDispatchLoopConfig(
            instance_id=instance_id,
            batch_size=int(os.getenv("TASK_DISPATCH_BATCH_SIZE", "20")),
            poll_interval_seconds=float(
                os.getenv("TASK_DISPATCH_POLL_INTERVAL_SECONDS", "1")
            ),
            lock_timeout_seconds=int(
                os.getenv("TASK_DISPATCH_LOCK_TIMEOUT_SECONDS", "300")
            ),
            error_backoff_seconds=float(
                os.getenv("TASK_DISPATCH_ERROR_BACKOFF_SECONDS", "5")
            ),
        ),
    )
    recording_command_dispatch_loop = TaskDispatchLoop(
        recording_command_dispatch_service,
        TaskDispatchLoopConfig(
            instance_id=f"{instance_id}-recording-command",
            batch_size=_environment_int("RECORDING_COMMAND_DISPATCH_BATCH_SIZE", 20),
            poll_interval_seconds=_environment_float(
                "RECORDING_COMMAND_DISPATCH_POLL_INTERVAL_SECONDS", 1.0
            ),
            lock_timeout_seconds=_environment_int(
                "RECORDING_COMMAND_DISPATCH_LOCK_TIMEOUT_SECONDS", 10
            ),
            error_backoff_seconds=_environment_float(
                "RECORDING_COMMAND_DISPATCH_ERROR_BACKOFF_SECONDS", 5.0
            ),
            log_name="录制命令",
        ),
    )
    event_handler = MediaEventHandler(
        SessionLocal,
        callback_timeout_seconds=_environment_float(
            "CONTROL_CENTER_CALLBACK_TIMEOUT_SECONDS",
            10.0,
        ),
        instance_id=instance_id,
    )
    admin_service = AdminService(
        SessionLocal,
        queue_inspector=RabbitQueueInspector(
            RabbitMQPublisherConfig(
                host=rabbitmq.host,
                port=rabbitmq.port,
                username=rabbitmq.username,
                password=rabbitmq.password,
                virtual_host=rabbitmq.vhost,
                heartbeat=rabbitmq.heartbeat,
                blocked_connection_timeout=rabbitmq.blocked_connection_timeout,
            )
        ),
        callback_handler=event_handler,
    )
    content_prompt_service = PromptManagementService(SessionLocal)
    event_routing_keys = tuple(
        item.strip()
        for item in os.getenv(
            "CONTROL_CENTER_EVENT_ROUTING_KEYS",
            "task.completed,task.failed",
        ).split(",")
        if item.strip()
    )
    event_consumer = PikaMediaEventConsumer(
        RabbitMQEventConsumerConfig(
            host=rabbitmq.host,
            port=rabbitmq.port,
            username=rabbitmq.username,
            password=rabbitmq.password,
            virtual_host=rabbitmq.vhost,
            heartbeat=rabbitmq.heartbeat,
            blocked_connection_timeout=rabbitmq.blocked_connection_timeout,
            queue_name=os.getenv(
                "CONTROL_CENTER_EVENT_QUEUE_NAME",
                CONTROL_CENTER_EVENT_QUEUE,
            ).strip(),
            routing_keys=event_routing_keys,
            prefetch_count=_environment_int(
                "CONTROL_CENTER_EVENT_PREFETCH_COUNT",
                10,
            ),
            retry_delay_milliseconds=_environment_int(
                "CONTROL_CENTER_EVENT_RETRY_DELAY_MILLISECONDS",
                5000,
            ),
            max_attempts=_environment_int(
                "CONTROL_CENTER_EVENT_MAX_ATTEMPTS",
                3,
            ),
        ),
        event_handler.handle,
    )
    return ControlCenterRuntime(
        task_service=task_service,
        node_heartbeat_service=node_heartbeat_service,
        node_selection_service=node_selection_service,
        stream_binding_service=stream_binding_service,
        recorder_command_service=recorder_command_service,
        recording_task_state_service=recording_task_state_service,
        recording_server_service=recording_server_service,
        task_query_service=task_query_service,
        dispatch_loop=dispatch_loop,
        recording_command_dispatch_loop=recording_command_dispatch_loop,
        publisher=publisher,
        command_publisher=command_publisher,
        api_key=settings.API_KEY,
        admin_service=admin_service,
        content_prompt_service=content_prompt_service,
        content_evaluation_query_service=content_evaluation_query_service,
        event_consumer=event_consumer,
        event_consumer_enabled=_environment_bool(
            "CONTROL_CENTER_EVENT_CONSUMER_ENABLED",
            False,
        ),
        event_reconnect_delay_seconds=_environment_float(
            "CONTROL_CENTER_EVENT_RECONNECT_DELAY_SECONDS",
            5.0,
        ),
        node_health_check_interval_seconds=_environment_float(
            "NODE_HEALTH_CHECK_INTERVAL",
            30.0,
        ),
        node_heartbeat_timeout_seconds=_environment_float(
            "NODE_HEARTBEAT_TIMEOUT",
            60.0,
        ),
    )


__all__ = ["ControlCenterRuntime", "build_control_center_runtime"]
