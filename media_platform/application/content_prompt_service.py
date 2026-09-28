"""调用中心和内容分析实例共享的提示词版本管理服务。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from media_platform.contracts.content_evaluation import PromptBundleContent
from media_platform.infrastructure.database.repositories.content_prompt_bundle import (
    GLOBAL_SCHOOL_CODE,
    PromptBundleRepository,
)


@dataclass(frozen=True)
class PromptBundleView:
    id: str
    school_code: str
    version: int
    status: str
    content: dict[str, Any]
    content_hash: str
    published_at: datetime | None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


class PromptManagementService:
    """直接读写共享 MySQL 中的整套评课提示词版本。"""

    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    @staticmethod
    def _view(row) -> PromptBundleView:
        return PromptBundleView(
            id=row.id,
            school_code=row.school_code,
            version=row.version,
            status=row.status,
            content=dict(row.content),
            content_hash=row.content_hash,
            published_at=row.published_at,
            created_by=row.created_by,
            updated_by=row.updated_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_versions(self, school_code: str = GLOBAL_SCHOOL_CODE) -> list[PromptBundleView]:
        with self.session_factory() as session:
            return [self._view(row) for row in PromptBundleRepository(session).list_versions(school_code)]

    def get_published(self, school_code: str) -> PromptBundleView | None:
        with self.session_factory() as session:
            row = PromptBundleRepository(session).get_published(school_code)
            return self._view(row) if row is not None else None

    def create_draft(self, content: PromptBundleContent, *, school_code: str, operator: str) -> PromptBundleView:
        with self.session_factory() as session:
            with session.begin():
                row = PromptBundleRepository(session).create_draft(content, school_code=school_code, operator=operator)
                view = self._view(row)
        return view

    def clone_draft(self, bundle_id: str, *, school_code: str, operator: str) -> PromptBundleView:
        with self.session_factory() as session:
            with session.begin():
                row = PromptBundleRepository(session).clone_as_draft(bundle_id, school_code=school_code, operator=operator)
                view = self._view(row)
        return view

    def update_draft(self, bundle_id: str, content: PromptBundleContent, *, operator: str) -> PromptBundleView:
        with self.session_factory() as session:
            with session.begin():
                row = PromptBundleRepository(session).update_draft(bundle_id, content, operator=operator)
                view = self._view(row)
        return view

    def publish(self, bundle_id: str, *, operator: str) -> PromptBundleView:
        with self.session_factory() as session:
            with session.begin():
                row = PromptBundleRepository(session).publish(bundle_id, operator=operator)
                view = self._view(row)
        return view


__all__ = ["PromptBundleView", "PromptManagementService"]
