"""范围化 API 凭据、轮换、吊销与审计测试。"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.common.time import app_now
from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models import (
    ApiAuditEventModel,
    ApiCredentialModel,
    ApiPrincipalModel,
)
from services.control_center.api.dependencies import verify_internal_api_key
from services.control_center.api.security_admin import router as security_router
from services.control_center.application.api_security_service import (
    ADMIN_WRITE,
    AuthenticationFailed,
    ApiCredentialService,
    CredentialConfigurationError,
    READ,
    RECORDING_WRITE,
    RequestAuditService,
    TASK_WRITE,
)


BOOTSTRAP_KEY = "test-bootstrap-key-credential-management"


def _app(tmp_path):
    database_file = (tmp_path / "security.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    credential_service = ApiCredentialService(
        factory,
        bootstrap_api_key=BOOTSTRAP_KEY,
    )
    audit_service = RequestAuditService(factory)
    runtime = SimpleNamespace(
        api_key=BOOTSTRAP_KEY,
        credential_service=credential_service,
        audit_service=audit_service,
    )
    app = FastAPI()
    app.state.control_center_runtime = runtime
    app.include_router(security_router)

    @app.get("/api/v1/tasks/example", dependencies=[Depends(verify_internal_api_key)])
    def read_task(request: Request):
        return {"caller_id": request.state.authenticated_principal.caller_id}

    @app.post("/api/v1/tasks/example", dependencies=[Depends(verify_internal_api_key)])
    def write_task(request: Request):
        return {"caller_id": request.state.authenticated_principal.caller_id}

    @app.post(
        "/api/v1/admin/tasks/example/retry",
        dependencies=[Depends(verify_internal_api_key)],
    )
    def retry_task():
        return {"ok": True}

    @app.post(
        "/api/v1/recordings/start",
        dependencies=[Depends(verify_internal_api_key)],
    )
    def start_recording():
        return {"ok": True}

    @app.post(
        "/api/v1/rtc/stream-bindings",
        dependencies=[Depends(verify_internal_api_key)],
    )
    def create_binding():
        return {"ok": True}

    @app.patch(
        "/api/v1/recording-servers/example/status",
        dependencies=[Depends(verify_internal_api_key)],
    )
    def update_recording_server():
        return {"ok": True}

    @app.middleware("http")
    async def complete_audit(request, call_next):
        response = await call_next(request)
        event_id = getattr(request.state, "audit_event_id", None)
        if event_id:
            audit_service.complete(event_id, http_status=response.status_code)
        return response

    return engine, factory, app


def _create_key(client: TestClient, *, scopes: list[str]) -> dict:
    response = client.post(
        "/api/v1/admin/security/credentials",
        headers={"X-API-Key": BOOTSTRAP_KEY, "Idempotency-Key": "create-automation"},
        json={
            "caller_id": "ci.automation",
            "display_name": "CI 自动化",
            "credential_name": "default",
            "scopes": scopes,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_bootstrap_key_is_enabled_by_default(tmp_path):
    engine, factory, _ = _app(tmp_path)
    try:
        service = ApiCredentialService(factory, bootstrap_api_key=BOOTSTRAP_KEY)

        service.validate_startup_configuration()
        principal = service.authenticate(BOOTSTRAP_KEY)

        assert principal.bootstrap is True
        assert principal.scopes == frozenset({"*"})
    finally:
        engine.dispose()


def test_disabled_bootstrap_key_requires_active_admin_credential(tmp_path):
    engine, factory, _ = _app(tmp_path)
    try:
        service = ApiCredentialService(
            factory,
            bootstrap_api_key=BOOTSTRAP_KEY,
            bootstrap_enabled=False,
        )

        with pytest.raises(CredentialConfigurationError, match="admin:write"):
            service.validate_startup_configuration()
        with pytest.raises(AuthenticationFailed, match="API Key 无效"):
            service.authenticate(BOOTSTRAP_KEY)
    finally:
        engine.dispose()


def test_scoped_admin_key_remains_available_when_bootstrap_is_disabled(tmp_path):
    engine, factory, app = _app(tmp_path)
    try:
        with TestClient(app) as client:
            created = _create_key(client, scopes=[ADMIN_WRITE])
        service = ApiCredentialService(
            factory,
            bootstrap_api_key=BOOTSTRAP_KEY,
            bootstrap_enabled=False,
        )

        service.validate_startup_configuration()
        principal = service.authenticate(created["api_key"])

        assert principal.bootstrap is False
        assert principal.caller_id == "ci.automation"
        assert principal.scopes == frozenset({ADMIN_WRITE})
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "invalid_state",
    ["read_only", "expired", "credential_revoked", "principal_disabled"],
)
def test_disabled_bootstrap_rejects_unusable_admin_credentials(
    tmp_path,
    invalid_state: str,
):
    engine, factory, app = _app(tmp_path)
    try:
        scopes = [READ] if invalid_state == "read_only" else [ADMIN_WRITE]
        with TestClient(app) as client:
            created = _create_key(client, scopes=scopes)
        with factory() as session:
            credential = session.get(ApiCredentialModel, created["credential_id"])
            assert credential is not None
            if invalid_state == "expired":
                credential.expires_at = app_now() - timedelta(seconds=1)
            elif invalid_state == "credential_revoked":
                credential.status = "REVOKED"
            elif invalid_state == "principal_disabled":
                principal = session.get(ApiPrincipalModel, credential.principal_id)
                assert principal is not None
                principal.status = "DISABLED"
            session.commit()
        service = ApiCredentialService(
            factory,
            bootstrap_api_key=BOOTSTRAP_KEY,
            bootstrap_enabled=False,
        )

        with pytest.raises(CredentialConfigurationError, match="admin:write"):
            service.validate_startup_configuration()
    finally:
        engine.dispose()


def test_scoped_key_allows_read_and_rejects_write(tmp_path):
    engine, _, app = _app(tmp_path)
    try:
        with TestClient(app) as client:
            created = _create_key(client, scopes=[READ])
            api_key = created["api_key"]
            read = client.get(
                "/api/v1/tasks/example",
                headers={"X-API-Key": api_key},
            )
            write = client.post(
                "/api/v1/tasks/example",
                headers={"X-API-Key": api_key},
            )

        assert created["caller_id"] == "ci.automation"
        assert created["scopes"] == [READ]
        assert read.status_code == 200
        assert read.json()["caller_id"] == "ci.automation"
        assert write.status_code == 403
        assert TASK_WRITE in write.json()["detail"]
    finally:
        engine.dispose()


def test_task_write_scope_covers_admin_retry_but_not_recording(tmp_path):
    engine, _, app = _app(tmp_path)
    try:
        with TestClient(app) as client:
            created = _create_key(client, scopes=[TASK_WRITE])
            headers = {"X-API-Key": created["api_key"]}
            task_retry = client.post(
                "/api/v1/admin/tasks/example/retry",
                headers=headers,
            )
            recording = client.post("/api/v1/recordings/start", headers=headers)

        assert task_retry.status_code == 200
        assert recording.status_code == 403
        assert RECORDING_WRITE in recording.json()["detail"]
    finally:
        engine.dispose()


def test_recording_write_scope_covers_commands_and_bindings_only(tmp_path):
    engine, _, app = _app(tmp_path)
    try:
        with TestClient(app) as client:
            created = _create_key(client, scopes=[RECORDING_WRITE])
            headers = {"X-API-Key": created["api_key"]}
            recording = client.post("/api/v1/recordings/start", headers=headers)
            binding = client.post("/api/v1/rtc/stream-bindings", headers=headers)
            server_status = client.patch(
                "/api/v1/recording-servers/example/status",
                headers=headers,
            )

        assert recording.status_code == 200
        assert binding.status_code == 200
        assert server_status.status_code == 403
        assert ADMIN_WRITE in server_status.json()["detail"]
    finally:
        engine.dispose()


def test_key_rotation_invalidates_old_secret_and_revoke_blocks_new_secret(tmp_path):
    engine, _, app = _app(tmp_path)
    try:
        with TestClient(app) as client:
            created = _create_key(client, scopes=[READ, TASK_WRITE])
            credential_id = created["credential_id"]
            rotated = client.post(
                f"/api/v1/admin/security/credentials/{credential_id}/rotate",
                headers={"X-API-Key": BOOTSTRAP_KEY},
            )
            assert rotated.status_code == 200
            new_key = rotated.json()["api_key"]
            assert new_key != created["api_key"]

            old_result = client.get(
                "/api/v1/tasks/example",
                headers={"X-API-Key": created["api_key"]},
            )
            new_result = client.get(
                "/api/v1/tasks/example",
                headers={"X-API-Key": new_key},
            )
            revoked = client.post(
                f"/api/v1/admin/security/credentials/{credential_id}/revoke",
                headers={"X-API-Key": BOOTSTRAP_KEY},
            )
            revoked_result = client.get(
                "/api/v1/tasks/example",
                headers={"X-API-Key": new_key},
            )

        assert old_result.status_code == 401
        assert new_result.status_code == 200
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "REVOKED"
        assert revoked.json()["api_key"] is None
        assert revoked_result.status_code == 401
    finally:
        engine.dispose()


def test_database_never_stores_plaintext_and_audit_hashes_idempotency_key(tmp_path):
    engine, factory, app = _app(tmp_path)
    try:
        with TestClient(app) as client:
            created = _create_key(client, scopes=[READ, ADMIN_WRITE])
            listed = client.get(
                "/api/v1/admin/security/credentials",
                headers={"X-API-Key": BOOTSTRAP_KEY},
            )
            audits = client.get(
                "/api/v1/admin/security/audit-events",
                headers={"X-API-Key": BOOTSTRAP_KEY},
            )

        assert listed.status_code == 200
        assert listed.json()[0]["api_key"] is None
        assert created["api_key"] not in str(listed.json())
        assert audits.status_code == 200
        with factory() as session:
            credential = session.get(ApiCredentialModel, created["credential_id"])
            assert credential is not None
            assert not hasattr(credential, "api_key")
            assert created["api_key"] not in credential.secret_digest
            assert created["api_key"] not in credential.secret_salt
            event = session.scalar(
                sa.select(ApiAuditEventModel)
                .where(ApiAuditEventModel.action == "post:create_credential")
            )
            assert event is not None
            assert event.result == "SUCCEEDED"
            assert event.idempotency_key is not None
            assert event.idempotency_key != "create-automation"
            database_text = " ".join(
                str(value)
                for value in session.execute(
                    sa.select(ApiAuditEventModel.path, ApiAuditEventModel.error_message)
                ).first()
                if value is not None
            )
            assert created["api_key"] not in database_text
    finally:
        engine.dispose()


def test_credential_create_idempotency_key_prevents_duplicate_secret_issue(tmp_path):
    engine, _, app = _app(tmp_path)
    body = {
        "caller_id": "ci.automation",
        "display_name": "CI 自动化",
        "credential_name": "default",
        "scopes": [READ],
    }
    headers = {
        "X-API-Key": BOOTSTRAP_KEY,
        "Idempotency-Key": "stable-create-request",
    }
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/v1/admin/security/credentials", headers=headers, json=body
            )
            duplicate = client.post(
                "/api/v1/admin/security/credentials", headers=headers, json=body
            )

        assert first.status_code == 201
        assert duplicate.status_code == 409
        assert "密钥明文不能再次返回" in duplicate.json()["detail"]
    finally:
        engine.dispose()
