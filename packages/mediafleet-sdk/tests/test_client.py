from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

import httpx
import pytest

from mediafleet_sdk import (
    AuthenticationError,
    ConfigurationError,
    ConflictError,
    MediaFleetClient,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ResponseValidationError,
    RetryPolicy,
    ServiceUnavailableError,
    TransportError,
    ValidationError,
)
from mediafleet_sdk.models import (
    BindingCreate,
    CredentialCreate,
    RecordingStart,
    TaskCreate,
)


API_KEY = "sdk-test-placeholder-key"
BASE_URL = "https://mediafleet.example.com"


def test_security_resource_sends_write_body_and_never_retries() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/rotate"):
            return httpx.Response(503, json={"detail": "busy"})
        return httpx.Response(
            201,
            json={
                "credential_id": "00000000-0000-0000-0000-000000000001",
                "caller_id": "ci.automation",
                "display_name": "CI",
                "credential_name": "default",
                "scopes": ["read"],
                "status": "ACTIVE",
                "expires_at": None,
                "api_key": "mfk_once_only",
            },
        )

    client = MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        retry=RetryPolicy(max_attempts=3, backoff_factor=0),
        transport=httpx.MockTransport(handler),
    )
    try:
        created = client.security.create_credential(
            CredentialCreate(
                caller_id="ci.automation",
                display_name="CI",
                credential_name="default",
                scopes=["read"],
            ),
            idempotency_key="credential-create-1",
        )
        with pytest.raises(ServiceUnavailableError):
            client.security.rotate_credential(
                "00000000-0000-0000-0000-000000000001"
            )
    finally:
        client.close()

    assert created.api_key == "mfk_once_only"
    assert requests[0].headers["Idempotency-Key"] == "credential-create-1"
    assert requests[0].headers["X-API-Key"] == API_KEY
    assert requests[0].content
    assert len([request for request in requests if request.url.path.endswith("/rotate")]) == 1


def test_security_read_models_parse_and_hide_absent_plaintext() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/scopes"):
            return httpx.Response(200, json={"scopes": ["read", "admin:write"]})
        if request.url.path.endswith("/credentials"):
            return httpx.Response(
                200,
                json=[
                    {
                        "credential_id": "credential-1",
                        "caller_id": "ops",
                        "display_name": "运维",
                        "credential_name": "laptop",
                        "scopes": ["read"],
                        "status": "ACTIVE",
                        "expires_at": None,
                        "created_at": "2026-10-16T10:00:00Z",
                        "api_key": None,
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "event_id": "event-1",
                    "request_id": "request-1",
                    "caller_id": "ops",
                    "credential_id": "credential-1",
                    "method": "POST",
                    "path": "/api/v1/tasks",
                    "action": "post:/api/v1/tasks",
                    "required_scopes": ["task:write"],
                    "idempotency_key_digest": "digest",
                    "result": "SUCCEEDED",
                    "http_status": 201,
                    "started_at": "2026-10-16T10:00:00Z",
                    "completed_at": "2026-10-16T10:00:01Z",
                    "error_code": None,
                }
            ],
        )

    with MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert "admin:write" in client.security.scopes().scopes
        assert client.security.list_credentials()[0].api_key is None
        assert client.security.list_audit_events(limit=10)[0].http_status == 201


