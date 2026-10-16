import asyncio
import json

from services.content_analysis.infrastructure import clients
from services.content_analysis.infrastructure.clients import BehaviorAnalysisClient, ProgressCallbackClient


def test_behavior_analysis_without_endpoint_is_explicitly_degraded():
    result = asyncio.run(
        BehaviorAnalysisClient(None).get(
            classroom_id="classroom-1",
            tenant_id=88888,
        )
    )

    assert result.available is False
    assert result.data is None
    assert result.missing_reason == "not_configured"


def test_progress_callback_uses_content_envelope(monkeypatch):
    sent = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def post(self, url, *, json, headers):
            sent.append((url, json, headers))
            return Response()

    monkeypatch.setattr(clients.aiohttp, "ClientSession", Session)
    payload = {"taskId": "task-1", "status": "RUNNING", "overview": "课堂摘要"}
    assert asyncio.run(ProgressCallbackClient().notify("https://example.invalid/callback", payload, "test-token"))
    url, body, headers = sent[0]
    assert url == "https://example.invalid/callback"
    assert set(body) == {"content"}
    assert json.loads(body["content"]) == payload
    assert headers["Authorization"] == "Bearer test-token"
