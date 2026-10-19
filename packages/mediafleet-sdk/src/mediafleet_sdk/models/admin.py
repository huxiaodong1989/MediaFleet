"""运维总览响应模型。"""

from __future__ import annotations

from mediafleet_sdk.models.base import SDKModel


class QueueSummary(SDKModel):
    queue_name: str
    message_count: int | None
    consumer_count: int | None
    status: str
    error_message: str | None = None


class AdminOverview(SDKModel):
    tasks_by_status: dict[str, int]
    tasks_by_type: dict[str, int]
    nodes_by_status: dict[str, int]
    queues: list[QueueSummary]


__all__ = ["AdminOverview", "QueueSummary"]
