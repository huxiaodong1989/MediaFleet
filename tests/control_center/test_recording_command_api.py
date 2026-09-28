"""调用中心录制命令 API 测试。"""

from dataclasses import dataclass

from fastapi.testclient import TestClient

from media_platform.application import RecorderCommandDispatchService
from media_platform.application.ports import PublishReceipt
from media_platform.contracts.command import RecorderCommandMessage
from media_platform.domain.node import MediaNodeStatus, MediaNodeType, MediaNodeView
from media_platform.domain.stream import (
    MediaStreamBindingResult,
    StreamBindingStatus,
    StreamMode,
    StreamResourceType,
)
from services.control_center.main import create_control_center_app
from services.control_center.application.recording_task_state_service import (
    RecordingReservationCapacityExceededError,
)


class RecordingCommandPublisher:
    """记录 API 发布的录制命令，避免测试连接 RabbitMQ。"""

    def __init__(self):
        self.messages: list[RecorderCommandMessage] = []

    def publish(self, message: RecorderCommandMessage) -> PublishReceipt:
        self.messages.append(message)
        return PublishReceipt(message_id=message.message_id)


class FakeStreamBindingService:
    """按 app + stream_id 返回预设绑定。"""

    def __init__(self, binding: MediaStreamBindingResult | None):
        self.binding = binding
        self.lookup_calls: list[tuple[str, str]] = []

    def get_active_by_app_stream(
        self,
        *,
        app: str,
        stream_id: str,
    ) -> MediaStreamBindingResult | None:
        self.lookup_calls.append((app, stream_id))
        return self.binding


class FakeRecordingTaskStateService:
    """模拟 MySQL 中的录制任务上下文。"""

    def __init__(self, start_error: Exception | None = None):
        self.contexts = {}
        self.start_commands = []
        self.stop_commands = []
        self.start_error = start_error
        self.dispatch_failures = []
        self.command_dispatch_failures = []
        self.published_commands = []

    def save_start_command(self, **kwargs):
        if self.start_error is not None:
            raise self.start_error
        self.start_commands.append(kwargs)
        self.contexts[kwargs["task_id"]] = type(
            "Context",
            (),
            {
                "task_id": kwargs["task_id"],
                "app": kwargs["app"],
                "stream_id": kwargs["stream_id"],
                "target_node_id": kwargs["target_node_id"],
                "binding_id": kwargs["binding_id"],
                "params": dict(kwargs["params"]),
                "callback_url": kwargs["callback_url"],
            },
        )()

    def get_context(self, task_id):
        return self.contexts.get(task_id)

    def save_stop_command(self, **kwargs):
        self.stop_commands.append(kwargs)

    def mark_start_dispatch_failed(self, **kwargs):
        self.dispatch_failures.append(kwargs)

    def mark_command_dispatch_failed(self, **kwargs):
        self.command_dispatch_failures.append(kwargs)
        return True

    def mark_command_published(self, **kwargs):
        self.published_commands.append(kwargs)
        return True


@dataclass
class FakeRuntime:
    """提供调用中心 API 依赖的最小运行时。"""

    stream_binding_service: FakeStreamBindingService
    recorder_command_service: RecorderCommandDispatchService
    recording_task_state_service: FakeRecordingTaskStateService
    api_key: str = "test-api-key"

    async def start(self):
        return None

    async def close(self):
        return None


def _binding(
    node_code: str = "recorder-1",
    *,
    node_id: str = "db-node-primary-key",
) -> MediaStreamBindingResult:
    return MediaStreamBindingResult(
        binding_id="binding-1",
        created=False,
        school_code="SCHOOL-001",
        resource_type=StreamResourceType.CAMERA,
        space_id="classroom-001",
        node_id=node_id,
        node=MediaNodeView(
            node_id=node_id,
            node_code=node_code,
            node_name=node_code,
            node_type=MediaNodeType.RECORDER,
            status=MediaNodeStatus.ONLINE,
        ),
        app="live",
        stream_id="rtc-camera-stream-001",
        stream_name="一号教室摄像头",
        stream_mode=StreamMode.PULL,
        status=StreamBindingStatus.ACTIVE,
        binding_version=0,
    )


def _runtime(binding: MediaStreamBindingResult | None = None):
    publisher = RecordingCommandPublisher()
    runtime = FakeRuntime(
        stream_binding_service=FakeStreamBindingService(binding),
        recorder_command_service=RecorderCommandDispatchService(publisher),
        recording_task_state_service=FakeRecordingTaskStateService(),
    )
    return runtime, publisher


