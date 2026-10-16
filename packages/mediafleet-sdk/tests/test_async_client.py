from __future__ import annotations

import httpx
import pytest

from mediafleet_sdk import AsyncMediaFleetClient, RetryPolicy
from mediafleet_sdk.models import BindingCreate, RecordingStart, TaskCreate


@pytest.mark.asyncio
async def test_async_client_retries_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["X-API-Key"] == "async-placeholder-key"
        if calls == 1:
            raise httpx.ConnectError("unavailable", request=request)
        return httpx.Response(
            200,
            json={
                "tasks_by_status": {},
                "tasks_by_type": {},
                "nodes_by_status": {},
                "queues": [],
            },
        )

    monkeypatch.setattr("mediafleet_sdk.async_client.asyncio.sleep", fake_sleep)
    client = AsyncMediaFleetClient(
        base_url="https://mediafleet.example.com",
        api_key="async-placeholder-key",
        retry=RetryPolicy(max_attempts=2, backoff_factor=0.1),
        transport=httpx.MockTransport(handler),
    )
    async with client:
        overview = await client.admin.overview()
        assert overview.queues == []

    assert calls == 2
    assert delays == [0.1]
    assert client.is_closed


@pytest.mark.asyncio
async def test_async_system_health_does_not_send_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "x-api-key" not in request.headers
        return httpx.Response(
            200,
            json={
                "service": "mediafleet-control-center",
                "role": "control_center",
                "version": "0.1.0",
                "status": "up",
                "checked_at": "2026-10-12T10:00:00Z",
            },
        )

    async with AsyncMediaFleetClient(
        base_url="https://mediafleet.example.com",
        api_key="async-placeholder-key",
        transport=httpx.MockTransport(handler),
    ) as client:
        health = await client.system.health()
        assert health.status == "up"


@pytest.mark.asyncio
async def test_async_business_write_resources() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        path = request.url.path
        if path == "/api/v1/tasks":
            assert request.headers["Idempotency-Key"] == "async-task-create"
            return httpx.Response(200, json={"task_id": "task-1", "created": True})
        if path.endswith("/callback/retry"):
            return httpx.Response(200, json={"accepted": True, "callback_result": {}})
        if path.endswith("/retry"):
            return httpx.Response(
                200,
                json={
                    "accepted": True,
                    "task": {
                        "task_id": "task-1",
                        "school_code": "SCHOOL",
                        "task_type": "video.cover.extract",
                        "status": "pending",
                        "publish_status": "PENDING",
                        "progress": 0,
                        "executor_node_id": None,
                        "retry_count": 0,
                        "max_retries": 3,
                        "callback_result": None,
                        "error_message": None,
                        "created_at": "2026-10-16T10:00:00Z",
                        "updated_at": "2026-10-16T10:00:00Z",
                        "started_at": None,
                        "completed_at": None,
                    },
                },
            )
        if path.startswith("/api/v1/rtc/stream-bindings"):
            return httpx.Response(
                200,
                json={
                    "stream_id": "stream-1",
                    "api_url": "https://zlm.example.invalid",
                    "server_id": "zlm-1",
                    "ip": "192.0.2.10",
                },
            )
        if path.startswith("/api/v1/recordings/"):
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
                    "message_id": "message-1",
                    "confirmed": True,
                },
            )
        if path.startswith("/api/v1/recording-servers/"):
            return httpx.Response(
                200,
                json={
                    "server_id": "server-1",
                    "server_code": "recorder-1",
                    "server_name": "Recorder",
                    "status": "ACTIVE",
                    "recorder_node_id": "node-1",
                    "zlm_server_id": "zlm-1",
                    "max_recordings": 10,
                    "max_bindings": 20,
                    "occupied_bindings": 0,
                },
            )
        raise AssertionError(path)

    async with AsyncMediaFleetClient(
        base_url="https://mediafleet.example.com",
        api_key="async-placeholder-key",
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.tasks.create(
            TaskCreate(
                school_code="SCHOOL",
                task_type="video.cover.extract",
                callback_url="https://callback.example.invalid/result",
            ),
            idempotency_key="async-task-create",
        )
        await client.tasks.retry("task-1")
        await client.tasks.retry_callback("task-1")
        await client.bindings.create(
            BindingCreate(
                school_code="10001",
                resource_type="DESKTOP",
                stream_id="stream-1",
                app="live",
            )
        )
        await client.bindings.release("binding-1")
        await client.recordings.start(
            RecordingStart(stream_id="stream-1", task_id="recording-1")
        )
        await client.recordings.stop("recording-1")
        await client.recording_servers.update_status("recorder-1", "ACTIVE")

    assert len(seen) == 8
