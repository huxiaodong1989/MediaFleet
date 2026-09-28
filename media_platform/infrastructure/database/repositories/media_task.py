"""国标媒体任务表仓储。"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel


_UNSET = object()


class MediaTaskRepository:
    """只读写 `media_task`，不包含旧 `tasks` 兼容逻辑。"""

    def __init__(self, session: Session):
        self.session = session

    def add(self, task: MediaTaskModel) -> MediaTaskModel:
        self.session.add(task)
        self.session.flush()
        return task

    def get(self, task_id: str) -> MediaTaskModel | None:
        return self.session.get(MediaTaskModel, task_id)

    def get_by_idempotency_key(
        self, school_code: str, idempotency_key: str
    ) -> MediaTaskModel | None:
        return self.session.scalar(
            select(MediaTaskModel).where(
                MediaTaskModel.school_code == school_code,
                MediaTaskModel.idempotency_key == idempotency_key,
            )
        )

    def get_by_request_id(
        self, school_code: str, request_id: str
    ) -> MediaTaskModel | None:
        return self.session.scalar(
            select(MediaTaskModel).where(
                MediaTaskModel.school_code == school_code,
                MediaTaskModel.request_id == request_id,
            )
        )

    def get_by_business_task_id(
        self,
        business_task_id: str,
        *,
        task_type: str | None = None,
        school_code: str | None = None,
    ) -> MediaTaskModel | None:
        conditions = [MediaTaskModel.business_task_id == business_task_id]
        if task_type is not None:
            conditions.append(MediaTaskModel.task_type == task_type)
        if school_code is not None:
            conditions.append(MediaTaskModel.school_code == school_code)
        return self.session.scalar(
            select(MediaTaskModel)
            .where(*conditions)
            .order_by(MediaTaskModel.created_at.desc())
            .limit(1)
        )

    @staticmethod
    def _claimable(stale_before: datetime):
        return or_(
            MediaTaskModel.publish_status == PublishStatus.PENDING.value,
            and_(
                MediaTaskModel.publish_status == PublishStatus.CLAIMED.value,
                MediaTaskModel.locked_at < stale_before,
            ),
        )

    @staticmethod
    def _status_values(status: TaskStatus) -> tuple[str, str]:
        """返回任务状态的新旧写法。

        当前代码统一写入小写任务状态；这里保留大写匹配，是为了让已经由
        前一版重构写入的本地/测试数据仍能被继续领取、完成或失败，不需要
        研发为了状态大小写变更清空库。
        """

        return (status.value, status.value.upper())

    def claim_pending(
        self,
        instance_id: str,
        *,
        limit: int = 10,
        lock_timeout: timedelta = timedelta(minutes=5),
        now: datetime | None = None,
        excluded_task_types: tuple[str, ...] = (),
    ) -> list[MediaTaskModel]:
        """通过条件更新原子领取任务，多个调用中心可安全竞争。"""

        instance_id = instance_id.strip()
        if not instance_id:
            raise ValueError("instance_id 不能为空")
        if limit < 1:
            raise ValueError("limit 必须大于0")
        if lock_timeout <= timedelta(0):
            raise ValueError("lock_timeout 必须大于0")

        now = now or datetime.now()
        stale_before = now - lock_timeout
        candidate_query = select(MediaTaskModel.id).where(
            self._claimable(stale_before)
        )
        if excluded_task_types:
            candidate_query = candidate_query.where(
                MediaTaskModel.task_type.not_in(excluded_task_types)
            )
        candidate_ids = list(
            self.session.scalars(
                candidate_query
                .order_by(
                    MediaTaskModel.priority.desc(),
                    MediaTaskModel.created_at.asc(),
                )
                .limit(limit * 4)
            )
        )

        claimed_ids: list[str] = []
        claim_filters = [self._claimable(stale_before)]
        if excluded_task_types:
            claim_filters.append(MediaTaskModel.task_type.not_in(excluded_task_types))
        for task_id in candidate_ids:
            if len(claimed_ids) >= limit:
                break
            result = self.session.execute(
                update(MediaTaskModel)
                .where(
                    MediaTaskModel.id == task_id,
                    *claim_filters,
                )
                .values(
                    publish_status=PublishStatus.CLAIMED.value,
                    message_id=func.coalesce(
                        MediaTaskModel.message_id, uuid4().hex
                    ),
                    locked_by=instance_id,
                    locked_at=now,
                )
            )
            if result.rowcount == 1:
                claimed_ids.append(task_id)

        if not claimed_ids:
            return []
        self.session.flush()
        return list(
            self.session.scalars(
                select(MediaTaskModel)
                .where(MediaTaskModel.id.in_(claimed_ids))
                .order_by(
                    MediaTaskModel.priority.desc(),
                    MediaTaskModel.created_at.asc(),
                )
            )
        )

    def mark_published(
        self,
        task_id: str,
        instance_id: str,
        message_id: str,
        *,
        published_at: datetime | None = None,
    ) -> bool:
        """只有当前领取实例可以确认消息发布成功。"""

        if not message_id.strip():
            raise ValueError("message_id 不能为空")
        result = self.session.execute(
            update(MediaTaskModel)
            .where(
                MediaTaskModel.id == task_id,
                MediaTaskModel.publish_status == PublishStatus.CLAIMED.value,
                MediaTaskModel.locked_by == instance_id,
                MediaTaskModel.message_id == message_id,
            )
            .values(
                publish_status=PublishStatus.PUBLISHED.value,
                published_at=published_at or datetime.now(),
                locked_by=None,
                locked_at=None,
            )
        )
        return result.rowcount == 1

    def release_claim(self, task_id: str, instance_id: str) -> bool:
        """发布前失败时释放领取，允许其他实例重试。"""

        result = self.session.execute(
            update(MediaTaskModel)
            .where(
                MediaTaskModel.id == task_id,
                MediaTaskModel.publish_status == PublishStatus.CLAIMED.value,
                MediaTaskModel.locked_by == instance_id,
            )
            .values(
                publish_status=PublishStatus.PENDING.value,
                locked_by=None,
                locked_at=None,
            )
        )
        return result.rowcount == 1

    def mark_publish_failed(
        self, task_id: str, instance_id: str, error_message: str
    ) -> bool:
        """超过发布重试上限后标记失败。"""

        result = self.session.execute(
            update(MediaTaskModel)
            .where(
                MediaTaskModel.id == task_id,
                MediaTaskModel.publish_status == PublishStatus.CLAIMED.value,
                MediaTaskModel.locked_by == instance_id,
            )
            .values(
                publish_status=PublishStatus.FAILED.value,
                error_message=error_message,
                locked_by=None,
                locked_at=None,
            )
        )
        return result.rowcount == 1

    def claim_execution(
        self,
        task_id: str,
        message_id: str,
        worker_id: str,
        *,
        attempt: int,
        stale_before: datetime,
        started_at: datetime | None = None,
    ) -> bool:
        """兼容旧调用方，原子领取一条已发布任务的执行权。"""

        return (
            self.claim_execution_with_lease(
                task_id,
                message_id,
                worker_id,
                attempt=attempt,
                stale_before=stale_before,
                started_at=started_at,
                lease_timeout=None,
            )
            is not None
        )

    def claim_execution_with_lease(
        self,
        task_id: str,
        message_id: str,
        worker_id: str,
        *,
        attempt: int,
        stale_before: datetime,
        started_at: datetime | None = None,
        lease_timeout: timedelta | None,
    ) -> int | None:
        """原子领取执行权并返回新的执行代次。

        正常待处理任务、等待重试任务，以及超过执行超时的 ``PROCESSING`` 任务
        都可以被领取。条件更新保证即使 RabbitMQ 重复投递或多个 Worker 同时
        收到重复消息，最终也只有一个实例能够把任务更新为处理中。启用租约后，
        旧执行者即使迟到，也不能继续提交结果。
        """

        worker_id = worker_id.strip()
        if not worker_id:
            raise ValueError("worker_id 不能为空")
        if not message_id.strip():
            raise ValueError("message_id 不能为空")
        if attempt < 0:
            raise ValueError("attempt 不能小于0")

        started_at = started_at or datetime.now()
        lease_expires_at = (
            started_at + lease_timeout if lease_timeout is not None else None
        )
        claimable_status = or_(
            MediaTaskModel.status.in_(self._status_values(TaskStatus.PENDING)),
            and_(
                MediaTaskModel.status.in_(
                    self._status_values(TaskStatus.PROCESSING)
                ),
                or_(
                    MediaTaskModel.lease_expires_at < started_at,
                    and_(
                        MediaTaskModel.lease_expires_at.is_(None),
                        MediaTaskModel.started_at < stale_before,
                    ),
                ),
            ),
        )
        result = self.session.execute(
            update(MediaTaskModel)
            .where(
                MediaTaskModel.id == task_id,
                MediaTaskModel.message_id == message_id,
                MediaTaskModel.publish_status == PublishStatus.PUBLISHED.value,
                MediaTaskModel.retry_count <= attempt,
                MediaTaskModel.max_retries > attempt,
                claimable_status,
            )
            .values(
                status=TaskStatus.PROCESSING.value,
                executor_node_id=worker_id,
                retry_count=attempt,
                progress=0,
                error_message=None,
                started_at=started_at,
                completed_at=None,
                execution_generation=MediaTaskModel.execution_generation + 1,
                lease_owner=worker_id if lease_timeout is not None else None,
                lease_expires_at=lease_expires_at,
                updated_by=worker_id,
            )
        )
        if result.rowcount != 1:
            return None
        self.session.flush()
        return self.session.scalar(
            select(MediaTaskModel.execution_generation).where(
                MediaTaskModel.id == task_id,
                MediaTaskModel.message_id == message_id,
            )
        )

    def renew_execution_lease(
        self,
        task_id: str,
        message_id: str,
        worker_id: str,
        execution_generation: int,
        *,
        lease_timeout: timedelta,
        now: datetime | None = None,
    ) -> bool:
        """续租当前执行权；过期或代次不匹配时拒绝续租。"""

        now = now or datetime.now()
        result = self.session.execute(
            update(MediaTaskModel)
            .where(
                MediaTaskModel.id == task_id,
                MediaTaskModel.message_id == message_id,
                MediaTaskModel.status.in_(self._status_values(TaskStatus.PROCESSING)),
                MediaTaskModel.executor_node_id == worker_id,
                MediaTaskModel.execution_generation == execution_generation,
                MediaTaskModel.lease_owner == worker_id,
                MediaTaskModel.lease_expires_at >= now,
            )
            .values(lease_expires_at=now + lease_timeout, updated_by=worker_id)
        )
        return result.rowcount == 1

    def mark_execution_retry(
        self,
        task_id: str,
        message_id: str,
        worker_id: str,
        *,
        next_attempt: int,
        error_message: str,
        execution_generation: int | None = None,
        now: datetime | None = None,
    ) -> bool:
        """执行失败后恢复为待处理，并记录下一次消息尝试序号。"""

        conditions = [
            MediaTaskModel.id == task_id,
            MediaTaskModel.message_id == message_id,
            MediaTaskModel.status.in_(
                self._status_values(TaskStatus.PROCESSING)
            ),
            MediaTaskModel.executor_node_id == worker_id,
        ]
        if execution_generation is not None:
            conditions.extend(
                [
                    MediaTaskModel.execution_generation == execution_generation,
                    MediaTaskModel.lease_owner == worker_id,
                    MediaTaskModel.lease_expires_at >= (now or datetime.now()),
                ]
            )
        result = self.session.execute(
            update(MediaTaskModel)
            .where(*conditions)
            .values(
                status=TaskStatus.PENDING.value,
                retry_count=next_attempt,
                error_message=error_message,
                executor_node_id=None,
                started_at=None,
                lease_owner=None,
                lease_expires_at=None,
                updated_by=worker_id,
            )
        )
        return result.rowcount == 1

    def mark_execution_completed(
        self,
        task_id: str,
        message_id: str,
        worker_id: str,
        result_payload: dict,
        *,
        completed_at: datetime | None = None,
        execution_generation: int | None = None,
        now: datetime | None = None,
    ) -> bool:
        """只有当前执行 Worker 可以把任务标记为完成。"""

        conditions = [
            MediaTaskModel.id == task_id,
            MediaTaskModel.message_id == message_id,
            MediaTaskModel.status.in_(
                self._status_values(TaskStatus.PROCESSING)
            ),
            MediaTaskModel.executor_node_id == worker_id,
        ]
        if execution_generation is not None:
            conditions.extend(
                [
                    MediaTaskModel.execution_generation == execution_generation,
                    MediaTaskModel.lease_owner == worker_id,
                    MediaTaskModel.lease_expires_at >= (now or datetime.now()),
                ]
            )
        result = self.session.execute(
            update(MediaTaskModel)
            .where(*conditions)
            .values(
                status=TaskStatus.COMPLETED.value,
                progress=100,
                result=result_payload,
                error_message=None,
                completed_at=completed_at or datetime.now(),
                lease_owner=None,
                lease_expires_at=None,
                updated_by=worker_id,
            )
        )
        return result.rowcount == 1

    def mark_execution_failed(
        self,
        task_id: str,
        message_id: str,
        worker_id: str,
        error_message: str,
        *,
        completed_at: datetime | None = None,
        execution_generation: int | None = None,
        now: datetime | None = None,
    ) -> bool:
        """记录不可恢复或已耗尽重试次数的最终失败状态。"""

        conditions = [
            MediaTaskModel.id == task_id,
            MediaTaskModel.message_id == message_id,
            MediaTaskModel.status.in_(
                self._status_values(TaskStatus.PROCESSING)
            ),
            MediaTaskModel.executor_node_id == worker_id,
        ]
        if execution_generation is not None:
            conditions.extend(
                [
                    MediaTaskModel.execution_generation == execution_generation,
                    MediaTaskModel.lease_owner == worker_id,
                    MediaTaskModel.lease_expires_at >= (now or datetime.now()),
                ]
            )
        result = self.session.execute(
            update(MediaTaskModel)
            .where(*conditions)
            .values(
                status=TaskStatus.FAILED.value,
                error_message=error_message,
                completed_at=completed_at or datetime.now(),
                lease_owner=None,
                lease_expires_at=None,
                updated_by=worker_id,
            )
        )
        return result.rowcount == 1

    def mark_callback_result(
        self,
        task_id: str,
        callback_result: dict,
        *,
        updated_by: str = "control-center",
    ) -> bool:
        """记录业务回调执行结果。

        ``media_task.hdjg`` 是首期回调结果事实字段。成功回调用于重复事件去重；
        失败回调记录最近一次错误，RabbitMQ 事件消息仍可按延迟重试策略再次投递。
        """

        result = self.session.execute(
            update(MediaTaskModel)
            .where(MediaTaskModel.id == task_id)
            .values(
                callback_result=callback_result,
                updated_by=updated_by,
            )
        )
        return result.rowcount == 1

    def update_status(
        self,
        task_id: str,
        status: TaskStatus | str,
        *,
        progress: float | int | None = None,
        result_payload: dict | None | object = _UNSET,
        error_message: str | None | object = _UNSET,
        completed_at: datetime | None | object = _UNSET,
        updated_by: str,
    ) -> bool:
        """按任务主键更新通用任务状态字段。

        本方法只表达 `media_task` 表的通用状态更新能力，不包含录制、Worker
        或调用中心的业务语义。具体服务应在各自应用层决定何时写入
        `post_processing/completed/failed` 以及结果字段内容。
        """

        status_value = status.value if isinstance(status, TaskStatus) else str(status)
        values = {
            "status": status_value,
            "updated_by": updated_by,
        }
        if progress is not None:
            values["progress"] = progress
        if result_payload is not _UNSET:
            values["result"] = result_payload
        if error_message is not _UNSET:
            values["error_message"] = error_message
        if completed_at is not _UNSET:
            values["completed_at"] = completed_at

        result = self.session.execute(
            update(MediaTaskModel)
            .where(MediaTaskModel.id == task_id)
            .values(**values)
        )
        return result.rowcount == 1


__all__ = ["MediaTaskRepository"]
