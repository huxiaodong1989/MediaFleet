"""媒体任务和产物响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from mediafleet_sdk.models.base import Page, SDKModel, UtcDateTime


class TaskCreate(SDKModel):
    model_config = ConfigDict(extra="forbid")

    school_code: str = Field(min_length=1, max_length=64)
    task_type: str = Field(min_length=1, max_length=64)
    callback_url: str = Field(min_length=1, max_length=1000)
    priority: int = Field(default=0, ge=0, le=255)
    max_retries: int = Field(default=3, ge=1, le=100)
    params: dict[str, Any] = Field(default_factory=dict)
    target_node_id: str | None = Field(default=None, max_length=36)


class TaskCreateResult(SDKModel):
    task_id: str
    created: bool


class TaskSummary(SDKModel):
    task_id: str
    school_code: str
    task_type: str
    status: str
    publish_status: str
    progress: float
    executor_node_id: str | None
    retry_count: int
    max_retries: int
    callback_result: dict[str, Any] | None
    error_message: str | None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    started_at: UtcDateTime | None
    completed_at: UtcDateTime | None


class TaskDetail(SDKModel):
    task_id: str
    school_code: str
    task_type: str
    status: str
    publish_status: str
    progress: float
    params: dict[str, Any]
    result: dict[str, Any] | None = None
    error_message: str | None = None
    callback_url: str | None = None
    callback_result: dict[str, Any] | None = None
    executor_node_id: str | None = None
    retry_count: int
    max_retries: int
    message_id: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    published_at: UtcDateTime | None = None
    started_at: UtcDateTime | None = None
    completed_at: UtcDateTime | None = None


class TaskFile(SDKModel):
    file_id: str
    task_id: str
    school_code: str
    file_type: str
    file_name: str
    file_url: str
    relative_path: str | None = None
    bucket_name: str | None = None
    file_size: int
    mime_type: str
    extra_info: dict[str, Any] | None = None
    created_at: UtcDateTime


class TaskPage(Page[TaskSummary]):
    pass


class TaskRetryResult(SDKModel):
    accepted: bool
    task: TaskSummary


class CallbackRetryResult(SDKModel):
    accepted: bool
    callback_result: dict[str, Any]


__all__ = [
    "CallbackRetryResult",
    "TaskCreate",
    "TaskCreateResult",
    "TaskDetail",
    "TaskFile",
    "TaskPage",
    "TaskRetryResult",
    "TaskSummary",
]
