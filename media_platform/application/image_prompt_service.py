"""图像提示词共享数据库控制面；不含模型调用或图像服务 HTTP 管理接口。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import TypeVar

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from media_platform.application.content_prompt_service import (
    PromptBundleView,
    PromptManagementService,
)
from media_platform.contracts.image_analysis import (
    IMAGE_PROMPT_SCHEME,
    ImagePromptBundleContent,
)
from media_platform.infrastructure.database.repositories.content_prompt_bundle import (
    GLOBAL_SCHOOL_CODE,
    PromptBundleRepository,
    prompt_content_hash,
)


LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


def _repository(session: Session, school_code: str) -> PromptBundleRepository:
    return PromptBundleRepository(
        session, scheme_code=IMAGE_PROMPT_SCHEME, school_code=school_code
    )


def _transaction(
    factory: Callable[[], Session], operation: Callable[[Session], T]
) -> T:
    """唯一版本竞争/MySQL 死锁时，最多三次完整新事务，不重试断连或任意 SQL 错误。"""
    for attempt in range(3):
        try:
            with factory() as session, session.begin():
                return operation(session)
        except (IntegrityError, OperationalError) as exc:
            args = getattr(exc.orig, "args", ())
            code = args[0] if args else None
            error_text = str(exc.orig)
            version_collision = isinstance(exc, IntegrityError) and (
                (code == 1062 and "uk_ai_tsbb_fa_bb" in error_text)
                or (
                    "UNIQUE constraint failed:" in error_text
                    and all(
                        column in error_text
                        for column in ("xxm", "fabm", "bbh")
                    )
                )
            )
            deadlock = isinstance(exc, OperationalError) and code in (1205, 1213)
            if attempt == 2 or not (version_collision or deadlock):
                raise
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class ImagePromptPolicy:
    """由受控部署配置注入；名单不是模型真实多图能力验证的替代。"""

    allowed_models: frozenset[str]
    output_schema_versions: frozenset[str]
    metric_definition_versions: frozenset[str]

    def __post_init__(self) -> None:
        for field in (
            "allowed_models",
            "output_schema_versions",
            "metric_definition_versions",
        ):
            values = getattr(self, field)
            if (
                isinstance(values, str)
                or not values
                or any(
                    not isinstance(value, str) or not value.strip() for value in values
                )
            ):
                raise ValueError("提示词发布能力名单不能为空")
            object.__setattr__(self, field, frozenset(values))

    def validate(self, content: ImagePromptBundleContent) -> None:
        if content.model not in self.allowed_models:
            raise ValueError("模型不在图像服务受控名单中")
        if content.output_schema_version not in self.output_schema_versions:
            raise ValueError("不支持的图像输出协议版本")
        if content.metric_definition_version not in self.metric_definition_versions:
            raise ValueError("未批准的指标定义版本")


@dataclass(frozen=True)
class ImagePromptSnapshot:
    """供接单持久化的版本引用；本对象本身不代表已经锁入课堂任务。"""

    id: str
    school_code: str
    version: int
    content_hash: str
    content: ImagePromptBundleContent
    scheme_code: str = IMAGE_PROMPT_SCHEME


class ImagePromptReader:
    """图像运行时只读接口；已锁定版本允许归档，不回退本地种子。"""

    def __init__(
        self, session_factory: Callable[[], Session], policy: ImagePromptPolicy
    ):
        self.session_factory = session_factory
        self.policy = policy

    def _snapshot(self, row, session=None, *, historical=False) -> ImagePromptSnapshot:
        if row.status not in {"PUBLISHED", "ARCHIVED"} or row.published_at is None:
            raise ValueError("提示词版本未发布过")
        if prompt_content_hash(row.content) != row.content_hash:
            raise ValueError("提示词内容摘要不匹配")
        content = ImagePromptBundleContent.model_validate(row.content)
        if session is not None and hasattr(self.policy, "validate_in_session"):
            self.policy.validate_in_session(content, session, historical=historical)
        else:
            self.policy.validate(content)
        return ImagePromptSnapshot(
            row.id, row.school_code, row.version, row.content_hash, content
        )

    def published(self, school_code: str) -> ImagePromptSnapshot:
        with self.session_factory() as session:
            return self.published_in_session(session, school_code)

    def published_in_session(
        self, session: Session, school_code: str
    ) -> ImagePromptSnapshot:
        """供接单事务锁版；绝不在此开启第二个 Session。"""
        for candidate in dict.fromkeys((school_code, GLOBAL_SCHOOL_CODE)):
            repository = _repository(session, candidate)
            row = repository.get_published(candidate)
            if row is not None:
                # 有发布版本但内容坏了必须失败，不能切 GLOBAL 或本地种子。
                return self._snapshot(row, session)
            if repository.list_versions():
                # 学校已显式建立方案但未发布，不使用 GLOBAL 遮盖配置错误。
                raise LookupError("图像提示词方案存在但没有发布版本")
        raise LookupError("图像提示词没有可用发布版本")

    def locked(
        self,
        bundle_id: str,
        *,
        school_code: str,
        expected_version: int,
        expected_hash: str,
    ) -> ImagePromptSnapshot:
        """按任务学校查询自身或 GLOBAL 版本；调用方须从持久任务读取引用，不能信任外部入参。"""
        with self.session_factory() as session:
            for candidate in dict.fromkeys((school_code, GLOBAL_SCHOOL_CODE)):
                row = _repository(session, candidate).get(bundle_id)
                if row is not None:
                    snapshot = self._snapshot(row, session, historical=True)
                    if (
                        snapshot.version != expected_version
                        or snapshot.content_hash != expected_hash
                    ):
                        raise ValueError("课堂锁定的提示词版本或摘要不匹配")
                    return snapshot
        raise LookupError("课堂锁定的图像提示词版本不存在")


def ensure_image_prompt_bootstrap(
    session_factory: Callable[[], Session],
    load_seed: Callable[[], ImagePromptBundleContent],
    policy: ImagePromptPolicy,
    *,
    school_code: str = GLOBAL_SCHOOL_CODE,
) -> PromptBundleView:
    """首次初始化独立入口；任何已有状态都阻止植入，已有数据时甚至不读本地文件。"""

    def insert_if_absent(session: Session) -> PromptBundleView:
        repository = _repository(session, school_code)
        versions = repository.list_versions()
        if versions:
            return PromptManagementService._view(versions[0])
        row = repository.ensure_bootstrap(load_seed(), validate_publish=policy.validate)
        return PromptManagementService._view(row)

    view = _transaction(session_factory, insert_if_absent)
    LOGGER.info(
        "图像提示词初始化检查完成: scheme=%s, school_code=%s, version=%s, status=%s",
        IMAGE_PROMPT_SCHEME,
        school_code,
        view.version,
        view.status,
    )
    return view


class ImagePromptManagementService:
    """供调用中心直接管理 DB；学校须由授权层确定，不能用本服务替代认证。"""

    def __init__(
        self, session_factory: Callable[[], Session], policy: ImagePromptPolicy
    ):
        self.session_factory = session_factory
        self.policy = policy

    def list_versions(self, *, school_code: str) -> list[PromptBundleView]:
        with self.session_factory() as session:
            return [
                PromptManagementService._view(row)
                for row in _repository(session, school_code).list_versions()
            ]

    def _write(self, school_code: str, operation) -> PromptBundleView:
        def execute(session):
            if hasattr(self.policy, "prepare_session"):
                self.policy.prepare_session(session)
            return PromptManagementService._view(
                operation(_repository(session, school_code))
            )

        return _transaction(self.session_factory, execute)

    def create_draft(
        self,
        content: ImagePromptBundleContent,
        *,
        school_code: str,
        operator: str,
    ) -> PromptBundleView:
        return self._write(
            school_code,
            lambda repository: repository.create_draft(
                content, school_code=school_code, operator=operator
            ),
        )

    def clone_draft(
        self, bundle_id: str, *, school_code: str, operator: str
    ) -> PromptBundleView:
        # 不允许通过传其他学校/全局版本 ID 偷读后克隆；跨作用域模板复制需单独授权设计。
        return self._write(
            school_code,
            lambda repository: repository.clone_as_draft(
                bundle_id, school_code=school_code, operator=operator
            ),
        )

    def update_draft(
        self,
        bundle_id: str,
        content: ImagePromptBundleContent,
        *,
        school_code: str,
        operator: str,
        expected_hash: str,
    ) -> PromptBundleView:
        if not expected_hash:
            raise ValueError("修改必须携带预期内容摘要")
        return self._write(
            school_code,
            lambda repository: repository.update_draft(
                bundle_id, content, operator=operator, expected_hash=expected_hash
            ),
        )

    def publish(
        self,
        bundle_id: str,
        *,
        school_code: str,
        operator: str,
        expected_hash: str,
    ) -> PromptBundleView:
        if not expected_hash:
            raise ValueError("发布必须携带预期内容摘要")
        return self._write(
            school_code,
            lambda repository: repository.publish(
                bundle_id,
                operator=operator,
                expected_hash=expected_hash,
                validate_publish=self.policy.validate,
            ),
        )
