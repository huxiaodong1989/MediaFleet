from __future__ import annotations

from typing import Any

import pytest

from mediafleet_mcp import server as server_module
from mediafleet_mcp.server import server
from mediafleet_sdk import AuthenticationError
from mediafleet_sdk.models import (
    AdminOverview,
    BindingPage,
    BindingSummary,
    NodePage,
    NodeSummary,
    QueueSummary,
    RecordingServer,
    ServiceHealth,
    TaskDetail,
    TaskFile,
    TaskPage,
    TaskSummary,
)

TIMESTAMP = "2026-10-12T00:00:00Z"
TOOL_NAMES = {
    "mediafleet_health",
    "mediafleet_get_overview",
    "mediafleet_list_tasks",
    "mediafleet_get_task",
    "mediafleet_list_task_files",
    "mediafleet_list_nodes",
    "mediafleet_list_bindings",
    "mediafleet_list_recording_servers",
}


class AsyncResource:
    def __init__(self, **responses: Any) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str):
        async def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            value = self.responses[name]
            if isinstance(value, Exception):
                raise value
            return value

        return call


class FakeClient:
    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        task = TaskSummary(
            task_id="task-1",
            school_code="tenant-demo",
            task_type="video.cover.extract",
            status="completed",
            publish_status="published",
            progress=100,
            executor_node_id="worker-1",
            retry_count=0,
            max_retries=3,
            callback_result=None,
            error_message=None,
            created_at=TIMESTAMP,
            updated_at=TIMESTAMP,
            started_at=TIMESTAMP,
            completed_at=TIMESTAMP,
        )
        detail_data = task.model_dump()
        credential_url = (
            "https://" + "placeholder-user" + ":" + "placeholder-password"
            "@example.invalid/input?token=secret"
        )
        detail_data["error_message"] = f"失败地址 {credential_url}"
        detail = TaskDetail(
            **detail_data,
            params={
                "source_url": credential_url.replace("/input", "/video.mp4"),
                "api_key": "never-return",
            },
            result={"download_url": "https://example.invalid/result?signature=secret"},
        )
        task_file = TaskFile(
            file_id="file-1",
            task_id="task-1",
            school_code="tenant-demo",
            file_type="COVER",
            file_name="cover.jpg",
            file_url="https://example.invalid/cover.jpg?signature=secret",
            file_size=123,
            mime_type="image/jpeg",
            created_at=TIMESTAMP,
        )
        node = NodeSummary(
            node_id="node-id-1",
            node_code="worker-1",
            node_name="Worker 1",
            node_type="media-worker",
            status="online",
            readiness_status="ready",
            agent_url="http://worker.internal.invalid:8009",
            zlm_api_url=None,
            capabilities=["video.cover.extract"],
            capacity={"max_tasks": 4},
            readiness_details={},
            last_heartbeat_at=TIMESTAMP,
            active_bindings=0,
            active_tasks=1,
            recording_server_code=None,
            recording_server_status=None,
        )
        binding = BindingSummary(
            binding_id="binding-1",
            school_code="tenant-demo",
            resource_type="classroom",
            space_id="room-1",
            node_id="node-id-1",
            node_code="recorder-1",
            app="live",
            stream_id="stream-1",
            stream_name="camera",
            stream_mode="push",
            status="active",
            version=1,
            last_active_at=TIMESTAMP,
        )
        recorder = RecordingServer(
            server_id="server-id-1",
            server_code="recorder-1",
            server_name="Recorder 1",
            status="enabled",
            recorder_node_id="node-id-1",
            zlm_server_id="zlm-1",
            zlm_api_url="http://zlm.internal.invalid:8080",
            record_root="D:/recordings",
            max_recordings=4,
            max_bindings=20,
            occupied_bindings=1,
        )
        self.system = AsyncResource(
            health=ServiceHealth(
                service="mediafleet",
                role="control-center",
                version="0.1.0",
                status="ok",
                checked_at=TIMESTAMP,
            )
        )
        self.admin = AsyncResource(
            overview=AdminOverview(
                tasks_by_status={"completed": 1},
                tasks_by_type={"video.cover.extract": 1},
                nodes_by_status={"online": 1},
                queues=[
                    QueueSummary(
                        queue_name="media-worker.tasks",
                        message_count=0,
                        consumer_count=1,
                        status="ok",
                    )
                ],
            )
        )
        self.tasks = AsyncResource(
            list=TaskPage(items=[task], total=1, page=1, page_size=20),
            get=detail,
            list_files=[task_file],
        )
        self.nodes = AsyncResource(
            list=NodePage(items=[node], total=1, page=1, page_size=20)
        )
        self.bindings = AsyncResource(
            list=BindingPage(items=[binding], total=1, page=1, page_size=20)
        )
        self.recording_servers = AsyncResource(list=[recorder])

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


