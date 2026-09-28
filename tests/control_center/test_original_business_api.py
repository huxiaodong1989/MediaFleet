"""原始 RTC/业务接口保留测试。"""

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.application import (
    RecorderCommandDispatchService,
    TaskDispatchService,
    TaskQueryService,
)
from media_platform.application.ports import PublishReceipt
from media_platform.contracts.command import RecorderCommandMessage
from media_platform.domain.node import MediaNodeStatus, MediaNodeType, MediaNodeView
from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.domain.stream import (
    MediaStreamBindingResult,
    StreamBindingStatus,
    StreamMode,
    StreamResourceType,
)
from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    MediaStreamBindingModel,
    MediaTaskModel,
    RecordingServerModel,
)
from media_platform.common.settings import get_settings
from services.control_center.application.recording_task_state_service import (
    RecordingTaskStateService,
)
from services.control_center.main import create_control_center_app
from services.control_center.api.legacy_schemas.object_detection_dto import (
    ObjectDetectionDetectionRequest,
    ObjectDetectionProcessRequest,
)
from services.control_center.api.legacy_schemas.recogDto import RecogProcessRequest
from services.control_center.api.legacy_schemas.stream import (
    StreamProcessRequest,
    StreamRecordRequest,
)
from services.control_center.api.legacy_schemas.videoDto import VideoProcessRequest


LEGACY_API_KEY = "test-only-api-key"


@pytest.fixture(autouse=True)
def _configure_legacy_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", LEGACY_API_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class NoopTaskPublisher:
    """旧媒体处理 API 测试只验证任务落库，不连接 RabbitMQ。"""

    def publish(self, message):
        return PublishReceipt(message_id=message.message_id)


class RecordingCommandPublisher:
    """记录旧录制 API 发布到 recorder-node 的命令。"""

    def __init__(self):
        self.messages: list[RecorderCommandMessage] = []

    def publish(self, message: RecorderCommandMessage) -> PublishReceipt:
        self.messages.append(message)
        return PublishReceipt(message_id=message.message_id)


class FakeStreamBindingService:
    """按 app + stream_id 返回已经由 RTC 绑定的 ZL/recorder-node。"""

    def get_active_by_app_stream(
        self,
        *,
        app: str,
        stream_id: str,
    ) -> MediaStreamBindingResult | None:
        return MediaStreamBindingResult(
            binding_id="binding-legacy-1",
            created=False,
            school_code="SCHOOL-001",
            resource_type=StreamResourceType.CAMERA,
            space_id="classroom-001",
            node_id="db-node-primary-key",
            node=MediaNodeView(
                node_id="db-node-primary-key",
                node_code="recorder-bound",
                node_name="recorder-bound",
                node_type=MediaNodeType.RECORDER,
                status=MediaNodeStatus.ONLINE,
            ),
            app=app,
            stream_id=stream_id,
            stream_name="一号教室摄像头",
            stream_mode=StreamMode.PULL,
            status=StreamBindingStatus.ACTIVE,
            binding_version=0,
        )


@dataclass
class FakeRuntime:
    """提供调用中心旧路由所需的最小运行时依赖。"""

    task_service: TaskDispatchService
    task_query_service: TaskQueryService
    stream_binding_service: FakeStreamBindingService
    recorder_command_service: RecorderCommandDispatchService
    recording_task_state_service: RecordingTaskStateService
    api_key: str = LEGACY_API_KEY

    async def start(self):
        return None

    async def close(self):
        return None


