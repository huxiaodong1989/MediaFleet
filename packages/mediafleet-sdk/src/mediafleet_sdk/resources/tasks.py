"""媒体任务与产物只读资源。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

from mediafleet_sdk.models import (
    CallbackRetryResult,
    TaskCreate,
    TaskCreateResult,
    TaskDetail,
    TaskFile,
    TaskPage,
    TaskRetryResult,
)
from mediafleet_sdk.resources._params import compact_params


def _idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not 8 <= len(normalized) <= 128:
        raise ValueError("idempotency_key 长度必须在 8 到 128 之间")
    return normalized


class TasksResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        task_type: str | None = None,
        status: str | None = None,
        node_id: str | None = None,
        school_code: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> TaskPage:
        return self._client._get_model(
            "api/v1/admin/tasks",
            TaskPage,
            params=compact_params(
                page=page,
                page_size=page_size,
                task_type=task_type,
                status=status,
                node_id=node_id,
                school_code=school_code,
                created_from=created_from,
                created_to=created_to,
            ),
        )

    def get(self, task_id: str) -> TaskDetail:
        return self._client._get_model(
            f"api/v1/tasks/{quote(task_id, safe='')}", TaskDetail
        )

    def list_files(self, task_id: str) -> list[TaskFile]:
        return self._client._get_model(
            f"api/v1/tasks/{quote(task_id, safe='')}/files", list[TaskFile]
        )

    def create(
        self,
        task: TaskCreate,
        *,
        idempotency_key: str,
    ) -> TaskCreateResult:
        return self._client._request_model(
            "POST",
            "api/v1/tasks",
            TaskCreateResult,
            json=task.model_dump(mode="json", exclude_none=True),
            extra_headers={"Idempotency-Key": _idempotency_key(idempotency_key)},
        )

    def retry(self, task_id: str) -> TaskRetryResult:
        return self._client._request_model(
            "POST",
            f"api/v1/admin/tasks/{quote(task_id, safe='')}/retry",
            TaskRetryResult,
        )

    def retry_callback(self, task_id: str) -> CallbackRetryResult:
        return self._client._request_model(
            "POST",
            f"api/v1/admin/tasks/{quote(task_id, safe='')}/callback/retry",
            CallbackRetryResult,
        )


class AsyncTasksResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        task_type: str | None = None,
        status: str | None = None,
        node_id: str | None = None,
        school_code: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> TaskPage:
        return await self._client._get_model(
            "api/v1/admin/tasks",
            TaskPage,
            params=compact_params(
                page=page,
                page_size=page_size,
                task_type=task_type,
                status=status,
                node_id=node_id,
                school_code=school_code,
                created_from=created_from,
                created_to=created_to,
            ),
        )

    async def get(self, task_id: str) -> TaskDetail:
        return await self._client._get_model(
            f"api/v1/tasks/{quote(task_id, safe='')}", TaskDetail
        )

    async def list_files(self, task_id: str) -> list[TaskFile]:
        return await self._client._get_model(
            f"api/v1/tasks/{quote(task_id, safe='')}/files", list[TaskFile]
        )

    async def create(
        self,
        task: TaskCreate,
        *,
        idempotency_key: str,
    ) -> TaskCreateResult:
        return await self._client._request_model(
            "POST",
            "api/v1/tasks",
            TaskCreateResult,
            json=task.model_dump(mode="json", exclude_none=True),
            extra_headers={"Idempotency-Key": _idempotency_key(idempotency_key)},
        )

    async def retry(self, task_id: str) -> TaskRetryResult:
        return await self._client._request_model(
            "POST",
            f"api/v1/admin/tasks/{quote(task_id, safe='')}/retry",
            TaskRetryResult,
        )

    async def retry_callback(self, task_id: str) -> CallbackRetryResult:
        return await self._client._request_model(
            "POST",
            f"api/v1/admin/tasks/{quote(task_id, safe='')}/callback/retry",
            CallbackRetryResult,
        )


__all__ = ["AsyncTasksResource", "TasksResource"]
