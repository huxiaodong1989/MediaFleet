"""可插拔计算服务任务分发契约。"""

from enum import Enum
from typing import Any

from pydantic import Field

from media_platform.contracts.base import MessageEnvelope


class TaskDeliveryChannel(str, Enum):
    """任务所属的独立 RabbitMQ 能力通道。"""

    MEDIA = "MEDIA"
    CONTENT_ANALYSIS = "CONTENT_ANALYSIS"


class TaskDispatchMessage(MessageEnvelope):
    """调用中心发布给独立计算服务的任务。"""

    source: str = "control_center"
    task_id: str = Field(min_length=1)
    business_task_id: str | None = Field(default=None, max_length=128)
    school_code: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    routing_key: str = Field(min_length=1)
    delivery_channel: TaskDeliveryChannel = TaskDeliveryChannel.MEDIA
    priority: int = Field(default=0, ge=0, le=255)
    target_node_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    callback_url: str | None = None


__all__ = ["TaskDeliveryChannel", "TaskDispatchMessage"]
