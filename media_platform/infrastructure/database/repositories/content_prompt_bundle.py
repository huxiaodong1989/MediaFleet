"""共享数据库中的 AI 评课提示词版本仓储。"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import logging
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from media_platform.contracts.content_evaluation import PromptBundleContent
from media_platform.infrastructure.database.models.content_prompt_bundle import (
    ContentPromptBundleModel,
)


GLOBAL_SCHOOL_CODE = "GLOBAL"
PROMPT_SCHEME_CODE = "CLASS_EVALUATION"
LOGGER = logging.getLogger(__name__)


def prompt_content_hash(content: dict[str, Any]) -> str:
    payload = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


class PromptBundleRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_versions(self, school_code: str = GLOBAL_SCHOOL_CODE) -> list[ContentPromptBundleModel]:
        return list(
            self.session.scalars(
                select(ContentPromptBundleModel)
                .where(
                    ContentPromptBundleModel.school_code == school_code,
                    ContentPromptBundleModel.scheme_code == PROMPT_SCHEME_CODE,
                )
                .order_by(ContentPromptBundleModel.version.desc())
            )
        )

    def get(self, bundle_id: str) -> ContentPromptBundleModel | None:
        return self.session.get(ContentPromptBundleModel, bundle_id)

    def get_published(self, school_code: str) -> ContentPromptBundleModel | None:
        for candidate in (school_code, GLOBAL_SCHOOL_CODE):
            bundle = self.session.scalar(
                select(ContentPromptBundleModel)
                .where(
                    ContentPromptBundleModel.school_code == candidate,
                    ContentPromptBundleModel.scheme_code == PROMPT_SCHEME_CODE,
                    ContentPromptBundleModel.status == "PUBLISHED",
                )
                .order_by(ContentPromptBundleModel.version.desc())
                .limit(1)
            )
            if bundle is not None:
                return bundle
        return None

    def ensure_bootstrap(self, content: PromptBundleContent, *, created_by: str = "bootstrap") -> ContentPromptBundleModel:
        current = self.get_published(GLOBAL_SCHOOL_CODE)
        if current is not None:
            LOGGER.info(
                "AI评课提示词已存在，复用数据库已发布版本: school_code=%s, version=%s, bundle_id=%s",
                current.school_code,
                current.version,
                current.id,
            )
            return current
        payload = content.model_dump(mode="json")
        bundle = ContentPromptBundleModel(
            id=str(uuid4()),
            scheme_code=PROMPT_SCHEME_CODE,
            version=1,
            status="PUBLISHED",
            content=payload,
            content_hash=prompt_content_hash(payload),
            published_at=datetime.now(),
            school_code=GLOBAL_SCHOOL_CODE,
            created_by=created_by,
            updated_by=created_by,
        )
        self.session.add(bundle)
        self.session.flush()
        LOGGER.info(
            "AI评课提示词库为空，已从本地bootstrap_prompts写入首个发布版本: "
            "school_code=%s, version=%s, bundle_id=%s",
            bundle.school_code,
            bundle.version,
            bundle.id,
        )
        return bundle

    def create_draft(self, content: PromptBundleContent, *, school_code: str, operator: str) -> ContentPromptBundleModel:
        latest = self.session.scalar(
            select(ContentPromptBundleModel)
            .where(
                ContentPromptBundleModel.school_code == school_code,
                ContentPromptBundleModel.scheme_code == PROMPT_SCHEME_CODE,
            )
            .order_by(ContentPromptBundleModel.version.desc())
            .limit(1)
            .with_for_update()
        )
        payload = content.model_dump(mode="json")
        bundle = ContentPromptBundleModel(
            id=str(uuid4()),
            scheme_code=PROMPT_SCHEME_CODE,
            version=int(latest.version if latest is not None else 0) + 1,
            status="DRAFT",
            content=payload,
            content_hash=prompt_content_hash(payload),
            school_code=school_code,
            created_by=operator,
            updated_by=operator,
        )
        self.session.add(bundle)
        self.session.flush()
        return bundle

    def update_draft(self, bundle_id: str, content: PromptBundleContent, *, operator: str) -> ContentPromptBundleModel:
        bundle = self.get(bundle_id)
        if bundle is None:
            raise LookupError("提示词版本不存在")
        if bundle.status != "DRAFT":
            raise ValueError("只有草稿版本允许修改")
        payload = content.model_dump(mode="json")
        bundle.content = payload
        bundle.content_hash = prompt_content_hash(payload)
        bundle.updated_by = operator
        self.session.flush()
        return bundle

    def publish(self, bundle_id: str, *, operator: str) -> ContentPromptBundleModel:
        candidate = self.get(bundle_id)
        if candidate is None:
            raise LookupError("提示词版本不存在")
        versions = list(
            self.session.scalars(
                select(ContentPromptBundleModel)
                .where(
                    ContentPromptBundleModel.school_code == candidate.school_code,
                    ContentPromptBundleModel.scheme_code == candidate.scheme_code,
                )
                .order_by(ContentPromptBundleModel.version)
                .with_for_update()
            )
        )
        bundle = next((item for item in versions if item.id == bundle_id), None)
        if bundle is None:
            raise LookupError("提示词版本不存在")
        PromptBundleContent.model_validate(bundle.content)
        self.session.execute(
            update(ContentPromptBundleModel)
            .where(
                ContentPromptBundleModel.school_code == bundle.school_code,
                ContentPromptBundleModel.scheme_code == bundle.scheme_code,
                ContentPromptBundleModel.status == "PUBLISHED",
                ContentPromptBundleModel.id != bundle.id,
            )
            .values(status="ARCHIVED", updated_by=operator)
        )
        bundle.status = "PUBLISHED"
        bundle.published_at = datetime.now()
        bundle.updated_by = operator
        self.session.flush()
        return bundle

    def clone_as_draft(self, bundle_id: str, *, school_code: str, operator: str) -> ContentPromptBundleModel:
        source = self.get(bundle_id)
        if source is None:
            raise LookupError("提示词版本不存在")
        return self.create_draft(
            PromptBundleContent.model_validate(source.content),
            school_code=school_code,
            operator=operator,
        )


__all__ = [
    "GLOBAL_SCHOOL_CODE",
    "PROMPT_SCHEME_CODE",
    "PromptBundleRepository",
    "prompt_content_hash",
]
