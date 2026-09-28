"""录制任务结果状态服务测试。"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from services.recorder_node.application.task_result_service import (
    RecordingTaskResultService,
)


def _factory(tmp_path):
    database_file = (tmp_path / "recording-task-result.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaTaskModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory


def _insert_recording_task(factory, task_id: str = "record-task-1") -> None:
    now = datetime(2026, 7, 30, 16, 0, 0)
    with factory() as session:
        with session.begin():
            session.add(
                MediaTaskModel(
                    id=task_id,
                    task_type="record.stream",
                    routing_key="record.start",
                    status=TaskStatus.PROCESSING.value,
                    publish_status=PublishStatus.PUBLISHED.value,
                    priority=0,
                    progress=0,
                    params={"app": "live", "stream_id": "stream-1"},
                    callback_url="https://rtc.invalid/record/callback",
                    executor_node_id="recorder-local-1",
                    recording_server_id="recording-server-1",
                    recording_app="live",
                    recording_stream_id="stream-1",
                    reservation_start_at=now,
                    reservation_end_at=None,
                    retry_count=0,
                    max_retries=3,
                    message_id=f"{task_id}:record.start",
                    published_at=now,
                    started_at=now,
                    created_by="test",
                    updated_by="test",
                    school_code="LEGACY",
                    created_at=now,
                    updated_at=now,
                )
            )


def test_recording_task_result_service_marks_post_processing(tmp_path):
    """录制进入后处理状态属于 recorder-node 应用层语义。"""

    engine, factory = _factory(tmp_path)
    _insert_recording_task(factory)
    try:
        service = RecordingTaskResultService(
            session_factory=factory,
            node_id="recorder-local-1",
        )

        updated = service.mark_post_processing(
            task_id="record-task-1",
            result_url="E:/tmp/result.mp4",
        )

        assert updated is True
        with factory() as session:
            task = session.get(MediaTaskModel, "record-task-1")
            assert task.status == "post_processing"
            assert task.progress == 90
            assert task.result["result_url"] == "E:/tmp/result.mp4"
            assert task.error_message is None
            assert task.updated_by == "recorder-local-1"
            assert task.reservation_end_at is not None
    finally:
        engine.dispose()


def test_recording_task_result_service_marks_completed(tmp_path):
    """录制完成状态写入国标任务表，但语义归属 recorder-node。"""

    engine, factory = _factory(tmp_path)
    _insert_recording_task(factory)
    try:
        service = RecordingTaskResultService(
            session_factory=factory,
            node_id="recorder-local-1",
        )

        updated = service.mark_completed(
            task_id="record-task-1",
            result_payload={"status": "completed", "result_url": "https://cdn/record.mp4"},
        )

        assert updated is True
        with factory() as session:
            task = session.get(MediaTaskModel, "record-task-1")
            assert task.status == "completed"
            assert task.progress == 100
            assert task.result["status"] == "completed"
            assert task.completed_at is not None
            assert task.updated_by == "recorder-local-1"
    finally:
        engine.dispose()


def test_recording_task_result_service_marks_failed_and_callback_result(tmp_path):
    """录制失败和回调结果都通过 recorder-node 私有服务写入。"""

    engine, factory = _factory(tmp_path)
    _insert_recording_task(factory)
    try:
        service = RecordingTaskResultService(
            session_factory=factory,
            node_id="recorder-local-1",
        )

        failed = service.mark_failed(
            task_id="record-task-1",
            error_message="上传失败",
            result_payload={"status": "failed"},
        )
        callback_saved = service.mark_callback_result(
            task_id="record-task-1",
            callback_result={"success": False, "attempt_count": 3},
        )

        assert failed is True
        assert callback_saved is True
        with factory() as session:
            task = session.get(MediaTaskModel, "record-task-1")
            assert task.status == "failed"
            assert task.error_message == "上传失败"
            assert task.result == {"status": "failed"}
            assert task.callback_result == {"success": False, "attempt_count": 3}
            assert task.completed_at is not None
    finally:
        engine.dispose()
