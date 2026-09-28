"""录制节点后处理持久化恢复测试。"""

from datetime import datetime, timedelta

import sqlalchemy as sa
import pytest
from sqlalchemy.orm import sessionmaker

from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from services.recorder_node.application.post_processing_recovery_service import (
    RecordingPostProcessingRecoveryService,
)


def _factory(tmp_path):
    database_file = (tmp_path / "post-processing-recovery.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaTaskModel.__table__.create(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _insert_task(factory, *, task_id: str, status: str, node_id: str, now: datetime):
    start = now - timedelta(minutes=30)
    end = now - timedelta(minutes=1)
    with factory() as session, session.begin():
        session.add(
            MediaTaskModel(
                id=task_id,
                task_type="record.stream",
                routing_key="record.start",
                status=status,
                publish_status=PublishStatus.PUBLISHED.value,
                priority=5,
                progress=90,
                params={
                    "app": "live",
                    "stream_id": "stream-1",
                    "start_time": start.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    "end_time": end.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    "extra_params": {"extract_cover": True},
                },
                result={
                    "post_processing_recovery": {
                        "recording_window": {
                            "start_time": start.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                            "end_time": end.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                            "stopped_at": end.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        },
                        "priority": 5,
                    }
                },
                callback_url="https://rtc.invalid/callback",
                executor_node_id=node_id,
                recording_app="live",
                recording_stream_id="stream-1",
                retry_count=0,
                max_retries=3,
                message_id=f"{task_id}:record.start",
                created_by="test",
                updated_by="test",
                school_code="TEST",
                created_at=start,
                updated_at=end,
            )
        )


@pytest.mark.asyncio
async def test_post_processing_task_is_recovered_once(tmp_path):
    now = datetime(2026, 9, 18, 10, 0, 0)
    engine, factory = _factory(tmp_path)
    _insert_task(
        factory,
        task_id="post-task",
        status=TaskStatus.POST_PROCESSING.value,
        node_id="recorder-1",
        now=now,
    )
    calls = []

    def recoverer(task_id, params):
        calls.append((task_id, params))
        return True

    try:
        service = RecordingPostProcessingRecoveryService(
            session_factory=factory,
            node_id="recorder-1",
            post_processing_recoverer=recoverer,
        )
        assert await service.recover_pending_post_processing(now=now) == 1
        assert await service.recover_pending_post_processing(now=now) == 0
        assert calls[0][0] == "post-task"
        assert calls[0][1]["task_status"]["stream_id"] == "stream-1"
        assert calls[0][1]["recording_window"]["stopped_at"]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_post_processing_recovery_ignores_other_node_and_terminal_tasks(tmp_path):
    now = datetime(2026, 9, 18, 10, 0, 0)
    engine, factory = _factory(tmp_path)
    _insert_task(
        factory,
        task_id="other-node",
        status=TaskStatus.POST_PROCESSING.value,
        node_id="recorder-2",
        now=now,
    )
    _insert_task(
        factory,
        task_id="completed-task",
        status=TaskStatus.COMPLETED.value,
        node_id="recorder-1",
        now=now,
    )
    calls = []
    try:
        service = RecordingPostProcessingRecoveryService(
            session_factory=factory,
            node_id="recorder-1",
            post_processing_recoverer=lambda task_id, params: calls.append(task_id)
            or True,
        )
        assert await service.recover_pending_post_processing(now=now) == 0
        assert calls == []
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_ended_processing_task_enters_post_processing_recovery(tmp_path):
    """进程若在录制结束边界崩溃，重启后仍应继续发现文件和后处理。"""

    now = datetime(2026, 9, 18, 10, 0, 0)
    engine, factory = _factory(tmp_path)
    _insert_task(
        factory,
        task_id="ended-processing-task",
        status=TaskStatus.PROCESSING.value,
        node_id="recorder-1",
        now=now,
    )
    calls = []
    try:
        service = RecordingPostProcessingRecoveryService(
            session_factory=factory,
            node_id="recorder-1",
            post_processing_recoverer=lambda task_id, params: calls.append(
                (task_id, params)
            )
            or True,
        )

        assert await service.recover_pending_post_processing(now=now) == 1
        assert calls[0][0] == "ended-processing-task"
        with factory() as session:
            task = session.get(MediaTaskModel, "ended-processing-task")
            assert task.status == TaskStatus.POST_PROCESSING.value
            assert task.result["post_processing_recovery"]["recovered_by_node"] == (
                "recorder-1"
            )
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_recovery_rechecks_each_snapshot_before_enqueue(tmp_path):
    """扫描期间已完成的任务不能被旧快照重新写回后处理。"""

    now = datetime(2026, 9, 22, 18, 0, 0)
    engine, factory = _factory(tmp_path)
    _insert_task(
        factory,
        task_id="first-task",
        status=TaskStatus.POST_PROCESSING.value,
        node_id="recorder-1",
        now=now,
    )
    _insert_task(
        factory,
        task_id="completed-during-scan",
        status=TaskStatus.POST_PROCESSING.value,
        node_id="recorder-1",
        now=now,
    )
    calls = []

    def recoverer(task_id, params):
        calls.append(task_id)
        if task_id == "first-task":
            with factory() as session, session.begin():
                task = session.get(MediaTaskModel, "completed-during-scan")
                task.status = TaskStatus.COMPLETED.value
                task.completed_at = now
        return True

    try:
        service = RecordingPostProcessingRecoveryService(
            session_factory=factory,
            node_id="recorder-1",
            post_processing_recoverer=recoverer,
        )

        assert await service.recover_pending_post_processing(now=now) == 1
        assert calls == ["first-task"]
        with factory() as session:
            task = session.get(MediaTaskModel, "completed-during-scan")
            assert task.status == TaskStatus.COMPLETED.value
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_recovery_skips_task_already_tracked_in_memory(tmp_path):
    """正常停录刚入队时，后台扫描不能重复提交同一任务。"""

    now = datetime(2026, 9, 22, 18, 0, 0)
    engine, factory = _factory(tmp_path)
    _insert_task(
        factory,
        task_id="already-queued-task",
        status=TaskStatus.POST_PROCESSING.value,
        node_id="recorder-1",
        now=now,
    )
    calls = []
    try:
        service = RecordingPostProcessingRecoveryService(
            session_factory=factory,
            node_id="recorder-1",
            post_processing_recoverer=lambda task_id, params: calls.append(task_id)
            or True,
            post_processing_tracker=lambda task_id: task_id
            == "already-queued-task",
        )

        assert await service.recover_pending_post_processing(now=now) == 0
        assert calls == []
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_failed_post_processing_can_be_retried_without_restart(tmp_path):
    """失败任务应通过显式恢复入口重新入队，并重置为 post_processing。"""

    now = datetime(2026, 9, 22, 17, 0, 0)
    engine, factory = _factory(tmp_path)
    _insert_task(
        factory,
        task_id="failed-db-save-task",
        status=TaskStatus.FAILED.value,
        node_id="recorder-1",
        now=now,
    )
    with factory() as session, session.begin():
        task = session.get(MediaTaskModel, "failed-db-save-task")
        task.result = {
            **dict(task.result or {}),
            "failed_stage": "db_save",
            "post_processing_state": "failed",
        }
        task.error_message = "Packet sequence number wrong"
        task.completed_at = now
    calls = []
    try:
        service = RecordingPostProcessingRecoveryService(
            session_factory=factory,
            node_id="recorder-1",
            post_processing_recoverer=lambda task_id, params: calls.append(
                (task_id, params)
            )
            or True,
        )

        assert await service.retry_failed_post_processing(
            "failed-db-save-task",
            now=now,
        ) is True
        assert calls[0][0] == "failed-db-save-task"
        assert calls[0][1]["recording_window"]
        with factory() as session:
            task = session.get(MediaTaskModel, "failed-db-save-task")
            assert task.status == TaskStatus.POST_PROCESSING.value
            assert task.completed_at is None
            assert task.error_message is None
            assert (
                task.result["post_processing_recovery"]["post_processing_state"]
                == "manual_retry_queued"
            )
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_manual_retry_rejects_non_failed_task(tmp_path):
    now = datetime(2026, 9, 22, 17, 0, 0)
    engine, factory = _factory(tmp_path)
    _insert_task(
        factory,
        task_id="processing-task",
        status=TaskStatus.PROCESSING.value,
        node_id="recorder-1",
        now=now,
    )
    try:
        service = RecordingPostProcessingRecoveryService(
            session_factory=factory,
            node_id="recorder-1",
            post_processing_recoverer=lambda task_id, params: True,
        )
        with pytest.raises(ValueError, match="只有 failed"):
            await service.retry_failed_post_processing("processing-task", now=now)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_auto_retry_waits_until_due_then_requeues_with_attempt_context(
    tmp_path,
):
    """自动重试未到期不能入队，到期后应保留次数和失败阶段重新入队。"""

    now = datetime(2026, 9, 22, 17, 0, 0)
    engine, factory = _factory(tmp_path)
    _insert_task(
        factory,
        task_id="auto-retry-waiting-task",
        status=TaskStatus.POST_PROCESSING.value,
        node_id="recorder-1",
        now=now,
    )
    next_retry_at = now + timedelta(minutes=1)
    with factory() as session, session.begin():
        task = session.get(MediaTaskModel, "auto-retry-waiting-task")
        result = dict(task.result or {})
        context = dict(result.get("post_processing_recovery") or {})
        context.update(
            {
                "post_processing_state": "auto_retry_waiting",
                "failed_stage": "db_save",
                "post_processing_retry_attempt": 2,
                "post_processing_retry_max_attempts": 3,
                "next_retry_at": next_retry_at.strftime(
                    "%Y-%m-%d %H:%M:%S.%f"
                )[:-3],
            }
        )
        result["post_processing_recovery"] = context
        task.result = result

    calls = []
    try:
        service = RecordingPostProcessingRecoveryService(
            session_factory=factory,
            node_id="recorder-1",
            post_processing_recoverer=lambda task_id, params: calls.append(
                (task_id, params)
            )
            or True,
        )

        assert await service.recover_pending_post_processing(now=now) == 0
        assert calls == []
        assert await service.recover_pending_post_processing(
            now=next_retry_at
        ) == 1
        assert calls[0][0] == "auto-retry-waiting-task"
        assert calls[0][1]["task_status"]["post_processing_retry_attempt"] == 2
        assert calls[0][1]["task_status"][
            "post_processing_retry_max_attempts"
        ] == 3
        assert calls[0][1]["task_status"]["post_processing_failed_stage"] == (
            "db_save"
        )
        with factory() as session:
            task = session.get(MediaTaskModel, "auto-retry-waiting-task")
            recovery = task.result["post_processing_recovery"]
            assert recovery["post_processing_state"] == "recovered_queued"
            assert recovery["recovery_reason"] == "auto_retry_due"
            assert recovery["post_processing_retry_attempt"] == 2
    finally:
        engine.dispose()
