"""调用中心管理后台的查询和受控运维操作。

管理后台只读取或修改调用中心负责的事实：任务、节点、绑定和录制单元。
它不执行 FFmpeg、不读取录像目录，也不把进程内状态当成事实来源。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pika
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from media_platform.contracts.topology import (
    CONTENT_ANALYSIS_TASK_QUEUE,
    recorder_command_queue_name,
)
from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    MediaStreamBindingModel,
    MediaTaskModel,
    RecordingServerModel,
)
from media_platform.infrastructure.messaging.pika_task_publisher import (
    RabbitMQPublisherConfig,
)


MEDIA_WORKER_QUEUE = "media-worker.tasks"


@dataclass(frozen=True)
class AdminTaskSummary:
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


@dataclass(frozen=True)
class AdminTaskPage:
    items: tuple[AdminTaskSummary, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class AdminNodeSummary:
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


@dataclass(frozen=True)
class AdminNodePage:
    items: tuple[AdminNodeSummary, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class AdminBindingSummary:
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


@dataclass(frozen=True)
class AdminBindingPage:
    items: tuple[AdminBindingSummary, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class AdminQueueSummary:
    queue_name: str
    message_count: int | None
    consumer_count: int | None
    status: str
    error_message: str | None = None


class AdminOperationError(ValueError):
    """管理后台操作不满足当前任务状态。"""


class RabbitQueueInspector:
    """读取已知持久队列的当前深度，不维护第二份队列事实。"""

    def __init__(self, config: RabbitMQPublisherConfig):
        self.config = config

    def inspect(self, queue_names: list[str]) -> tuple[AdminQueueSummary, ...]:
        if not queue_names:
            return ()
        connection = None
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=self.config.host,
                    port=self.config.port,
                    virtual_host=self.config.virtual_host,
                    credentials=pika.PlainCredentials(
                        self.config.username,
                        self.config.password,
                    ),
                    heartbeat=self.config.heartbeat,
                    blocked_connection_timeout=self.config.blocked_connection_timeout,
                    socket_timeout=self.config.socket_timeout,
                )
            )
            channel = connection.channel()
            results: list[AdminQueueSummary] = []
            for queue_name in queue_names:
                try:
                    declared = channel.queue_declare(queue=queue_name, passive=True)
                    results.append(
                        AdminQueueSummary(
                            queue_name=queue_name,
                            message_count=int(declared.method.message_count),
                            consumer_count=int(declared.method.consumer_count),
                            status="AVAILABLE",
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - one queue must not hide others
                    results.append(
                        AdminQueueSummary(
                            queue_name=queue_name,
                            message_count=None,
                            consumer_count=None,
                            status="UNAVAILABLE",
                            error_message=f"{type(exc).__name__}: {exc!r}",
                        )
                    )
            return tuple(results)
        except Exception as exc:  # noqa: BLE001 - management page remains readable
            message = f"{type(exc).__name__}: {exc!r}"
            return tuple(
                AdminQueueSummary(
                    queue_name=queue_name,
                    message_count=None,
                    consumer_count=None,
                    status="UNAVAILABLE",
                    error_message=message,
                )
                for queue_name in queue_names
            )
        finally:
            if connection is not None and not connection.is_closed:
                try:
                    connection.close()
                except pika.exceptions.AMQPError:
                    pass


class AdminService:
    """提供管理后台查询、任务重试和回调重发。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        queue_inspector: RabbitQueueInspector | None = None,
        callback_handler: Any | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.queue_inspector = queue_inspector
        self.callback_handler = callback_handler

    @staticmethod
    def _task_summary(task: MediaTaskModel) -> AdminTaskSummary:
        return AdminTaskSummary(
            task_id=task.id,
            school_code=task.school_code,
            task_type=task.task_type,
            status=task.status,
            publish_status=task.publish_status,
            progress=float(task.progress or 0),
            executor_node_id=task.executor_node_id,
            retry_count=int(task.retry_count or 0),
            max_retries=int(task.max_retries or 0),
            callback_result=task.callback_result,
            error_message=task.error_message,
            created_at=task.created_at,
            updated_at=task.updated_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
        )

    def list_tasks(
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
    ) -> AdminTaskPage:
        if page < 1 or page_size < 1 or page_size > 200:
            raise ValueError("page/page_size 参数无效")
        filters = []
        if task_type:
            filters.append(MediaTaskModel.task_type == task_type)
        if status:
            filters.append(func.lower(MediaTaskModel.status) == status.lower())
        if node_id:
            filters.append(MediaTaskModel.executor_node_id == node_id)
        if school_code:
            filters.append(MediaTaskModel.school_code == school_code)
        if created_from:
            filters.append(MediaTaskModel.created_at >= created_from)
        if created_to:
            filters.append(MediaTaskModel.created_at < created_to)

        with self.session_factory() as session:
            count_query = select(func.count()).select_from(MediaTaskModel)
            if filters:
                count_query = count_query.where(*filters)
            total = int(session.scalar(count_query) or 0)

            query = select(MediaTaskModel).order_by(
                MediaTaskModel.created_at.desc(),
                MediaTaskModel.id.desc(),
            )
            if filters:
                query = query.where(*filters)
            rows = list(
                session.scalars(
                    query.offset((page - 1) * page_size).limit(page_size)
                )
            )
            return AdminTaskPage(
                items=tuple(self._task_summary(row) for row in rows),
                total=total,
                page=page,
                page_size=page_size,
            )

    @staticmethod
    def _validate_page(page: int, page_size: int) -> None:
        if page < 1 or page_size < 1 or page_size > 200:
            raise ValueError("page/page_size 参数无效")

    def list_nodes_page(
        self, *, page: int = 1, page_size: int = 20
    ) -> AdminNodePage:
        """分页读取节点快照，避免管理页面一次性加载全部节点。"""

        self._validate_page(page, page_size)
        with self.session_factory() as session:
            binding_counts = dict(
                session.execute(
                    select(
                        MediaStreamBindingModel.node_id,
                        func.count(MediaStreamBindingModel.id),
                    )
                    .where(MediaStreamBindingModel.status == "ACTIVE")
                    .group_by(MediaStreamBindingModel.node_id)
                ).all()
            )
            task_counts = dict(
                session.execute(
                    select(MediaTaskModel.executor_node_id, func.count(MediaTaskModel.id))
                    .where(
                        MediaTaskModel.executor_node_id.is_not(None),
                        func.lower(MediaTaskModel.status).in_(
                            (TaskStatus.PROCESSING.value, TaskStatus.POST_PROCESSING.value)
                        ),
                    )
                    .group_by(MediaTaskModel.executor_node_id)
                ).all()
            )
            total = int(session.scalar(select(func.count()).select_from(MediaNodeModel)) or 0)
            rows = list(
                session.scalars(
                    select(MediaNodeModel).order_by(
                        MediaNodeModel.node_type.asc(),
                        MediaNodeModel.node_code.asc(),
                    ).offset((page - 1) * page_size).limit(page_size)
                )
            )
            servers = {
                row.recorder_node_id: row
                for row in session.scalars(select(RecordingServerModel)).all()
            }
            items = tuple(
                AdminNodeSummary(
                    node_id=row.id,
                    node_code=row.node_code,
                    node_name=row.node_name,
                    node_type=row.node_type,
                    status=row.status,
                    readiness_status=row.readiness_status,
                    agent_url=row.agent_url,
                    zlm_api_url=row.zlm_api_url,
                    capabilities=list(row.capabilities or []),
                    capacity=dict(row.capacity_config or {}),
                    readiness_details=dict(row.readiness_details or {}),
                    last_heartbeat_at=row.last_heartbeat_at,
                    active_bindings=int(binding_counts.get(row.id, 0)),
                    active_tasks=int(task_counts.get(row.id, 0)),
                    recording_server_code=(servers.get(row.id).server_code if row.id in servers else None),
                    recording_server_status=(servers.get(row.id).status if row.id in servers else None),
                )
                for row in rows
            )
            return AdminNodePage(
                items=items,
                total=total,
                page=page,
                page_size=page_size,
            )

    def list_nodes(self) -> tuple[AdminNodeSummary, ...]:
        """保留旧接口的兼容读取；页面和新调用方必须使用分页方法。"""

        return self.list_nodes_page(page=1, page_size=200).items

    def list_bindings_page(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        node_id: str | None = None,
        status: str | None = None,
        resource_type: str | None = None,
        space_id: str | None = None,
    ) -> AdminBindingPage:
        """分页读取绑定关系，筛选条件在数据库侧执行。"""

        self._validate_page(page, page_size)
        filters = []
        if node_id:
            filters.append(MediaStreamBindingModel.node_id == node_id)
        if status:
            filters.append(MediaStreamBindingModel.status == status)
        if resource_type:
            filters.append(MediaStreamBindingModel.resource_type == resource_type)
        if space_id:
            filters.append(MediaStreamBindingModel.space_id == space_id)
        with self.session_factory() as session:
            count_query = select(func.count()).select_from(MediaStreamBindingModel)
            if filters:
                count_query = count_query.where(*filters)
            total = int(session.scalar(count_query) or 0)
            query = select(MediaStreamBindingModel, MediaNodeModel.node_code).join(
                MediaNodeModel,
                MediaNodeModel.id == MediaStreamBindingModel.node_id,
                isouter=True,
            )
            if filters:
                query = query.where(*filters)
            query = query.order_by(
                MediaStreamBindingModel.updated_at.desc(),
                MediaStreamBindingModel.id.desc(),
            ).offset((page - 1) * page_size).limit(page_size)
            items = tuple(
                AdminBindingSummary(
                    binding_id=binding.id,
                    school_code=binding.school_code,
                    resource_type=binding.resource_type,
                    space_id=binding.space_id,
                    node_id=binding.node_id,
                    node_code=node_code,
                    app=binding.app,
                    stream_id=binding.stream_id,
                    stream_name=binding.stream_name,
                    stream_mode=binding.stream_mode,
                    status=binding.status,
                    version=int(binding.version or 0),
                    last_active_at=binding.last_active_at,
                )
                for binding, node_code in session.execute(query).all()
            )
            return AdminBindingPage(
                items=items,
                total=total,
                page=page,
                page_size=page_size,
            )

    def list_bindings(
        self,
        *,
        node_id: str | None = None,
        status: str | None = None,
        resource_type: str | None = None,
        space_id: str | None = None,
    ) -> tuple[AdminBindingSummary, ...]:
        """保留旧接口的兼容读取；页面和新调用方必须使用分页方法。"""

        return self.list_bindings_page(
            page=1,
            page_size=200,
            node_id=node_id,
            status=status,
            resource_type=resource_type,
            space_id=space_id,
        ).items

    def overview(self) -> dict[str, Any]:
        with self.session_factory() as session:
            task_counts = {
                str(status): int(count)
                for status, count in session.execute(
                    select(MediaTaskModel.status, func.count(MediaTaskModel.id)).group_by(
                        MediaTaskModel.status
                    )
                ).all()
            }
            type_counts = {
                str(task_type): int(count)
                for task_type, count in session.execute(
                    select(MediaTaskModel.task_type, func.count(MediaTaskModel.id)).group_by(
                        MediaTaskModel.task_type
                    )
                ).all()
            }
            node_counts = {
                str(status): int(count)
                for status, count in session.execute(
                    select(MediaNodeModel.status, func.count(MediaNodeModel.id)).group_by(
                        MediaNodeModel.status
                    )
                ).all()
            }
            node_codes = list(session.scalars(select(MediaNodeModel.node_code)))
        queues = ()
        if self.queue_inspector is not None:
            queues = self.queue_inspector.inspect(
                [MEDIA_WORKER_QUEUE, CONTENT_ANALYSIS_TASK_QUEUE]
                + [recorder_command_queue_name(code) for code in node_codes]
            )
        return {
            "tasks_by_status": task_counts,
            "tasks_by_type": type_counts,
            "nodes_by_status": node_counts,
            "queues": queues,
        }

    def retry_task(self, task_id: str, *, updated_by: str) -> AdminTaskSummary:
        with self.session_factory() as session:
            with session.begin():
                task = session.get(MediaTaskModel, task_id)
                if task is None:
                    raise LookupError(f"任务不存在: {task_id}")
                if task.task_type == "record.stream":
                    raise AdminOperationError("录制任务必须通过原录制接口重新发起")
                if str(task.status).lower() not in {
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                }:
                    raise AdminOperationError("只有失败或取消任务允许手动重试")
                task.status = TaskStatus.PENDING.value
                task.publish_status = PublishStatus.PENDING.value
                task.progress = 0
                task.result = None
                task.error_message = None
                task.executor_node_id = None
                task.retry_count = 0
                task.locked_by = None
                task.locked_at = None
                task.lease_owner = None
                task.lease_expires_at = None
                task.published_at = None
                task.started_at = None
                task.completed_at = None
                task.updated_by = updated_by
                session.flush()
                return self._task_summary(task)

    def retry_callback(self, task_id: str) -> dict[str, Any]:
        if self.callback_handler is None:
            raise AdminOperationError("调用中心回调处理器未启用")
        try:
            self.callback_handler.retry_callback(task_id)
        except LookupError:
            raise
        except Exception as exc:
            raise AdminOperationError(str(exc) or f"{type(exc).__name__}: {exc!r}") from exc
        with self.session_factory() as session:
            task = session.get(MediaTaskModel, task_id)
            if task is None:
                raise LookupError(f"任务不存在: {task_id}")
            return dict(task.callback_result or {})


__all__ = [
    "AdminBindingPage",
    "AdminBindingSummary",
    "AdminNodePage",
    "AdminNodeSummary",
    "AdminOperationError",
    "AdminQueueSummary",
    "AdminService",
    "AdminTaskPage",
    "AdminTaskSummary",
    "RabbitQueueInspector",
]