def _start_payload():
    return {
        "task_id": "rtc-record-plan-001",
        "app": "live",
        "stream_id": "rtc-camera-stream-001",
        "start_time": "2026-07-27 14:30:00.000",
        "end_time": "2026-07-27 15:30:00.000",
        "output_format": "mp4",
        "callback_url": "https://callback.invalid/record",
        "extra_params": {
            "business_id": "lesson-001",
            "record_seconds": 3600,
            "extract_cover": True,
            "cover_strategy": "timestamp",
            "cover_info": {"timestamp": 60},
        },
    }


def _stop_payload():
    return {
        "task_id": "rtc-record-plan-001",
    }


def test_start_recording_command_routes_to_bound_recorder_node():
    runtime, publisher = _runtime(
        _binding("recorder-bound", node_id="db-node-primary-key")
    )
    app = create_control_center_app(lambda: runtime)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recordings/start",
            json=_start_payload(),
            headers={"X-API-Key": runtime.api_key},
        )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["command"] == "record.start"
    assert response.json()["target_node_id"] == "recorder-bound"
    assert response.json()["binding_id"] == "binding-1"
    assert runtime.stream_binding_service.lookup_calls == [
        ("live", "rtc-camera-stream-001")
    ]
    message = publisher.messages[0]
    assert message.command == "record.start"
    assert message.task_id == "rtc-record-plan-001"
    assert message.target_node_id == "recorder-bound"
    assert message.params["app"] == "live"
    assert message.params["stream_id"] == "rtc-camera-stream-001"
    assert message.params["task_id"] == "rtc-record-plan-001"
    assert message.params["start_time"] == "2026-07-27 14:30:00.000"
    assert message.params["end_time"] == "2026-07-27 15:30:00.000"
    assert message.params["output_format"] == "mp4"
    assert message.params["callback_url"] == "https://callback.invalid/record"
    assert message.params["extract_cover"] is True
    assert message.params["cover_strategy"] == "timestamp"
    assert message.params["cover_info"] == {"timestamp": 60}
    assert runtime.recording_task_state_service.start_commands[0]["task_id"] == (
        "rtc-record-plan-001"
    )
    assert runtime.recording_task_state_service.start_commands[0][
        "target_node_id"
    ] == "recorder-bound"
    assert runtime.recording_task_state_service.start_commands[0][
        "target_node_id"
    ] != "db-node-primary-key"
    assert message.idempotency_key == (
        "record.start:live:rtc-camera-stream-001:rtc-record-plan-001"
    )
    assert message.message_id == "rtc-record-plan-001:record.start"


def test_start_recording_accepts_minimal_rtc_payload_without_extra_keys():
    runtime, publisher = _runtime(_binding())
    app = create_control_center_app(lambda: runtime)
    payload = {
        "app": "live",
        "stream_id": "rtc-camera-stream-001",
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recordings/start",
            json=payload,
            headers={"X-API-Key": runtime.api_key},
        )

    assert response.status_code == 200
    assert response.json()["task_id"] == "record-live-rtc-camera-stream-001"
    message = publisher.messages[0]
    assert message.task_id == "record-live-rtc-camera-stream-001"
    assert message.idempotency_key == (
        "record.start:live:rtc-camera-stream-001:record-live-rtc-camera-stream-001"
    )


def test_stop_recording_command_routes_to_bound_recorder_node():
    runtime, publisher = _runtime(_binding("recorder-bound"))
    runtime.recording_task_state_service.save_start_command(
        task_id="rtc-record-plan-001",
        app="live",
        stream_id="rtc-camera-stream-001",
        target_node_id="recorder-bound",
        binding_id="binding-1",
        params={
            "task_id": "rtc-record-plan-001",
            "app": "live",
            "stream_id": "rtc-camera-stream-001",
            "target_node_id": "recorder-bound",
            "binding_id": "binding-1",
        },
        callback_url="https://callback.invalid/record",
        message_id="rtc-record-plan-001:record.start",
    )
    app = create_control_center_app(lambda: runtime)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recordings/stop",
            json=_stop_payload(),
            headers={"X-API-Key": runtime.api_key},
        )

    assert response.status_code == 200
    assert response.json()["command"] == "record.stop"
    assert response.json()["target_node_id"] == "recorder-bound"
    message = publisher.messages[0]
    assert message.command == "record.stop"
    assert message.task_id == "rtc-record-plan-001"
    assert message.target_node_id == "recorder-bound"
    assert message.params["delete_from_memory"] is False
    assert message.params["task_id"] == "rtc-record-plan-001"
    assert message.params["app"] == "live"
    assert message.params["stream_id"] == "rtc-camera-stream-001"
    assert message.params["reason"] == "manual_stop"
    assert message.idempotency_key == "record.stop:rtc-record-plan-001"
    assert message.message_id == "rtc-record-plan-001:record.stop"
    assert runtime.stream_binding_service.lookup_calls == []
    assert runtime.recording_task_state_service.stop_commands[0]["task_id"] == (
        "rtc-record-plan-001"
    )


