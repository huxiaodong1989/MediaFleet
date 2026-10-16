"""调用方凭据与可信审计模型。"""

from __future__ import annotations

from mediafleet_sdk.models.base import SDKModel, UtcDateTime


class SecurityScopes(SDKModel):
    scopes: list[str]


class CredentialCreate(SDKModel):
    caller_id: str
    display_name: str
    credential_name: str
    scopes: list[str]
    expires_at: UtcDateTime | None = None


class Credential(SDKModel):
    credential_id: str
    caller_id: str
    display_name: str
    credential_name: str
    scopes: list[str]
    status: str
    expires_at: UtcDateTime | None = None
    last_used_at: UtcDateTime | None = None
    rotated_at: UtcDateTime | None = None
    revoked_at: UtcDateTime | None = None
    created_at: UtcDateTime | None = None
    api_key: str | None = None


class AuditEvent(SDKModel):
    event_id: str
    request_id: str
    caller_id: str
    credential_id: str | None = None
    method: str
    path: str
    action: str
    required_scopes: list[str]
    idempotency_key_digest: str | None = None
    result: str
    http_status: int | None = None
    started_at: UtcDateTime
    completed_at: UtcDateTime | None = None
    error_code: str | None = None


__all__ = ["AuditEvent", "Credential", "CredentialCreate", "SecurityScopes"]
