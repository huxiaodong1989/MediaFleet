"""StreamRecorder 正常停止语义测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from services.recorder_node.postprocess import post_processor
from services.recorder_node.recorder.stream_recorder import StreamRecorder


@pytest.mark.asyncio
async def test_start_recording_duplicate_task_id_is_idempotent() -> None:
    """RabbitMQ 重试同一开始命令时，不能创建第二个后台录制协程。"""

    recorder = StreamRecorder.__new__(StreamRecorder)
    recorder.recording_tasks = {}
    record_stream_calls = []

    async def fake_record_stream(task_id, task_status):
        record_stream_calls.append((task_id, task_status))

    recorder._record_stream = fake_record_stream

    params = {
        "app": "live",
        "stream_id": "stream-1",
        "start_time": "2026-07-29 15:08:00.000",
        "end_time": "2026-07-29 15:11:00.000",
        "extra_params": {"extract_audio": True},
    }

    first_result = await recorder.start_recording("task-1", params)
    await asyncio.sleep(0)
    second_result = await recorder.start_recording("task-1", params)
    await asyncio.sleep(0)

    assert first_result is second_result
    assert second_result["status"] == "pending"
    assert len(record_stream_calls) == 1


@pytest.mark.asyncio
async def test_start_recording_enforces_atomic_local_capacity() -> None:
    """本机实际录制名额的申请、重复申请和释放必须原子且幂等。"""

    recorder = StreamRecorder.__new__(StreamRecorder)
    recorder.recording_tasks = {}
    recorder.max_recordings = 1

    assert recorder._try_acquire_recording_slot("task-1") is True
    assert recorder._try_acquire_recording_slot("task-1") is True
    assert recorder._try_acquire_recording_slot("task-2") is False
    assert recorder.active_recording_count() == 1

    recorder._release_recording_slot("task-1")
    recorder._release_recording_slot("task-1")
    assert recorder.active_recording_count() == 0
    assert recorder._try_acquire_recording_slot("task-2") is True


class FakeArtifactService:
    """记录 StreamRecorder 是否把产物落库委托给应用服务。"""

    def __init__(self):
        self.calls = []

    async def save_file(self, **kwargs):
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_save_file_to_db_delegates_to_artifact_service() -> None:
    """StreamRecorder 不再持有国标文件表细节，只委托应用服务。"""

    recorder = StreamRecorder.__new__(StreamRecorder)
    recorder.artifact_service = FakeArtifactService()
    await recorder._save_file_to_db(
        file_name="cover.jpg",
        task_id="record-task-1",
        file_id="file-1",
        file_url="https://cdn.files.example/upload/cover/cover.jpg",
        file_path="C:/tmp/cover.jpg",
        mime_type="image/jpeg",
        storage_key="upload/cover/cover.jpg",
        metadata={"bucket": "media-bucket", "md5": "abc"},
    )

    assert recorder.artifact_service.calls == [
        {
            "file_name": "cover.jpg",
            "task_id": "record-task-1",
            "file_id": "file-1",
            "file_url": "https://cdn.files.example/upload/cover/cover.jpg",
            "file_path": "C:/tmp/cover.jpg",
            "mime_type": "image/jpeg",
            "storage_key": "upload/cover/cover.jpg",
            "metadata": {"bucket": "media-bucket", "md5": "abc"},
        }
    ]


@pytest.mark.asyncio
async def test_stop_recording_marks_running_task_as_stopping_without_canceling() -> None:
    """手动停止开放式录制时，任务应进入停止中，而不是取消。"""

    recorder = StreamRecorder.__new__(StreamRecorder)
    recorder.recording_tasks = {
        "task-1": {
            "task_id": "task-1",
            "status": "recording",
            "errors": [],
        }
    }

    accepted = await recorder.stop_recording(
        "task-1",
        {
            "reason": "manual_stop",
            "stop_time": "2026-07-27T10:30:00",
        },
    )

    task_status = recorder.recording_tasks["task-1"]
    assert accepted is True
    assert task_status["status"] == "stopping"
    assert task_status["stop_requested"] is True
    assert task_status["stop_reason"] == "manual_stop"
    assert task_status["stop_requested_at"] == "2026-07-27 10:30:00.000"
    assert "cancel_reason" not in task_status


@pytest.mark.asyncio
async def test_stop_recording_missing_task_is_idempotent_miss() -> None:
    """本机没有任务时返回 False，命令处理器会把重复停止按幂等成功处理。"""

    recorder = StreamRecorder.__new__(StreamRecorder)
    recorder.recording_tasks = {}

    assert await recorder.stop_recording("missing-task") is False


@pytest.mark.asyncio
async def test_stop_recording_terminal_task_is_idempotent_success() -> None:
    """已终态任务重复停止不改变状态。"""

    recorder = StreamRecorder.__new__(StreamRecorder)
    recorder.recording_tasks = {
        "task-1": {
            "task_id": "task-1",
            "status": "completed",
        }
    }

    assert await recorder.stop_recording("task-1") is True
    assert recorder.recording_tasks["task-1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_record_stream_fails_when_post_processing_submit_fails(monkeypatch) -> None:
    """后处理队列提交失败时不再走同步降级处理，任务应直接失败。"""

    class FakePostProcessingManager:
        async def submit_task(self, **kwargs):
            return False

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(post_processor, "get_post_processing_manager", lambda: FakePostProcessingManager())
    monkeypatch.setattr("services.recorder_node.recorder.stream_recorder.asyncio.sleep", fake_sleep)

    recorder = StreamRecorder.__new__(StreamRecorder)
    recorder.dispatcher = None
    recorder.recording_tasks = {}
    recorder.max_recordings = 1

    async def fake_start_recording(stream_id, app):
        task_status["stop_requested"] = True
        task_status["stop_requested_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        return True

    recorder._start_zlm_recording_with_app = fake_start_recording
    recorder._stop_zlm_recording_with_app = lambda stream_id, app: _async_result(True)
    recorder._get_mp4_record_files_with_app = lambda stream_id, app, start, end: _async_result(
        [{"file_path": "E:/record/live/stream/part.mp4", "time_len": 1, "file_size": 100}]
    )
    recorder._process_result_files = lambda task_id, mp4_files: _async_result("E:/record/live/stream/result.mp4")
    recorder._report_flow_trace = lambda *args, **kwargs: _async_result(None)
    callback_calls = []

    async def fake_callback(task_id, task_status):
        callback_calls.append((task_id, dict(task_status)))
        return True

    recorder._send_callback_notification = fake_callback

    now = datetime.now()
    task_status = {
        "task_id": "record-task-1",
        "stream_id": "stream-1",
        "app": "live",
        "start_time": now - timedelta(seconds=1),
        "end_time": now + timedelta(minutes=10),
        "errors": [],
        "extra_params": {},
    }

    await recorder._record_stream("record-task-1", task_status)

    assert task_status["status"] == "failed"
    assert "提交后处理任务失败" in task_status["error"]
    assert callback_calls
    assert callback_calls[-1][1]["status"] == "failed"
    assert recorder.active_recording_count() == 0


@pytest.mark.asyncio
async def test_record_stream_capacity_failure_does_not_stop_existing_zlm_recording(
    monkeypatch,
) -> None:
    """硬并发保护发生在 startRecord 前，不能误发 stopRecord。"""

    class FakePostProcessingManager:
        async def submit_task(self, **kwargs):
            return False

    monkeypatch.setattr(
        post_processor,
        "get_post_processing_manager",
        lambda: FakePostProcessingManager(),
    )
    recorder = StreamRecorder.__new__(StreamRecorder)
    recorder.recording_tasks = {}
    recorder.max_recordings = 1
    assert recorder._try_acquire_recording_slot("other-task") is True
    stop_calls = []
    recorder._stop_zlm_recording_with_app = (
        lambda stream_id, app: _append_async(stop_calls, (stream_id, app), True)
    )
    recorder._report_flow_trace = lambda *args, **kwargs: _async_result(None)
    callback_calls = []

    async def fake_callback(task_id, task_status):
        callback_calls.append((task_id, dict(task_status)))
        return True

    recorder._send_callback_notification = fake_callback
    now = datetime.now()
    task_status = {
        "task_id": "record-task-capacity",
        "stream_id": "stream-capacity",
        "app": "live",
        "start_time": now - timedelta(seconds=1),
        "end_time": now + timedelta(minutes=10),
        "errors": [],
        "extra_params": {},
    }

    await recorder._record_stream("record-task-capacity", task_status)

    assert stop_calls == []
    assert task_status["status"] == "failed"
    assert "本机实际录制并发已满" in task_status["error"]
    assert callback_calls
    assert recorder.active_recording_count() == 1


@pytest.mark.asyncio
async def test_record_stream_zlm_start_rejection_fails_without_stop() -> None:
    """ZLM 明确拒绝 startRecord 时应进入失败尾链路，且不能误发 stopRecord。"""

    recorder = StreamRecorder.__new__(StreamRecorder)
    recorder.recording_tasks = {}
    recorder.max_recordings = 1
    recorder._report_flow_trace = lambda *args, **kwargs: _async_result(None)
    recorder._start_zlm_recording_with_app = lambda stream_id, app: _async_result(False)

    stop_calls = []
    recorder._stop_zlm_recording_with_app = (
        lambda stream_id, app: _append_async(stop_calls, (stream_id, app), True)
    )
    queued_results = []

    async def fake_queue_result(task_id, task_status, **kwargs):
        queued_results.append((task_id, dict(task_status), kwargs))

    recorder._queue_recording_result = fake_queue_result
    now = datetime.now()
    task_status = {
        "task_id": "record-task-zlm-rejected",
        "stream_id": "missing-stream",
        "app": "live",
        "start_time": now - timedelta(seconds=1),
        "end_time": None,
        "errors": [],
        "extra_params": {},
    }

    await recorder._record_stream("record-task-zlm-rejected", task_status)

    assert stop_calls == []
    assert len(queued_results) == 1
    assert queued_results[0][0] == "record-task-zlm-rejected"
    assert queued_results[0][1]["zlm_start_recording_accepted"] is False
    assert "ZLMediaKit拒绝开始录制" in queued_results[0][2]["recording_error"]
    assert recorder.active_recording_count() == 0


async def _async_result(value):
    return value


async def _append_async(target, item, result):
    target.append(item)
    return result
