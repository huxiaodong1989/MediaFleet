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