def test_business_write_resources_send_typed_requests_and_encoded_paths() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/api/v1/tasks":
            return httpx.Response(200, json={"task_id": "task-1", "created": True})
        if path.endswith("/retry") and "/callback/" not in path:
            return httpx.Response(
                200,
                json={"accepted": True, "task": _task_summary(status="pending")},
            )
        if path.endswith("/callback/retry"):
            return httpx.Response(
                200,
                json={"accepted": True, "callback_result": {"success": True}},
            )
        if path == "/api/v1/rtc/stream-bindings":
            return httpx.Response(
                200,
                json={
                    "stream_id": "stream-1",
                    "api_url": "https://zlm.example.invalid",
                    "server_id": "zlm-1",
                    "ip": "192.0.2.10",
                },
            )
        if path.startswith("/api/v1/rtc/stream-bindings/"):
            return httpx.Response(
                200,
                json={
                    "stream_id": "stream-1",
                    "api_url": "https://zlm.example.invalid",
                    "server_id": "zlm-1",
                    "ip": "192.0.2.10",
                },
            )
        if path in {"/api/v1/recordings/start", "/api/v1/recordings/stop"}:
            command = "record.start" if path.endswith("start") else "record.stop"
            return httpx.Response(
                200,
                json={
                    "accepted": True,
                    "command": command,
                    "task_id": "recording-1",
                    "target_node_id": "node-1",
                    "binding_id": "binding-1",
                    "app": "live",
                    "stream_id": "stream-1",
                    "message_id": f"recording-1:{command}",
                    "confirmed": True,
                },
            )
        if path.startswith("/api/v1/recording-servers/"):
            return httpx.Response(
                200,
                json={
                    "server_id": "server-1",
                    "server_code": "recorder/one",
                    "server_name": "Recorder One",
                    "status": "DRAINING",
                    "recorder_node_id": "node-1",
                    "zlm_server_id": "zlm-1",
                    "max_recordings": 10,
                    "max_bindings": 20,
                    "occupied_bindings": 1,
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    with MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        retry=RetryPolicy(max_attempts=3, backoff_factor=0),
        transport=httpx.MockTransport(handler),
    ) as client:
        created = client.tasks.create(
            TaskCreate(
                school_code="SCHOOL",
                task_type="video.cover.extract",
                callback_url="https://callback.example.invalid/result",
                params={"video_url": "https://media.example.invalid/video.mp4"},
            ),
            idempotency_key="task-create-001",
        )
        assert client.tasks.retry("task/failed").task.status == "pending"
        assert client.tasks.retry_callback("task/failed").callback_result["success"]
        assert client.bindings.create(
            BindingCreate(
                school_code="10001",
                resource_type="CAMERA",
                stream_id="stream-1",
                app="live",
            )
        ).server_id == "zlm-1"
        client.bindings.release("binding/one")
        assert client.recordings.start(
            RecordingStart(stream_id="stream-1", task_id="recording-1")
        ).command == "record.start"
        assert client.recordings.stop("recording-1").command == "record.stop"
        assert (
            client.recording_servers.update_status("recorder/one", "DRAINING").status
            == "DRAINING"
        )

    assert created.created is True
    assert requests[0].headers["Idempotency-Key"] == "task-create-001"
    assert json.loads(requests[0].content)["task_type"] == "video.cover.extract"
    assert requests[1].url.raw_path.endswith(b"task%2Ffailed/retry")
    assert requests[4].url.raw_path.endswith(b"binding%2Fone")
    assert requests[-1].url.raw_path.endswith(b"recorder%2Fone/status")
    assert json.loads(requests[-1].content) == {"status": "DRAINING"}


def test_write_request_models_reject_unknown_fields_and_short_idempotency_key() -> None:
    with pytest.raises(ValueError):
        TaskCreate.model_validate(
            {
                "school_code": "SCHOOL",
                "task_type": "video.cover.extract",
                "callback_url": "https://callback.example.invalid/result",
                "routing_key": "forbidden",
            }
        )
    with MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"task_id": "x", "created": True})
        ),
    ) as client:
        with pytest.raises(ValueError, match="8 到 128"):
            client.tasks.create(
                TaskCreate(
                    school_code="SCHOOL",
                    task_type="video.cover.extract",
                    callback_url="https://callback.example.invalid/result",
                ),
                idempotency_key="short",
            )


def _task_summary(**extra: Any) -> dict[str, Any]:
    return {
        "task_id": "task-1",
        "school_code": "SCHOOL",
        "task_type": "video.cover.extract",
        "status": "completed",
        "publish_status": "PUBLISHED",
        "progress": 100,
        "executor_node_id": "worker-1",
        "retry_count": 0,
        "max_retries": 3,
        "callback_result": None,
        "error_message": None,
        "created_at": "2026-10-12T10:00:00",
        "updated_at": "2026-10-12T18:00:00+08:00",
        "started_at": None,
        "completed_at": "2026-10-12T10:00:08Z",
        **extra,
    }


