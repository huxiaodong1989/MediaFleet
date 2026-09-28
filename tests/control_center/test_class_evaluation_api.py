import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.application import TaskDispatchService, TaskQueryService
from media_platform.application.ports import PublishReceipt
from media_platform.infrastructure.database.models import MediaTaskModel
from services.control_center.application.admin_service import AdminService
from services.control_center.main import create_control_center_app


class NoopPublisher:
    def publish(self, message):
        return PublishReceipt(message_id=message.message_id)


class FakeRuntime:
    def __init__(self, factory):
        self.api_key = "test-api-key"
        self.task_service = TaskDispatchService(factory, NoopPublisher())
        self.task_query_service = TaskQueryService(factory)
        self.admin_service = AdminService(factory)

    async def start(self):
        return None

    async def close(self):
        return None


def _payload(**overrides):
    payload = {
        "taskId": "business-evaluation-1",
        "schoolCode": "school-1",
        "classroomId": "classroom-1",
        "videoMetadata": {
            "durationSeconds": 3600,
            "fileSizeMb": 120,
            "resolution": "1920x1080",
            "format": "mp4",
            "createdAt": "2026-09-23T10:00:00",
        },
        "subtitleUrl": "https://example.invalid/subtitle.txt",
        "evaluationForm": {
            "aiFormId": "form-1",
            "formType": 1,
            "courseName": "语文",
        },
        "callbackUrl": "https://example.invalid/evaluation/callback",
    }
    payload.update(overrides)
    return payload


def test_class_evaluation_api_is_idempotent_and_queryable(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'evaluation-api.db').as_posix()}")
    MediaTaskModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_control_center_app(lambda: FakeRuntime(factory))
    headers = {"X-API-Key": "test-api-key"}
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/v1/tasks/class-evaluation",
                json=_payload(),
                headers=headers,
            )
            second = client.post(
                "/api/v1/tasks/class-evaluation",
                json=_payload(),
                headers=headers,
            )
            result = client.get(
                "/api/v1/tasks/class-evaluation/result/business-evaluation-1",
                params={"school_code": "school-1"},
                headers=headers,
            )

        assert first.status_code == 200
        assert first.json()["created"] is True
        assert second.status_code == 200
        assert second.json()["created"] is False
        assert second.json()["internalTaskId"] == first.json()["internalTaskId"]
        assert result.status_code == 200
        assert result.json()["taskId"] == "business-evaluation-1"
        assert result.json()["status"] == "PENDING"

        with factory() as session:
            task = session.get(MediaTaskModel, first.json()["internalTaskId"])
            assert task.delivery_channel == "CONTENT_ANALYSIS"
            assert task.routing_key == "content.class_evaluation"
            assert task.business_task_id == "business-evaluation-1"
    finally:
        engine.dispose()


def test_quest_type_retry_rejects_completed_task(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'evaluation-retry.db').as_posix()}")
    MediaTaskModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_control_center_app(lambda: FakeRuntime(factory))
    headers = {"X-API-Key": "test-api-key"}
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/tasks/class-evaluation",
                json=_payload(),
                headers=headers,
            ).json()
            with factory.begin() as session:
                task = session.get(MediaTaskModel, created["internalTaskId"])
                task.status = "completed"
            retried = client.post(
                "/api/v1/tasks/class-evaluation",
                json=_payload(questType=1),
                headers=headers,
            )
        assert retried.status_code == 409
    finally:
        engine.dispose()


def test_class_evaluation_accepts_legacy_tenant_payload(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'evaluation-legacy.db').as_posix()}")
    MediaTaskModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_control_center_app(lambda: FakeRuntime(factory))
    headers = {"X-API-Key": "test-api-key"}
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/tasks/class-evaluation",
                json=_payload(
                    schoolCode=None,
                    tenant_id=88888,
                    max_text_chunk_size=0,
                ),
                headers=headers,
            )

        assert response.status_code == 200
        with factory() as session:
            task = session.get(MediaTaskModel, response.json()["internalTaskId"])
            assert task.school_code == "88888"
            assert task.params["maxTextChunkSize"] == 0
    finally:
        engine.dispose()
