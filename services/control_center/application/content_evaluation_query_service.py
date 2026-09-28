"""调用中心读取 AI 评课持久化进度投影。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_platform.infrastructure.database.models import (
    ContentEvaluationRecordModel,
)


@dataclass(frozen=True)
class ContentEvaluationProgress:
    """供调用中心查询接口展示的数据库进度快照。"""

    status: str
    progress: float
    current_step: str | None
    result: dict[str, Any] | None
    error_message: str | None


class ContentEvaluationQueryService:
    """只读 MySQL 评课记录，不与任一内容分析实例直接通信。"""

    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    def get_by_task_id(self, task_id: str) -> ContentEvaluationProgress | None:
        with self.session_factory() as session:
            record = session.scalar(
                select(ContentEvaluationRecordModel).where(
                    ContentEvaluationRecordModel.task_id == task_id
                )
            )
            if record is None:
                return None
            return ContentEvaluationProgress(
                status=str(record.status),
                progress=float(record.progress or 0),
                current_step=record.current_step,
                result=record.result if isinstance(record.result, dict) else None,
                error_message=record.error_message,
            )


__all__ = ["ContentEvaluationProgress", "ContentEvaluationQueryService"]