def test_config_rejects_unsafe_values_and_redacts_key() -> None:
    with pytest.raises(ConfigurationError):
        MediaFleetClient(base_url="ftp://example.com", api_key=API_KEY)
    with pytest.raises(ConfigurationError):
        MediaFleetClient(
            base_url="https://user:password@example.com", api_key=API_KEY
        )
    with pytest.raises(ConfigurationError):
        MediaFleetClient(base_url=BASE_URL, api_key="")
    with pytest.raises(ConfigurationError):
        MediaFleetClient(base_url=BASE_URL, api_key="key\r\ninjected: true")
    with pytest.raises(ConfigurationError):
        MediaFleetClient(base_url=BASE_URL, api_key=API_KEY, timeout=True)

    client = MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    try:
        assert API_KEY not in repr(client.config)
    finally:
        client.close()


def test_system_endpoints_do_not_receive_api_key_and_context_closes() -> None:
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={
                    "service": "mediafleet-control-center",
                    "role": "control_center",
                    "version": "0.1.0",
                    "status": "up",
                    "checked_at": "2026-10-12T10:00:00",
                },
            )
        return httpx.Response(
            200,
            json={
                "name": "mediafleet-control-center",
                "role": "control_center",
                "version": "0.1.0",
                "description": "control plane",
                "compatibility_entrypoint": None,
            },
        )

    client = MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
    )
    with client:
        health = client.system.health()
        info = client.system.service_info()
        assert health.checked_at.tzinfo == timezone.utc
        assert info.role == "control_center"
        assert all("x-api-key" not in headers for headers in seen_headers)
    assert client.is_closed


def test_task_list_sends_auth_filters_and_preserves_unknown_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == API_KEY
        assert request.url.params["status"] == "completed"
        assert request.url.params["page"] == "2"
        assert request.url.params["page_size"] == "50"
        assert request.url.params["created_from"] == "2026-10-12T00:00:00+00:00"
        return httpx.Response(
            200,
            json={
                "items": [_task_summary(server_extension="kept")],
                "total": 1,
                "page": 2,
                "page_size": 50,
                "page_extension": True,
            },
        )

    with MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.tasks.list(
            status="completed",
            page=2,
            page_size=50,
            created_from=datetime(2026, 10, 12, tzinfo=timezone.utc),
        )

    assert result.items[0].created_at.tzinfo == timezone.utc
    assert result.items[0].updated_at.hour == 10
    assert result.items[0].server_extension == "kept"
    assert result.page_extension is True


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, NotFoundError),
        (409, ConflictError),
        (422, ValidationError),
        (429, RateLimitError),
        (503, ServiceUnavailableError),
    ],
)
def test_http_status_maps_to_public_error(status_code: int, error_type: type) -> None:
    detail = f"failure {API_KEY} https://user:pass@example.com/file?token=secret"
    client = MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        retry=RetryPolicy(max_attempts=1),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status_code,
                json={"detail": detail},
                headers={"X-Request-ID": "request-1"},
            )
        ),
    )
    try:
        with pytest.raises(error_type) as captured:
            client.admin.overview()
    finally:
        client.close()

    error = captured.value
    assert error.status_code == status_code
    assert error.path == "/api/v1/admin/overview"
    assert error.request_id == "request-1"
    assert API_KEY not in str(error)
    assert "token=secret" not in str(error)
    assert "user:pass" not in str(error)


def test_response_validation_never_keeps_raw_sensitive_input() -> None:
    client = MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"service": API_KEY, "role": "control_center"},
            )
        ),
    )
    try:
        with pytest.raises(ResponseValidationError) as captured:
            client.system.health()
    finally:
        client.close()

    assert API_KEY not in str(captured.value)
    assert API_KEY not in repr(captured.value.issues)
    assert captured.value.__context__ is None
    assert {issue["type"] for issue in captured.value.issues} == {"missing"}


def test_transport_error_does_not_retain_request_with_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        retry=RetryPolicy(max_attempts=1),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(TransportError) as captured:
            client.admin.overview()
    finally:
        client.close()

    assert API_KEY not in str(captured.value)
    assert captured.value.__context__ is None


