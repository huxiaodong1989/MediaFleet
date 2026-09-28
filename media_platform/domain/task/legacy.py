"""历史任务模型兼容出口。

旧录制链路仍使用小写状态和 ``POST_PROCESSING`` 等扩展状态。新多实例任务链路
也使用 ``media_platform.domain.task.status.TaskStatus``，任务表状态保持小写，
避免改变原业务接口和数据库可见状态语义。
"""

from media_platform.domain.task.legacy_models import (
    Task,
    TaskCreate,
    TaskStatus,
    TaskType,
    TaskUpdate,
)

__all__ = [
    "Task",
    "TaskCreate",
    "TaskStatus",
    "TaskType",
    "TaskUpdate",
]
