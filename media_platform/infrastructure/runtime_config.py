"""通用配置存储、加密与发布；服务业务校验由调用方在同一事务内注入。"""

from dataclasses import dataclass, field
from os import getenv
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from media_platform.contracts.runtime_config import config_schema
from media_platform.infrastructure.database.models.service_config import (
    ServiceConfigModel as Config,
    ServiceConfigVersionModel as Version,
)


class ConfigConflict(ValueError):
    pass


@dataclass(frozen=True)
class ConfigSnapshot:
    id: str
    revision: int
    content: dict
    api_key: str = field(default="", repr=False)


class RuntimeConfigStore:
    def __init__(self, session_factory, encryption_key=None):
        self.session_factory = session_factory
        key = (
            encryption_key
            if encryption_key is not None
            else getenv("RUNTIME_CONFIG_ENCRYPTION_KEY", "")
        )
        try:
            self.cipher = Fernet(key.encode()) if key else None
        except (ValueError, TypeError):
            raise ValueError("RUNTIME_CONFIG_ENCRYPTION_KEY 必须是有效Fernet密钥") from None

    def _encrypt(self, value):
        if self.cipher is None:
            raise ValueError("尚未配置运行配置加密密钥")
        return self.cipher.encrypt(value.encode()).decode()

    def _decrypt(self, value):
        if not value:
            return ""
        if self.cipher is None:
            raise ValueError("尚未配置运行配置加密密钥")
        try:
            return self.cipher.decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            raise ValueError("配置密钥解密失败，请检查部署加密密钥") from None

    @staticmethod
    def validate(service, category, content):
        try:
            return (
                config_schema(service, category)
                .model_validate(content)
                .model_dump(mode="json")
            )
        except ValidationError as exc:
            fields = ", ".join(
                ".".join(map(str, e["loc"])) for e in exc.errors(include_input=False)
            )
            raise ValueError("配置字段校验失败：" + fields) from None

    def head(self, session, service, category, *, lock=False, create=False):
        config_schema(service, category)
        query = select(Config).where(
            Config.service == service,
            Config.category == category,
            Config.scope == "GLOBAL",
            Config.school_code == "GLOBAL",
        )
        row = session.scalar(query.with_for_update() if lock else query)
        if row is None and create:
            try:
                with session.begin_nested():
                    session.add(
                        Config(
                            id=str(uuid4()),
                            service=service,
                            category=category,
                            scope="GLOBAL",
                            revision=0,
                            school_code="GLOBAL",
                        )
                    )
                    session.flush()
            except IntegrityError:
                pass  # 唯一键处理多个中心/实例的首次并发初始化。
            row = session.scalar(query.with_for_update())
        return row

    def current(self, session, service, category, *, lock=False):
        head = self.head(session, service, category, lock=lock)
        return (
            session.get(Version, head.version_id) if head and head.version_id else None
        )

    def snapshot(self, service, category, version_id=None):
        with self.session_factory() as session:
            head = self.head(session, service, category)
            if head is None:
                return None
            if version_id is None and not head.version_id:
                return None
            row = (
                session.get(Version, version_id or head.version_id)
                if (version_id or head.version_id)
                else None
            )
            if row is None or row.config_id != head.id or row.status != "PUBLISHED":
                raise ValueError("指定的已发布配置版本不存在或不属于本服务")
            content = self.validate(service, category, row.content)
            return ConfigSnapshot(
                row.id, row.revision, content, self._decrypt(row.secret_ciphertext)
            )

    @staticmethod
    def view(row):
        return {
            "id": row.id,
            "revision": row.revision,
            "base_revision": row.base_revision,
            "status": row.status,
            "content": row.content,
            "has_api_key": bool(row.secret_ciphertext),
            "prompt_updates": row.prompt_updates,
            "reason": row.reason,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "operator": row.updated_by,
        }

    def list_versions(self, service, category):
        with self.session_factory() as session:
            head = self.head(session, service, category)
            rows = (
                []
                if head is None
                else session.scalars(
                    select(Version)
                    .where(Version.config_id == head.id)
                    .order_by(Version.created_at.desc(), Version.id.desc())
                    .limit(100)
                ).all()
            )
            return {
                "revision": head.revision if head else 0,
                "current_id": head.version_id if head else None,
                "current": (
                    self.view(session.get(Version, head.version_id))
                    if head and head.version_id
                    else None
                ),
                "versions": [self.view(row) for row in rows],
            }

    def draft(
        self,
        service,
        category,
        *,
        content,
        expected_revision,
        request_id,
        reason,
        api_key=None,
        prompt_updates=None,
        source_version_id=None,
    ):
        content = self.validate(service, category, content)
        prompt_updates = prompt_updates or []
        if category != "model_connection" and (api_key is not None or prompt_updates):
            raise ValueError("性能配置不能包含密钥或提示词更新")
        with self.session_factory() as session, session.begin():
            head = self.head(session, service, category, lock=True, create=True)
            old = session.scalar(
                select(Version).where(
                    Version.config_id == head.id, Version.request_id == request_id
                )
            )
            if old:
                same_secret = (
                    api_key is None or self._decrypt(old.secret_ciphertext) == api_key
                )
                if api_key is None and source_version_id:
                    source = session.get(Version, source_version_id)
                    same_secret = (
                        source is not None
                        and source.config_id == head.id
                        and source.status in {"DRAFT", "PUBLISHED"}
                        and self._decrypt(source.secret_ciphertext)
                        == self._decrypt(old.secret_ciphertext)
                    )
                if (
                    old.content != content
                    or old.prompt_updates != prompt_updates
                    or old.reason != reason
                    or old.base_revision != expected_revision
                    or not same_secret
                ):
                    raise ConfigConflict("请求编号已用于不同配置")
                return self.view(old)
            if head.revision != expected_revision:
                raise ConfigConflict("配置已被其他管理员更新，请刷新后重试")
            source = (
                session.get(Version, source_version_id or head.version_id)
                if (source_version_id or head.version_id)
                else None
            )
            if source_version_id and (
                not source
                or source.config_id != head.id
                or source.status not in {"DRAFT", "PUBLISHED"}
            ):
                raise ValueError("复制来源必须是本配置的草稿或历史版本")
            secret = source.secret_ciphertext if source else None
            if api_key is not None:
                secret = self._encrypt(api_key) if api_key else None
            row = Version(
                id=str(uuid4()),
                config_id=head.id,
                request_id=request_id,
                base_revision=expected_revision,
                content=content,
                secret_ciphertext=secret,
                prompt_updates=prompt_updates,
                reason=reason,
                status="DRAFT",
                school_code="GLOBAL",
                created_by="CONFIG_ADMIN",
                updated_by="CONFIG_ADMIN",
            )
            session.add(row)
            session.flush()
            return self.view(row)

    def publish(self, service, category, version_id, validator=None):
        with self.session_factory() as session, session.begin():
            head = self.head(session, service, category, lock=True)
            row = session.get(Version, version_id)
            if not head or not row or row.config_id != head.id:
                raise LookupError("配置草稿不存在")
            if row.status == "PUBLISHED":
                return self.view(row)  # 网络重试不能再次切换当前引用。
            self.validate(service, category, row.content)
            if (
                category == "model_connection"
                and not self._decrypt(row.secret_ciphertext).strip()
            ):
                raise ValueError("模型API Key不能为空")
            if row.base_revision != head.revision:
                raise ConfigConflict("草稿已过期，请基于当前配置重新编辑")
            changed = session.execute(
                update(Config)
                .where(Config.id == head.id, Config.revision == row.base_revision)
                .values(
                    revision=row.base_revision + 1,
                    version_id=row.id,
                    updated_by="CONFIG_ADMIN",
                )
            )
            if changed.rowcount != 1:
                raise ConfigConflict("配置已被其他管理员更新")
            if validator:
                validator(session, row)
            row.status = "PUBLISHED"
            row.revision = row.base_revision + 1
            row.updated_by = "CONFIG_ADMIN"
            session.flush()
            return self.view(row)

    def bootstrap(self, service, category, content, api_key=None, prompt_updates=None):
        """只在空配置时导入；已有版本绝不被环境变量覆盖。"""
        with self.session_factory() as session:
            current = self.current(session, service, category)
            if current:
                return
        draft = self.draft(
            service,
            category,
            content=content,
            expected_revision=0,
            request_id="initial-environment-import",
            reason="首次导入部署配置",
            api_key=api_key,
            prompt_updates=prompt_updates,
        )
        self.publish(service, category, draft["id"])
