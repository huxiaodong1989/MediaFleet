"""录制任务结果状态写入服务。

本服务归属 recorder-node 应用层，负责把“录制进入后处理、录制完成、录制失败”
这些录制节点业务语义转换为国标任务表的通用状态更新。公共 `media_platform`
只提供表模型和通用仓储能力，不承载 recorder-node 的业务语义。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
from typing import Any

from sqlalchemy import case, or_, select, update
from sqlalchemy.orm import Session

from media_platform.domain.task import TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from media_platform.infrastructure.database.repositories import MediaTaskRepository
from media_platform.infrastructure.database.session import SessionLocal


LOGGER = logging.getLogger(__name__)


class RecordingTaskResultService:
    """录制任务状态和结果落库服务。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        node_id: str = "recorder-node",
    ) -> None:
        self.session_factory = session_factory or SessionLocal
        self.node_id = node_id

    def _close_recording_reservation(
        self,
        session: Session,
        *,
        task_id: str,
        ended_at: datetime,
    ) -> None:
        """录制真实停止后关闭容量预约，不把该业务语义放入公共仓储。"""

        session.execute(
            update(MediaTaskModel)
            .where(
                MediaTaskModel.id == task_id,
                MediaTaskModel.task_type == "record.stream",
                MediaTaskModel.reservation_start_at.is_not(None),
                or_(
                    MediaTaskModel.reservation_end_at.is_(None),
                    MediaTaskModel.reservation_end_at > ended_at,
                ),
            )
            .values(
                reservation_end_at=case(
                    (
                        MediaTaskModel.reservation_start_at > ended_at,
                        MediaTaskModel.reservation_start_at,
                    ),
                    else_=ended_at,
                ),
                updated_by=self.node_id,
            )
        )

    def mark_post_processing(
        self,
        *,
        task_id: str,
        result_url: str | None = None,
        recovery_context: dict[str, Any] | None = None,
    ) -> bool:
        """录制已停止并进入本机后处理队列。"""

        result_payload = {
            "message": "录制已停止，正在发现录像分片并执行后处理",
        }
        if result_url:
            result_payload["result_url"] = result_url
        if recovery_context:
            result_payload["post_processing_recovery"] = dict(recovery_context)
        with self.session_factory() as session:
            with session.begin():
                repository = MediaTaskRepository(session)
                self._close_recording_reservation(
                    session,
                    task_id=task_id,
                    ended_at=datetime.now(),
                )
                updated = repository.update_status(
                    task_id,
                    TaskStatus.POST_PROCESSING,
                    progress=90.0,
                    result_payload=result_payload,
                    error_message=None,
                    completed_at=None,
                    updated_by=self.node_id,
                )
                if updated:
                    session.execute(
                        update(MediaTaskModel)
                        .where(MediaTaskModel.id == task_id)
                        .values(callback_result=None)
                    )
        self._log_update_result(task_id, "post_processing", updated)
        return updated

    def mark_post_processing_if_active(
        self,
        *,
        task_id: str,
        result_url: str | None = None,
        recovery_context: dict[str, Any] | None = None,
    ) -> bool:
        """仅当任务仍为录制中/后处理中时登记恢复入队。

        后台扫描使用的是数据库快照。任务可能在扫描过程中已经完成；这里通过
        行锁再次校验状态，禁止旧快照把 completed/failed 反向覆盖成
        post_processing。
        """

        with self.session_factory() as session:
            with session.begin():
                task = session.scalar(
                    select(MediaTaskModel)
                    .where(MediaTaskModel.id == task_id)
                    .with_for_update()
                )
                if task is None or str(task.status or "").lower() not in {
                    TaskStatus.PROCESSING.value,
                    TaskStatus.POST_PROCESSING.value,
                }:
                    return False
                self._close_recording_reservation(
                    session,
                    task_id=task_id,
                    ended_at=datetime.now(),
                )
                result_payload = {
                    "message": "录制已停止，正在发现录像分片并执行后处理",
                }
                if result_url:
                    result_payload["result_url"] = result_url
                if recovery_context:
                    result_payload["post_processing_recovery"] = dict(
                        recovery_context
                    )
                task.status = TaskStatus.POST_PROCESSING.value
                task.progress = 90.0
                task.result = result_payload
                task.error_message = None
                task.completed_at = None
                task.callback_result = None
                task.updated_by = self.node_id
        self._log_update_result(task_id, "post_processing", True)
        return True

    def mark_completed(
        self,
        *,
        task_id: str,
        result_payload: dict[str, Any],
    ) -> bool:
        """录制后处理完成，写入最终完成状态和结果。"""

        with self.session_factory() as session:
            with session.begin():
                repository = MediaTaskRepository(session)
                self._close_recording_reservation(
                    session,
                    task_id=task_id,
                    ended_at=datetime.now(),
                )
                updated = repository.update_status(
                    task_id,
                    TaskStatus.COMPLETED,
                    progress=100,
                    result_payload=result_payload,
                    error_message=None,
                    completed_at=datetime.now(),
                    updated_by=self.node_id,
                )
        self._log_update_result(task_id, "completed", updated)
        return updated

    def mark_failed(
        self,
        *,
        task_id: str,
        error_message: str,
        result_payload: dict[str, Any] | None = None,
    ) -> bool:
        """录制或后处理失败，写入最终失败状态和错误信息。"""

        with self.session_factory() as session:
            with session.begin():
                repository = MediaTaskRepository(session)
                self._close_recording_reservation(
                    session,
                    task_id=task_id,
                    ended_at=datetime.now(),
                )
                updated = repository.update_status(
                    task_id,
                    TaskStatus.FAILED,
                    result_payload=result_payload,
                    error_message=error_message,
                    completed_at=datetime.now(),
                    updated_by=self.node_id,
                )
        self._log_update_result(task_id, "failed", updated)
        return updated

    def mark_callback_result(
        self,
        *,
        task_id: str,
        callback_result: dict[str, Any],
    ) -> bool:
        """写入录制业务回调执行结果。"""

        with self.session_factory() as session:
            with session.begin():
                updated = MediaTaskRepository(session).mark_callback_result(
                    task_id,
                    callback_result,
                    updated_by=self.node_id,
                )
        self._log_update_result(task_id, "callback_result", updated)
        return updated

    @staticmethod
    def _log_update_result(task_id: str, status: str, updated: bool) -> None:
        """记录任务结果写入情况。"""

        if updated:
            LOGGER.info("录制任务状态已写入MySQL: task_id=%s, status=%s", task_id, status)
        else:
            LOGGER.warning(
                "录制任务状态写入失败，任务不存在: task_id=%s, status=%s",
                task_id,
                status,
            )


__all__ = ["RecordingTaskResultService"]
