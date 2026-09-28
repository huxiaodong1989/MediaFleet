"""录制后处理上传和清理服务测试。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.recorder_node.postprocess import (
    RecordingCleanupService,
    RecordingMediaProcessingService,
    RecordingPostProcessPersistenceService,
    RecordingPostProcessStageExecutor,
    RecordingUploadService,
    PostProcessStage,
    PostProcessTask,
    PostProcessingManager,
)


@dataclass
class FakeUploadResult:
    """模拟对象存储上传结果。"""

    file_url: str
    file_id: str
    bucket: str
    storage_key: str
    file_name: str = "artifact.bin"
    md5: str = "fake-md5"


class FakeStorage:
    """记录上传调用，避免测试依赖真实 COS/MinIO。"""

    def __init__(self, result: FakeUploadResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    async def upload_file_enhanced(self, file_path: str, *, upload_type: str):
        self.calls.append((file_path, upload_type))
        return self.result


class FakeCoverExtractor:
    """模拟封面提取器，避免测试依赖真实 OpenCV/FFmpeg。"""

    def __init__(
        self,
        *,
        cover_file_path: str | None = None,
        validation_result: bool = True,
    ) -> None:
        self.cover_file_path = cover_file_path
        self.validation_result = validation_result
        self.validation_calls: list[tuple[str, dict]] = []
        self.extract_calls: list[dict] = []

    def validate_cover_params(self, cover_strategy: str, cover_info: dict):
        self.validation_calls.append((cover_strategy, dict(cover_info)))
        return self.validation_result

    async def extract_cover(self, **kwargs):
        self.extract_calls.append(kwargs)
        return self.cover_file_path


class FakeDispatcher:
    """记录旧分发器调用，确保后处理不再依赖它。"""

    def __init__(self, task_exists: bool) -> None:
        self.task_exists = task_exists
        self.get_calls: list[str] = []
        self.updated_status: dict | None = None

    async def get_task(self, task_id: str):
        self.get_calls.append(task_id)
        return {"id": task_id} if self.task_exists else None

    async def update_task_status(self, **kwargs):
        self.updated_status = kwargs


class FakeStreamRecorder:
    """模拟 StreamRecorder 暴露给后处理持久化服务的最小接口。"""

    def __init__(self, dispatcher: FakeDispatcher | None = None) -> None:
        self.dispatcher = dispatcher
        self.settings = SimpleNamespace(storage=SimpleNamespace(type="COS"))
        self.saved_files: list[dict] = []

    async def _save_file_to_db(self, **kwargs):
        self.saved_files.append(kwargs)


class FakeArtifactService:
    """记录录制产物落库服务调用。"""

    def __init__(self) -> None:
        self.saved_files: list[dict] = []

    async def save_file(self, **kwargs):
        self.saved_files.append(kwargs)
        return kwargs["file_id"]


class FakeNotificationRecorder(FakeStreamRecorder):
    """记录后处理失败通知调用。"""

    def __init__(self) -> None:
        super().__init__()
        self.result_notification_service = FakeResultNotificationService()

    async def _send_callback_notification(self, task_id: str, task_status: dict):
        raise AssertionError("后处理主链路不应再调用 StreamRecorder._send_callback_notification")


class FakeResultNotificationService:
    """记录录制结果通知服务调用。"""

    def __init__(self) -> None:
        self.notify_calls: list[tuple[str, dict]] = []

    async def notify(self, *, task_id: str, task_status: dict):
        self.notify_calls.append((task_id, dict(task_status)))
        return True


class FakeTaskResultService:
    """记录自动重试等待状态是否写入持久化任务事实。"""

    def __init__(self) -> None:
        self.post_processing_calls: list[dict] = []

    def mark_post_processing(self, **kwargs):
        self.post_processing_calls.append(kwargs)
        return True


class FakeAutoRetryRecorder(FakeStreamRecorder):
    """为自动重试测试提供任务结果服务和最终通知出口。"""

    def __init__(self) -> None:
        super().__init__()
        self.task_result_service = FakeTaskResultService()
        self.result_notification_service = FakeResultNotificationService()


class FakeFailingUploadService:
    """模拟关键上传阶段失败。"""

    async def upload_video(self, **kwargs):
        raise RuntimeError("mock upload failed")


class FakeCleanupService:
    """记录失败后清理调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    async def cleanup(
        self,
        *,
        task_id: str,
        result_url: str,
        task_status: dict,
        mp4_files: list,
        record_root: str | None = None,
    ):
        self.calls.append((task_id, result_url, record_root))


