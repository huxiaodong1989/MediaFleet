from __future__ import annotations

from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

from mediafleet_mcp import server as server_module
from mediafleet_mcp.server import register_write_tools, server
from mediafleet_mcp.settings import WRITE_TOOL_NAMES
from mediafleet_sdk import AuthenticationError, PermissionDeniedError, TimeoutError
from mediafleet_sdk.models import (
    AdminOverview,
    BindingEndpoint,
    BindingPage,
    BindingSummary,
    CallbackRetryResult,
    NodePage,
    NodeSummary,
    QueueSummary,
    RecordingCommandResult,
    RecordingServer,
    ServiceHealth,
    TaskCreateResult,
    TaskDetail,
    TaskFile,
    TaskPage,
    TaskRetryResult,
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
            create=TaskCreateResult(task_id="task-created", created=True),
            retry=TaskRetryResult(accepted=True, task=task),
            retry_callback=CallbackRetryResult(
                accepted=True,
                callback_result={
                    "success": True,
                    "callback_url": "https://example.invalid/callback?token=secret",
                },
            ),
        )
        self.nodes = AsyncResource(
            list=NodePage(items=[node], total=1, page=1, page_size=20)
        )
        self.bindings = AsyncResource(
            list=BindingPage(items=[binding], total=1, page=1, page_size=20),
            create=BindingEndpoint(
                stream_id="stream-1",
                api_url="http://zlm.internal.invalid:8080",
                server_id="zlm-1",
                ip="10.0.0.8",
            ),
            release=BindingEndpoint(
                stream_id="stream-1",
                api_url="http://zlm.internal.invalid:8080",
                server_id="zlm-1",
                ip="10.0.0.8",
            ),
        )
        self.recording_servers = AsyncResource(list=[recorder])
        recording_result = RecordingCommandResult(
            accepted=True,
            command="record.start",
            task_id="recording-task-1",
            target_node_id="node-id-1",
            binding_id="binding-1",
            app="live",
            stream_id="stream-1",
            message_id="recording-task-1:record.start",
            confirmed=True,
        )
        self.recordings = AsyncResource(
            start=recording_result,
            stop=recording_result.model_copy(
                update={
                    "command": "record.stop",
                    "message_id": "recording-task-1:record.stop",
                }
            ),
        )

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
    monkeypatch.setenv(
        "MEDIAFLEET_MCP_WRITE_TOOLS",
        ",".join(sorted(WRITE_TOOL_NAMES)),
    )
    return clients


@pytest.fixture
def write_server():
    instance = MCPServer(name="mediafleet-write-test", version="0.1.0")
    register_write_tools(instance, WRITE_TOOL_NAMES)
    return instance


@pytest.mark.asyncio
async def test_server_exposes_only_eight_read_only_tools():
    tools = await server.list_tools()

    assert {tool.name for tool in tools} == TOOL_NAMES
    assert all(tool.annotations.read_only_hint is True for tool in tools)
    assert all(tool.annotations.destructive_hint is False for tool in tools)
    assert all(tool.annotations.idempotent_hint is True for tool in tools)


@pytest.mark.asyncio
async def test_write_tools_are_registered_only_from_explicit_allowlist():
    instance = MCPServer(name="mediafleet-write-subset", version="0.1.0")
    register_write_tools(
        instance,
        frozenset({"mediafleet_create_task", "mediafleet_release_binding"}),
    )

    tools = {tool.name: tool for tool in await instance.list_tools()}

    assert set(tools) == {"mediafleet_create_task", "mediafleet_release_binding"}
    assert tools["mediafleet_create_task"].annotations.read_only_hint is False
    assert tools["mediafleet_create_task"].annotations.destructive_hint is False
    assert tools["mediafleet_create_task"].annotations.idempotent_hint is True
    assert tools["mediafleet_release_binding"].annotations.destructive_hint is True
    assert "mediafleet_update_recording_server_status" not in tools


@pytest.mark.asyncio
async def test_each_write_tool_has_reviewed_risk_annotations(write_server):
    tools = {tool.name: tool for tool in await write_server.list_tools()}
    expected = {
        "mediafleet_create_task": (False, True),
        "mediafleet_retry_task": (False, False),
        "mediafleet_retry_task_callback": (False, False),
        "mediafleet_create_binding": (False, True),
        "mediafleet_release_binding": (True, True),
        "mediafleet_start_recording": (False, True),
        "mediafleet_stop_recording": (True, True),
    }

    assert set(tools) == set(expected)
    for name, (destructive, idempotent) in expected.items():
        annotations = tools[name].annotations
        assert annotations.read_only_hint is False
        assert annotations.destructive_hint is destructive
        assert annotations.idempotent_hint is idempotent
        assert annotations.open_world_hint is True


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


@pytest.mark.asyncio
async def test_write_tool_requires_explicit_confirmation(fake_client, write_server):
    result = await write_server.call_tool(
        "mediafleet_retry_task",
        {"task_id": "task-1", "confirmed": False},
    )

    assert result.structured_content["ok"] is False
    assert result.structured_content["error"]["code"] == "confirmation_required"
    assert fake_client[-1].tasks.calls == []