def test_stop_recording_accepts_task_id_only_without_stream_fields():
    runtime, publisher = _runtime(_binding("recorder-bound"))
    runtime.recording_task_state_service.save_start_command(
        task_id="record-live-rtc-camera-stream-001",
        app="live",
        stream_id="rtc-camera-stream-001",
        target_node_id="recorder-bound",
        binding_id="binding-1",
        params={
            "task_id": "record-live-rtc-camera-stream-001",
            "app": "live",
            "stream_id": "rtc-camera-stream-001",
            "target_node_id": "recorder-bound",
            "binding_id": "binding-1",
        },
        callback_url=None,
        message_id="record-live-rtc-camera-stream-001:record.start",
    )
    app = create_control_center_app(lambda: runtime)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recordings/stop",
            json={
                "task_id": "record-live-rtc-camera-stream-001",
            },
            headers={"X-API-Key": runtime.api_key},
        )

    assert response.status_code == 200
    assert response.json()["task_id"] == "record-live-rtc-camera-stream-001"
    message = publisher.messages[0]
    assert message.command == "record.stop"
    assert message.task_id == "record-live-rtc-camera-stream-001"
    assert message.params["app"] == "live"
    assert message.params["stream_id"] == "rtc-camera-stream-001"


def test_stop_recording_rejects_unknown_task_id_without_publish():
    runtime, publisher = _runtime(_binding("recorder-bound"))
    app = create_control_center_app(lambda: runtime)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recordings/stop",
            json={"task_id": "missing-task"},
            headers={"X-API-Key": runtime.api_key},
        )

    assert response.status_code == 404
    assert "未找到 task_id 对应的录制任务上下文" in response.text
    assert publisher.messages == []


def test_recording_command_rejects_missing_stream_binding_without_publish():
    runtime, publisher = _runtime(None)
    app = create_control_center_app(lambda: runtime)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recordings/start",
            json=_start_payload(),
            headers={"X-API-Key": runtime.api_key},
        )

    assert response.status_code == 409
    assert "未找到 app + stream_id 对应的有效流绑定" in response.text
    assert publisher.messages == []


def test_recording_command_returns_conflict_when_bound_server_capacity_is_full():
    runtime, publisher = _runtime(_binding())
    runtime.recording_task_state_service = FakeRecordingTaskStateService(
        RecordingReservationCapacityExceededError("目标时间段录制容量已满")
    )
    app = create_control_center_app(lambda: runtime)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recordings/start",
            json=_start_payload(),
            headers={"X-API-Key": runtime.api_key},
        )

    assert response.status_code == 409
    assert "目标时间段录制容量已满" in response.text
    assert publisher.messages == []


def test_recording_command_requires_internal_api_key():
    runtime, publisher = _runtime(_binding())
    app = create_control_center_app(lambda: runtime)

    with TestClient(app) as client:
        response = client.post("/api/v1/recordings/start", json=_start_payload())

    assert response.status_code == 401
    assert publisher.messages == []


def test_recording_command_openapi_exposes_swagger_examples():
    runtime, _ = _runtime(_binding())
    app = create_control_center_app(lambda: runtime)

    schema = app.openapi()
    operation = schema["paths"]["/api/v1/recordings/start"]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    schema_name = request_schema["$ref"].rsplit("/", maxsplit=1)[-1]
    start_schema = schema["components"]["schemas"][schema_name]

    assert operation["tags"] == ["recording-commands"]
    assert operation["security"] == [{"APIKeyHeader": []}]
    assert start_schema["examples"][0]["app"] == "classCard"
    assert start_schema["examples"][0]["stream_id"] == "1912078168135675904"
    assert "task_id" in start_schema["properties"]
    assert "request_id" not in start_schema["properties"]
    assert start_schema["examples"][0]["task_id"] == "rtc-record-plan-20260728-0001"
    assert "start_time" in start_schema["examples"][0]
    assert "end_time" in start_schema["examples"][0]
    assert start_schema["examples"][0]["extra_params"]["extract_cover"] is True
    assert start_schema["examples"][0]["extra_params"]["cover_strategy"] == "custom"
    stop_operation = schema["paths"]["/api/v1/recordings/stop"]["post"]
    stop_ref = stop_operation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].rsplit("/", maxsplit=1)[-1]
    stop_schema = schema["components"]["schemas"][stop_ref]
    assert stop_schema["examples"][0] == {
        "task_id": "rtc-open-record-20260728-0002"
    }
    assert set(stop_schema["properties"]) == {"task_id"}
