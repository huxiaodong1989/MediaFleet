"""调用中心任务 API 与生命周期测试。"""

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.application import TaskDispatchService
from media_platform.application.ports import PublishReceipt
from media_platform.infrastructure.database.models import MediaTaskModel
from services.control_center.main import create_control_center_app


class NoopPublisher:
    """API 测试只验证任务落库，不连接 RabbitMQ。"""

    def publish(self, message):
        return PublishReceipt(message_id=message.message_id)


class FakeRuntime:
    """记录 FastAPI lifespan 是否正确启动和关闭运行时。"""

    def __init__(self, task_service):
        self.task_service = task_service
        self.api_key = "test-api-key"
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True


def _runtime(tmp_path):
    database_file = (tmp_path / "control-center-api.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaTaskModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime = FakeRuntime(TaskDispatchService(factory, NoopPublisher()))
    return engine, runtime


def _payload():
    return {
        "school_code": "school-1",
        "task_type": "video.cover.extract",
        "priority": 10,
        "params": {"media_url": "https://example.invalid/video.mp4"},
        "callback_url": "https://rtc.invalid/media/callback",
        "created_by": "rtc-service",
    }


def test_create_task_api_creates_task_and_manages_runtime(tmp_path):
    engine, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            assert runtime.started is True
            headers = {"X-API-Key": runtime.api_key}
            first = client.post("/api/v1/tasks", json=_payload(), headers=headers)
            second = client.post("/api/v1/tasks", json=_payload(), headers=headers)

            assert first.status_code == 200
            assert first.json()["created"] is True
            assert second.status_code == 200
            assert second.json()["created"] is True
            assert second.json()["task_id"] != first.json()["task_id"]
        assert runtime.closed is True
    finally:
        engine.dispose()


def test_create_task_api_accepts_minimal_payload_without_keys(tmp_path):
    engine, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/tasks",
                json=_payload(),
                headers={"X-API-Key": runtime.api_key},
            )
            assert response.status_code == 200
            assert response.json()["created"] is True
    finally:
        engine.dispose()


def test_create_task_api_requires_callback_url(tmp_path):
    engine, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    payload = dict(_payload())
    payload.pop("callback_url")
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/tasks",
                json=payload,
                headers={"X-API-Key": runtime.api_key},
            )
            assert response.status_code == 422
            assert "callback_url" in response.text
    finally:
        engine.dispose()


def test_create_task_api_requires_internal_api_key(tmp_path):
    engine, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/tasks", json=_payload())
            assert response.status_code == 401
    finally:
        engine.dispose()


def test_create_task_api_openapi_exposes_cover_task_example(tmp_path):
    engine, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        schema = app.openapi()
        operation = schema["paths"]["/api/v1/tasks"]["post"]
        request_schema = operation["requestBody"]["content"]["application/json"][
            "schema"
        ]
        task_schema_name = request_schema["$ref"].rsplit("/", maxsplit=1)[-1]
        task_schema = schema["components"]["schemas"][task_schema_name]

        assert operation["tags"] == ["media-tasks"]
        assert operation["security"] == [{"APIKeyHeader": []}]
        assert task_schema["examples"][0]["task_type"] == "video.cover.extract"
        assert task_schema["examples"][0]["callback_url"]
        assert "callback_url" in task_schema["required"]
        assert "routing_key" not in task_schema["examples"][0]
        assert "request_id" not in task_schema["examples"][0]
        assert "idempotency_key" not in task_schema["properties"]
        assert "routing_key" not in task_schema["properties"]
        assert "request_id" not in task_schema["properties"]
    finally:
        engine.dispose()


def test_create_task_api_rejects_removed_idempotency_fields(tmp_path):
    engine, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    payload = _payload() | {"idempotency_key": "idem-1", "routing_key": "x"}
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/tasks",
                json=payload,
                headers={"X-API-Key": runtime.api_key},
            )
            assert response.status_code == 422
    finally:
        engine.dispose()
