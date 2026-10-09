"""图像课堂接单仅写MySQL；默认关闭，同号异内容拒绝。"""

from dataclasses import dataclass

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.application.image_prompt_service import (
    ImagePromptPolicy,
    ensure_image_prompt_bootstrap,
)
from media_platform.application.task_dispatch_service import TaskDispatchService
from media_platform.contracts.image_analysis import IMAGE_TASK_TYPE
from media_platform.contracts.task import TaskDeliveryChannel
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    MediaTaskModel,
    RecordingServerModel,
)
from media_platform.infrastructure.database.models.content_prompt_bundle import (
    ContentPromptBundleModel,
)
from services.control_center.application.image_analysis_intake_service import (
    ImageAnalysisIntakeService,
)
from services.control_center.main import create_control_center_app
from services.image_analysis.prompt_defaults import load_bootstrap_prompt_bundle


@dataclass
class FakeRuntime:
    image_intake_service: ImageAnalysisIntakeService
    image_intake_enabled: bool = False
    api_key: str = "test-key"

    async def start(self):
        return None

    async def close(self):
        return None


def test_image_analysis_intake_locks_prompt_and_rejects_changed_message(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'intake.db').as_posix()}")
    sa.event.listen(
        engine, "connect", lambda dbapi, _: dbapi.execute("PRAGMA foreign_keys=ON")
    )
    MediaTaskModel.metadata.create_all(
        engine,
        tables=[
            MediaNodeModel.__table__,
            RecordingServerModel.__table__,
            MediaTaskModel.__table__,
            ContentPromptBundleModel.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    policy = ImagePromptPolicy(
        allowed_models=frozenset({"test-vision"}),
        output_schema_versions=frozenset({"image-metrics.v1"}),
        metric_definition_versions=frozenset({"classroom-behavior.v1"}),
    )
    seed = ensure_image_prompt_bootstrap(
        factory, lambda: load_bootstrap_prompt_bundle("test-vision"), policy
    )
    runtime = FakeRuntime(
        ImageAnalysisIntakeService(TaskDispatchService(factory, None), policy)
    )
    app = create_control_center_app(lambda: runtime)
    payload = {
        "messageId": "business-message-1",
        "classroomId": "class-1",
        "spaceId": "room-1",
        "schoolId": "school-a",
        "timeZone": "UTC",
        "startTime": "2026-09-29 08:30:00",
        "endTime": "2026-09-29 08:34:00",
        "streamInfoList": [
            {
                "id": "stream-row-1",
                "streamId": "camera-1",
                "videoUrl": "https://example.test/one.mp4",
                "streamingMediaSource": "ROOM_CAMERA",
                "aiAnalysisTypeList": ["T_ANALYSIS"],
            }
        ],
    }
    try:
        with TestClient(app) as client:
            url = "/api/v1/tasks/image-analysis"
            headers = {"X-API-Key": "test-key"}
            assert client.post(url, json=payload).status_code == 401
            assert client.post(url, json=payload, headers=headers).status_code == 503
            runtime.image_intake_enabled = True
            first = client.post(url, json=payload, headers=headers)
            assert first.status_code == 200, first.text
            assert first.json()["created"] is True
            task_id = first.json()["taskId"]
            again = client.post(
                url,
                json={**payload, "scheduleTime": "2000-01-01 09:00:00"},
                headers=headers,
            )
            assert again.status_code == 200
            assert again.json() == {
                "taskId": task_id,
                "created": False,
                "status": "EXISTS",
            }
            changed = {**payload, "endTime": "2026-09-29 08:36:00"}
            assert client.post(url, json=changed, headers=headers).status_code == 409
            student = {
                **payload,
                "messageId": "student-1",
                "streamInfoList": [
                    {
                        **payload["streamInfoList"][0],
                        "aiAnalysisTypeList": ["S_ANALYSIS"],
                    }
                ],
            }
            accepted = client.post(url, json=student, headers=headers)
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["created"] is True
            obsolete = {
                **student,
                "messageId": "obsolete-1",
                "streamInfoList": [
                    {**student["streamInfoList"][0], "aiConfig": ["DISCUSS"]}
                ],
            }
            assert client.post(url, json=obsolete, headers=headers).status_code == 422
        with factory() as session:
            task = session.get(MediaTaskModel, task_id)
            assert task.task_type == IMAGE_TASK_TYPE
            assert task.delivery_channel == TaskDeliveryChannel.IMAGE_ANALYSIS.value
            assert task.params["promptBundle"]["id"] == seed.id
            assert task.params["request"]["streamInfoList"][0]["streamId"] == "camera-1"
            assert task.params["request"]["messageId"] == payload["messageId"]
            assert task.params["request"]["scheduleTime"] != "2000-01-01 09:00:00"
            assert session.query(MediaTaskModel).count() == 2
            student_task = session.get(MediaTaskModel, accepted.json()["taskId"])
            assert (
                len(student_task.params["request"]["streamInfoList"][0]["aiConfig"])
                == 13
            )
    finally:
        engine.dispose()
