"""调用中心 RTC 流绑定 API 测试。"""

from datetime import datetime

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.application import (
    MediaNodeHeartbeatService,
    MediaNodeSelectionService,
    MediaStreamBindingService,
    TaskDispatchService,
)
from media_platform.application.ports import PublishReceipt
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    MediaStreamBindingModel,
    RecordingServerModel,
)
from services.control_center.main import create_control_center_app


class NoopPublisher:
    """API 测试不连接 RabbitMQ。"""

    def publish(self, message):
        return PublishReceipt(message_id=message.message_id)


class FakeRuntime:
    """提供调用中心 API 依赖所需的应用服务。"""

    def __init__(self, factory):
        self.task_service = TaskDispatchService(factory, NoopPublisher())
        self.node_heartbeat_service = MediaNodeHeartbeatService(factory)
        self.node_selection_service = MediaNodeSelectionService(factory)
        self.stream_binding_service = MediaStreamBindingService(factory)
        self.api_key = "test-api-key"

    async def start(self):
        return None

    async def close(self):
        return None


def _runtime(tmp_path):
    database_file = (tmp_path / "stream-binding-api.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaNodeModel.__table__.create(engine)
    RecordingServerModel.__table__.create(engine)
    MediaStreamBindingModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory, FakeRuntime(factory)


def _add_recorder(
    factory,
    *,
    zlm_api_url="http://127.0.0.1:8080",
    include_playback_config=True,
):
    with factory() as session:
        with session.begin():
            session.add_all(
                [MediaNodeModel(
                    id="recorder-1",
                    node_code="recorder-1",
                    node_name="录制节点1",
                    node_type="RECORDER",
                    status="ONLINE",
                    agent_url="http://127.0.0.1:8010",
                    zlm_api_url=zlm_api_url,
                    zlm_server_id="zlm-1",
                    weight=100,
                    capabilities=["record.start"],
                    capacity_config={
                        "current_recordings": 0,
                        "max_recordings": 10,
                        "disk_usage_percent": 50,
                        "postprocess_current_processing": 0,
                        "postprocess_max_workers": 2,
                    },
                    readiness_status="READY",
                    last_heartbeat_at=datetime.now(),
                    created_by="test",
                    updated_by="test",
                ), RecordingServerModel(
                    id="server-1",
                    server_code="recording-server-1",
                    server_name="录制服务器1",
                    status="ACTIVE",
                    recorder_node_id="recorder-1",
                    zlm_server_id="zlm-1",
                    play_host=(
                        "zlm.example.com" if include_playback_config else None
                    ),
                    play_port="443" if include_playback_config else None,
                    play_protocol="https" if include_playback_config else None,
                    rtmp_port="1935" if include_playback_config else None,
                    rtsp_port="554" if include_playback_config else None,
                    max_recordings=10,
                    max_bindings=10,
                    occupied_bindings=0,
                    created_by="test",
                    updated_by="test",
                )]
            )


def _payload():
    return {
        "school_code": "88888",
        "resource_type": "CAMERA",
        "space_id": "10001",
        "stream_id": "2079198571392",
        "stream_name": "一号教室摄像头",
        "app": "proxys",
    }


def test_stream_binding_api_creates_and_reuses_binding(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _add_recorder(factory)
    app = create_control_center_app(lambda: runtime)
    headers = {"X-API-Key": runtime.api_key}
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/v1/rtc/stream-bindings",
                json=_payload(),
                headers=headers,
            )
            second = client.post(
                "/api/v1/rtc/stream-bindings",
                json=_payload(),
                headers=headers,
            )

        assert first.status_code == 200
        endpoint = first.json()
        assert endpoint["api_url"] == "http://127.0.0.1:8080"
        assert endpoint["server_id"] == "zlm-1"
        assert endpoint["stream_id"] == "2079198571392"
        assert endpoint["ip"] == "127.0.0.1"
        assert set(endpoint) == {
            "stream_id",
            "api_url",
            "server_id",
            "ip",
        }
        assert "resource_id" not in first.json()
        assert second.status_code == 200
        assert second.json() == first.json()
    finally:
        engine.dispose()


def test_stream_binding_api_does_not_require_playback_config(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _add_recorder(factory, include_playback_config=False)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/rtc/stream-bindings",
                json=_payload(),
                headers={"X-API-Key": runtime.api_key},
            )

        assert response.status_code == 200
        assert response.json() == {
            "stream_id": "2079198571392",
            "api_url": "http://127.0.0.1:8080",
            "server_id": "zlm-1",
            "ip": "127.0.0.1",
        }
    finally:
        engine.dispose()


def test_stream_binding_api_rejects_api_url_without_host(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _add_recorder(factory, zlm_api_url="not-a-url")
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/rtc/stream-bindings",
                json=_payload(),
                headers={"X-API-Key": runtime.api_key},
            )

        assert response.status_code == 503
        assert response.json()["detail"] == "绑定节点 api_url 无法解析流媒体 IP"
    finally:
        engine.dispose()


def test_stream_binding_api_requires_internal_api_key(tmp_path):
    engine, _, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/rtc/stream-bindings",
                json=_payload(),
            )
        assert response.status_code == 401
    finally:
        engine.dispose()


