from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class ControlCenterHandler(BaseHTTPRequestHandler):
    api_key_seen: str | None = None
    idempotency_key_seen: str | None = None
    task_create_count = 0

    def do_GET(self):
        if self.path == "/health":
            self._json(
                {
                    "service": "mediafleet",
                    "role": "control-center",
                    "version": "0.1.0",
                    "status": "ok",
                    "checked_at": "2026-10-12T00:00:00Z",
                }
            )
            return
        if self.path == "/api/v1/admin/overview":
            type(self).api_key_seen = self.headers.get("X-API-Key")
            self._json(
                {
                    "tasks_by_status": {"completed": 1},
                    "tasks_by_type": {"video.cover.extract": 1},
                    "nodes_by_status": {"online": 1},
                    "queues": [],
                }
            )
            return
        self.send_error(404)

    def do_POST(self):
        if self.path == "/api/v1/tasks":
            type(self).api_key_seen = self.headers.get("X-API-Key")
            type(self).idempotency_key_seen = self.headers.get("Idempotency-Key")
            type(self).task_create_count += 1
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length) or b"{}")
            assert payload["task_type"] == "video.cover.extract"
            self._json(
                {
                    "task_id": "task-created",
                    "created": type(self).task_create_count == 1,
                }
            )
            return
        self.send_error(404)

    def log_message(self, format, *args):
        return

    def _json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def control_center():
    ControlCenterHandler.api_key_seen = None
    ControlCenterHandler.idempotency_key_seen = None
    ControlCenterHandler.task_create_count = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), ControlCenterHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_stdio_protocol_lists_tools_and_calls_real_sdk(control_center):
    env = os.environ.copy()
    env["MEDIAFLEET_URL"] = control_center
    env["MEDIAFLEET_API_KEY"] = "stdio-test-secret"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mediafleet_mcp"],
        env=env,
    )

    with open(os.devnull, "w", encoding="utf-8") as error_log:
        async with stdio_client(parameters, errlog=error_log) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                health = await session.call_tool("mediafleet_health", {})
                overview = await session.call_tool("mediafleet_get_overview", {})

    assert {tool.name for tool in tools.tools} == {
        "mediafleet_health",
        "mediafleet_get_overview",
        "mediafleet_list_tasks",
        "mediafleet_get_task",
        "mediafleet_list_task_files",
        "mediafleet_list_nodes",
        "mediafleet_list_bindings",
        "mediafleet_list_recording_servers",
    }
    assert health.structured_content["data"]["status"] == "ok"
    assert overview.structured_content["data"]["tasks_by_status"] == {
        "completed": 1
    }
    assert ControlCenterHandler.api_key_seen == "stdio-test-secret"
    assert "stdio-test-secret" not in str(overview)


@pytest.mark.asyncio
async def test_stdio_protocol_exposes_only_allowlisted_write_tool(control_center):
    env = os.environ.copy()
    env["MEDIAFLEET_URL"] = control_center
    env["MEDIAFLEET_API_KEY"] = "stdio-write-secret"
    env["MEDIAFLEET_MCP_WRITE_TOOLS"] = "mediafleet_create_task"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mediafleet_mcp"],
        env=env,
    )

    with open(os.devnull, "w", encoding="utf-8") as error_log:
        async with stdio_client(parameters, errlog=error_log) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                arguments = {
                    "school_code": "1001",
                    "task_type": "video.cover.extract",
                    "callback_url": "https://example.invalid/callback",
                    "idempotency_key": "stdio-stable-key",
                    "confirmed": True,
                    "params": {
                        "video_url": "https://example.invalid/video.mp4"
                    },
                }
                result = await session.call_tool(
                    "mediafleet_create_task",
                    arguments,
                )
                duplicate = await session.call_tool(
                    "mediafleet_create_task",
                    arguments,
                )

    tool_names = {tool.name for tool in tools.tools}
    assert "mediafleet_create_task" in tool_names
    assert "mediafleet_stop_recording" not in tool_names
    assert result.structured_content["data"] == {
        "task_id": "task-created",
        "created": True,
    }
    assert duplicate.structured_content["data"] == {
        "task_id": "task-created",
        "created": False,
    }
    assert ControlCenterHandler.task_create_count == 2
    assert ControlCenterHandler.api_key_seen == "stdio-write-secret"
    assert ControlCenterHandler.idempotency_key_seen == "stdio-stable-key"
    assert "stdio-write-secret" not in str(result)
