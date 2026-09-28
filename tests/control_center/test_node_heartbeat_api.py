"""调用中心节点心跳 API 测试。"""

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.application import (
    MediaNodeHeartbeatService,
    MediaNodeSelectionService,
    TaskDispatchService,
)
from media_platform.application.ports import PublishReceipt
from media_platform.infrastructure.database.models import MediaNodeModel
from services.control_center.main import create_control_center_app


class NoopPublisher:
    """复用调用中心运行时结构，测试不连接 RabbitMQ。"""

    def publish(self, message):
        return PublishReceipt(message_id=message.message_id)


class FakeRuntime:
    """只提供 API 依赖所需的服务对象和内部密钥。"""

    def __init__(self, factory):
        self.task_service = TaskDispatchService(factory, NoopPublisher())
        self.node_heartbeat_service = MediaNodeHeartbeatService(factory)
        self.node_selection_service = MediaNodeSelectionService(factory)
        self.api_key = "test-api-key"
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True


def _runtime(tmp_path):
    database_file = (tmp_path / "node-heartbeat-api.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaNodeModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory, FakeRuntime(factory)


def _payload():
    return {
        "node_code": "media-worker-01",
        "node_name": "媒体处理节点01",
        "node_type": "WORKER",
        "status": "ONLINE",
        "agent_url": "http://127.0.0.1:8009",
        "weight": 100,
        "capabilities": ["video.cover.extract", "speech.offline.recognize"],
        "capacity": {
            "worker_prefetch": 1,
            "consumer_enabled": True,
            "processing_tasks": 1,
            "media_recog_concurrency": 2,
        },
        "reported_at": "2026-07-27T10:30:00",
        "updated_by": "media-worker",
    }


def test_node_heartbeat_api_upserts_node_state(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            headers = {"X-API-Key": runtime.api_key}
            first = client.post(
                "/api/v1/nodes/heartbeat",
                json=_payload(),
                headers=headers,
            )
            second_payload = _payload()
            second_payload["status"] = "DRAINING"
            second = client.post(
                "/api/v1/nodes/heartbeat",
                json=second_payload,
                headers=headers,
            )

        assert first.status_code == 200
        assert first.json()["created"] is True
        assert second.status_code == 200
        assert second.json()["created"] is False
        assert second.json()["node_id"] == first.json()["node_id"]

        with factory() as session:
            node = session.scalar(sa.select(MediaNodeModel))

        assert node.node_code == "media-worker-01"
        assert node.node_type == "WORKER"
        assert node.status == "DRAINING"
        assert node.capabilities == [
            "video.cover.extract",
            "speech.offline.recognize",
        ]
        assert node.capacity_config["media_recog_concurrency"] == 2
    finally:
        engine.dispose()


def test_node_heartbeat_api_requires_internal_api_key(tmp_path):
    engine, _, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/nodes/heartbeat", json=_payload())
        assert response.status_code == 401
    finally:
        engine.dispose()


def test_node_heartbeat_api_rejects_same_code_with_different_live_agent(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            headers = {"X-API-Key": runtime.api_key}
            first = client.post(
                "/api/v1/nodes/heartbeat",
                json=_payload(),
                headers=headers,
            )
            conflict_payload = _payload()
            conflict_payload["agent_url"] = "http://127.0.0.1:8019"
            conflict = client.post(
                "/api/v1/nodes/heartbeat",
                json=conflict_payload,
                headers=headers,
            )

        assert first.status_code == 200
        assert conflict.status_code == 409
        assert "节点编号已被其他健康节点使用" in conflict.text
    finally:
        engine.dispose()


def test_node_heartbeat_api_rejects_school_code_field(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            payload = _payload()
            payload["school_code"] = "SCHOOL-001"
            response = client.post(
                "/api/v1/nodes/heartbeat",
                json=payload,
                headers={"X-API-Key": runtime.api_key},
            )

        assert response.status_code == 422
    finally:
        engine.dispose()


def test_node_heartbeat_api_does_not_write_school_code(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/nodes/heartbeat",
                json=_payload(),
                headers={"X-API-Key": runtime.api_key},
            )

        assert response.status_code == 200
        with factory() as session:
            node = session.get(MediaNodeModel, response.json()["node_id"])
            assert node is not None
            assert not hasattr(node, "school_code")
    finally:
        engine.dispose()


def test_node_heartbeat_api_openapi_exposes_examples(tmp_path):
    engine, _, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        schema = app.openapi()
        operation = schema["paths"]["/api/v1/nodes/heartbeat"]["post"]
        request_schema = operation["requestBody"]["content"]["application/json"][
            "schema"
        ]
        schema_name = request_schema["$ref"].rsplit("/", maxsplit=1)[-1]
        heartbeat_schema = schema["components"]["schemas"][schema_name]

        assert operation["tags"] == ["media-nodes"]
        assert operation["security"] == [{"APIKeyHeader": []}]
        assert heartbeat_schema["examples"][0]["node_type"] == "RECORDER"
        assert heartbeat_schema["examples"][1]["node_type"] == "WORKER"
    finally:
        engine.dispose()
