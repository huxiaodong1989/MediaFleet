"""简易管理后台查询和受控操作测试。"""

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    MediaStreamBindingModel,
    MediaTaskModel,
    RecordingServerModel,
)
from services.control_center.application.admin_service import AdminService
from services.control_center.main import create_control_center_app


@dataclass
class FakeRuntime:
    admin_service: AdminService
    api_key: str = "admin-test-key"

    async def start(self):
        return None

    async def close(self):
        return None


def _runtime(tmp_path, *, callback_handler=None):
    database_file = (tmp_path / "admin.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = AdminService(factory, callback_handler=callback_handler)
    return engine, factory, FakeRuntime(service)


def _seed(factory):
    now = datetime(2026, 9, 18, 10, 0, 0)
    with factory() as session:
        with session.begin():
            session.add(
                MediaNodeModel(
                    id="node-1",
                    node_code="worker-1",
                    node_name="媒体节点1",
                    node_type="WORKER",
                    status="ONLINE",
                    readiness_status="READY",
                    capabilities=["video.cover.extract"],
                    capacity_config={"processing_tasks": 2, "cpu_percent": 31.5},
                    readiness_details={},
                    last_heartbeat_at=now,
                    created_by="test",
                    updated_by="test",
                )
            )
            session.add(
                RecordingServerModel(
                    id="server-1",
                    server_code="server-1",
                    server_name="录制服务器1",
                    status="ACTIVE",
                    recorder_node_id="node-1",
                    zlm_server_id="zlm-1",
                    max_recordings=10,
                    max_bindings=20,
                    occupied_bindings=1,
                    created_by="test",
                    updated_by="test",
                )
            )
            session.add(
                MediaStreamBindingModel(
                    id="binding-1",
                    school_code="SCHOOL-1",
                    resource_type="CAMERA",
                    resource_id="camera-1",
                    space_id="room-1",
                    node_id="node-1",
                    app="live",
                    stream_id="stream-1",
                    stream_name="摄像头1",
                    stream_mode="PULL",
                    status="ACTIVE",
                    version=1,
                    created_by="test",
                    updated_by="test",
                )
            )
            session.add(
                MediaTaskModel(
                    id="task-failed",
                    school_code="SCHOOL-1",
                    task_type="video.cover.extract",
                    routing_key="video.cover.extract",
                    status=TaskStatus.FAILED.value,
                    publish_status=PublishStatus.PUBLISHED.value,
                    progress=20,
                    params={"video_url": "https://example.invalid/video.mp4"},
                    result=None,
                    error_message="ffmpeg failed",
                    callback_url="https://example.invalid/callback",
                    executor_node_id="node-1",
                    retry_count=3,
                    max_retries=3,
                    message_id="message-failed",
                    created_by="test",
                    updated_by="test",
                    created_at=now,
                    updated_at=now,
                )
            )


def test_admin_queries_tasks_nodes_and_bindings(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _seed(factory)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            headers = {"X-API-Key": runtime.api_key}
            tasks = client.get("/api/v1/admin/tasks?status=FAILED", headers=headers)
            nodes = client.get("/api/v1/admin/nodes", headers=headers)
            bindings = client.get("/api/v1/admin/bindings?resource_type=CAMERA", headers=headers)
            nodes_page = client.get(
                "/api/v1/admin/nodes/page?page=1&page_size=1",
                headers=headers,
            )
            bindings_page = client.get(
                "/api/v1/admin/bindings/page?page=1&page_size=1&resource_type=CAMERA",
                headers=headers,
            )
            overview = client.get("/api/v1/admin/overview", headers=headers)

        assert tasks.status_code == 200
        assert tasks.json()["total"] == 1
        assert tasks.json()["items"][0]["task_id"] == "task-failed"
        assert nodes.status_code == 200
        assert nodes.json()[0]["active_bindings"] == 1
        assert nodes.json()[0]["active_tasks"] == 0
        assert bindings.status_code == 200
        assert bindings.json()[0]["node_code"] == "worker-1"
        assert nodes_page.status_code == 200
        assert nodes_page.json()["total"] == 1
        assert len(nodes_page.json()["items"]) == 1
        assert bindings_page.status_code == 200
        assert bindings_page.json()["total"] == 1
        assert len(bindings_page.json()["items"]) == 1
        assert overview.status_code == 200
        assert overview.json()["tasks_by_status"]["failed"] == 1
    finally:
        engine.dispose()


def test_admin_page_queries_return_distinct_database_pages(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _seed(factory)
    now = datetime(2026, 9, 18, 10, 1, 0)
    with factory() as session:
        with session.begin():
            session.add_all(
                [
                    MediaNodeModel(
                        id="node-2",
                        node_code="worker-2",
                        node_name="媒体节点2",
                        node_type="WORKER",
                        status="ONLINE",
                        readiness_status="READY",
                        capabilities=["video.audio.extract"],
                        capacity_config={},
                        readiness_details={},
                        last_heartbeat_at=now,
                        created_by="test",
                        updated_by="test",
                    ),
                    MediaNodeModel(
                        id="node-3",
                        node_code="worker-3",
                        node_name="媒体节点3",
                        node_type="WORKER",
                        status="OFFLINE",
                        readiness_status="NOT_READY",
                        capabilities=[],
                        capacity_config={},
                        readiness_details={},
                        last_heartbeat_at=now,
                        created_by="test",
                        updated_by="test",
                    ),
                ]
            )
            session.add_all(
                [
                    MediaStreamBindingModel(
                        id="binding-2",
                        school_code="SCHOOL-1",
                        resource_type="CAMERA",
                        resource_id="camera-2",
                        space_id="room-1",
                        node_id="node-2",
                        app="live",
                        stream_id="stream-2",
                        stream_name="摄像头2",
                        stream_mode="PULL",
                        status="ACTIVE",
                        version=1,
                        created_by="test",
                        updated_by="test",
                    ),
                    MediaStreamBindingModel(
                        id="binding-3",
                        school_code="SCHOOL-1",
                        resource_type="CAMERA",
                        resource_id="camera-3",
                        space_id="room-1",
                        node_id="node-3",
                        app="live",
                        stream_id="stream-3",
                        stream_name="摄像头3",
                        stream_mode="PULL",
                        status="ACTIVE",
                        version=1,
                        created_by="test",
                        updated_by="test",
                    ),
                ]
            )
            session.add_all(
                [
                    MediaTaskModel(
                        id="task-completed-2",
                        school_code="SCHOOL-1",
                        task_type="video.cover.extract",
                        routing_key="video.cover.extract",
                        status=TaskStatus.COMPLETED.value,
                        publish_status=PublishStatus.PUBLISHED.value,
                        progress=100,
                        params={},
                        result={},
                        retry_count=0,
                        max_retries=3,
                        message_id="message-completed-2",
                        created_by="test",
                        updated_by="test",
                        created_at=now,
                        updated_at=now,
                    ),
                    MediaTaskModel(
                        id="task-completed-3",
                        school_code="SCHOOL-1",
                        task_type="video.cover.extract",
                        routing_key="video.cover.extract",
                        status=TaskStatus.COMPLETED.value,
                        publish_status=PublishStatus.PUBLISHED.value,
                        progress=100,
                        params={},
                        result={},
                        retry_count=0,
                        max_retries=3,
                        message_id="message-completed-3",
                        created_by="test",
                        updated_by="test",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            headers = {"X-API-Key": runtime.api_key}
            tasks_page_1 = client.get("/api/v1/admin/tasks?page=1&page_size=1", headers=headers)
            tasks_page_2 = client.get("/api/v1/admin/tasks?page=2&page_size=1", headers=headers)
            nodes_page_1 = client.get("/api/v1/admin/nodes/page?page=1&page_size=1", headers=headers)
            nodes_page_2 = client.get("/api/v1/admin/nodes/page?page=2&page_size=1", headers=headers)
            bindings_page_1 = client.get("/api/v1/admin/bindings/page?page=1&page_size=1", headers=headers)
            bindings_page_2 = client.get("/api/v1/admin/bindings/page?page=2&page_size=1", headers=headers)

        assert tasks_page_1.json()["total"] == 3
        assert tasks_page_1.json()["page"] == 1
        assert tasks_page_2.json()["page"] == 2
        assert tasks_page_1.json()["items"][0]["task_id"] != tasks_page_2.json()["items"][0]["task_id"]
        assert nodes_page_1.json()["total"] == 3
        assert nodes_page_2.json()["items"][0]["node_id"] != nodes_page_1.json()["items"][0]["node_id"]
        assert bindings_page_1.json()["total"] == 3
        assert bindings_page_2.json()["items"][0]["binding_id"] != bindings_page_1.json()["items"][0]["binding_id"]
    finally:
        engine.dispose()


def test_admin_can_reset_failed_task_for_background_retry(tmp_path):
    engine, factory, runtime = _runtime(tmp_path)
    _seed(factory)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/admin/tasks/task-failed/retry",
                headers={"X-API-Key": runtime.api_key},
            )
        assert response.status_code == 200
        assert response.json()["task"]["status"] == "pending"
        with factory() as session:
            task = session.get(MediaTaskModel, "task-failed")
            assert task.publish_status == "PENDING"
            assert task.retry_count == 0
            assert task.executor_node_id is None
    finally:
        engine.dispose()


def test_admin_callback_retry_delegates_without_reprocessing_task(tmp_path):
    calls = []

    class CallbackHandler:
        def retry_callback(self, task_id):
            calls.append(task_id)

    engine, factory, runtime = _runtime(tmp_path, callback_handler=CallbackHandler())
    _seed(factory)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/admin/tasks/task-failed/callback/retry",
                headers={"X-API-Key": runtime.api_key},
            )
        assert response.status_code == 200
        assert calls == ["task-failed"]
    finally:
        engine.dispose()


def test_admin_routes_require_internal_api_key(tmp_path):
    engine, _, runtime = _runtime(tmp_path)
    app = create_control_center_app(lambda: runtime)
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/admin/overview")
        assert response.status_code == 401
    finally:
        engine.dispose()
