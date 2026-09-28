"""调用中心任务创建与可靠发布应用服务。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from media_platform.application.ports import TaskPublisher
from media_platform.contracts.task import TaskDeliveryChannel, TaskDispatchMessage
from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from media_platform.infrastructure.database.repositories import MediaTaskRepository


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateTaskCommand:
    school_code: str
    task_type: str
    delivery_channel: TaskDeliveryChannel = TaskDeliveryChannel.MEDIA
    business_task_id: str | None = None
    routing_key: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    idempotency_key: str | None = None
    priority: int = 0
    max_retries: int = 3
    callback_url: str | None = None
    target_node_id: str | None = None
    created_by: str = "SYSTEM"


@dataclass(frozen=True)
class TaskCreationResult:
    task_id: str
    created: bool


@dataclass(frozen=True)
class DispatchBatchResult:
    claimed: int
    published: int
    failed_task_ids: tuple[str, ...] = ()


class TaskDispatchService:
    """以 MySQL 任务状态协调多个调用中心实例和 RabbitMQ 发布。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        publisher: TaskPublisher,
    ):
        self.session_factory = session_factory
        self.publisher = publisher

    @staticmethod
    def _validate_create(command: CreateTaskCommand) -> None:
        if not command.school_code.strip():
            raise ValueError("school_code 不能为空")
        if not command.task_type.strip():
            raise ValueError("task_type 不能为空")
        if command.routing_key is not None and not command.routing_key.strip():
            raise ValueError("routing_key 不能为空")
        if command.priority < 0 or command.priority > 255:
            raise ValueError("priority 必须在0到255之间")
        if command.max_retries < 1:
            raise ValueError("max_retries 必须大于0")

    @staticmethod
    def _effective_routing_key(command: CreateTaskCommand) -> str:
        """通用媒体任务默认按任务类型路由，调用方无需重复传 routing_key。"""

        return (command.routing_key or command.task_type).strip()

    @staticmethod
    def _find_existing(
        repository: MediaTaskRepository, command: CreateTaskCommand
    ) -> MediaTaskModel | None:
        if command.idempotency_key:
            existing = repository.get_by_idempotency_key(
                command.school_code, command.idempotency_key
            )
            if existing is not None:
                return existing
        if command.request_id:
            return repository.get_by_request_id(
                command.school_code, command.request_id
            )
        return None

    def create_task(self, command: CreateTaskCommand) -> TaskCreationResult:
        """幂等创建任务，新任务只写入国标任务表。"""

        self._validate_create(command)
        try:
            with self.session_factory() as session:
                with session.begin():
                    repository = MediaTaskRepository(session)
                    existing = self._find_existing(repository, command)
                    if existing is not None:
                        LOGGER.info(
                            "媒体任务幂等命中: task_id=%s, task_type=%s, "
                            "request_id=%s, idempotency_key=%s",
                            existing.id,
                            existing.task_type,
                            command.request_id,
                            command.idempotency_key,
                        )
                        return TaskCreationResult(existing.id, created=False)

                    request_id = command.request_id or f"auto-{uuid4().hex}"
                    task = MediaTaskModel(
                        id=str(uuid4()),
                        request_id=request_id,
                        idempotency_key=command.idempotency_key,
                        business_task_id=command.business_task_id,
                        task_type=command.task_type,
                        delivery_channel=command.delivery_channel.value,
                        routing_key=self._effective_routing_key(command),
                        status=TaskStatus.PENDING.value,
                        priority=command.priority,
                        progress=0,
                        params=command.params,
                        callback_url=command.callback_url,
                        executor_node_id=command.target_node_id,
                        retry_count=0,
                        max_retries=command.max_retries,
                        publish_status=PublishStatus.PENDING.value,
                        created_by=command.created_by,
                        updated_by=command.created_by,
                        school_code=command.school_code,
                    )
                    repository.add(task)
                    LOGGER.info(
                        "媒体任务已写入MySQL: task_id=%s, task_type=%s, "
                        "routing_key=%s, publish_status=%s",
                        task.id,
                        task.task_type,
                        task.routing_key,
                        task.publish_status,
                    )
                    return TaskCreationResult(task.id, created=True)
        except IntegrityError:
            # 多实例并发创建相同幂等键时，唯一约束决定胜者。
            with self.session_factory() as session:
                repository = MediaTaskRepository(session)
                existing = self._find_existing(repository, command)
                if existing is not None:
                    return TaskCreationResult(existing.id, created=False)
            raise

    @staticmethod
    def _to_message(task: MediaTaskModel) -> TaskDispatchMessage:
        if not task.message_id:
            raise RuntimeError(f"领取后的任务缺少message_id: {task.id}")
        return TaskDispatchMessage(
            message_id=task.message_id,
            trace_id=task.request_id or task.id,
            idempotency_key=task.idempotency_key,
            task_id=task.id,
            business_task_id=task.business_task_id,
            school_code=task.school_code,
            task_type=task.task_type,
            routing_key=task.routing_key,
            delivery_channel=task.delivery_channel,
            priority=task.priority,
            target_node_id=task.executor_node_id,
            params=task.params,
            attempt=task.retry_count,
            max_attempts=task.max_retries,
            callback_url=task.callback_url,
        )

    def dispatch_batch(
        self,
        instance_id: str,
        *,
        limit: int = 10,
        lock_timeout: timedelta = timedelta(minutes=5),
        now: datetime | None = None,
    ) -> DispatchBatchResult:
        """领取任务并逐条等待 RabbitMQ Publisher Confirm。"""

        with self.session_factory() as session:
            with session.begin():
                tasks = MediaTaskRepository(session).claim_pending(
                    instance_id,
                    limit=limit,
                    lock_timeout=lock_timeout,
                    now=now,
                    excluded_task_types=("record.stream",),
                )
                messages = [self._to_message(task) for task in tasks]
        if messages:
            LOGGER.info(
                "调用中心已从MySQL领取待发布任务: instance_id=%s, count=%s, "
                "task_ids=%s",
                instance_id,
                len(messages),
                [message.task_id for message in messages],
            )

        published = 0
        failed_task_ids: list[str] = []
        for message in messages:
            try:
                LOGGER.info(
                    "准备发布媒体任务消息: task_id=%s, message_id=%s, "
                    "routing_key=%s, attempt=%s/%s",
                    message.task_id,
                    message.message_id,
                    message.routing_key,
                    message.attempt,
                    message.max_attempts,
                )
                receipt = self.publisher.publish(message)
                if not receipt.confirmed or receipt.message_id != message.message_id:
                    raise RuntimeError(
                        f"Publisher Confirm不匹配: task_id={message.task_id}"
                    )
                with self.session_factory() as session:
                    with session.begin():
                        updated = MediaTaskRepository(session).mark_published(
                            message.task_id,
                            instance_id,
                            message.message_id,
                            published_at=now,
                        )
                        if not updated:
                            raise RuntimeError(
                                f"发布成功但任务状态确认失败: {message.task_id}"
                            )
                published += 1
                LOGGER.info(
                    "媒体任务消息已确认发布: task_id=%s, message_id=%s",
                    message.task_id,
                    message.message_id,
                )
            except Exception:
                LOGGER.exception(
                    "任务消息发布或状态确认失败: task_id=%s, message_id=%s",
                    message.task_id,
                    message.message_id,
                )
                failed_task_ids.append(message.task_id)
                with self.session_factory() as session:
                    with session.begin():
                        MediaTaskRepository(session).release_claim(
                            message.task_id, instance_id
                        )

        return DispatchBatchResult(
            claimed=len(messages),
            published=published,
            failed_task_ids=tuple(failed_task_ids),
        )


__all__ = [
    "CreateTaskCommand",
    "DispatchBatchResult",
    "TaskCreationResult",
    "TaskDispatchService",
]