def test_stream_binding_api_allows_another_stream_in_same_space(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _add_recorder(factory)
    app = create_control_center_app(lambda: runtime)
    headers = {"X-API-Key": runtime.api_key}
    changed_payload = _payload() | {"stream_id": "2079198571393"}
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/rtc/stream-bindings",
                json=_payload(),
                headers=headers,
            )
            second = client.post(
                "/api/v1/rtc/stream-bindings",
                json=changed_payload,
                headers=headers,
            )

        assert created.status_code == 200
        assert second.status_code == 200
        assert second.json()["stream_id"] != created.json()["stream_id"]
        assert second.json()["server_id"] == created.json()["server_id"]
    finally:
        engine.dispose()


def test_stream_binding_api_rejects_removed_idempotency_fields(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _add_recorder(factory)
    app = create_control_center_app(lambda: runtime)
    payload = _payload() | {"request_id": "request-1", "idempotency_key": "idem-1"}
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/rtc/stream-bindings",
                json=payload,
                headers={"X-API-Key": runtime.api_key},
            )
        assert response.status_code == 422
    finally:
        engine.dispose()


def test_stream_binding_api_rejects_removed_resource_id(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _add_recorder(factory)
    app = create_control_center_app(lambda: runtime)
    payload = _payload() | {"resource_id": "camera-001"}
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/rtc/stream-bindings",
                json=payload,
                headers={"X-API-Key": runtime.api_key},
            )
        assert response.status_code == 422
    finally:
        engine.dispose()


def test_stream_binding_api_requires_stream_and_app(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _add_recorder(factory)
    app = create_control_center_app(lambda: runtime)
    headers = {"X-API-Key": runtime.api_key}
    try:
        with TestClient(app) as client:
            for field in ("stream_id", "app"):
                payload = _payload()
                payload.pop(field)
                response = client.post(
                    "/api/v1/rtc/stream-bindings",
                    json=payload,
                    headers=headers,
                )
                assert response.status_code == 422
    finally:
        engine.dispose()


def test_stream_binding_api_allows_missing_or_blank_optional_metadata(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _add_recorder(factory)
    app = create_control_center_app(lambda: runtime)
    headers = {"X-API-Key": runtime.api_key}
    missing_metadata = _payload() | {
        "resource_type": "DESKTOP",
        "app": "live",
    }
    missing_metadata.pop("space_id")
    missing_metadata.pop("stream_name")
    blank_metadata = _payload() | {
        "stream_id": "2079198571394",
        "space_id": "  ",
        "stream_name": "  ",
    }
    try:
        with TestClient(app) as client:
            missing_response = client.post(
                "/api/v1/rtc/stream-bindings",
                json=missing_metadata,
                headers=headers,
            )
            blank_response = client.post(
                "/api/v1/rtc/stream-bindings",
                json=blank_metadata,
                headers=headers,
            )

        assert missing_response.status_code == 200
        assert blank_response.status_code == 200
        with factory() as session:
            bindings = session.scalars(
                sa.select(MediaStreamBindingModel).order_by(
                    MediaStreamBindingModel.stream_id
                )
            ).all()
        assert len(bindings) == 2
        assert all(binding.space_id is None for binding in bindings)
        assert all(binding.stream_name is None for binding in bindings)
    finally:
        engine.dispose()


def test_stream_binding_api_rejects_changed_space_for_same_stream(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _add_recorder(factory)
    app = create_control_center_app(lambda: runtime)
    headers = {"X-API-Key": runtime.api_key}
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/rtc/stream-bindings",
                json=_payload(),
                headers=headers,
            )
            conflict = client.post(
                "/api/v1/rtc/stream-bindings",
                json=_payload() | {"space_id": "10002"},
                headers=headers,
            )

        assert created.status_code == 200
        assert conflict.status_code == 409
    finally:
        engine.dispose()


def test_stream_binding_api_rejects_non_numeric_school_or_space(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _add_recorder(factory)
    app = create_control_center_app(lambda: runtime)
    headers = {"X-API-Key": runtime.api_key}
    try:
        with TestClient(app) as client:
            bad_school = client.post(
                "/api/v1/rtc/stream-bindings",
                json=_payload() | {"school_code": "school-88888"},
                headers=headers,
            )
            bad_space = client.post(
                "/api/v1/rtc/stream-bindings",
                json=_payload() | {"space_id": "classroom-10001"},
                headers=headers,
            )

        assert bad_school.status_code == 422
        assert bad_space.status_code == 422
    finally:
        engine.dispose()


def test_stream_binding_api_releases_capacity_idempotently(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _add_recorder(factory)
    app = create_control_center_app(lambda: runtime)
    headers = {"X-API-Key": runtime.api_key}
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/rtc/stream-bindings",
                json=_payload(),
                headers=headers,
            )
            assert created.status_code == 200
            with factory() as session:
                binding_id = session.scalar(
                    sa.select(MediaStreamBindingModel.id)
                )
            released = client.delete(
                f"/api/v1/rtc/stream-bindings/{binding_id}",
                headers=headers,
            )
            released_again = client.delete(
                f"/api/v1/rtc/stream-bindings/{binding_id}",
                headers=headers,
            )

        assert released.status_code == 200
        assert released.json()["stream_id"] == _payload()["stream_id"]
        assert released_again.status_code == 200
        with factory() as session:
            server = session.get(RecordingServerModel, "server-1")
            assert server.occupied_bindings == 0
    finally:
        engine.dispose()
