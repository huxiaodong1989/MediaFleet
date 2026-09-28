"""媒体领域事件契约。"""

from typing import Any

from pydantic import Field

from media_platform.contracts.base import MessageEnvelope


class MediaEventMessage(MessageEnvelope):
    """录制节点或媒体 Worker 返回给调用中心的领域事件。"""

    event_type: str = Field(min_length=1)
    aggregate_id: str = Field(min_length=1)
    task_id: str | None = None
    node_id: str | None = None
    status: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
