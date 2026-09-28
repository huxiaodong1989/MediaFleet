"""调用中心媒体事件处理和业务回调测试。"""

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.contracts.event import MediaEventMessage
from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from media_platform.infrastructure.messaging import TaskRetryableError
from services.control_center.consumers import MediaEventHandler


class FakeResponse:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class FakeHttpClient:
    """记录业务回调请求，不发起真实网络连接。"""

    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def post(self, url, json):
        self.posts.append((url, json))
        if self.error is not None:
            raise self.error
        return self.response


def _database(tmp_path):
    database_file = (tmp_path / "control-center-events.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaTaskModel.__table__.create(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _task(*, callback_url="https://biz.example/callback", callback_result=None):
    return MediaTaskModel(
        id="task-1",
        request_id="request-1",
        idempotency_key="idem-1",
        task_type="video.cover.extract",
        routing_key="video.cover.extract",
        status=TaskStatus.COMPLETED.value,
        priority=0,
        progress=100,
        params={"video_url": "https://files.example/video.mp4"},
        result={"cover_url": "https://files.example/cover.jpg"},
        callback_url=callback_url,
        callback_result=callback_result,
        retry_count=0,
        max_retries=3,
        publish_status=PublishStatus.PUBLISHED.value,
        message_id="message-1",
        created_by="SYSTEM",
        updated_by="SYSTEM",
        school_code="school-1",
    )


def _event(event_type="task.completed"):
    return MediaEventMessage(
        message_id="event-1",
        source="media-worker:worker-a",
        event_type=event_type,
        aggregate_id="task-1",
        task_id="task-1",
        node_id="worker-a",
        status="completed",
        result={"cover_url": "https://files.example/cover.jpg"},
    )


def test_completed_event_posts_business_callback_and_records_result(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    client = FakeHttpClient()
    try:
        with SessionLocal.begin() as session:
            session.add(_task())

        handler = MediaEventHandler(
            SessionLocal,
            http_client_factory=lambda: client,
            instance_id="center-a",
        )
        handler.handle(_event())

        assert client.posts[0][0] == "https://biz.example/callback"
        payload = client.posts[0][1]
        assert payload["task_id"] == "task-1"
        assert payload["status"] == TaskStatus.COMPLETED.value
        assert payload["result"]["cover_url"].endswith("cover.jpg")
        with SessionLocal() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.callback_result["success"] is True
            assert task.callback_result["status_code"] == 200
            assert task.updated_by == "center-a"
    finally:
        engine.dispose()


def test_duplicate_event_skips_after_successful_callback(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    client = FakeHttpClient()
    try:
        with SessionLocal.begin() as session:
            session.add(_task(callback_result={"success": True}))

        handler = MediaEventHandler(
            SessionLocal,
            http_client_factory=lambda: client,
        )
        handler.handle(_event())

        assert client.posts == []
    finally:
        engine.dispose()


def test_missing_callback_url_is_marked_skipped(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    client = FakeHttpClient()
    try:
        with SessionLocal.begin() as session:
            session.add(_task(callback_url=None))

        MediaEventHandler(
            SessionLocal,
            http_client_factory=lambda: client,
        ).handle(_event())

        assert client.posts == []
        with SessionLocal() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.callback_result["success"] is True
            assert task.callback_result["skipped"] is True
    finally:
        engine.dispose()


def test_callback_http_failure_records_failure_and_retries(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    client = FakeHttpClient(response=FakeResponse(status_code=500, text="failed"))
    try:
        with SessionLocal.begin() as session:
            session.add(_task())

        handler = MediaEventHandler(
            SessionLocal,
            http_client_factory=lambda: client,
        )
        with pytest.raises(TaskRetryableError, match="业务回调响应失败"):
            handler.handle(_event())

        with SessionLocal() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.callback_result["success"] is False
            assert task.callback_result["status_code"] == 500
    finally:
        engine.dispose()
