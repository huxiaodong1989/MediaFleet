"""跨服务 API 与消息契约。"""

from media_platform.contracts.base import MessageEnvelope
from media_platform.contracts.command import (
    RecorderCommandMessage,
    RecorderCommandType,
)
from media_platform.contracts.event import MediaEventMessage
from media_platform.contracts.service import (
    HealthStatus,
    ServiceHealth,
    ServiceInfo,
    ServiceRole,
)
from media_platform.contracts.task import TaskDispatchMessage
from media_platform.contracts.topology import (
    MEDIA_COMMAND_DEAD_LETTER_EXCHANGE,
    MEDIA_COMMAND_EXCHANGE,
    MEDIA_COMMAND_RETRY_EXCHANGE,
    MEDIA_EVENT_DEAD_LETTER_EXCHANGE,
    MEDIA_EVENT_EXCHANGE,
    MEDIA_EVENT_RETRY_EXCHANGE,
    MEDIA_TASK_DEAD_LETTER_EXCHANGE,
    MEDIA_TASK_EXCHANGE,
    MEDIA_TASK_RETRY_EXCHANGE,
    normalized_binding_key,
    normalized_rabbitmq_name,
    queue_dead_letter_queue_name,
    queue_retry_queue_name,
    recorder_command_queue_name,
    recorder_command_routing_key,
    worker_dead_letter_queue_name,
    worker_retry_queue_name,
)

__all__ = [
    "HealthStatus",
    "MEDIA_COMMAND_DEAD_LETTER_EXCHANGE",
    "MEDIA_COMMAND_EXCHANGE",
    "MEDIA_COMMAND_RETRY_EXCHANGE",
    "MEDIA_EVENT_DEAD_LETTER_EXCHANGE",
    "MEDIA_EVENT_EXCHANGE",
    "MEDIA_EVENT_RETRY_EXCHANGE",
    "MEDIA_TASK_DEAD_LETTER_EXCHANGE",
    "MEDIA_TASK_EXCHANGE",
    "MEDIA_TASK_RETRY_EXCHANGE",
    "MediaEventMessage",
    "MessageEnvelope",
    "RecorderCommandMessage",
    "RecorderCommandType",
    "ServiceHealth",
    "ServiceInfo",
    "ServiceRole",
    "TaskDispatchMessage",
    "normalized_binding_key",
    "normalized_rabbitmq_name",
    "queue_dead_letter_queue_name",
    "queue_retry_queue_name",
    "recorder_command_queue_name",
    "recorder_command_routing_key",
    "worker_dead_letter_queue_name",
    "worker_retry_queue_name",
]