def _runtime(tmp_path):
    database_file = (tmp_path / "original-business-api.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        with session.begin():
            session.add(
                MediaNodeModel(
                    id="db-node-primary-key",
                    node_code="recorder-bound",
                    node_name="录制节点",
                    node_type="RECORDER",
                    status="ONLINE",
                    readiness_status="READY",
                    capabilities=["record.start", "record.stop"],
                    capacity_config={},
                    created_by="test",
                    updated_by="test",
                )
            )
            session.add(
                RecordingServerModel(
                    id="recording-server-1",
                    server_code="recording-server-1",
                    server_name="录制服务器",
                    status="ACTIVE",
                    recorder_node_id="db-node-primary-key",
                    zlm_server_id="zlm-1",
                    max_recordings=100,
                    max_bindings=300,
                    occupied_bindings=1,
                    created_by="test",
                    updated_by="test",
                )
            )
            session.add(
                MediaStreamBindingModel(
                    id="binding-legacy-1",
                    school_code="SCHOOL-001",
                    resource_type="CAMERA",
                    resource_id="camera-001",
                    node_id="db-node-primary-key",
                    app="live",
                    stream_id="rtc-camera-stream-001",
                    stream_mode="PULL",
                    status="ACTIVE",
                    version=0,
                    created_by="test",
                    updated_by="test",
                )
            )
    command_publisher = RecordingCommandPublisher()
    runtime = FakeRuntime(
        task_service=TaskDispatchService(factory, NoopTaskPublisher()),
        task_query_service=TaskQueryService(factory),
        stream_binding_service=FakeStreamBindingService(),
        recorder_command_service=RecorderCommandDispatchService(command_publisher),
        recording_task_state_service=RecordingTaskStateService(factory),
    )
    return engine, runtime, command_publisher


def test_original_business_routes_are_available_in_control_center(tmp_path):
    """调用中心必须保留原始业务路由，避免 RTC 和业务端大改。"""

    engine, runtime, _ = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        paths = app.openapi()["paths"]

        assert "/api/v1/stream/record" in paths
        assert "/api/v1/stream/record/{task_id}" in paths
        assert "/api/v1/stream/record_cancel/{task_id}" in paths
        assert "/api/v1/stream/mcp/record_cancel/{task_id}" in paths
        assert "/api/v1/stream/process" in paths
        assert "/api/v1/video/process" in paths
        assert "/api/v1/video/process/{task_id}" in paths
        assert "/api/v1/recog/process" in paths
        assert "/api/v1/recog/result" in paths
        assert "/api/v1/object_detection/process" in paths
        assert "/api/v1/object_detection/detection" in paths
        assert "/api/v1/object_detection/result" in paths
    finally:
        engine.dispose()


def test_original_business_request_fields_are_unchanged():
    """原业务请求模型不能新增 RTC/业务方不该关心的内部技术字段。"""

    expected_fields = {
        StreamRecordRequest: {
            "task_id",
            "app",
            "stream_id",
            "start_time",
            "end_time",
            "output_format",
            "callback_url",
            "extra_params",
        },
        StreamProcessRequest: {
            "operations",
            "stream_url",
            "api_endpoint",
            "callback_url",
            "start_time",
            "end_time",
            "chunk_duration",
            "params",
        },
        VideoProcessRequest: {
            "operations",
            "video_url",
            "callback_url",
            "params",
        },
        RecogProcessRequest: {"media_url", "callback_url", "params"},
        ObjectDetectionProcessRequest: {"media_url", "callback_url", "params"},
        ObjectDetectionDetectionRequest: {"img_url", "callback_url", "params"},
    }
    forbidden_fields = {"request_id", "idempotency_key", "routing_key"}

    for model, fields in expected_fields.items():
        actual_fields = set(model.model_fields)
        assert actual_fields == fields
        assert actual_fields.isdisjoint(forbidden_fields)


def test_original_video_process_payload_creates_new_media_task(tmp_path):
    """旧 `/api/v1/video/process` 入参不变，内部写入新任务表。"""

    engine, runtime, _ = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/video/process",
                json={
                    "operations": "video_extract_cover",
                    "video_url": "https://example.invalid/video.mp4",
                    "callback_url": "https://rtc.invalid/media/callback",
                    "params": {
                        "cover_strategy": "timestamp",
                        "cover_info": {"timestamp": 60},
                        "xxm": "SCHOOL-001",
                    },
                },
                headers={"X-API-Key": LEGACY_API_KEY},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending"
        with next(iter([sessionmaker(bind=engine)()])) as session:
            task = session.get(MediaTaskModel, body["task_id"])
            assert task is not None
            assert task.school_code == "SCHOOL-001"
            assert task.task_type == "video_extract_cover"
            assert task.routing_key == "video_extract_cover"
            assert task.params["video_url"] == "https://example.invalid/video.mp4"
            assert task.params["ex_params"]["cover_strategy"] == "timestamp"
    finally:
        engine.dispose()


def test_original_stream_record_and_cancel_use_task_id_only(tmp_path):
    """旧录制创建和停止路径不变，停止只通过 task_id 定位 recorder-node。"""

    engine, runtime, command_publisher = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            start = client.post(
                "/api/v1/stream/record",
                json={
                    "task_id": "rtc-record-plan-legacy-001",
                    "app": "live",
                    "stream_id": "rtc-camera-stream-001",
                    "start_time": "2026-07-28 16:30:00.000",
                    "end_time": "2026-07-28 17:30:00.000",
                    "output_format": "mp4",
                    "callback_url": "https://rtc.invalid/record/callback",
                    "extra_params": {
                        "extract_cover": True,
                        "cover_strategy": "timestamp",
                        "cover_info": {"timestamp": 60},
                    },
                },
                headers={"X-API-Key": LEGACY_API_KEY},
            )
            stop = client.get(
                "/api/v1/stream/record_cancel/rtc-record-plan-legacy-001",
                headers={"X-API-Key": LEGACY_API_KEY},
            )

        assert start.status_code == 200
        assert start.json()["task_id"] == "rtc-record-plan-legacy-001"
        assert start.json()["status"] == "pending"
        assert start.json()["message"] == "录制任务已创建"
        assert stop.status_code == 200
        assert stop.json()["msg"] == "停止命令已发送，录制节点将停止录制并继续后处理"
        assert stop.json()["data"]["stop_requested"] is True
        assert stop.json()["data"]["action"] == "record_stop_dispatched"
        assert command_publisher.messages[0].command == "record.start"
        assert command_publisher.messages[1].command == "record.stop"
        assert command_publisher.messages[1].task_id == "rtc-record-plan-legacy-001"
        assert command_publisher.messages[1].target_node_id == "recorder-bound"
    finally:
        engine.dispose()


def test_original_stream_record_status_reads_media_task_table(tmp_path):
    """旧录制状态查询读取 media_task，不依赖旧 dispatcher 内存状态。"""

    engine, runtime, _ = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    now = datetime(2026, 7, 30, 15, 0, 0)
    with sessionmaker(bind=engine)() as session:
        with session.begin():
            session.add(
                MediaTaskModel(
                    id="rtc-record-plan-status-001",
                    task_type="record.stream",
                    routing_key="record.start",
                    status=TaskStatus.COMPLETED.value,
                    publish_status=PublishStatus.PUBLISHED.value,
                    priority=0,
                    progress=100,
                    params={
                        "app": "live",
                        "stream_id": "rtc-camera-stream-001",
                    },
                    result={
                        "status": "completed",
                        "result_url": "https://cdn.example.com/record/video.mp4",
                        "audio_url": "https://cdn.example.com/audio/audio.mp3",
                        "extra_info": {
                            "stream_id": "rtc-camera-stream-001",
                            "video_file_id": "video-file-1",
                        },
                    },
                    callback_url="https://rtc.invalid/record/callback",
                    executor_node_id="recorder-bound",
                    retry_count=0,
                    max_retries=3,
                    message_id="rtc-record-plan-status-001:record.start",
                    published_at=now,
                    started_at=now,
                    completed_at=now,
                    created_by="test",
                    updated_by="recorder-bound",
                    school_code="LEGACY",
                    created_at=now,
                    updated_at=now,
                )
            )
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/stream/record/rtc-record-plan-status-001",
                headers={"X-API-Key": LEGACY_API_KEY},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["task_id"] == "rtc-record-plan-status-001"
        assert body["data"]["status"] == "completed"
        assert body["data"]["progress"] == 100
        assert body["data"]["result_url"] == "https://cdn.example.com/record/video.mp4"
        assert body["data"]["extra_params"]["audio_url"] == (
            "https://cdn.example.com/audio/audio.mp3"
        )
        assert body["data"]["extra_params"]["stream_id"] == "rtc-camera-stream-001"
    finally:
        engine.dispose()


def test_original_stream_record_status_returns_404_for_missing_task(tmp_path):
    """旧录制状态查询找不到任务时返回 HTTP 404，不再误包装为 500。"""

    engine, runtime, _ = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/stream/record/missing-record-task",
                headers={"X-API-Key": LEGACY_API_KEY},
            )

        assert response.status_code == 404
    finally:
        engine.dispose()
