"""任务创建、领取和可靠发布纵向链路测试。"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.application import CreateTaskCommand, TaskDispatchService
from media_platform.application.ports import PublishReceipt
from media_platform.domain.task import PublishStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from media_platform.infrastructure.database.repositories import MediaTaskRepository


class FakePublisher:
    def __init__(self, *, failures: int = 0):
        self.failures = failures
        self.messages = []

    def publish(self, message):
        self.messages.append(message)
        if self.failures:
            self.failures -= 1
            raise OSError("rabbitmq unavailable")
        return PublishReceipt(message_id=message.message_id)


def _service(tmp_path, publisher=None):
    database_file = (tmp_path / "dispatch-service.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaTaskModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory, TaskDispatchService(factory, publisher or FakePublisher())


def _command() -> CreateTaskCommand:
    return CreateTaskCommand(
        school_code="school-1",
        request_id="request-1",
        idempotency_key="idem-1",
        task_type="video.cover.extract",
        routing_key="video.cover.extract",
        priority=10,
        params={"media_url": "https://example.invalid/video.mp4"},
    )


def _minimal_command() -> CreateTaskCommand:
    return CreateTaskCommand(
        school_code="school-1",
        task_type="video.cover.extract",
        params={"media_url": "https://example.invalid/video.mp4"},
    )


def test_create_task_is_idempotent_and_only_writes_new_table(tmp_path):
    engine, factory, service = _service(tmp_path)
    try:
        first = service.create_task(_command())
        second = service.create_task(_command())

        assert first.created is True
        assert second.created is False
        assert second.task_id == first.task_id
        with factory() as session:
            assert session.scalar(
                sa.select(sa.func.count()).select_from(MediaTaskModel)
            ) == 1
            assert "tasks" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_dispatch_publishes_contract_and_marks_database(tmp_path):
    publisher = FakePublisher()
    engine, factory, service = _service(tmp_path, publisher)
    now = datetime(2026, 7, 23, 12, 0, 0)
    try:
        created = service.create_task(_command())
        result = service.dispatch_batch("center-a", now=now)

        assert result.claimed == 1
        assert result.published == 1
        assert result.failed_task_ids == ()
        message = publisher.messages[0]
        assert message.task_id == created.task_id
        assert message.school_code == "school-1"
        assert message.routing_key == "video.cover.extract"
        with factory() as session:
            task = MediaTaskRepository(session).get(created.task_id)
            assert task is not None
            assert task.publish_status == PublishStatus.PUBLISHED.value
            assert task.message_id == message.message_id
            assert task.published_at == now
    finally:
        engine.dispose()


def test_publish_failure_releases_claim_and_reuses_message_id(tmp_path):
    publisher = FakePublisher(failures=1)
    engine, factory, service = _service(tmp_path, publisher)
    now = datetime(2026, 7, 23, 12, 0, 0)
    try:
        created = service.create_task(_command())
        failed = service.dispatch_batch("center-a", now=now)
        assert failed.failed_task_ids == (created.task_id,)

        with factory() as session:
            task = MediaTaskRepository(session).get(created.task_id)
            assert task is not None
            assert task.publish_status == PublishStatus.PENDING.value
            stable_message_id = task.message_id

        succeeded = service.dispatch_batch("center-b", now=now)
        assert succeeded.published == 1
        assert publisher.messages[0].message_id == stable_message_id
        assert publisher.messages[1].message_id == stable_message_id
    finally:
        engine.dispose()


def test_create_generates_request_id_and_defaults_routing_key(tmp_path):
    engine, _, service = _service(tmp_path)
    try:
        result = service.create_task(_minimal_command())
        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            task = MediaTaskRepository(session).get(result.task_id)
            assert task is not None
            assert task.request_id.startswith("auto-")
            assert task.routing_key == "video.cover.extract"
    finally:
        engine.dispose()
