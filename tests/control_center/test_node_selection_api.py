"""调用中心节点查询与选择 API 测试。"""

from datetime import datetime

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.application import (
    MediaNodeHeartbeatService,
    MediaNodeSelectionService,
    TaskDispatchService,
)
from media_platform.application.ports import PublishReceipt
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
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
        self.api_key = "test-api-key"

    async def start(self):
        return None

    async def close(self):
        return None


def _runtime(tmp_path):
    database_file = (tmp_path / "node-selection-api.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaNodeModel.__table__.create(engine)
    RecordingServerModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory, FakeRuntime(factory)


def _add_recorder(factory):
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
                    zlm_api_url="http://127.0.0.1:8080",
                    zlm_server_id="zlm-1",
                    record_root="D:/record",
                    weight=100,
                    capabilities=["record.start", "record.stop"],
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
                    max_recordings=10,
                    created_by="test",
                    updated_by="test",
                )]
            )


def test_list_node_candidates_and_select_best_node(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _add_recorder(factory)
    app = create_control_center_app(lambda: runtime)
    headers = {"X-API-Key": runtime.api_key}
    try:
        with TestClient(app) as client:
            candidates = client.get(
                "/api/v1/nodes/candidates",
                params={
                    "node_type": "RECORDER",
                    "capability": "record.start",
                },
                headers=headers,
            )
            selected = client.post(
                "/api/v1/nodes/select",
                json={
                    "node_type": "RECORDER",
                    "capability": "record.start",
                    "heartbeat_timeout_seconds": 90,
                },
                headers=headers,
            )

        assert candidates.status_code == 200
        assert candidates.json()[0]["node_id"] == "recorder-1"
        assert candidates.json()[0]["zlm_api_url"] == "http://127.0.0.1:8080"
        assert selected.status_code == 200
        assert selected.json()["selected"]["node_id"] == "recorder-1"
        assert selected.json()["candidates"][0]["score"] > 0
    finally:
        engine.dispose()


def test_node_selection_api_requires_internal_api_key(tmp_path):
    engine, _, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/nodes/candidates",
                params={"node_type": "RECORDER"},
            )
        assert response.status_code == 401
    finally:
        engine.dispose()
