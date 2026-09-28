"""Worker MySQL 执行抢占、幂等和重试状态测试。"""

from datetime import datetime, timedelta
import time

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaFileModel, MediaTaskModel
from media_platform.infrastructure.messaging import (
    TaskBusyError,
    TaskPermanentError,
    TaskRetryableError,
)
from services.media_worker.application import CallbackSendResult, TaskExecutionService
from services.media_worker.registry import ProcessorResult, TaskProcessorRegistry


class CountingProcessor:
    """记录执行次数，验证重复消息不会重复调用媒体算法。"""

    def __init__(self, errors=None):
        self.calls = 0
        self.errors = list(errors or [])

    def process(self, message):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return ProcessorResult(payload={"cover_url": "cover.jpg"})


class ArtifactProcessor:
    """返回标准产物，验证 Worker 完成任务时写入国标媒体文件表。"""

    def process(self, message):
        return ProcessorResult(
            payload={"cover_url": "https://files.example/cover.jpg"},
            artifacts=(
                {
                    "file_type": "COVER",
                    "file_url": "https://files.example/covers/cover.jpg",
                    "file_size": 12345,
                    "mime_type": "image/jpeg",
                    "bucket_name": "media",
                    "relative_path": "covers/cover.jpg",
                    "extra": {"width": 1280, "height": 720},
                },
                {
                    "file_type": "IMAGE",
                    "file_url": "https://files.example/detect/result.png",
                    "label_count": 3,
                },
            ),
        )


class SlowProcessor:
    """执行时间超过初始租约，验证 Worker 会自动续租。"""

    def process(self, message):
        time.sleep(0.25)
        return ProcessorResult(payload={"slow": True})


class RecordingCallbackSender:
    """记录 Worker 发送的业务回调，不发真实 HTTP 请求。"""

    def __init__(self, result=None):
        self.calls = []
        if isinstance(result, list):
            self.results = list(result)
        else:
            self.results = [result or CallbackSendResult(success=True, status_code=200)]

    def __call__(self, callback_url, payload):
        self.calls.append({"callback_url": callback_url, "payload": payload})
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


