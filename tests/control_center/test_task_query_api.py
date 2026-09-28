"""调用中心任务状态和产物查询 API 测试。"""

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.application import TaskQueryService
from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaFileModel, MediaTaskModel
from services.control_center.main import create_control_center_app


@dataclass
class FakeRuntime:
    """提供任务查询 API 依赖的最小调用中心运行时。"""

    task_query_service: TaskQueryService
    api_key: str = "test-api-key"

    async def start(self):
        return None

    async def close(self):
        return None


def _runtime(tmp_path):
    database_file = (tmp_path / "task-query-api.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaTaskModel.__table__.create(engine)
    MediaFileModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime = FakeRuntime(TaskQueryService(factory))
    return engine, factory, runtime


def _insert_task(factory, *, task_id: str = "task-001") -> str:
    now = datetime(2026, 7, 27, 10, 0, 0)
    with factory() as session:
        with session.begin():
            session.add(
                MediaTaskModel(
                    id=task_id,
                    request_id="auto-request-001",
                    idempotency_key=None,
                    task_type="video.cover.extract",
                    routing_key="video.cover.extract",
                    status=TaskStatus.COMPLETED.value,
                    publish_status=PublishStatus.PUBLISHED.value,
                    priority=0,
                    progress=100,
                    params={"video_url": "https://example.invalid/sample.mp4"},
                    result={"cover_url": "https://minio.invalid/media/cover.jpg"},
                    error_message=None,
                    callback_url="https://rtc.invalid/media/callback",
                    callback_result={"success": True, "status_code": 200},
                    executor_node_id="worker-001",
                    retry_count=0,
                    max_retries=3,
                    message_id="message-001",
                    published_at=now,
                    started_at=now,
                    completed_at=now,
                    created_by="SWAGGER",
                    updated_by="worker-001",
                    school_code="LOCAL_TEST",
                    created_at=now,
                    updated_at=now,
                )
            )
    return task_id


def _insert_file(factory, *, task_id: str = "task-001", file_id: str = "file-001"):
    now = datetime(2026, 7, 27, 10, 0, 8)
    with factory() as session:
        with session.begin():
            session.add(
                MediaFileModel(
                    id=file_id,
                    task_id=task_id,
                    file_type="COVER",
                    file_name="cover.jpg",
                    file_url="https://minio.invalid/media/cover.jpg",
                    relative_path="covers/2026/07/27/cover.jpg",
                    bucket_name="media",
                    file_size=102400,
                    mime_type="image/jpeg",
                    extra_info={"width": 1280, "height": 720},
                    created_by="worker-001",
                    updated_by="worker-001",
                    school_code="LOCAL_TEST",
                    created_at=now,
                    updated_at=now,
                )
            )


def test_get_task_status_reads_national_standard_task_table(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    task_id = _insert_task(factory)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/tasks/{task_id}",
                headers={"X-API-Key": runtime.api_key},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["task_id"] == task_id
        assert body["school_code"] == "LOCAL_TEST"
        assert body["task_type"] == "video.cover.extract"
        assert body["status"] == "completed"
        assert body["publish_status"] == "PUBLISHED"
        assert body["progress"] == 100
        assert body["params"] == {"video_url": "https://example.invalid/sample.mp4"}
        assert body["result"] == {
            "cover_url": "https://minio.invalid/media/cover.jpg"
        }
        assert body["callback_result"] == {"success": True, "status_code": 200}
        assert body["executor_node_id"] == "worker-001"
        assert body["message_id"] == "message-001"
        assert "request_id" not in body
        assert "idempotency_key" not in body
        assert "routing_key" not in body
    finally:
        engine.dispose()


def test_get_task_status_requires_internal_api_key(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    task_id = _insert_task(factory)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/tasks/{task_id}")

        assert response.status_code == 401
    finally:
        engine.dispose()


def test_get_task_status_returns_404_for_missing_task(tmp_path):
    engine, _, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/tasks/missing-task",
                headers={"X-API-Key": runtime.api_key},
            )

        assert response.status_code == 404
        assert "未找到媒体任务" in response.text
    finally:
        engine.dispose()


def test_list_task_files_reads_national_standard_file_table(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    task_id = _insert_task(factory)
    _insert_file(factory, task_id=task_id)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/tasks/{task_id}/files",
                headers={"X-API-Key": runtime.api_key},
            )

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["file_id"] == "file-001"
        assert body[0]["task_id"] == task_id
        assert body[0]["school_code"] == "LOCAL_TEST"
        assert body[0]["file_type"] == "COVER"
        assert body[0]["file_url"] == "https://minio.invalid/media/cover.jpg"
        assert body[0]["bucket_name"] == "media"
        assert body[0]["extra_info"] == {"width": 1280, "height": 720}
    finally:
        engine.dispose()


def test_list_task_files_returns_empty_list_when_task_has_no_files(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    task_id = _insert_task(factory)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/tasks/{task_id}/files",
                headers={"X-API-Key": runtime.api_key},
            )

        assert response.status_code == 200
        assert response.json() == []
    finally:
        engine.dispose()


def test_list_task_files_returns_404_for_missing_task(tmp_path):
    engine, _, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/tasks/missing-task/files",
                headers={"X-API-Key": runtime.api_key},
            )

        assert response.status_code == 404
    finally:
        engine.dispose()


def test_task_query_openapi_exposes_status_and_files_routes(tmp_path):
    engine, _, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        schema = app.openapi()

        status_operation = schema["paths"]["/api/v1/tasks/{task_id}"]["get"]
        files_operation = schema["paths"]["/api/v1/tasks/{task_id}/files"]["get"]
        assert status_operation["tags"] == ["media-tasks"]
        assert files_operation["tags"] == ["media-tasks"]
        assert status_operation["security"] == [{"APIKeyHeader": []}]
        assert files_operation["security"] == [{"APIKeyHeader": []}]
    finally:
        engine.dispose()
