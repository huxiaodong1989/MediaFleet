from __future__ import annotations

import httpx
import pytest

from mediafleet_sdk import AsyncMediaFleetClient, RetryPolicy


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
