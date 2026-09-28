"""管理后台 API 的输入输出模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AdminTaskSummaryResponse(BaseModel):
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
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AdminTaskPageResponse(BaseModel):
    items: list[AdminTaskSummaryResponse]
    total: int
    page: int
    page_size: int


class AdminNodeResponse(BaseModel):
    node_id: str
    node_code: str
    node_name: str
    node_type: str
    status: str
    readiness_status: str
    agent_url: str | None
    zlm_api_url: str | None
    capabilities: list[str]
    capacity: dict[str, Any]
    readiness_details: dict[str, Any]
    last_heartbeat_at: datetime | None
    active_bindings: int
    active_tasks: int
    recording_server_code: str | None
    recording_server_status: str | None


class AdminNodePageResponse(BaseModel):
    items: list[AdminNodeResponse]
    total: int
    page: int
    page_size: int


class AdminBindingResponse(BaseModel):
    binding_id: str
    school_code: str
    resource_type: str
    space_id: str | None
    node_id: str
    node_code: str | None
    app: str
    stream_id: str
    stream_name: str | None
    stream_mode: str
    status: str
    version: int
    last_active_at: datetime | None


class AdminBindingPageResponse(BaseModel):
    items: list[AdminBindingResponse]
    total: int
    page: int
    page_size: int


class AdminQueueResponse(BaseModel):
    queue_name: str
    message_count: int | None
    consumer_count: int | None
    status: str
    error_message: str | None = None


class AdminOverviewResponse(BaseModel):
    tasks_by_status: dict[str, int]
    tasks_by_type: dict[str, int]
    nodes_by_status: dict[str, int]
    queues: list[AdminQueueResponse]


class AdminTaskRetryResponse(BaseModel):
    accepted: bool = True
    task: AdminTaskSummaryResponse


class AdminCallbackRetryResponse(BaseModel):
    accepted: bool = True
    callback_result: dict[str, Any]


__all__ = [
    "AdminBindingResponse",
    "AdminBindingPageResponse",
    "AdminCallbackRetryResponse",
    "AdminNodeResponse",
    "AdminNodePageResponse",
    "AdminOverviewResponse",
    "AdminQueueResponse",
    "AdminTaskPageResponse",
    "AdminTaskRetryResponse",
    "AdminTaskSummaryResponse",
]