def _database(tmp_path):
    database_file = (tmp_path / "worker-execution.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaTaskModel.__table__.create(engine)
    MediaFileModel.__table__.create(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _task(
    *,
    status=TaskStatus.PENDING.value,
    worker_id=None,
    started_at=None,
    callback_url="https://biz.example/callback",
):
    return MediaTaskModel(
        id="task-1",
        request_id="request-1",
        idempotency_key="idem-1",
        task_type="video.cover.extract",
        routing_key="video.cover.extract",
        status=status,
        priority=0,
        progress=0,
        params={"video_url": "video.mp4"},
        callback_url=callback_url,
        retry_count=0,
        max_retries=3,
        publish_status=PublishStatus.PUBLISHED.value,
        message_id="message-1",
        executor_node_id=worker_id,
        started_at=started_at,
        created_by="SYSTEM",
        updated_by="SYSTEM",
        school_code="school-1",
    )


def _message(*, attempt=0, callback_url="https://biz.example/callback"):
    return TaskDispatchMessage(
        message_id="message-1",
        task_id="task-1",
        school_code="school-1",
        task_type="video.cover.extract",
        routing_key="video.cover.extract",
        params={"video_url": "video.mp4"},
        attempt=attempt,
        max_attempts=3,
        callback_url=callback_url,
    )


def _service(SessionLocal, processor, callback_sender=None):
    registry = TaskProcessorRegistry()
    registry.register("video.cover.extract", processor)
    return TaskExecutionService(
        SessionLocal,
        registry,
        "worker-a",
        execution_timeout=timedelta(minutes=10),
        callback_retry_interval_seconds=0,
        callback_sender=callback_sender,
    )


def test_completed_task_skips_duplicate_message_without_reprocessing(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    processor = CountingProcessor()
    try:
        with SessionLocal.begin() as session:
            session.add(_task())

        service = _service(SessionLocal, processor)
        service.handle(_message())
        service.handle(_message())

        with SessionLocal() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.status == TaskStatus.COMPLETED.value
            assert task.result == {"cover_url": "cover.jpg"}
            assert task.executor_node_id == "worker-a"
        assert processor.calls == 1
    finally:
        engine.dispose()


def test_completed_task_executes_business_callback_and_records_result(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    callback_sender = RecordingCallbackSender()
    try:
        with SessionLocal.begin() as session:
            session.add(_task())

        _service(
            SessionLocal,
            CountingProcessor(),
            callback_sender,
        ).handle(_message())

        assert len(callback_sender.calls) == 1
        call = callback_sender.calls[0]
        assert call["callback_url"] == "https://biz.example/callback"
        assert call["payload"]["task_id"] == "task-1"
        assert call["payload"]["status"] == TaskStatus.COMPLETED.value
        assert call["payload"]["result"]["cover_url"] == "cover.jpg"

        with SessionLocal() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.callback_result["success"] is True
            assert task.callback_result["status_code"] == 200
            assert task.callback_result["attempt_count"] == 1
            assert task.callback_result["max_retries"] == 3
            assert task.callback_result["callback_payload"]["status"] == "completed"
    finally:
        engine.dispose()


def test_completed_task_records_failed_business_callback_process(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    callback_sender = RecordingCallbackSender(
        CallbackSendResult(
            success=False,
            status_code=500,
            response_text="mock callback failed",
        )
    )
    try:
        with SessionLocal.begin() as session:
            session.add(_task())

        _service(
            SessionLocal,
            CountingProcessor(),
            callback_sender,
        ).handle(_message())

        with SessionLocal() as session:
            task = session.get(MediaTaskModel, "task-1")

        assert task.status == TaskStatus.COMPLETED.value
        assert task.callback_result["success"] is False
        assert task.callback_result["status_code"] == 500
        assert task.callback_result["response_text"] == "mock callback failed"
        assert task.callback_result["attempt_count"] == 3
        assert len(task.callback_result["attempts"]) == 3
        assert task.callback_result["callback_payload"]["result"] == {
            "cover_url": "cover.jpg"
        }
    finally:
        engine.dispose()


def test_business_callback_retries_until_success(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    callback_sender = RecordingCallbackSender(
        [
            CallbackSendResult(
                success=False,
                status_code=500,
                response_text="first failed",
            ),
            CallbackSendResult(
                success=False,
                status_code=502,
                response_text="second failed",
            ),
            CallbackSendResult(success=True, status_code=200, response_text="ok"),
        ]
    )
    try:
        with SessionLocal.begin() as session:
            session.add(_task())

        _service(
            SessionLocal,
            CountingProcessor(),
            callback_sender,
        ).handle(_message())

        with SessionLocal() as session:
            task = session.get(MediaTaskModel, "task-1")

        assert len(callback_sender.calls) == 3
        assert task.callback_result["success"] is True
        assert task.callback_result["status_code"] == 200
        assert task.callback_result["attempt_count"] == 3
        assert [item["status_code"] for item in task.callback_result["attempts"]] == [
            500,
            502,
            200,
        ]
    finally:
        engine.dispose()


def test_completed_task_persists_artifacts_to_media_file_table(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    try:
        with SessionLocal.begin() as session:
            session.add(_task())

        _service(SessionLocal, ArtifactProcessor()).handle(_message())

        with SessionLocal() as session:
            files = (
                session.query(MediaFileModel)
                .filter(MediaFileModel.task_id == "task-1")
                .order_by(MediaFileModel.file_type.asc(), MediaFileModel.file_name.asc())
                .all()
            )
            assert len(files) == 2
            cover = next(file for file in files if file.file_type == "COVER")
            assert cover.file_name == "cover.jpg"
            assert cover.file_url == "https://files.example/covers/cover.jpg"
            assert cover.file_size == 12345
            assert cover.mime_type == "image/jpeg"
            assert cover.bucket_name == "media"
            assert cover.relative_path == "covers/cover.jpg"
            assert cover.extra_info == {"width": 1280, "height": 720}
            assert cover.school_code == "school-1"
            assert cover.created_by == "worker-a"

            other = next(file for file in files if file.file_type == "OTHER")
            assert other.file_name == "result.png"
            assert other.mime_type == "application/octet-stream"
            assert other.extra_info == {
                "label_count": 3,
                "source_file_type": "IMAGE",
            }
    finally:
        engine.dispose()


def test_recent_processing_task_is_reported_as_busy(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    try:
        with SessionLocal.begin() as session:
            session.add(
                _task(
                    status=TaskStatus.PROCESSING.value,
                    worker_id="worker-b",
                    started_at=datetime.now(),
                )
            )

        with pytest.raises(TaskBusyError):
            _service(SessionLocal, CountingProcessor()).handle(_message())
    finally:
        engine.dispose()


def test_retryable_failure_returns_task_to_pending_then_succeeds(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    processor = CountingProcessor(errors=[RuntimeError("storage unavailable")])
    try:
        with SessionLocal.begin() as session:
            session.add(_task())
        service = _service(SessionLocal, processor)

        with pytest.raises(TaskRetryableError, match="storage unavailable"):
            service.handle(_message(attempt=0))
        with SessionLocal() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.status == TaskStatus.PENDING.value
            assert task.retry_count == 1
            assert task.executor_node_id is None

        service.handle(_message(attempt=1))
        with SessionLocal() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.status == TaskStatus.COMPLETED.value
        assert processor.calls == 2
    finally:
        engine.dispose()


def test_retryable_failure_does_not_execute_business_callback(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    callback_sender = RecordingCallbackSender()
    processor = CountingProcessor(errors=[RuntimeError("storage unavailable")])
    try:
        with SessionLocal.begin() as session:
            session.add(_task())
        service = _service(SessionLocal, processor, callback_sender)

        with pytest.raises(TaskRetryableError, match="storage unavailable"):
            service.handle(_message(attempt=0))

        assert callback_sender.calls == []
    finally:
        engine.dispose()


def test_unsupported_task_type_is_permanent_failure(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    try:
        with SessionLocal.begin() as session:
            session.add(_task())
        service = TaskExecutionService(
            SessionLocal,
            TaskProcessorRegistry(),
            "worker-a",
        )

        with pytest.raises(TaskPermanentError, match="未注册任务处理器"):
            service.handle(_message())
        # RabbitMQ 在 ACK 丢失后重复投递时，最终失败记录同样视为终态，
        # 不会再次进入处理器或重复写入错误状态。
        service.handle(_message())
        with SessionLocal() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.status == TaskStatus.FAILED.value
    finally:
        engine.dispose()


def test_final_failure_executes_business_callback_and_records_result(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    callback_sender = RecordingCallbackSender()
    try:
        with SessionLocal.begin() as session:
            session.add(_task())
        service = TaskExecutionService(
            SessionLocal,
            TaskProcessorRegistry(),
            "worker-a",
            callback_retry_interval_seconds=0,
            callback_sender=callback_sender,
        )

        with pytest.raises(TaskPermanentError, match="未注册任务处理器"):
            service.handle(_message())

        assert len(callback_sender.calls) == 1
        call = callback_sender.calls[0]
        assert call["payload"]["status"] == TaskStatus.FAILED.value
        assert "未注册任务处理器" in call["payload"]["error_message"]

        with SessionLocal() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.callback_result["success"] is True
            assert task.callback_result["callback_payload"]["status"] == "failed"
    finally:
        engine.dispose()


def test_long_running_processor_renews_execution_lease(tmp_path):
    engine, SessionLocal = _database(tmp_path)
    try:
        with SessionLocal.begin() as session:
            session.add(_task())

        registry = TaskProcessorRegistry()
        registry.register("video.cover.extract", SlowProcessor())
        service = TaskExecutionService(
            SessionLocal,
            registry,
            "worker-a",
            execution_timeout=timedelta(seconds=1),
            lease_timeout=timedelta(milliseconds=100),
            lease_renew_interval=timedelta(milliseconds=20),
            callback_retry_interval_seconds=0,
            callback_sender=RecordingCallbackSender(),
        )
        service.handle(_message())

        with SessionLocal() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.status == TaskStatus.COMPLETED.value
            assert task.execution_generation == 1
            assert task.lease_owner is None
            assert task.lease_expires_at is None
    finally:
        engine.dispose()
