"""共享提示词版本仓储：评课保留旧调用约定，图像强制绑定学校作用域。"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import logging
from typing import Any
from collections.abc import Callable
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from media_platform.contracts.content_evaluation import PromptBundleContent
from media_platform.contracts.image_analysis import (
    IMAGE_PROMPT_SCHEME,
    ImagePromptBundleContent,
)
from media_platform.infrastructure.database.models.content_prompt_bundle import (
    ContentPromptBundleModel,
)


GLOBAL_SCHOOL_CODE = "GLOBAL"
PROMPT_SCHEME_CODE = "CLASS_EVALUATION"
LOGGER = logging.getLogger(__name__)
PromptContent = PromptBundleContent | ImagePromptBundleContent


def prompt_content_hash(content: dict[str, Any]) -> str:
    payload = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(payload.encode("utf-8")).hexdigest()


class PromptBundleRepository:
    def __init__(
        self,
        session: Session,
        *,
        scheme_code: str = PROMPT_SCHEME_CODE,
        school_code: str | None = None,
    ):
        schemas = {
            PROMPT_SCHEME_CODE: PromptBundleContent,
            IMAGE_PROMPT_SCHEME: ImagePromptBundleContent,
        }
        if scheme_code not in schemas:
            raise ValueError("不支持的提示词方案")
        if scheme_code == IMAGE_PROMPT_SCHEME and school_code is None:
            raise ValueError("图像提示词必须指定学校作用域")
        self.session = session
        self.scheme_code = scheme_code
        self.school_code = school_code
        self.schema = schemas[scheme_code]
        if school_code is not None:
            self._school(school_code)

    def _school(self, requested: str | None = None) -> str:
        value = (
            requested
            if requested is not None
            else self.school_code or GLOBAL_SCHOOL_CODE
        )
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 64
            or value != value.strip()
        ):
            raise ValueError("学校作用域无效")
        if self.school_code is not None and value != self.school_code:
            raise LookupError("提示词学校作用域不匹配")
        return value

    def _payload(self, content: PromptContent) -> dict[str, Any]:
        # 不允许用评课 Schema 写入图像方案，或通过 model_construct 绕过验证。
        parsed = self.schema.model_validate(content.model_dump(mode="json"))
        return parsed.model_dump(mode="json")

    def _lock_versions(self, school_code: str) -> list[ContentPromptBundleModel]:
        return list(
            self.session.scalars(
                select(ContentPromptBundleModel)
                .where(
                    ContentPromptBundleModel.school_code == self._school(school_code),
                    ContentPromptBundleModel.scheme_code == self.scheme_code,
                )
                .order_by(ContentPromptBundleModel.version)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def _locked_bundle(self, bundle_id: str) -> ContentPromptBundleModel:
        candidate = self.get(bundle_id)
        if candidate is None:
            raise LookupError("提示词版本不存在")
        # 所有修改按同一作用域/升序锁行，避免编辑和发布互相越过状态校验。
        versions = self._lock_versions(candidate.school_code)
        bundle = next((row for row in versions if row.id == bundle_id), None)
        if bundle is None:
            raise LookupError("提示词版本不存在")
        return bundle

    @staticmethod
    def _check_hash(
        bundle: ContentPromptBundleModel, expected_hash: str | None = None
    ) -> None:
        if prompt_content_hash(bundle.content) != bundle.content_hash:
            raise ValueError("提示词内容摘要不匹配")
        if expected_hash is not None and bundle.content_hash != expected_hash:
            raise ValueError("提示词已被修改，请刷新版本后重试")

    def list_versions(
        self, school_code: str | None = None
    ) -> list[ContentPromptBundleModel]:
        return list(
            self.session.scalars(
                select(ContentPromptBundleModel)
                .where(
                    ContentPromptBundleModel.school_code == self._school(school_code),
                    ContentPromptBundleModel.scheme_code == self.scheme_code,
                )
                .order_by(ContentPromptBundleModel.version.desc())
            )
        )

    def get(self, bundle_id: str) -> ContentPromptBundleModel | None:
        query = select(ContentPromptBundleModel).where(
            ContentPromptBundleModel.id == bundle_id,
            ContentPromptBundleModel.scheme_code == self.scheme_code,
        )
        if self.school_code is not None:
            query = query.where(
                ContentPromptBundleModel.school_code == self.school_code
            )
        return self.session.scalar(query)

    def get_published(self, school_code: str) -> ContentPromptBundleModel | None:
        self._school(school_code)
        # 已绑定作用域不隐式跨学校读取；图像 GLOBAL 回退由只读服务显式处理。
        candidates = (
            (school_code,)
            if self.school_code is not None
            else (school_code, GLOBAL_SCHOOL_CODE)
        )
        for candidate in candidates:
            bundle = self.session.scalar(
                select(ContentPromptBundleModel)
                .where(
                    ContentPromptBundleModel.school_code == candidate,
                    ContentPromptBundleModel.scheme_code == self.scheme_code,
                    ContentPromptBundleModel.status == "PUBLISHED",
                )
                .order_by(ContentPromptBundleModel.version.desc())
                .limit(1)
            )
            if bundle is not None:
                return bundle
        return None

    def ensure_bootstrap(
        self,
        content: PromptContent,
        *,
        created_by: str = "bootstrap",
        validate_publish: Callable[[Any], None] | None = None,
    ) -> ContentPromptBundleModel:
        school_code = self._school()
        versions = self._lock_versions(school_code)
        if versions:
            current = next(
                (row for row in reversed(versions) if row.status == "PUBLISHED"),
                versions[-1],
            )
            LOGGER.info(
                "提示词已存在，%s: scheme=%s, school_code=%s, version=%s, bundle_id=%s",
                "复用数据库已发布版本"
                if current.status == "PUBLISHED"
                else "保留数据库现有版本，不植入种子",
                self.scheme_code,
                current.school_code,
                current.version,
                current.id,
            )
            return current
        payload = self._payload(content)
        if validate_publish is not None:
            validate_publish(self.schema.model_validate(payload))
        bundle = ContentPromptBundleModel(
            id=str(uuid4()),
            scheme_code=self.scheme_code,
            version=1,
            status="PUBLISHED",
            content=payload,
            content_hash=prompt_content_hash(payload),
            published_at=datetime.now(),
            school_code=school_code,
            created_by=created_by,
            updated_by=created_by,
        )
        self.session.add(bundle)
        self.session.flush()
        LOGGER.info(
            "%s提示词库为空，已从本地bootstrap_prompts写入首个发布版本: "
            "school_code=%s, version=%s, bundle_id=%s",
            "AI评课" if self.scheme_code == PROMPT_SCHEME_CODE else "课堂图像",
            bundle.school_code,
            bundle.version,
            bundle.id,
        )
        return bundle

    def create_draft(
        self, content: PromptContent, *, school_code: str, operator: str
    ) -> ContentPromptBundleModel:
        versions = self._lock_versions(school_code)
        latest = versions[-1] if versions else None
        payload = self._payload(content)
        bundle = ContentPromptBundleModel(
            id=str(uuid4()),
            scheme_code=self.scheme_code,
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

    def update_draft(
        self,
        bundle_id: str,
        content: PromptContent,
        *,
        operator: str,
        expected_hash: str | None = None,
    ) -> ContentPromptBundleModel:
        bundle = self._locked_bundle(bundle_id)
        self._check_hash(bundle, expected_hash)
        if bundle.status != "DRAFT":
            raise ValueError("只有草稿版本允许修改")
        payload = self._payload(content)
        bundle.content = payload
        bundle.content_hash = prompt_content_hash(payload)
        bundle.updated_by = operator
        self.session.flush()
        return bundle

    def publish(
        self,
        bundle_id: str,
        *,
        operator: str,
        expected_hash: str | None = None,
        validate_publish: Callable[[Any], None] | None = None,
    ) -> ContentPromptBundleModel:
        bundle = self._locked_bundle(bundle_id)
        self._check_hash(bundle, expected_hash)
        if bundle.status not in {"DRAFT", "PUBLISHED", "ARCHIVED"}:
            raise ValueError("无效的提示词状态")
        content = self.schema.model_validate(bundle.content)
        if validate_publish is not None:
            validate_publish(content)
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

    def clone_as_draft(
        self, bundle_id: str, *, school_code: str, operator: str
    ) -> ContentPromptBundleModel:
        self._school(school_code)
        source = self._locked_bundle(bundle_id)
        self._check_hash(source)
        return self.create_draft(
            self.schema.model_validate(source.content),
            school_code=school_code,
            operator=operator,
        )


__all__ = [
    "GLOBAL_SCHOOL_CODE",
    "PROMPT_SCHEME_CODE",
    "PromptBundleRepository",
    "prompt_content_hash",
]
