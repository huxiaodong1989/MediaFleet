"""调用方凭据、权限范围和审计资源。"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from mediafleet_sdk.models import AuditEvent, Credential, CredentialCreate, SecurityScopes


class SecurityResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    def scopes(self) -> SecurityScopes:
        return self._client._get_model("api/v1/admin/security/scopes", SecurityScopes)

    def list_credentials(self) -> list[Credential]:
        return self._client._get_model(
            "api/v1/admin/security/credentials", list[Credential]
        )

    def create_credential(
        self,
        credential: CredentialCreate,
        *,
        idempotency_key: str | None = None,
    ) -> Credential:
        return self._client._request_model(
            "POST",
            "api/v1/admin/security/credentials",
            Credential,
            json=credential.model_dump(mode="json", exclude_none=True),
            extra_headers=(
                {"Idempotency-Key": idempotency_key} if idempotency_key else None
            ),
        )

    def rotate_credential(self, credential_id: str) -> Credential:
        encoded = quote(credential_id, safe="")
        return self._client._request_model(
            "POST",
            f"api/v1/admin/security/credentials/{encoded}/rotate",
            Credential,
        )

    def revoke_credential(self, credential_id: str) -> Credential:
        encoded = quote(credential_id, safe="")
        return self._client._request_model(
            "POST",
            f"api/v1/admin/security/credentials/{encoded}/revoke",
            Credential,
        )

    def list_audit_events(self, *, limit: int = 100) -> list[AuditEvent]:
        if not 1 <= limit <= 200:
            raise ValueError("limit 必须在 1 到 200 之间")
        return self._client._get_model(
            "api/v1/admin/security/audit-events",
            list[AuditEvent],
            params={"limit": limit},
        )


class AsyncSecurityResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def scopes(self) -> SecurityScopes:
        return await self._client._get_model(
            "api/v1/admin/security/scopes", SecurityScopes
        )

    async def list_credentials(self) -> list[Credential]:
        return await self._client._get_model(
            "api/v1/admin/security/credentials", list[Credential]
        )

    async def create_credential(
        self,
        credential: CredentialCreate,
        *,
        idempotency_key: str | None = None,
    ) -> Credential:
        return await self._client._request_model(
            "POST",
            "api/v1/admin/security/credentials",
            Credential,
            json=credential.model_dump(mode="json", exclude_none=True),
            extra_headers=(
                {"Idempotency-Key": idempotency_key} if idempotency_key else None
            ),
        )

    async def rotate_credential(self, credential_id: str) -> Credential:
        encoded = quote(credential_id, safe="")
        return await self._client._request_model(
            "POST",
            f"api/v1/admin/security/credentials/{encoded}/rotate",
            Credential,
        )

    async def revoke_credential(self, credential_id: str) -> Credential:
        encoded = quote(credential_id, safe="")
        return await self._client._request_model(
            "POST",
            f"api/v1/admin/security/credentials/{encoded}/revoke",
            Credential,
        )

    async def list_audit_events(self, *, limit: int = 100) -> list[AuditEvent]:
        if not 1 <= limit <= 200:
            raise ValueError("limit 必须在 1 到 200 之间")
        return await self._client._get_model(
            "api/v1/admin/security/audit-events",
            list[AuditEvent],
            params={"limit": limit},
        )


__all__ = ["AsyncSecurityResource", "SecurityResource"]
