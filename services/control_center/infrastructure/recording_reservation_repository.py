"""调用中心录制容量预约数据库仓储。

本仓储只服务调用中心的录制准入，不进入公共 ``media_platform``。录制业务的时段
重叠、同流互斥和预约关闭语义由调用中心应用层决定，仓储只执行对应数据库操作。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.orm import Session

from media_platform.domain.task import TaskStatus
from media_platform.infrastructure.database.models import (
    MediaTaskModel,
    RecordingServerModel,
)


class RecordingReservationRepository:
    """录制计划时段容量查询与写锁适配器。"""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _active_reservation():
        return and_(
            MediaTaskModel.task_type == "record.stream",
            MediaTaskModel.recording_server_id.is_not(None),
            MediaTaskModel.reservation_start_at.is_not(None),
            MediaTaskModel.status.in_(
                (
                    TaskStatus.PENDING.value,
                    TaskStatus.PENDING.value.upper(),
                    TaskStatus.PROCESSING.value,
                    TaskStatus.PROCESSING.value.upper(),
                )
            ),
        )

    @staticmethod
    def _overlaps(*, start_at: datetime, end_at: datetime | None):
        conditions = [
            or_(
                MediaTaskModel.reservation_end_at.is_(None),
                MediaTaskModel.reservation_end_at > start_at,
            )
        ]
        if end_at is not None:
            conditions.append(MediaTaskModel.reservation_start_at < end_at)
        return and_(*conditions)

    def lock_active_server(
        self,
        *,
        server_id: str,
        updated_by: str,
    ) -> RecordingServerModel | None:
        """以实际更新获取服务器行写锁，串行同机录制预约准入。"""

        result = self.session.execute(
            update(RecordingServerModel)
            .where(
                RecordingServerModel.id == server_id,
                RecordingServerModel.status == "ACTIVE",
            )
            .values(updated_at=datetime.now(), updated_by=updated_by)
        )
        if result.rowcount != 1:
            return None
        self.session.flush()
        return self.session.get(RecordingServerModel, server_id)

    def count_overlapping(
        self,
        *,
        recording_server_id: str,
        start_at: datetime,
        end_at: datetime | None,
    ) -> int:
        """统计同一录制服务器目标时段内的有效预约数。"""

        return int(
            self.session.scalar(
                select(func.count(MediaTaskModel.id)).where(
                    self._active_reservation(),
                    MediaTaskModel.recording_server_id == recording_server_id,
                    self._overlaps(start_at=start_at, end_at=end_at),
                )
            )
            or 0
        )

    def find_overlapping_stream(
        self,
        *,
        app: str,
        stream_id: str,
        start_at: datetime,
        end_at: datetime | None,
    ) -> MediaTaskModel | None:
        """查找同一技术流的重叠有效预约。"""

        return self.session.scalar(
            select(MediaTaskModel)
            .where(
                self._active_reservation(),
                MediaTaskModel.recording_app == app,
                MediaTaskModel.recording_stream_id == stream_id,
                self._overlaps(start_at=start_at, end_at=end_at),
            )
            .order_by(MediaTaskModel.reservation_start_at.asc())
            .limit(1)
        )

    def close(
        self,
        task_id: str,
        *,
        ended_at: datetime,
        updated_by: str,
    ) -> bool:
        """把开放式或提前停止预约收口到真实停止时间。"""

        result = self.session.execute(
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
                updated_by=updated_by,
            )
        )
        return result.rowcount == 1


__all__ = ["RecordingReservationRepository"]
