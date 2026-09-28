"""调用中心录制服务器注册和运维状态 API 测试。"""

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.application import MediaNodeHeartbeatService
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    RecordingServerModel,
)
from services.control_center.application.recording_server_service import (
    RecordingServerService,
)
from services.control_center.main import create_control_center_app


class FakeRuntime:
    def __init__(self, factory):
        self.node_heartbeat_service = MediaNodeHeartbeatService(factory)
        self.recording_server_service = RecordingServerService(factory)
        self.api_key = "test-api-key"

    async def start(self):
        return None

    async def close(self):
        return None


def _runtime(tmp_path):
    database_file = (tmp_path / "recording-server-api.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaNodeModel.__table__.create(engine)
    RecordingServerModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory, FakeRuntime(factory)


def _recorder_heartbeat():
    return {
        "node_code": "recorder-01",
        "node_name": "录制节点01",
        "node_type": "RECORDER",
        "server_code": "recording-server-01",
        "server_name": "录制服务器01",
        "agent_url": "http://127.0.0.1:8010",
        "zlm_api_url": "http://127.0.0.1:8080",
        "zlm_server_id": "zlm-01",
        "play_host": "zlm.example.com",
        "play_port": "443",
        "play_protocol": "https",
        "rtmp_port": "1935",
        "rtsp_port": "554",
        "record_root": "D:/record",
        "capabilities": ["record.start", "record.stop"],
        "capacity": {
            "current_recordings": 0,
            "max_recordings": 100,
            "max_bindings": 300,
        },
        "readiness": "READY",
        "readiness_details": {
            "zlm_api_ready": True,
            "record_root_ready": True,
            "errors": [],
        },
        "updated_by": "recorder-node",
    }


def test_recorder_heartbeat_registers_server_and_status_survives_heartbeat(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    headers = {"X-API-Key": runtime.api_key}
    try:
        with TestClient(app) as client:
            heartbeat = client.post(
                "/api/v1/nodes/heartbeat",
                json=_recorder_heartbeat(),
                headers=headers,
            )
            draining = client.patch(
                "/api/v1/recording-servers/recording-server-01/status",
                json={"status": "DRAINING", "updated_by": "operator"},
                headers=headers,
            )
            repeated_heartbeat = client.post(
                "/api/v1/nodes/heartbeat",
                json=_recorder_heartbeat(),
                headers=headers,
            )
            servers = client.get("/api/v1/recording-servers", headers=headers)

        assert heartbeat.status_code == 200
        assert heartbeat.json()["recording_server_id"]
        assert draining.status_code == 200
        assert repeated_heartbeat.status_code == 200
        assert servers.json()[0]["status"] == "DRAINING"
        assert servers.json()[0]["max_bindings"] == 300
        assert servers.json()[0]["occupied_bindings"] == 0
        assert servers.json()[0]["play_host"] == "zlm.example.com"
        assert servers.json()[0]["rtmp_port"] == "1935"
        assert servers.json()[0]["rtsp_port"] == "554"
    finally:
        engine.dispose()


def test_recorder_heartbeat_rejects_zlm_identity_rebinding(tmp_path):
    engine, _, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    headers = {"X-API-Key": runtime.api_key}
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/v1/nodes/heartbeat",
                json=_recorder_heartbeat(),
                headers=headers,
            )
            conflict_payload = _recorder_heartbeat() | {
                "server_code": "recording-server-02"
            }
            conflict = client.post(
                "/api/v1/nodes/heartbeat",
                json=conflict_payload,
                headers=headers,
            )

        assert first.status_code == 200
        assert conflict.status_code == 409
        assert "已绑定其他录制服务器编号" in conflict.text
    finally:
        engine.dispose()


def test_recorder_heartbeat_requires_positive_max_recordings(tmp_path):
    engine, _, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    payload = _recorder_heartbeat()
    payload["capacity"] = {"current_recordings": 0, "max_recordings": 0}
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/nodes/heartbeat",
                json=payload,
                headers={"X-API-Key": runtime.api_key},
            )

        assert response.status_code == 422
        assert "max_recordings" in response.text
    finally:
        engine.dispose()