class FakeVideoInfoExtractor:
    """模拟视频信息提取器。"""

    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result or {"width": 1280, "height": 720}
        self.error = error
        self.calls: list[str] = []

    async def get_video_info(self, result_url: str):
        self.calls.append(result_url)
        if self.error:
            raise self.error
        return self.result


class FakeMediaRecorder(FakeStreamRecorder):
    """模拟媒体处理阶段需要的 StreamRecorder 方法。"""

    def __init__(
        self,
        *,
        video_info_result: dict | None = None,
        video_info_error: Exception | None = None,
        audio_result: str | None = None,
    ) -> None:
        super().__init__()
        self.video_info_extractor = FakeVideoInfoExtractor(
            result=video_info_result,
            error=video_info_error,
        )
        self.audio_result = audio_result
        self.audio_calls: list[tuple[str, str, str]] = []
        self.cover_calls: list[dict] = []

    async def _extract_audio(self, result_url: str, task_id: str, audio_format: str):
        raise AssertionError("后处理主链路不应再调用 StreamRecorder._extract_audio")

    async def _extract_cover_background(self, **kwargs):
        raise AssertionError("后处理主链路不应再调用 StreamRecorder._extract_cover_background")


@pytest.mark.asyncio
async def test_upload_service_uploads_video_and_writes_task_status() -> None:
    """视频上传服务只处理存储上传和任务状态写回。"""

    upload_result = FakeUploadResult(
        file_url="https://cdn.example.com/record/video.mp4",
        file_id="video-file-1",
        bucket="media",
        storage_key="record/video.mp4",
    )
    recorder = SimpleNamespace(enhanced_storage=FakeStorage(upload_result))
    task_status: dict = {}

    result = await RecordingUploadService().upload_video(
        task_id="record-task-1",
        result_url="E:/tmp/video.mp4",
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert result is upload_result
    assert recorder.enhanced_storage.calls == [("E:/tmp/video.mp4", "record")]
    assert task_status["upload_url"] == upload_result.file_url
    assert task_status["video_upload_result"] is upload_result
    assert task_status["video_bucket"] == "media"
    assert task_status["video_key"] == "record/video.mp4"


@pytest.mark.asyncio
async def test_media_processing_service_collects_video_info() -> None:
    """视频信息读取成功时写回 task_status。"""

    recorder = FakeMediaRecorder(video_info_result={"width": 1920, "height": 1080})
    task_status: dict = {}

    await RecordingMediaProcessingService().collect_video_info(
        task_id="record-task-1",
        result_url="E:/tmp/result.mp4",
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert recorder.video_info_extractor.calls == ["E:/tmp/result.mp4"]
    assert task_status["video_info"] == {"width": 1920, "height": 1080}


@pytest.mark.asyncio
async def test_media_processing_service_records_video_info_error() -> None:
    """视频信息读取失败不抛出，只按原结构记录错误。"""

    recorder = FakeMediaRecorder(video_info_error=RuntimeError("ffprobe failed"))
    task_status: dict = {}

    await RecordingMediaProcessingService().collect_video_info(
        task_id="record-task-1",
        result_url="E:/tmp/result.mp4",
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert "获取视频详细信息失败" in task_status["errors"][0]["error"]


@pytest.mark.asyncio
async def test_media_processing_service_extracts_audio_with_extra_params(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """音频提取优先使用 extra_params 中的开关和格式，并记录本地文件大小。"""

    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"audio-bytes")
    recorder = FakeMediaRecorder()
    task_status = {
        "extract_audio": False,
        "audio_format": "mp3",
        "extra_params": {"extract_audio": True, "audio_format": "wav"},
    }
    service = RecordingMediaProcessingService()
    audio_calls: list[tuple[str, str]] = []

    async def fake_extract_audio_file(result_url: str, audio_format: str):
        audio_calls.append((result_url, audio_format))
        return str(audio_file)

    monkeypatch.setattr(service, "_extract_audio_file", fake_extract_audio_file)

    await service.extract_audio(
        task_id="record-task-1",
        result_url="E:/tmp/result.mp4",
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert audio_calls == [("E:/tmp/result.mp4", "wav")]
    assert task_status["audio_result_url"] == str(audio_file)
    assert task_status["audio_local_path"] == str(audio_file)
    assert task_status["audio_file_size"] == len(b"audio-bytes")


@pytest.mark.asyncio
async def test_media_processing_service_records_audio_extract_failure() -> None:
    """音频提取方法返回空路径时按原结构记录错误。"""

    video_file = "E:/tmp/not-exists.mp4"
    recorder = FakeMediaRecorder()
    task_status = {"extra_params": {"extract_audio": True, "audio_format": "aac"}}

    await RecordingMediaProcessingService().extract_audio(
        task_id="record-task-1",
        result_url=video_file,
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert task_status["audio_format"] == "mp3"
    assert "音频提取失败" in task_status["errors"][0]["error"]


@pytest.mark.asyncio
async def test_media_processing_service_extracts_cover_when_requested(
    tmp_path: Path,
) -> None:
    """封面提取开关打开时只生成本地封面，不执行上传和落库。"""

    recorder = FakeMediaRecorder()
    cover_file = tmp_path / "covers" / "cover.jpg"
    cover_file.parent.mkdir()
    cover_file.write_bytes(b"cover")
    cover_extractor = FakeCoverExtractor(cover_file_path=str(cover_file))
    service = RecordingMediaProcessingService(cover_extractor=cover_extractor)
    task_status = {
        "start_time": "2026-07-29 15:08:00.000",
        "end_time": "2026-07-29 15:11:00.000",
        "extra_params": {
            "extract_cover": True,
            "cover_strategy": "timestamp",
            "cover_info": {"timestamp": 60},
        },
    }

    await service.extract_cover(
        task_id="record-task-1",
        result_url="E:/tmp/result.mp4",
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert cover_extractor.validation_calls == [("timestamp", {"timestamp": 60})]
    assert cover_extractor.extract_calls[0]["video_path"] == "E:/tmp/result.mp4"
    assert cover_extractor.extract_calls[0]["cover_info"] == {"timestamp": 60}
    assert task_status["cover_local_path"] == str(cover_file)
    assert task_status["cover_file_path"] == str(cover_file)
    assert task_status["cover_file_size"] == len(b"cover")


@pytest.mark.asyncio
async def test_upload_service_uploads_audio_and_writes_task_status() -> None:
    """音频上传服务只读取 audio_local_path 并写回音频上传结果。"""

    upload_result = FakeUploadResult(
        file_url="https://cdn.example.com/audio/audio.mp3",
        file_id="audio-file-1",
        bucket="media",
        storage_key="audio/audio.mp3",
    )
    recorder = SimpleNamespace(audio_storage=FakeStorage(upload_result))
    task_status = {"audio_local_path": "E:/tmp/audio.mp3"}

    result = await RecordingUploadService().upload_audio(
        task_id="record-task-1",
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert result is upload_result
    assert recorder.audio_storage.calls == [("E:/tmp/audio.mp3", "audio")]
    assert task_status["audio_result_url"] == upload_result.file_url
    assert task_status["audio_upload_result"] is upload_result
    assert task_status["audio_bucket"] == "media"
    assert task_status["audio_key"] == "audio/audio.mp3"


@pytest.mark.asyncio
async def test_upload_service_uploads_cover_and_writes_task_status() -> None:
    """封面上传服务只读取本地封面路径并写回封面上传结果。"""

    upload_result = FakeUploadResult(
        file_url="https://cdn.example.com/cover/cover.jpg",
        file_id="cover-file-1",
        bucket="media",
        storage_key="cover/cover.jpg",
        file_name="cover.jpg",
    )
    recorder = SimpleNamespace(enhanced_storage=FakeStorage(upload_result))
    task_status = {"cover_local_path": "E:/tmp/cover.jpg"}

    result = await RecordingUploadService().upload_cover(
        task_id="record-task-1",
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert result is upload_result
    assert recorder.enhanced_storage.calls == [("E:/tmp/cover.jpg", "cover")]
    assert task_status["cover_url"] == upload_result.file_url
    assert task_status["cover_upload_result"] is upload_result
    assert task_status["cover_bucket"] == "media"
    assert task_status["cover_key"] == "cover/cover.jpg"


@pytest.mark.asyncio
async def test_cleanup_service_removes_source_segments_and_derived_files(tmp_path: Path) -> None:
    """完整成功后立即删除任务源分片和全部本地派生文件。"""

    record_root = tmp_path / "record"
    work_dir = tmp_path / "work"
    record_root.mkdir()
    work_dir.mkdir()
    first_part = record_root / "part-1.mp4"
    second_part = record_root / "part-2.mp4"
    result_file = work_dir / "result.mp4"
    audio_file = work_dir / "audio.mp3"
    cover_file = work_dir / "cover.jpg"
    for path in (first_part, second_part, result_file, audio_file, cover_file):
        path.write_text("temp", encoding="utf-8")

    task_status = {
        "work_dir": str(work_dir),
        "audio_local_path": str(audio_file),
        "cover_file_path": str(cover_file),
    }
    await RecordingCleanupService().cleanup(
        task_id="record-task-1",
        result_url=str(result_file),
        task_status=task_status,
        mp4_files=[
            {"file_path": str(first_part)},
            {"file_path": str(second_part)},
            {"file_path": str(first_part)},
        ],
        record_root=str(record_root),
    )

    assert not first_part.exists()
    assert not second_part.exists()
    assert not result_file.exists()
    assert not audio_file.exists()
    assert not cover_file.exists()
    assert task_status["cleanup_pending"] is False


@pytest.mark.asyncio
async def test_cleanup_service_removes_result_when_it_is_the_source_file(
    tmp_path: Path,
) -> None:
    """单分片直接作为上传结果时只按源文件路径删除一次。"""

    source_file = tmp_path / "record" / "part.mp4"
    source_file.parent.mkdir()
    source_file.write_text("temp", encoding="utf-8")

    task_status: dict[str, object] = {"work_dir": str(tmp_path / "work")}
    await RecordingCleanupService().cleanup(
        task_id="record-task-source-result",
        result_url=str(source_file),
        task_status=task_status,
        mp4_files=[
            {"file_path": str(source_file)},
            {"file_path": str(source_file)},
        ],
        record_root=str(source_file.parent),
    )

    assert not source_file.exists()
    assert task_status["cleanup_pending"] is False


@pytest.mark.asyncio
async def test_cleanup_service_rejects_source_outside_record_root(tmp_path: Path) -> None:
    """任务数据中的越界路径不得借清理阶段删除。"""

    record_root = tmp_path / "record"
    record_root.mkdir()
    outside_file = tmp_path / "outside.mp4"
    outside_file.write_text("keep", encoding="utf-8")
    task_status: dict[str, object] = {}

    await RecordingCleanupService().cleanup(
        task_id="record-task-outside",
        result_url=str(outside_file),
        task_status=task_status,
        mp4_files=[{"file_path": str(outside_file)}],
        record_root=str(record_root),
    )

    assert outside_file.exists()
    assert task_status["cleanup_pending"] is True
    assert "不在录像根目录内" in str(task_status["cleanup_errors"])


@pytest.mark.asyncio
async def test_cleanup_service_refuses_source_delete_without_record_root(
    tmp_path: Path,
) -> None:
    """缺少录像根目录配置时保留原片并明确登记兜底清理。"""

    source_file = tmp_path / "part.mp4"
    source_file.write_text("keep", encoding="utf-8")
    task_status: dict[str, object] = {}

    await RecordingCleanupService().cleanup(
        task_id="record-task-no-root",
        result_url=str(source_file),
        task_status=task_status,
        mp4_files=[{"file_path": str(source_file)}],
    )

    assert source_file.exists()
    assert task_status["cleanup_pending"] is True
    assert "录像根目录未配置" in str(task_status["cleanup_errors"])


@pytest.mark.asyncio
async def test_cleanup_service_keeps_task_completed_when_source_delete_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """源文件删除失败留给定时清理器兜底，不反转已完成任务状态。"""

    source_file = tmp_path / "record" / "part.mp4"
    source_file.parent.mkdir()
    source_file.write_text("temp", encoding="utf-8")
    task_status: dict[str, object] = {"status": "completed"}

    def fail_remove(path: str) -> None:
        raise PermissionError(path)

    monkeypatch.setattr(
        "services.recorder_node.postprocess.cleanup_service.os.remove",
        fail_remove,
    )

    await RecordingCleanupService().cleanup(
        task_id="record-task-delete-failed",
        result_url=str(source_file),
        task_status=task_status,
        mp4_files=[{"file_path": str(source_file)}],
        record_root=str(source_file.parent),
    )

    assert source_file.exists()
    assert task_status["status"] == "completed"
    assert task_status["cleanup_pending"] is True
    assert "PermissionError" in str(task_status["cleanup_errors"])


@pytest.mark.asyncio
async def test_stage_executor_passes_recorder_record_root_to_cleanup() -> None:
    """清理阶段使用 recorder 实际录像卷根目录约束源文件删除。"""

    cleanup_service = FakeCleanupService()
    executor = RecordingPostProcessStageExecutor(
        media_processing_service=SimpleNamespace(),
        upload_service=SimpleNamespace(),
        persistence_service=SimpleNamespace(),
        cleanup_service=cleanup_service,
    )
    recorder = SimpleNamespace(
        config={"ZLM_RECORD_LOCAL_ROOT": "/data/zlmediakit/record"}
    )

    await executor.execute(
        stage=PostProcessStage.CLEANUP,
        task_id="record-task-cleanup",
        result_url="/work/result.mp4",
        task_status={},
        stream_recorder=recorder,
        mp4_files=[{"file_path": "/data/zlmediakit/record/live/s/part.mp4"}],
    )

    assert cleanup_service.calls == [
        (
            "record-task-cleanup",
            "/work/result.mp4",
            "/data/zlmediakit/record",
        )
    ]


@pytest.mark.asyncio
async def test_post_processing_manager_notifies_failure_on_critical_stage(
    monkeypatch,
) -> None:
    """关键后处理阶段失败时写失败状态、发送失败通知并继续清理。"""

    manager = PostProcessingManager()
    cleanup_service = FakeCleanupService()
    monkeypatch.setattr(manager, "upload_service", FakeFailingUploadService())
    monkeypatch.setattr(manager, "cleanup_service", cleanup_service)
    recorder = FakeNotificationRecorder()
    task_status = {"stream_id": "stream-1"}
    post_task = PostProcessTask(
        task_id="record-task-failed",
        task_status=task_status,
        result_url="E:/tmp/result.mp4",
        mp4_files=[{"file_path": "E:/tmp/part.mp4"}],
        stages=[PostProcessStage.VIDEO_UPLOAD],
        stream_recorder=recorder,
    )

    success = await manager._process_task(0, post_task)

    assert success is False
    assert task_status["status"] == "failed"
    assert task_status["failed_stage"] == "video_upload"
    assert task_status["error"] == "mock upload failed"
    assert "阶段 video_upload 执行失败" in task_status["post_processing_errors"][0]
    assert recorder.result_notification_service.notify_calls[0][0] == "record-task-failed"
    assert recorder.result_notification_service.notify_calls[0][1]["status"] == "failed"
    assert cleanup_service.calls == []


@pytest.mark.asyncio
async def test_db_save_retries_transient_packet_sequence_error(monkeypatch) -> None:
    """db_save 的 PyMySQL 协议抖动应在当前任务内有限重试。"""

    manager = PostProcessingManager()
    manager.db_save_max_attempts = 5
    manager.db_save_retry_delay_seconds = 0
    calls = 0

    async def execute_stage(**kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError(
                "PyMySQL Packet sequence number wrong - got 1 expected 2"
            )

    monkeypatch.setattr(manager, "_execute_stage", execute_stage)
    task_status = {}

    await manager._execute_stage_with_retry(
        stage=PostProcessStage.DB_SAVE,
        task_id="record-task-db-retry",
        result_url="E:/tmp/result.mp4",
        task_status=task_status,
        stream_recorder=SimpleNamespace(),
        mp4_files=[],
    )

    assert calls == 3
    assert task_status["db_save_retry_attempts"] == 2


@pytest.mark.asyncio
async def test_db_save_does_not_retry_permanent_error(monkeypatch) -> None:
    """任务事实缺失等永久业务错误不能用数据库连接重试掩盖。"""

    manager = PostProcessingManager()
    manager.db_save_max_attempts = 5
    manager.db_save_retry_delay_seconds = 0
    calls = 0

    async def execute_stage(**kwargs):
        nonlocal calls
        calls += 1
        raise ValueError("录制任务事实不存在")

    monkeypatch.setattr(manager, "_execute_stage", execute_stage)

    with pytest.raises(ValueError, match="录制任务事实不存在"):
        await manager._execute_stage_with_retry(
            stage=PostProcessStage.DB_SAVE,
            task_id="record-task-db-permanent",
            result_url="E:/tmp/result.mp4",
            task_status={},
            stream_recorder=SimpleNamespace(),
            mp4_files=[],
        )

    assert calls == 1


@pytest.mark.asyncio
async def test_db_save_exhaustion_enters_persisted_auto_retry_waiting(
    monkeypatch,
) -> None:
    """任务内重试耗尽后应持久化等待，不能立即最终失败并通知业务。"""

    manager = PostProcessingManager()
    manager.db_save_max_attempts = 1
    manager.failed_auto_retry_enabled = True
    manager.failed_auto_retry_max_attempts = 3
    manager.failed_auto_retry_initial_delay_seconds = 0
    manager.failed_auto_retry_max_delay_seconds = 0
    recorder = FakeAutoRetryRecorder()

    async def execute_stage(**kwargs):
        raise RuntimeError("PyMySQL Packet sequence number wrong")

    monkeypatch.setattr(manager, "_execute_stage", execute_stage)
    task_status = {"stream_id": "stream-auto-retry"}
    post_task = PostProcessTask(
        task_id="record-task-auto-retry",
        task_status=task_status,
        result_url="E:/tmp/result.mp4",
        mp4_files=[{"file_path": "E:/tmp/part.mp4"}],
        stages=[PostProcessStage.DB_SAVE],
        stream_recorder=recorder,
        recording_window={
            "start_time": datetime(2026, 9, 22, 10, 0, 0),
            "end_time": datetime(2026, 9, 22, 11, 0, 0),
            "stopped_at": datetime(2026, 9, 22, 11, 0, 0),
        },
    )

    success = await manager._process_task(0, post_task)

    assert success is False
    assert task_status["status"] == "post_processing"
    assert task_status["post_processing_state"] == "auto_retry_waiting"
    assert task_status["post_processing_retry_attempt"] == 1
    assert recorder.result_notification_service.notify_calls == []
    persisted = recorder.task_result_service.post_processing_calls[0]
    assert persisted["task_id"] == "record-task-auto-retry"
    assert (
        persisted["recovery_context"]["post_processing_state"]
        == "auto_retry_waiting"
    )
    assert persisted["recovery_context"]["failed_stage"] == "db_save"


@pytest.mark.asyncio
async def test_auto_retry_budget_exhaustion_becomes_final_failure(
    monkeypatch,
) -> None:
    """完整尾链路自动重试预算耗尽后才进入最终失败和业务通知。"""

    manager = PostProcessingManager()
    manager.db_save_max_attempts = 1
    manager.failed_auto_retry_enabled = True
    manager.failed_auto_retry_max_attempts = 3
    recorder = FakeAutoRetryRecorder()

    async def execute_stage(**kwargs):
        raise RuntimeError("PyMySQL Packet sequence number wrong")

    monkeypatch.setattr(manager, "_execute_stage", execute_stage)
    task_status = {
        "stream_id": "stream-auto-retry-final",
        "post_processing_retry_attempt": 3,
    }
    post_task = PostProcessTask(
        task_id="record-task-auto-retry-final",
        task_status=task_status,
        result_url="E:/tmp/result.mp4",
        mp4_files=[],
        stages=[PostProcessStage.DB_SAVE],
        stream_recorder=recorder,
    )

    success = await manager._process_task(0, post_task)

    assert success is False
    assert task_status["status"] == "failed"
    assert task_status["post_processing_state"] == "failed"
    assert task_status["post_processing_recovery"][
        "post_processing_retry_attempt"
    ] == 3
    assert recorder.task_result_service.post_processing_calls == []
    assert len(recorder.result_notification_service.notify_calls) == 1


@pytest.mark.asyncio
async def test_stage_executor_callback_keeps_completion_semantics() -> None:
    """CALLBACK 阶段由阶段执行器负责，保持原完成状态和提前终止说明。"""

    recorder = FakeNotificationRecorder()
    task_status = {
        "early_termination": True,
        "termination_reason": "业务端提前停止录制",
    }
    executor = RecordingPostProcessStageExecutor(
        media_processing_service=SimpleNamespace(),
        upload_service=SimpleNamespace(),
        persistence_service=SimpleNamespace(),
        cleanup_service=SimpleNamespace(),
    )

    await executor.execute(
        stage=PostProcessStage.CALLBACK,
        task_id="record-task-callback",
        result_url="E:/tmp/result.mp4",
        task_status=task_status,
        stream_recorder=recorder,
        mp4_files=[],
    )

    assert task_status["status"] == "completed"
    assert task_status["progress"] == 100.0
    assert task_status["completed_with_cancellation"] is True
    assert "业务端提前停止录制" in task_status["cancellation_note"]
    assert recorder.result_notification_service.notify_calls[0][0] == "record-task-callback"


@pytest.mark.asyncio
async def test_persistence_service_saves_video_artifact_with_generated_file_id() -> None:
    """视频 file_id 为空时自动生成 UUID，并保留原视频元数据结构。"""

    recorder = FakeStreamRecorder()
    artifact_service = FakeArtifactService()
    task_status = {
        "upload_url": "https://cdn.example.com/record/video.mp4",
        "result_url": "E:/tmp/result.mp4",
        "segments": [{"time_len": 10.5}, {"time_len": 2}],
        "video_info": {"width": 1920, "height": 1080, "video_codec": "h264"},
        "video_upload_result": FakeUploadResult(
            file_url="https://cdn.example.com/record/video.mp4",
            file_id="",
            bucket="record-bucket",
            storage_key="record/video.mp4",
            file_name="video.mp4",
            md5="video-md5",
        ),
    }

    await RecordingPostProcessPersistenceService(
        artifact_service=artifact_service,
    ).save(
        task_id="record-task-1",
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert recorder.saved_files == []
    assert len(artifact_service.saved_files) == 1
    saved_file = artifact_service.saved_files[0]
    assert saved_file["file_name"] == "video.mp4"
    assert saved_file["task_id"] == "record-task-1"
    assert saved_file["file_id"]
    assert saved_file["mime_type"] == "video/mp4"
    assert saved_file["storage_key"] == "record/video.mp4"
    assert task_status["video_file_id"] == saved_file["file_id"]
    assert saved_file["metadata"]["duration"] == 12.5
    assert saved_file["metadata"]["bucket"] == "record-bucket"
    assert saved_file["metadata"]["key"] == "record/video.mp4"
    assert saved_file["metadata"]["storge_type"] == "cos"
    assert saved_file["metadata"]["video_info"]["width"] == 1920


@pytest.mark.asyncio
async def test_persistence_service_saves_audio_artifact() -> None:
    """音频产物落库沿用任务中的音频格式和对象存储信息。"""

    recorder = FakeStreamRecorder()
    artifact_service = FakeArtifactService()
    task_status = {
        "audio_local_path": "E:/tmp/audio.aac",
        "audio_format": "aac",
        "audio_upload_result": FakeUploadResult(
            file_url="https://cdn.example.com/audio/audio.aac",
            file_id="audio-file-1",
            bucket="audio-bucket",
            storage_key="audio/audio.aac",
            file_name="audio.aac",
            md5="audio-md5",
        ),
    }

    await RecordingPostProcessPersistenceService(
        artifact_service=artifact_service,
    ).save(
        task_id="record-task-1",
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert recorder.saved_files == []
    assert len(artifact_service.saved_files) == 1
    saved_file = artifact_service.saved_files[0]
    assert saved_file["file_id"] == "audio-file-1"
    assert saved_file["mime_type"] == "audio/aac"
    assert saved_file["metadata"]["extracted_from_video"] is True
    assert saved_file["metadata"]["audio_info"]["bucket"] == "audio-bucket"
    assert task_status["audio_file_id"] == "audio-file-1"


@pytest.mark.asyncio
async def test_persistence_service_saves_cover_artifact_with_generated_file_id() -> None:
    """封面 file_id 为空时自动生成 UUID，并保留原封面元数据结构。"""

    recorder = FakeStreamRecorder()
    artifact_service = FakeArtifactService()
    task_status = {
        "cover_url": "https://cdn.example.com/cover/cover.jpg",
        "cover_local_path": "E:/tmp/cover.jpg",
        "cover_strategy": "timestamp",
        "cover_info": {"timestamp": 60},
        "cover_upload_result": FakeUploadResult(
            file_url="https://cdn.example.com/cover/cover.jpg",
            file_id="",
            bucket="cover-bucket",
            storage_key="cover/cover.jpg",
            file_name="cover.jpg",
            md5="cover-md5",
        ),
    }

    await RecordingPostProcessPersistenceService(
        artifact_service=artifact_service,
    ).save(
        task_id="record-task-1",
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert recorder.saved_files == []
    assert len(artifact_service.saved_files) == 1
    saved_file = artifact_service.saved_files[0]
    assert saved_file["file_name"] == "cover.jpg"
    assert saved_file["file_id"]
    assert saved_file["mime_type"] == "image/jpeg"
    assert saved_file["storage_key"] == "cover/cover.jpg"
    assert saved_file["metadata"]["strategy"] == "timestamp"
    assert saved_file["metadata"]["generated_from_video"] is True
    assert saved_file["metadata"]["cover_info"]["timestamp"] == 60
    assert saved_file["metadata"]["cover_info"]["bucket"] == "cover-bucket"
    assert task_status["cover_file_id"] == saved_file["file_id"]


@pytest.mark.asyncio
async def test_persistence_service_does_not_sync_legacy_dispatcher() -> None:
    """产物落库服务不再同步旧分发器，任务状态以国标任务表为准。"""

    dispatcher = FakeDispatcher(task_exists=True)
    recorder = FakeStreamRecorder(dispatcher=dispatcher)
    task_status = {
        "upload_url": "https://cdn.example.com/record/video.mp4",
        "audio_result_url": "https://cdn.example.com/audio/audio.mp3",
        "cover_url": "https://cdn.example.com/cover/cover.jpg",
        "segments": [{"time_len": 3}],
        "stream_id": "stream-1",
        "video_file_id": "video-file-1",
        "audio_file_id": "audio-file-1",
        "cover_file_id": "cover-file-1",
    }

    await RecordingPostProcessPersistenceService(
        artifact_service=FakeArtifactService(),
    ).save(
        task_id="record-task-1",
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert dispatcher.get_calls == []
    assert dispatcher.updated_status is None
    assert "should_delete_from_memory" not in task_status


@pytest.mark.asyncio
async def test_persistence_service_ignores_missing_legacy_dispatcher_task() -> None:
    """旧分发器任务是否存在不再影响国标产物落库。"""

    dispatcher = FakeDispatcher(task_exists=False)
    recorder = FakeStreamRecorder(dispatcher=dispatcher)
    task_status = {"early_termination": False}

    await RecordingPostProcessPersistenceService(
        artifact_service=FakeArtifactService(),
    ).save(
        task_id="record-task-1",
        task_status=task_status,
        stream_recorder=recorder,
    )

    assert dispatcher.get_calls == []
    assert dispatcher.updated_status is None
    assert "should_delete_from_memory" not in task_status
