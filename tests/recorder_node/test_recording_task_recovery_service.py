"""录制节点启动恢复服务测试。"""

from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from services.recorder_node.application.task_recovery_service import (
    RecordingTaskRecoveryService,
)


def _factory(tmp_path):
    database_file = (tmp_path / "recording-task-recovery.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaTaskModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory


def _time_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _insert_recording_task(
    factory,
    task_id: str,
    *,
    node_id: str = "recorder-local-1",
    status: str = TaskStatus.PROCESSING.value,
    start_time: datetime | None,
    end_time: datetime | None,
    created_at: datetime,
) -> None:
    params = {
        "task_id": task_id,
        "app": "live",
        "stream_id": f"stream-{task_id}",
        "start_time": _time_text(start_time),
        "end_time": _time_text(end_time),
        "output_format": "mp4",
        "extra_params": {"extract_cover": True},
    }
    with factory() as session:
        with session.begin():
            session.add(
                MediaTaskModel(
                    id=task_id,
                    task_type="record.stream",
                    routing_key="record.start",
                    status=status,
                    publish_status=PublishStatus.PUBLISHED.value,
                    priority=0,
                    progress=0,
                    params=params,
                    callback_url="https://rtc.invalid/record/callback",
                    executor_node_id=node_id,
                    retry_count=0,
                    max_retries=3,
                    message_id=f"{task_id}:record.start",
                    published_at=created_at,
                    started_at=created_at,
                    created_by="test",
                    updated_by="test",
                    school_code="LEGACY",
                    created_at=created_at,
                    updated_at=created_at,
                )
            )


class RecordingStarter:
    """记录恢复服务重新交给本机录制器的任务。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, task_id: str, params: dict) -> bool:
        self.calls.append((task_id, params))
        return True


def test_recovery_restores_plan_after_start_before_end(tmp_path):
    """过了开始时间但未到结束时间，属于正常录制窗口，必须恢复。"""

    engine, factory = _factory(tmp_path)
    now = datetime(2026, 7, 30, 10, 30, 0)
    _insert_recording_task(
        factory,
        "record-plan-running",
        start_time=now - timedelta(minutes=30),
        end_time=now + timedelta(minutes=30),
        created_at=now - timedelta(hours=1),
    )
    starter = RecordingStarter()
    try:
        service = RecordingTaskRecoveryService(
            session_factory=factory,
            node_id="recorder-local-1",
            recording_starter=starter,
        )

        recovered = service.recover_active_recordings(now=now)

        assert recovered == 1
        assert starter.calls[0][0] == "record-plan-running"
        assert starter.calls[0][1]["app"] == "live"
        assert starter.calls[0][1]["stream_id"] == "stream-record-plan-running"
        assert starter.calls[0][1]["callback_url"] == (
            "https://rtc.invalid/record/callback"
        )
    finally:
        engine.dispose()


def test_load_recovery_params_returns_single_active_task_context(tmp_path):
    """停止命令内存未命中时，可按 task_id 读取本节点可恢复上下文。"""

    engine, factory = _factory(tmp_path)
    now = datetime(2026, 7, 30, 10, 30, 0)
    _insert_recording_task(
        factory,
        "record-plan-running",
        start_time=now - timedelta(minutes=30),
        end_time=now + timedelta(minutes=30),
        created_at=now - timedelta(hours=1),
    )
    try:
        service = RecordingTaskRecoveryService(
            session_factory=factory,
            node_id="recorder-local-1",
            recording_starter=RecordingStarter(),
        )

        params = service.load_recovery_params("record-plan-running", now=now)

        assert params is not None
        assert params["app"] == "live"
        assert params["stream_id"] == "stream-record-plan-running"
        assert params["callback_url"] == "https://rtc.invalid/record/callback"
        assert params["recovered_by_node"] == "recorder-local-1"
    finally:
        engine.dispose()


def test_recovery_restores_future_plan_before_start(tmp_path):
    """未到开始时间但任务还有效时，恢复到内存后继续等待开始时间。"""

    engine, factory = _factory(tmp_path)
    now = datetime(2026, 7, 30, 10, 30, 0)
    _insert_recording_task(
        factory,
        "record-plan-waiting",
        start_time=now + timedelta(minutes=10),
        end_time=now + timedelta(hours=1),
        created_at=now,
    )
    starter = RecordingStarter()
    try:
        service = RecordingTaskRecoveryService(
            session_factory=factory,
            node_id="recorder-local-1",
            recording_starter=starter,
        )

        assert service.recover_active_recordings(now=now) == 1
        assert starter.calls[0][0] == "record-plan-waiting"
    finally:
        engine.dispose()


def test_recovery_restores_open_ended_recording_within_24_hours(tmp_path):
    """只有开始时间没有结束时间，开始后 24 小时内允许恢复。"""

    engine, factory = _factory(tmp_path)
    now = datetime(2026, 7, 30, 10, 30, 0)
    _insert_recording_task(
        factory,
        "record-open-running",
        start_time=now - timedelta(hours=23, minutes=59),
        end_time=None,
        created_at=now - timedelta(hours=24),
    )
    starter = RecordingStarter()
    try:
        service = RecordingTaskRecoveryService(
            session_factory=factory,
            node_id="recorder-local-1",
            recording_starter=starter,
        )

        assert service.recover_active_recordings(now=now) == 1
        assert starter.calls[0][0] == "record-open-running"
        assert starter.calls[0][1]["end_time"] is None
    finally:
        engine.dispose()


def test_recovery_skips_expired_and_other_node_tasks(tmp_path):
    """过结束时间、开放式超过 24 小时、终态和其它节点任务都不恢复。"""

    engine, factory = _factory(tmp_path)
    now = datetime(2026, 7, 30, 10, 30, 0)
    _insert_recording_task(
        factory,
        "record-ended",
        start_time=now - timedelta(hours=2),
        end_time=now - timedelta(minutes=1),
        created_at=now - timedelta(hours=2),
    )
    _insert_recording_task(
        factory,
        "record-open-expired",
        start_time=now - timedelta(hours=24, minutes=1),
        end_time=None,
        created_at=now - timedelta(hours=25),
    )
    _insert_recording_task(
        factory,
        "record-completed",
        status=TaskStatus.COMPLETED.value,
        start_time=now - timedelta(minutes=30),
        end_time=now + timedelta(minutes=30),
        created_at=now - timedelta(hours=1),
    )
    _insert_recording_task(
        factory,
        "record-other-node",
        node_id="recorder-other",
        start_time=now - timedelta(minutes=30),
        end_time=now + timedelta(minutes=30),
        created_at=now - timedelta(hours=1),
    )
    starter = RecordingStarter()
    try:
        service = RecordingTaskRecoveryService(
            session_factory=factory,
            node_id="recorder-local-1",
            recording_starter=starter,
        )

        assert service.recover_active_recordings(now=now) == 0
        assert starter.calls == []
    finally:
        engine.dispose()
