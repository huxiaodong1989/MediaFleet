"""国标媒体文件表仓储。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_platform.infrastructure.database.models import MediaFileModel


class MediaTaskFileRepository:
    """只读写 `media_artifact`，用于查询媒体任务产物。"""

    def __init__(self, session: Session):
        self.session = session

    def list_by_task(self, task_id: str) -> list[MediaFileModel]:
        """按任务主键查询产物文件。

        产物文件由 media-worker 或 recorder-node 写入，调用中心只作为状态查询入口
        读取 MySQL 事实表，不读取本地文件目录或对象存储。
        """

        return list(
            self.session.scalars(
                select(MediaFileModel)
                .where(MediaFileModel.task_id == task_id)
                .order_by(MediaFileModel.created_at.asc(), MediaFileModel.id.asc())
            )
        )

    def add_many(self, files: list[MediaFileModel]) -> list[MediaFileModel]:
        """批量新增媒体产物文件。"""

        if not files:
            return []
        self.session.add_all(files)
        self.session.flush()
        return files


__all__ = ["MediaTaskFileRepository"]
