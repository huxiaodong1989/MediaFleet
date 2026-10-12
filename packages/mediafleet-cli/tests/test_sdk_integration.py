from __future__ import annotations

import json

import httpx
from typer.testing import CliRunner

from mediafleet_cli.commands import query as query_commands
from mediafleet_cli.main import app
from mediafleet_sdk import MediaFleetClient

runner = CliRunner()


def test_overview_uses_real_sdk_and_sends_api_key_only_to_protected_endpoint(monkeypatch):
    captured_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(dict(request.headers))
        assert request.url.path == "/api/v1/admin/overview"
        return httpx.Response(
            200,
            json={
                "tasks_by_status": {"completed": 2},
                "tasks_by_type": {"video.cover.extract": 2},
                "nodes_by_status": {"online": 1},
                "queues": [],
            },
        )

    def create_client(options, *, require_api_key=True):
        return MediaFleetClient(
            base_url="https://mediafleet.example.com",
            api_key="integration-secret",
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(query_commands, "create_client", create_client)

    result = runner.invoke(app, ["overview", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["tasks_by_status"] == {"completed": 2}
    assert captured_headers[0]["x-api-key"] == "integration-secret"
    assert "integration-secret" not in result.output


def test_health_uses_real_sdk_without_sending_api_key(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert "x-api-key" not in request.headers
        return httpx.Response(
            200,
            json={
                "service": "mediafleet",
                "role": "control-center",
                "version": "0.1.0",
                "status": "ok",
                "checked_at": "2026-10-12T00:00:00Z",
            },
        )

    def create_client(options, *, require_api_key=True):
        assert require_api_key is False
        return MediaFleetClient(
            base_url="https://mediafleet.example.com",
            api_key="placeholder-not-sent",
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(query_commands, "create_client", create_client)

    result = runner.invoke(app, ["health", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "ok"
