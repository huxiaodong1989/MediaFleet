"""视频音频提取处理器的新旧参数兼容测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.processors.audio import VideoAudioExtractProcessor


class FakeStorage:
    """模拟对象存储的下载和上传，不连接真实 COS/MinIO。"""

    def __init__(self):
        self.download_calls = []
        self.upload_calls = []

    async def download_file(self, file_url, target_path):
        self.download_calls.append((file_url, target_path))
        video_path = Path(target_path) / "source.mp4"
        video_path.write_bytes(b"fake-video")
        return str(video_path)

    async def upload_file_enhanced(self, file_path, upload_type="video"):
        self.upload_calls.append((file_path, upload_type))
        return SimpleNamespace(file_url="https://files.example/audio.mp3")


class FakeAudioInfo:
    async def get_audio_info(self, file_path):
        return {"duration": 12.3, "file_size": Path(file_path).stat().st_size}


def _message(params):
    return TaskDispatchMessage(
        task_id="task-1",
        school_code="school-1",
        task_type="video.audio.extract",
        routing_key="video.audio.extract",
        params=params,
    )


def test_processor_extracts_audio_with_legacy_ex_params():
    storage = FakeStorage()

    def fake_ffmpeg(command, timeout_seconds):
        assert timeout_seconds == 600
        assert "-vn" in command
        assert "libmp3lame" in command
        Path(command[-1]).write_bytes(b"fake-audio")

    processor = VideoAudioExtractProcessor(
        storage_factory=lambda: storage,
        audio_info_factory=FakeAudioInfo,
        ffmpeg_runner=fake_ffmpeg,
    )

    result = processor.process(
        _message(
            {
                "media_url": "https://files.example/video.mp4?signature=secret",
                "ex_params": {"audio_format": "mp3"},
            }
        )
    )

    assert storage.download_calls[0][0].startswith("https://files.example/video.mp4")
    assert storage.upload_calls[0][1] == "audio"
    assert result.payload["audio_url"].endswith("audio.mp3")
    assert result.payload["audio_format"] == "mp3"
    assert result.payload["audio_info"]["duration"] == 12.3
    assert result.artifacts[0]["file_type"] == "AUDIO"
    assert result.artifacts[0]["mime_type"] == "audio/mpeg"


def test_processor_accepts_mp4_url_and_m4a_format():
    storage = FakeStorage()

    def fake_ffmpeg(command, timeout_seconds):
        assert "aac" in command
        Path(command[-1]).write_bytes(b"fake-audio")

    processor = VideoAudioExtractProcessor(
        storage_factory=lambda: storage,
        audio_info_factory=FakeAudioInfo,
        ffmpeg_runner=fake_ffmpeg,
    )

    result = processor.process(
        _message(
            {
                "mp4Url": "https://files.example/video.mp4",
                "audio_format": ".m4a",
            }
        )
    )

    assert result.payload["audio_format"] == "m4a"
    assert result.artifacts[0]["mime_type"] == "audio/mp4"


def test_processor_rejects_missing_video_url_before_loading_dependencies():
    processor = VideoAudioExtractProcessor(
        storage_factory=lambda: pytest.fail("参数无效时不应创建存储服务"),
        audio_info_factory=lambda: pytest.fail("参数无效时不应创建音频信息工具"),
    )

    with pytest.raises(TaskPermanentError, match="缺少video_url"):
        processor.process(_message({"audio_format": "mp3"}))


def test_processor_rejects_unsupported_audio_format():
    processor = VideoAudioExtractProcessor(
        storage_factory=lambda: pytest.fail("参数无效时不应创建存储服务"),
        audio_info_factory=lambda: pytest.fail("参数无效时不应创建音频信息工具"),
    )

    with pytest.raises(TaskPermanentError, match="不支持的audio_format"):
        processor.process(
            _message(
                {
                    "video_url": "https://files.example/video.mp4",
                    "audio_format": "ogg",
                }
            )
        )
