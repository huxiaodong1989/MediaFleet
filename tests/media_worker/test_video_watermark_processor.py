"""视频水印处理器的新旧参数兼容测试。"""

from types import SimpleNamespace

import pytest

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.processors.video import VideoWatermarkProcessor


class FakeHttpClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class FakeVideoProcess:
    def __init__(self):
        self.http_client = FakeHttpClient()
        self.calls = []

    async def video_set_watermark_url(self, video_url, watermark):
        self.calls.append((video_url, watermark))
        return SimpleNamespace(file_url="https://files.example/watermark.mp4")


def _message(params):
    return TaskDispatchMessage(
        task_id="task-1",
        school_code="school-1",
        task_type="video.watermark",
        routing_key="video.watermark",
        params=params,
    )


def test_processor_accepts_direct_watermark_and_closes_http_client():
    fake = FakeVideoProcess()
    processor = VideoWatermarkProcessor(lambda: fake)

    result = processor.process(
        _message(
            {
                "video_url": "https://files.example/video.mp4?signature=secret",
                "watermark": "课堂录制",
            }
        )
    )

    assert fake.calls == [
        ("https://files.example/video.mp4?signature=secret", "课堂录制")
    ]
    assert result.payload["result_url"].endswith("watermark.mp4")
    assert result.payload["watermark"] == "课堂录制"
    assert result.artifacts[0]["file_type"] == "VIDEO"
    assert fake.http_client.closed is True


def test_processor_accepts_nested_legacy_watermark_param():
    fake = FakeVideoProcess()
    processor = VideoWatermarkProcessor(lambda: fake)

    result = processor.process(
        _message(
            {
                "media_url": "https://files.example/video.mp4",
                "params": {"watermark": "LY"},
            }
        )
    )

    assert fake.calls == [("https://files.example/video.mp4", "LY")]
    assert result.payload["result_url"].endswith("watermark.mp4")


def test_processor_rejects_missing_video_url_before_loading_algorithm():
    processor = VideoWatermarkProcessor(
        lambda: pytest.fail("参数无效时不应创建旧视频处理器")
    )

    with pytest.raises(TaskPermanentError, match="缺少video_url"):
        processor.process(_message({"watermark": "LY"}))


def test_processor_rejects_missing_watermark_before_loading_algorithm():
    processor = VideoWatermarkProcessor(
        lambda: pytest.fail("参数无效时不应创建旧视频处理器")
    )

    with pytest.raises(TaskPermanentError, match="缺少watermark"):
        processor.process(
            _message({"video_url": "https://files.example/video.mp4"})
        )
