"""媒体任务状态和产物查询应用服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from media_platform.infrastructure.database.repositories import (
    MediaTaskFileRepository,
    MediaTaskRepository,
)


@dataclass(frozen=True)
class MediaTaskDetail:
    """调用中心公开的任务状态视图。"""

    task_id: str
    school_code: str
    task_type: str
    status: str
    publish_status: str
    progress: float
    params: dict[str, Any]
    result: dict[str, Any] | None
    error_message: str | None
    callback_url: str | None
    callback_result: dict[str, Any] | None
    executor_node_id: str | None
    retry_count: int
    max_retries: int
    message_id: str | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class MediaTaskFileDetail:
    """调用中心公开的任务产物文件视图。"""

    file_id: str
    task_id: str
    school_code: str
    file_type: str
    file_name: str
    file_url: str
    relative_path: str | None
    bucket_name: str | None
    file_size: int
    mime_type: str
    extra_info: dict[str, Any] | None
    created_at: datetime


class TaskQueryService:
    """从 MySQL 国标表查询媒体任务状态和产物。

    查询服务只读取 `media_task` 和 `media_artifact`。调用中心不在这里访问 Redis、
    RabbitMQ、本地录像目录或对象存储，确保 MySQL 始终是任务状态事实来源。
    """

    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    def get_task(self, task_id: str) -> MediaTaskDetail | None:
        """按创建任务返回的 `task_id` 查询任务状态。"""

        with self.session_factory() as session:
            task = MediaTaskRepository(session).get(task_id)
            if task is None:
                return None
            return self._detail(task)

    @staticmethod
    def _detail(task) -> MediaTaskDetail:
        return MediaTaskDetail(
            task_id=task.id,
            school_code=task.school_code,
            task_type=task.task_type,
            status=task.status,
            publish_status=task.publish_status,
            progress=float(task.progress or 0),
            params=task.params or {},
            result=task.result,
            error_message=task.error_message,
            callback_url=task.callback_url,
            callback_result=task.callback_result,
            executor_node_id=task.executor_node_id,
            retry_count=task.retry_count,
            max_retries=task.max_retries,
            message_id=task.message_id,
            created_at=task.created_at,
            updated_at=task.updated_at,
            published_at=task.published_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
        )

    def get_by_business_task_id(
        self,
        business_task_id: str,
        *,
        task_type: str | None = None,
        school_code: str | None = None,
    ) -> MediaTaskDetail | None:
        with self.session_factory() as session:
            task = MediaTaskRepository(session).get_by_business_task_id(
                business_task_id,
                task_type=task_type,
                school_code=school_code,
            )
            return self._detail(task) if task is not None else None

    def list_task_files(self, task_id: str) -> list[MediaTaskFileDetail] | None:
        """按任务主键查询产物文件。

        Returns:
            任务不存在时返回 `None`；任务存在但没有产物时返回空列表。
        """

        with self.session_factory() as session:
            task = MediaTaskRepository(session).get(task_id)
            if task is None:
                return None
            files = MediaTaskFileRepository(session).list_by_task(task_id)
            return [
                MediaTaskFileDetail(
                    file_id=file.id,
                    task_id=file.task_id,
                    school_code=file.school_code,
                    file_type=file.file_type,
                    file_name=file.file_name,
                    file_url=file.file_url,
                    relative_path=file.relative_path,
                    bucket_name=file.bucket_name,
                    file_size=int(file.file_size or 0),
                    mime_type=file.mime_type,
                    extra_info=file.extra_info,
                    created_at=file.created_at,
                )
                for file in files
            ]


__all__ = [
    "MediaTaskDetail",
    "MediaTaskFileDetail",
    "TaskQueryService",
]
