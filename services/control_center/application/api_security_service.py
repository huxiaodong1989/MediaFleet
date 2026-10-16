"""调用方身份、范围化 API Key 和可信写请求审计。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import pbkdf2_hmac, sha256
import base64
import re
import secrets
from secrets import compare_digest
from typing import Any
import uuid

from sqlalchemy import select

from media_platform.common.time import app_now
from media_platform.infrastructure.database.models.api_security import (
    ApiAuditEventModel,
    ApiCredentialModel,
    ApiPrincipalModel,
)


READ = "read"
TASK_WRITE = "task:write"
RECORDING_WRITE = "recording:write"
NODE_WRITE = "node:write"
ADMIN_WRITE = "admin:write"
ALL_SCOPES = frozenset({READ, TASK_WRITE, RECORDING_WRITE, NODE_WRITE, ADMIN_WRITE})
DERIVATION_ROUNDS = 310_000
KEY_PATTERN = re.compile(r"^mfk_([0-9a-f]{32})_([A-Za-z0-9_-]{40,64})$")


class AuthenticationFailed(Exception):
    """凭据缺失、无效、过期或已吊销。"""


class PermissionDenied(Exception):
    """调用方缺少端点所需权限范围。"""


class CredentialOperationError(Exception):
    """凭据创建、轮换或吊销请求不合法。"""


class CredentialConfigurationError(Exception):
    """凭据启动配置会导致管理入口不可用。"""


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    caller_id: str
    display_name: str
    scopes: frozenset[str]
    principal_id: str | None = None
    credential_id: str | None = None
    bootstrap: bool = False

    def require(self, required_scopes: frozenset[str]) -> None:
        if "*" in self.scopes or required_scopes <= self.scopes:
            return
        raise PermissionDenied(
            "缺少权限范围: " + ", ".join(sorted(required_scopes - self.scopes))
        )


@dataclass(frozen=True)
class IssuedCredential:
    credential_id: str
    caller_id: str
    display_name: str
    name: str
    scopes: tuple[str, ...]
    status: str
    expires_at: datetime | None
    api_key: str


@dataclass(frozen=True)
class CredentialView:
    credential_id: str
    caller_id: str
    display_name: str
    name: str
    scopes: tuple[str, ...]
    status: str
    expires_at: datetime | None
    last_used_at: datetime | None
    rotated_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class AuditEventView:
    event_id: str
    request_id: str
    caller_id: str
    credential_id: str | None
    method: str
    path: str
    action: str
    required_scopes: tuple[str, ...]
    idempotency_key_digest: str | None
    result: str
    http_status: int | None
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None


def _derive(secret: str, salt: bytes, iterations: int) -> str:
    digest = pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _new_secret() -> str:
    return secrets.token_urlsafe(32)


def _api_key(credential_id: str, secret: str) -> str:
    return f"mfk_{credential_id.replace('-', '')}_{secret}"


def _parse_api_key(api_key: str) -> tuple[str, str] | None:
    match = KEY_PATTERN.fullmatch(api_key.strip())
    if match is None:
        return None
    compact_id, secret = match.groups()
    return str(uuid.UUID(hex=compact_id)), secret


def _normalize_scopes(scopes: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted({item.strip().lower() for item in scopes if item.strip()}))
    invalid = set(normalized) - ALL_SCOPES
    if invalid:
        raise CredentialOperationError(
            "不支持的权限范围: " + ", ".join(sorted(invalid))
        )
    if not normalized:
        raise CredentialOperationError("权限范围不能为空")
    return normalized


def _database_datetime(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class ApiCredentialService:
    def __init__(
        self,
        session_factory,
        *,
        bootstrap_api_key: str,
        bootstrap_enabled: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._bootstrap_api_key = str(bootstrap_api_key or "")
        self._bootstrap_enabled = bootstrap_enabled

    def validate_startup_configuration(self) -> None:
        """关闭引导密钥前确认至少保留一个有效管理凭据。"""
        if self._bootstrap_enabled or self.has_active_admin_credential():
            return
        raise CredentialConfigurationError(
            "共享引导 API Key 已关闭，但数据库中没有有效的 admin:write 管理凭据"
        )

    def has_active_admin_credential(self) -> bool:
        now = app_now()
        with self._session_factory() as session:
            rows = session.execute(
                select(ApiCredentialModel.scopes, ApiCredentialModel.expires_at)
                .join(
                    ApiPrincipalModel,
                    ApiPrincipalModel.id == ApiCredentialModel.principal_id,
                )
                .where(
                    ApiCredentialModel.status == "ACTIVE",
                    ApiPrincipalModel.status == "ACTIVE",
                )
            ).all()
        return any(
            (expires_at is None or expires_at > now)
            and bool({ADMIN_WRITE, "*"} & set(scopes or []))
            for scopes, expires_at in rows
        )

    def authenticate(self, api_key: str | None) -> AuthenticatedPrincipal:
        candidate = str(api_key or "")
        if not candidate:
            raise AuthenticationFailed("缺少 API Key")
        if (
            self._bootstrap_enabled
            and self._bootstrap_api_key
            and compare_digest(candidate, self._bootstrap_api_key)
        ):
            return AuthenticatedPrincipal(
                caller_id="bootstrap",
                display_name="部署引导凭据",
                scopes=frozenset({"*"}),
                bootstrap=True,
            )

        parsed = _parse_api_key(candidate)
        if parsed is None:
            raise AuthenticationFailed("API Key 无效")
        credential_id, secret = parsed
        now = app_now()
        with self._session_factory() as session:
            row = session.execute(
                select(ApiCredentialModel, ApiPrincipalModel)
                .join(ApiPrincipalModel, ApiPrincipalModel.id == ApiCredentialModel.principal_id)
                .where(ApiCredentialModel.id == credential_id)
            ).first()
            if row is None:
                raise AuthenticationFailed("API Key 无效")
            credential, principal = row
            if credential.status != "ACTIVE" or principal.status != "ACTIVE":
                raise AuthenticationFailed("API Key 已停用")
            if credential.expires_at is not None and credential.expires_at <= now:
                raise AuthenticationFailed("API Key 已过期")
            salt = base64.urlsafe_b64decode(credential.secret_salt + "==")
            digest = _derive(secret, salt, credential.secret_iterations)
            if not compare_digest(digest, credential.secret_digest):
                raise AuthenticationFailed("API Key 无效")
            if (
                credential.last_used_at is None
                or credential.last_used_at <= now - timedelta(minutes=5)
            ):
                credential.last_used_at = now
                session.commit()
            return AuthenticatedPrincipal(
                caller_id=principal.caller_id,
                display_name=principal.display_name,
                scopes=frozenset(credential.scopes or []),
                principal_id=principal.id,
                credential_id=credential.id,
            )

    def create(
        self,
        *,
        caller_id: str,
        display_name: str,
        credential_name: str,
        scopes: list[str],
        expires_at: datetime | None,
        idempotency_key: str | None,
        operator: AuthenticatedPrincipal,
    ) -> IssuedCredential:
        caller_id = caller_id.strip().lower()
        display_name = display_name.strip()
        credential_name = credential_name.strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", caller_id):
            raise CredentialOperationError("caller_id 必须为 3-64 位小写字母、数字、点、下划线或连字符")
        if not display_name or not credential_name:
            raise CredentialOperationError("调用方名称和凭据名称不能为空")
        expires_at = _database_datetime(expires_at)
        if expires_at is not None and expires_at <= app_now():
            raise CredentialOperationError("过期时间必须晚于当前时间")
        normalized_scopes = _normalize_scopes(scopes)
        request_key_digest = (
            sha256(idempotency_key.encode("utf-8")).hexdigest()
            if idempotency_key
            else None
        )
        credential_id = str(uuid.uuid4())
        secret = _new_secret()
        salt = secrets.token_bytes(16)
        now = app_now()
        principal_display_name = display_name
        with self._session_factory() as session:
            if request_key_digest is not None:
                existing = session.scalar(
                    select(ApiCredentialModel).where(
                        ApiCredentialModel.request_key_digest == request_key_digest
                    )
                )
                if existing is not None:
                    raise CredentialOperationError(
                        "该幂等键已创建凭据；密钥明文不能再次返回，请轮换已有凭据"
                    )
            principal = session.scalar(
                select(ApiPrincipalModel).where(ApiPrincipalModel.caller_id == caller_id)
            )
            if principal is None:
                principal = ApiPrincipalModel(
                    caller_id=caller_id,
                    display_name=display_name,
                    status="ACTIVE",
                    created_by=operator.caller_id,
                    updated_by=operator.caller_id,
                )
                session.add(principal)
                session.flush()
            elif principal.status != "ACTIVE":
                raise CredentialOperationError("调用方已停用")
            principal_display_name = principal.display_name
            credential = ApiCredentialModel(
                id=credential_id,
                principal_id=principal.id,
                name=credential_name,
                secret_salt=base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
                secret_digest=_derive(secret, salt, DERIVATION_ROUNDS),
                secret_iterations=DERIVATION_ROUNDS,
                request_key_digest=request_key_digest,
                scopes=list(normalized_scopes),
                status="ACTIVE",
                expires_at=expires_at,
                created_by=operator.caller_id,
                updated_by=operator.caller_id,
                created_at=now,
                updated_at=now,
            )
            session.add(credential)
            session.commit()
        return IssuedCredential(
            credential_id=credential_id,
            caller_id=caller_id,
            display_name=principal_display_name,
            name=credential_name,
            scopes=normalized_scopes,
            status="ACTIVE",
            expires_at=expires_at,
            api_key=_api_key(credential_id, secret),
        )

    def list(self) -> list[CredentialView]:
        with self._session_factory() as session:
            rows = session.execute(
                select(ApiCredentialModel, ApiPrincipalModel)
                .join(ApiPrincipalModel, ApiPrincipalModel.id == ApiCredentialModel.principal_id)
                .order_by(ApiCredentialModel.created_at.desc())
            ).all()
            return [self._view(credential, principal) for credential, principal in rows]

    def rotate(
        self,
        credential_id: str,
        *,
        operator: AuthenticatedPrincipal,
    ) -> IssuedCredential:
        secret = _new_secret()
        salt = secrets.token_bytes(16)
        now = app_now()
        with self._session_factory() as session:
            row = session.execute(
                select(ApiCredentialModel, ApiPrincipalModel)
                .join(ApiPrincipalModel, ApiPrincipalModel.id == ApiCredentialModel.principal_id)
                .where(ApiCredentialModel.id == credential_id)
            ).first()
            if row is None:
                raise LookupError("凭据不存在")
            credential, principal = row
            if credential.status != "ACTIVE":
                raise CredentialOperationError("只有 ACTIVE 凭据可以轮换")
            credential.secret_salt = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
            credential.secret_digest = _derive(secret, salt, DERIVATION_ROUNDS)
            credential.secret_iterations = DERIVATION_ROUNDS
            credential.rotated_at = now
            credential.updated_at = now
            credential.updated_by = operator.caller_id
            session.commit()
            return IssuedCredential(
                credential_id=credential.id,
                caller_id=principal.caller_id,
                display_name=principal.display_name,
                name=credential.name,
                scopes=tuple(sorted(credential.scopes or [])),
                status=credential.status,
                expires_at=credential.expires_at,
                api_key=_api_key(credential.id, secret),
            )

    def revoke(self, credential_id: str, *, operator: AuthenticatedPrincipal) -> CredentialView:
        now = app_now()
        with self._session_factory() as session:
            row = session.execute(
                select(ApiCredentialModel, ApiPrincipalModel)
                .join(ApiPrincipalModel, ApiPrincipalModel.id == ApiCredentialModel.principal_id)
                .where(ApiCredentialModel.id == credential_id)
            ).first()
            if row is None:
                raise LookupError("凭据不存在")
            credential, principal = row
            credential.status = "REVOKED"
            credential.revoked_at = credential.revoked_at or now
            credential.updated_at = now
            credential.updated_by = operator.caller_id
            session.commit()
            return self._view(credential, principal)

    @staticmethod
    def _view(credential: ApiCredentialModel, principal: ApiPrincipalModel) -> CredentialView:
        return CredentialView(
            credential_id=credential.id,
            caller_id=principal.caller_id,
            display_name=principal.display_name,
            name=credential.name,
            scopes=tuple(sorted(credential.scopes or [])),
            status=credential.status,
            expires_at=credential.expires_at,
            last_used_at=credential.last_used_at,
            rotated_at=credential.rotated_at,
            revoked_at=credential.revoked_at,
            created_at=credential.created_at,
        )


class RequestAuditService:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def begin(
        self,
        *,
        request_id: str,
        principal: AuthenticatedPrincipal,
        method: str,
        path: str,
        action: str,
        required_scopes: frozenset[str],
        idempotency_key: str | None,
    ) -> str:
        event_id = str(uuid.uuid4())
        idempotency_digest = (
            sha256(idempotency_key.encode("utf-8")).hexdigest()
            if idempotency_key
            else None
        )
        with self._session_factory() as session:
            session.add(
                ApiAuditEventModel(
                    id=event_id,
                    request_id=request_id,
                    principal_id=principal.principal_id,
                    credential_id=principal.credential_id,
                    caller_id=principal.caller_id,
                    method=method,
                    path=path[:512],
                    action=action[:128],
                    required_scopes=sorted(required_scopes),
                    idempotency_key=idempotency_digest,
                    result="STARTED",
                    started_at=app_now(),
                )
            )
            session.commit()
        return event_id

    def complete(
        self,
        event_id: str,
        *,
        http_status: int,
        error_message: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            event = session.get(ApiAuditEventModel, event_id)
            if event is None or event.completed_at is not None:
                return
            event.http_status = http_status
            event.result = "SUCCEEDED" if 200 <= http_status < 400 else "FAILED"
            event.error_code = None if http_status < 400 else f"http_{http_status}"
            event.error_message = (error_message or "")[:1000] or None
            event.completed_at = app_now()
            session.commit()

    def list(self, *, limit: int = 100) -> list[AuditEventView]:
        with self._session_factory() as session:
            events = session.scalars(
                select(ApiAuditEventModel)
                .order_by(ApiAuditEventModel.started_at.desc())
                .limit(max(1, min(limit, 200)))
            ).all()
            return [
                AuditEventView(
                    event_id=event.id,
                    request_id=event.request_id,
                    caller_id=event.caller_id,
                    credential_id=event.credential_id,
                    method=event.method,
                    path=event.path,
                    action=event.action,
                    required_scopes=tuple(event.required_scopes or []),
                    idempotency_key_digest=event.idempotency_key,
                    result=event.result,
                    http_status=event.http_status,
                    started_at=event.started_at,
                    completed_at=event.completed_at,
                    error_code=event.error_code,
                )
                for event in events
            ]


__all__ = [
    "ADMIN_WRITE",
    "ALL_SCOPES",
    "AuditEventView",
    "AuthenticationFailed",
    "AuthenticatedPrincipal",
    "ApiCredentialService",
    "CredentialConfigurationError",
    "CredentialOperationError",
    "CredentialView",
    "IssuedCredential",
    "NODE_WRITE",
    "PermissionDenied",
    "READ",
    "RECORDING_WRITE",
    "RequestAuditService",
    "TASK_WRITE",
]
