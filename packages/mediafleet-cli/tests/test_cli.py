from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from mediafleet_cli.commands import auth as auth_commands
from mediafleet_cli.commands import query as query_commands
from mediafleet_cli.main import app
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

runner = CliRunner()
TIMESTAMP = "2026-10-12T00:00:00Z"


class Resource:
    def __init__(self, **responses):
        self.responses = responses
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.responses[name]

        return call


class FakeClient:
    def __init__(self):
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
        detail = TaskDetail(
            **task.model_dump(),
            params={"source": "example.mp4"},
            result={"ok": True},
        )
        task_file = TaskFile(
            file_id="file-1",
            task_id="task-1",
            school_code="tenant-demo",
            file_type="COVER",
            file_name="cover.jpg",
            file_url="https://media.example.invalid/cover.jpg",
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
            agent_url=None,
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
            max_recordings=4,
            max_bindings=20,
            occupied_bindings=1,
        )
        self.system = Resource(
            health=ServiceHealth(
                service="mediafleet",
                role="control-center",
                version="0.1.0",
                status="ok",
                checked_at=TIMESTAMP,
            )
        )
        self.admin = Resource(
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
        self.tasks = Resource(
            list=TaskPage(items=[task], total=1, page=1, page_size=20),
            get=detail,
            list_files=[task_file],
        )
        self.nodes = Resource(
            list=NodePage(items=[node], total=1, page=1, page_size=20)
        )
        self.bindings = Resource(
            list=BindingPage(items=[binding], total=1, page=1, page_size=20)
        )
        self.recording_servers = Resource(list=[recorder])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    calls = []

    def create(options, *, require_api_key=True):
        calls.append((options, require_api_key))
        return client

    monkeypatch.setattr(query_commands, "create_client", create)
    return client, calls


def parse_stdout(result):
    return json.loads(result.stdout)


def test_version_json_is_stable():
    result = runner.invoke(app, ["version", "--json"])

    assert result.exit_code == 0
    assert parse_stdout(result) == {"name": "mediafleet-cli", "version": "0.1.0"}


@pytest.mark.parametrize(
    ("arguments", "expected_key"),
    [
        (["health", "--json"], "service"),
        (["overview", "--json"], "tasks_by_status"),
        (["task", "list", "--json"], "items"),
        (["task", "get", "task-1", "--json"], "task_id"),
        (["task", "files", "task-1", "--json"], None),
        (["node", "list", "--json"], "items"),
        (["binding", "list", "--json"], "items"),
        (["recorder", "list", "--json"], None),
    ],
)
def test_query_commands_emit_json(fake_client, arguments, expected_key):
    result = runner.invoke(app, arguments)

    assert result.exit_code == 0, result.output
    payload = parse_stdout(result)
    if expected_key is None:
        assert isinstance(payload, list)
    else:
        assert expected_key in payload


def test_health_does_not_require_api_key(fake_client):
    _, calls = fake_client

    result = runner.invoke(app, ["health", "--json"])

    assert result.exit_code == 0
    assert calls[-1][1] is False


def test_task_filters_are_forwarded(fake_client):
    client, _ = fake_client

    result = runner.invoke(
        app,
        [
            "task",
            "list",
            "--page",
            "2",
            "--page-size",
            "50",
            "--task-type",
            "video.cover.extract",
            "--status",
            "completed",
            "--node-id",
            "node-1",
            "--school-code",
            "tenant-demo",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert client.tasks.calls[-1][2] == {
        "page": 2,
        "page_size": 50,
        "task_type": "video.cover.extract",
        "status": "completed",
        "node_id": "node-1",
        "school_code": "tenant-demo",
        "created_from": None,
        "created_to": None,
    }


def test_binding_filters_are_forwarded(fake_client):
    client, _ = fake_client

    result = runner.invoke(
        app,
        [
            "binding",
            "list",
            "--node-id",
            "node-1",
            "--status",
            "active",
            "--resource-type",
            "classroom",
            "--space-id",
            "room-1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert client.bindings.calls[-1][2]["space_id"] == "room-1"


def test_json_error_uses_stderr_and_authorization_exit_code(monkeypatch):
    def fail(*args, **kwargs):
        raise AuthenticationError(
            status_code=401,
            detail="API Key 无效",
            path="/api/v1/admin/overview",
        )

    monkeypatch.setattr(query_commands, "create_client", fail)
    result = runner.invoke(app, ["overview", "--json"])

    assert result.exit_code == 3
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["exit_code"] == 3
    assert payload["error"]["status_code"] == 401


def test_api_key_is_not_printed_on_configuration_error(tmp_path, monkeypatch):
    secret = "do-not-print-this-key"
    monkeypatch.setenv("MEDIAFLEET_API_KEY", secret)
    monkeypatch.delenv("MEDIAFLEET_URL", raising=False)
    monkeypatch.delenv("MEDIAFLEET_CONTEXT", raising=False)
    monkeypatch.setenv("MEDIAFLEET_CONFIG_FILE", str(tmp_path / "missing.json"))

    result = runner.invoke(app, ["overview", "--json"])

    assert result.exit_code == 2
    assert secret not in result.output


def test_context_and_auth_workflow(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setenv("MEDIAFLEET_CONFIG_FILE", str(config_file))
    stored = {}
    monkeypatch.setattr(
        auth_commands,
        "set_api_key",
        lambda context, api_key: stored.update({context: api_key.strip()}),
    )

    set_result = runner.invoke(
        app,
        ["context", "set", "dev", "--url", "https://mediafleet.example.com", "--json"],
    )
    login_result = runner.invoke(
        app,
        ["auth", "login", "--api-key-stdin", "--json"],
        input="secret-from-stdin\n",
    )
    list_result = runner.invoke(app, ["context", "list", "--json"])

    assert set_result.exit_code == 0, set_result.output
    assert login_result.exit_code == 0, login_result.output
    assert stored == {"dev": "secret-from-stdin"}
    assert "secret-from-stdin" not in config_file.read_text(encoding="utf-8")
    assert parse_stdout(list_result)["current_context"] == "dev"


def test_auth_login_without_explicit_stdin_does_not_read_input(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAFLEET_CONFIG_FILE", str(tmp_path / "config.json"))
    runner.invoke(
        app,
        ["context", "set", "dev", "--url", "https://mediafleet.example.com"],
    )

    result = runner.invoke(app, ["auth", "login", "--json"])

    assert result.exit_code == 2
    assert json.loads(result.stderr)["error"]["type"] == "configuration_error"