@pytest.mark.asyncio
async def test_enabled_write_tools_call_async_sdk_and_project_safe_results(
    fake_client,
    write_server,
):
    create_task = await write_server.call_tool(
        "mediafleet_create_task",
        {
            "school_code": "1001",
            "task_type": "video.cover.extract",
            "callback_url": "https://example.invalid/callback",
            "idempotency_key": "stable-key-1001",
            "confirmed": True,
            "params": {"video_url": "https://example.invalid/video.mp4"},
        },
    )
    retry_task = await write_server.call_tool(
        "mediafleet_retry_task",
        {"task_id": "task-1", "confirmed": True},
    )
    retry_callback = await write_server.call_tool(
        "mediafleet_retry_task_callback",
        {"task_id": "task-1", "confirmed": True},
    )
    create_binding = await write_server.call_tool(
        "mediafleet_create_binding",
        {
            "school_code": "1001",
            "resource_type": "CAMERA",
            "stream_id": "stream-1",
            "app": "live",
            "confirmed": True,
        },
    )
    release_binding = await write_server.call_tool(
        "mediafleet_release_binding",
        {"binding_id": "binding-1", "confirmed": True},
    )
    start_recording = await write_server.call_tool(
        "mediafleet_start_recording",
        {
            "stream_id": "stream-1",
            "task_id": "recording-task-1",
            "confirmed": True,
        },
    )
    stop_recording = await write_server.call_tool(
        "mediafleet_stop_recording",
        {"task_id": "recording-task-1", "confirmed": True},
    )

    assert create_task.structured_content["data"] == {
        "task_id": "task-created",
        "created": True,
    }
    assert retry_task.structured_content["data"]["accepted"] is True
    assert "token=secret" not in str(retry_callback.structured_content)
    assert create_binding.structured_content["data"] == {
        "stream_id": "stream-1",
        "server_id": "zlm-1",
        "endpoint_available": True,
    }
    assert "10.0.0.8" not in str(create_binding.structured_content)
    assert "internal.invalid" not in str(release_binding.structured_content)
    assert start_recording.structured_content["data"]["command"] == "record.start"
    assert stop_recording.structured_content["data"]["command"] == "record.stop"

    task_create_call = fake_client[0].tasks.calls[-1]
    assert task_create_call[0] == "create"
    assert task_create_call[2]["idempotency_key"] == "stable-key-1001"
    assert fake_client[3].bindings.calls[-1][0] == "create"
    assert fake_client[4].bindings.calls[-1] == ("release", ("binding-1",), {})
    assert fake_client[5].recordings.calls[-1][0] == "start"
    assert fake_client[6].recordings.calls[-1] == (
        "stop",
        ("recording-task-1",),
        {},
    )


@pytest.mark.asyncio
async def test_task_write_payload_has_size_limit(fake_client, write_server):
    result = await write_server.call_tool(
        "mediafleet_create_task",
        {
            "school_code": "1001",
            "task_type": "video.cover.extract",
            "callback_url": "https://example.invalid/callback",
            "idempotency_key": "stable-key-large",
            "confirmed": True,
            "params": {"text": "x" * (64 * 1024)},
        },
    )

    assert result.structured_content["ok"] is False
    assert result.structured_content["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_validation_error_does_not_echo_sensitive_write_input(
    fake_client,
    write_server,
):
    result = await write_server.call_tool(
        "mediafleet_start_recording",
        {
            "stream_id": "stream-1",
            "task_id": "recording-task-invalid-window",
            "confirmed": True,
            "start_time": "2026-10-19T02:00:00Z",
            "end_time": "2026-10-19T01:00:00Z",
            "extra_params": {"api_key": "must-not-appear"},
        },
    )

    assert result.structured_content["error"]["code"] == "validation_error"
    assert "end_time" in result.structured_content["error"]["message"]
    assert "must-not-appear" not in str(result.structured_content)


@pytest.mark.asyncio
async def test_write_permission_denial_is_structured(monkeypatch, fake_client, write_server):
    error = PermissionDeniedError(
        status_code=403,
        detail="缺少 task:write 权限",
        path="/api/v1/tasks",
    )

    class ErrorClient(FakeClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.tasks = AsyncResource(create=error)

    monkeypatch.setattr(server_module, "AsyncMediaFleetClient", ErrorClient)

    result = await write_server.call_tool(
        "mediafleet_create_task",
        {
            "school_code": "1001",
            "task_type": "video.cover.extract",
            "callback_url": "https://example.invalid/callback",
            "idempotency_key": "stable-key-denied",
            "confirmed": True,
        },
    )

    assert result.structured_content["error"] == {
        "code": "permission_denied",
        "message": str(error),
        "retryable": False,
        "http_status": 403,
    }


@pytest.mark.asyncio
async def test_write_timeout_is_not_retried_by_mcp(monkeypatch, fake_client, write_server):
    error = TimeoutError("开始录制请求超时")
    clients: list[FakeClient] = []

    class ErrorClient(FakeClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.recordings = AsyncResource(start=error)
            clients.append(self)

    monkeypatch.setattr(server_module, "AsyncMediaFleetClient", ErrorClient)

    result = await write_server.call_tool(
        "mediafleet_start_recording",
        {
            "stream_id": "stream-1",
            "task_id": "recording-task-timeout",
            "confirmed": True,
        },
    )

    assert result.structured_content["error"]["code"] == "timeout"
    assert result.structured_content["error"]["retryable"] is True
    assert len(clients[0].recordings.calls) == 1
    assert clients[0].recordings.calls[0][0] == "start"