@pytest.fixture
def fake_client(monkeypatch):
    clients: list[FakeClient] = []

    def factory(**kwargs: Any) -> FakeClient:
        client = FakeClient(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(server_module, "AsyncMediaFleetClient", factory)
    monkeypatch.setenv("MEDIAFLEET_URL", "https://mediafleet.example.com")
    monkeypatch.setenv("MEDIAFLEET_API_KEY", "test-secret")
    return clients


@pytest.mark.asyncio
async def test_server_exposes_only_eight_read_only_tools():
    tools = await server.list_tools()

    assert {tool.name for tool in tools} == TOOL_NAMES
    assert all(tool.annotations.read_only_hint is True for tool in tools)
    assert all(tool.annotations.destructive_hint is False for tool in tools)
    assert all(tool.annotations.idempotent_hint is True for tool in tools)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("mediafleet_health", {}),
        ("mediafleet_get_overview", {}),
        ("mediafleet_list_tasks", {"page": 1, "page_size": 20}),
        ("mediafleet_get_task", {"task_id": "task-1"}),
        ("mediafleet_list_task_files", {"task_id": "task-1"}),
        ("mediafleet_list_nodes", {"page": 1, "page_size": 20}),
        ("mediafleet_list_bindings", {"page": 1, "page_size": 20}),
        ("mediafleet_list_recording_servers", {}),
    ],
)
async def test_each_tool_returns_structured_success(fake_client, name, arguments):
    result = await server.call_tool(name, arguments)

    assert result.structured_content["ok"] is True
    assert result.structured_content["error"] is None


@pytest.mark.asyncio
async def test_task_filters_are_forwarded(fake_client):
    result = await server.call_tool(
        "mediafleet_list_tasks",
        {
            "page": 2,
            "page_size": 50,
            "task_type": "video.cover.extract",
            "status": "completed",
            "node_id": "worker-1",
            "school_code": "tenant-demo",
        },
    )

    assert result.structured_content["ok"] is True
    call = fake_client[-1].tasks.calls[-1]
    assert call[2]["page"] == 2
    assert call[2]["page_size"] == 50
    assert call[2]["status"] == "completed"


@pytest.mark.asyncio
async def test_sensitive_task_and_infrastructure_fields_are_omitted(fake_client):
    task_result = await server.call_tool("mediafleet_get_task", {"task_id": "task-1"})
    file_result = await server.call_tool(
        "mediafleet_list_task_files", {"task_id": "task-1"}
    )
    node_result = await server.call_tool(
        "mediafleet_list_nodes", {"page": 1, "page_size": 20}
    )
    recorder_result = await server.call_tool(
        "mediafleet_list_recording_servers", {}
    )

    task_payload = task_result.structured_content["data"]
    assert task_payload["params"]["api_key"] == "[REDACTED]"
    assert task_payload["params"]["source_url"] == "https://example.invalid/video.mp4"
    assert "signature=secret" not in str(task_result.structured_content)
    assert "placeholder-password" not in task_payload["error_message"]
    assert "token=secret" not in task_payload["error_message"]
    assert "file_url" not in file_result.structured_content["data"]["items"][0]
    assert "agent_url" not in node_result.structured_content["data"]["items"][0]
    assert "zlm_api_url" not in recorder_result.structured_content["data"]["items"][0]
    assert "record_root" not in recorder_result.structured_content["data"]["items"][0]


@pytest.mark.asyncio
async def test_page_size_is_bounded_by_tool_schema(fake_client):
    with pytest.raises(Exception) as captured:
        await server.call_tool(
            "mediafleet_list_tasks", {"page": 1, "page_size": 101}
        )

    assert "100" in str(captured.value)


@pytest.mark.asyncio
async def test_sdk_error_is_returned_as_structured_failure(monkeypatch, fake_client):
    error = AuthenticationError(
        status_code=401,
        detail="API Key 无效",
        path="/api/v1/admin/overview",
    )

    class ErrorClient(FakeClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.admin = AsyncResource(overview=error)

    monkeypatch.setattr(server_module, "AsyncMediaFleetClient", ErrorClient)

    result = await server.call_tool("mediafleet_get_overview", {})

    assert result.structured_content == {
        "ok": False,
        "data": None,
        "error": {
            "code": "authentication_error",
            "message": str(error),
            "retryable": False,
            "http_status": 401,
        },
    }
    assert "test-secret" not in str(result.structured_content)
