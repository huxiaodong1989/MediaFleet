"""媒体任务状态定义，不依赖数据库和消息中间件。"""

from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    POST_PROCESSING = "post_processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PublishStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


__all__ = ["PublishStatus", "TaskStatus"]