def test_get_retries_and_honors_bounded_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"detail": "busy"}, headers={"Retry-After": "9"})
        return httpx.Response(
            200,
            json={
                "tasks_by_status": {},
                "tasks_by_type": {},
                "nodes_by_status": {},
                "queues": [],
            },
        )

    monkeypatch.setattr("mediafleet_sdk.client.time.sleep", delays.append)
    with MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        retry=RetryPolicy(max_attempts=2, backoff_factor=0.1, max_delay=1.5),
        transport=httpx.MockTransport(handler),
    ) as client:
        client.admin.overview()

    assert calls == 2
    assert delays == [1.5]


def test_non_read_request_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"detail": "busy"})

    client = MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        retry=RetryPolicy(max_attempts=3, backoff_factor=0),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ServiceUnavailableError):
            client._request_model("POST", "api/v1/example", dict[str, bool])
    finally:
        client.close()
    assert calls == 1


def test_task_id_is_path_encoded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path == b"/api/v1/tasks/task%2Fwith%3Freserved"
        return httpx.Response(404, json={"detail": "missing"})

    client = MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        retry=RetryPolicy(max_attempts=1),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(NotFoundError):
            client.tasks.get("task/with?reserved")
    finally:
        client.close()


def test_remaining_read_resources_parse_control_center_responses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/tasks/task-1":
            return httpx.Response(
                200,
                json={
                    **_task_summary(),
                    "params": {},
                    "result": {"ok": True},
                    "callback_url": None,
                    "message_id": "message-1",
                    "published_at": "2026-10-12T10:00:01Z",
                },
            )
        if path == "/api/v1/tasks/task-1/files":
            return httpx.Response(
                200,
                json=[
                    {
                        "file_id": "file-1",
                        "task_id": "task-1",
                        "school_code": "SCHOOL",
                        "file_type": "COVER",
                        "file_name": "cover.jpg",
                        "file_url": "https://objects.example.com/cover.jpg",
                        "relative_path": "cover.jpg",
                        "bucket_name": "media",
                        "file_size": 10,
                        "mime_type": "image/jpeg",
                        "extra_info": None,
                        "created_at": "2026-10-12T10:00:08Z",
                    }
                ],
            )
        if path == "/api/v1/admin/nodes/page":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "node_id": "node-1",
                            "node_code": "worker-1",
                            "node_name": "Worker 1",
                            "node_type": "WORKER",
                            "status": "ONLINE",
                            "readiness_status": "READY",
                            "agent_url": None,
                            "zlm_api_url": None,
                            "capabilities": ["video.cover.extract"],
                            "capacity": {},
                            "readiness_details": {},
                            "last_heartbeat_at": "2026-10-12T10:00:00Z",
                            "active_bindings": 0,
                            "active_tasks": 1,
                            "recording_server_code": None,
                            "recording_server_status": None,
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "page_size": 20,
                },
            )
        if path == "/api/v1/admin/bindings/page":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "binding_id": "binding-1",
                            "school_code": "SCHOOL",
                            "resource_type": "classroom",
                            "space_id": "room-1",
                            "node_id": "node-1",
                            "node_code": "recorder-1",
                            "app": "live",
                            "stream_id": "stream-1",
                            "stream_name": "Camera",
                            "stream_mode": "push",
                            "status": "ACTIVE",
                            "version": 1,
                            "last_active_at": "2026-10-12T10:00:00Z",
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "page_size": 20,
                },
            )
        if path == "/api/v1/recording-servers":
            return httpx.Response(
                200,
                json=[
                    {
                        "server_id": "server-1",
                        "server_code": "recording-1",
                        "server_name": "Recorder 1",
                        "status": "ACTIVE",
                        "recorder_node_id": "node-1",
                        "zlm_server_id": "zlm-1",
                        "zlm_api_url": None,
                        "play_host": None,
                        "play_port": None,
                        "play_protocol": None,
                        "rtmp_port": None,
                        "rtsp_port": None,
                        "record_root": None,
                        "max_recordings": 20,
                        "max_bindings": 50,
                        "occupied_bindings": 2,
                    }
                ],
            )
        raise AssertionError(f"unexpected path: {path}")

    with MediaFleetClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.tasks.get("task-1").result == {"ok": True}
        assert client.tasks.list_files("task-1")[0].file_name == "cover.jpg"
        assert client.nodes.list().items[0].node_code == "worker-1"
        assert client.bindings.list(status="ACTIVE").items[0].stream_id == "stream-1"
        assert client.recording_servers.list()[0].server_code == "recording-1"
