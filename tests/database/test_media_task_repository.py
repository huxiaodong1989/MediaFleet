"""国标媒体任务仓储与多实例发布领取测试。"""

from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from media_platform.infrastructure.database.repositories import MediaTaskRepository


def _session_factory(tmp_path):
    database_file = (tmp_path / "task-repository.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaTaskModel.__table__.create(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _task(task_id: str, *, priority: int, created_at: datetime) -> MediaTaskModel:
    return MediaTaskModel(
        id=task_id,
        idempotency_key=f"idem-{task_id}",
        task_type="VIDEO_PROCESS",
        status=TaskStatus.PENDING.value,
        priority=priority,
        progress=0,
        params={"task_id": task_id},
        publish_status=PublishStatus.PENDING.value,
        created_by="SYSTEM",
        updated_by="SYSTEM",
        school_code="school-1",
        created_at=created_at,
        updated_at=created_at,
    )


def test_instances_compete_without_claiming_the_same_task(tmp_path):
    engine, SessionLocal = _session_factory(tmp_path)
    now = datetime(2026, 7, 23, 12, 0, 0)
    try:
        with SessionLocal.begin() as session:
            repository = MediaTaskRepository(session)
            repository.add(_task("task-low", priority=1, created_at=now))
            repository.add(_task("task-high", priority=10, created_at=now))
            repository.add(_task("task-middle", priority=5, created_at=now))

        with SessionLocal.begin() as session:
            first_claim = MediaTaskRepository(session).claim_pending(
                "center-a", limit=2, now=now
            )
            assert [task.id for task in first_claim] == ["task-high", "task-middle"]

        with SessionLocal.begin() as session:
            second_claim = MediaTaskRepository(session).claim_pending(
                "center-b", limit=2, now=now
            )
            assert [task.id for task in second_claim] == ["task-low"]
    finally:
        engine.dispose()


def test_only_claim_owner_can_mark_message_published(tmp_path):
    engine, SessionLocal = _session_factory(tmp_path)
    now = datetime(2026, 7, 23, 12, 0, 0)
    try:
        with SessionLocal.begin() as session:
            MediaTaskRepository(session).add(
                _task("task-1", priority=1, created_at=now)
            )
        with SessionLocal.begin() as session:
            repository = MediaTaskRepository(session)
            claimed = repository.claim_pending("center-a", now=now)
            message_id = claimed[0].message_id
            assert message_id
            assert not repository.mark_published(
                "task-1", "center-b", message_id, published_at=now
            )
            assert repository.mark_published(
                "task-1", "center-a", message_id, published_at=now
            )
        with SessionLocal() as session:
            task = MediaTaskRepository(session).get("task-1")
            assert task is not None
            assert task.publish_status == PublishStatus.PUBLISHED.value
            assert task.message_id == message_id
            assert task.locked_by is None
    finally:
        engine.dispose()


def test_stale_claim_can_be_recovered_by_another_instance(tmp_path):
    engine, SessionLocal = _session_factory(tmp_path)
    now = datetime(2026, 7, 23, 12, 0, 0)
    try:
        task = _task("task-stale", priority=1, created_at=now)
        task.publish_status = PublishStatus.CLAIMED.value
        task.locked_by = "dead-center"
        task.locked_at = now - timedelta(minutes=10)
        with SessionLocal.begin() as session:
            MediaTaskRepository(session).add(task)
        with SessionLocal.begin() as session:
            claimed = MediaTaskRepository(session).claim_pending(
                "center-b",
                now=now,
                lock_timeout=timedelta(minutes=5),
            )
            assert [item.id for item in claimed] == ["task-stale"]
            assert claimed[0].locked_by == "center-b"
    finally:
        engine.dispose()


def test_release_and_failure_transitions_require_owner(tmp_path):
    engine, SessionLocal = _session_factory(tmp_path)
    now = datetime(2026, 7, 23, 12, 0, 0)
    try:
        with SessionLocal.begin() as session:
            MediaTaskRepository(session).add(
                _task("task-release", priority=1, created_at=now)
            )
        with SessionLocal.begin() as session:
            repository = MediaTaskRepository(session)
            repository.claim_pending("center-a", now=now)
            assert not repository.release_claim("task-release", "center-b")
            assert repository.release_claim("task-release", "center-a")
            repository.claim_pending("center-b", now=now)
            assert repository.mark_publish_failed(
                "task-release", "center-b", "publisher confirm timeout"
            )
        with SessionLocal() as session:
            task = MediaTaskRepository(session).get("task-release")
            assert task is not None
            assert task.publish_status == PublishStatus.FAILED.value
            assert task.error_message == "publisher confirm timeout"
    finally:
        engine.dispose()


def test_idempotency_lookup_uses_school_scope(tmp_path):
    engine, SessionLocal = _session_factory(tmp_path)
    now = datetime(2026, 7, 23, 12, 0, 0)
    try:
        with SessionLocal.begin() as session:
            MediaTaskRepository(session).add(
                _task("task-1", priority=1, created_at=now)
            )
        with SessionLocal() as session:
            repository = MediaTaskRepository(session)
            assert repository.get_by_idempotency_key("school-1", "idem-task-1")
            assert not repository.get_by_idempotency_key(
                "school-2", "idem-task-1"
            )
    finally:
        engine.dispose()


def test_generic_dispatch_can_exclude_recording_command_intents(tmp_path):
    """通用 media.task 发布器不能领取 record.stream 定向命令。"""

    engine, SessionLocal = _session_factory(tmp_path)
    now = datetime(2026, 9, 18, 10, 0, 0)
    try:
        recording = _task("record-1", priority=10, created_at=now)
        recording.task_type = "record.stream"
        recording.routing_key = "record.start"
        media = _task("media-1", priority=1, created_at=now)
        with SessionLocal.begin() as session:
            repository = MediaTaskRepository(session)
            repository.add(recording)
            repository.add(media)

        with SessionLocal.begin() as session:
            claimed = MediaTaskRepository(session).claim_pending(
                "center-a",
                now=now,
                excluded_task_types=("record.stream",),
            )

        assert [task.id for task in claimed] == ["media-1"]
        with SessionLocal() as session:
            assert session.get(MediaTaskModel, "record-1").publish_status == (
                PublishStatus.PENDING.value
            )
    finally:
        engine.dispose()


def test_execution_lease_increments_generation_and_clears_on_completion(tmp_path):
    engine, SessionLocal = _session_factory(tmp_path)
    now = datetime(2026, 9, 18, 10, 0, 0)
    try:
        task = _task("task-lease", priority=1, created_at=now)
        task.publish_status = PublishStatus.PUBLISHED.value
        task.message_id = "message-lease"
        with SessionLocal.begin() as session:
            MediaTaskRepository(session).add(task)

        with SessionLocal.begin() as session:
            repository = MediaTaskRepository(session)
            generation = repository.claim_execution_with_lease(
                "task-lease",
                "message-lease",
                "worker-a",
                attempt=0,
                stale_before=now - timedelta(minutes=30),
                started_at=now,
                lease_timeout=timedelta(minutes=30),
            )
            assert generation == 1
            assert repository.renew_execution_lease(
                "task-lease",
                "message-lease",
                "worker-a",
                generation,
                lease_timeout=timedelta(minutes=30),
                now=now + timedelta(minutes=1),
            )
            assert not repository.mark_execution_completed(
                "task-lease",
                "message-lease",
                "worker-a",
                {"ok": False},
                execution_generation=generation + 1,
                now=now + timedelta(minutes=2),
            )
            assert repository.mark_execution_completed(
                "task-lease",
                "message-lease",
                "worker-a",
                {"ok": True},
                execution_generation=generation,
                now=now + timedelta(minutes=2),
            )

        with SessionLocal() as session:
            saved = session.get(MediaTaskModel, "task-lease")
            assert saved.status == TaskStatus.COMPLETED.value
            assert saved.execution_generation == 1
            assert saved.lease_owner is None
            assert saved.lease_expires_at is None
    finally:
        engine.dispose()


def test_expired_execution_lease_allows_new_generation_but_rejects_old_worker(
    tmp_path,
):
    engine, SessionLocal = _session_factory(tmp_path)
    now = datetime(2026, 9, 18, 10, 0, 0)
    try:
        task = _task("task-takeover", priority=1, created_at=now)
        task.publish_status = PublishStatus.PUBLISHED.value
        task.message_id = "message-takeover"
        with SessionLocal.begin() as session:
            MediaTaskRepository(session).add(task)

        with SessionLocal.begin() as session:
            repository = MediaTaskRepository(session)
            first_generation = repository.claim_execution_with_lease(
                "task-takeover",
                "message-takeover",
                "worker-a",
                attempt=0,
                stale_before=now - timedelta(minutes=30),
                started_at=now,
                lease_timeout=timedelta(minutes=5),
            )
            assert first_generation == 1

        takeover_at = now + timedelta(minutes=6)
        with SessionLocal.begin() as session:
            repository = MediaTaskRepository(session)
            second_generation = repository.claim_execution_with_lease(
                "task-takeover",
                "message-takeover",
                "worker-b",
                attempt=0,
                stale_before=now - timedelta(minutes=30),
                started_at=takeover_at,
                lease_timeout=timedelta(minutes=5),
            )
            assert second_generation == 2
            assert not repository.mark_execution_completed(
                "task-takeover",
                "message-takeover",
                "worker-a",
                {"worker": "old"},
                execution_generation=first_generation,
                now=takeover_at,
            )
            assert repository.mark_execution_completed(
                "task-takeover",
                "message-takeover",
                "worker-b",
                {"worker": "new"},
                execution_generation=second_generation,
                now=takeover_at,
            )
    finally:
        engine.dispose()
