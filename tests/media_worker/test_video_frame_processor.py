"""视频截图处理器的新旧参数兼容测试。"""

import pytest

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.processors.video import VideoFrameExtractProcessor


class FakeHttpClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class FakeVideoProcess:
    def __init__(self):
        self.http_client = FakeHttpClient()
        self.calls = []

    async def get_video_imgs_url(self, video_url, interval):
        self.calls.append((video_url, interval))
        return [
            {"time": 0, "url": "https://files.example/frame-0.jpg"},
            {"time": 5, "url": "https://files.example/frame-5.jpg"},
        ]


def _message(params):
    return TaskDispatchMessage(
        task_id="task-1",
        school_code="school-1",
        task_type="video.frames.extract",
        routing_key="video.frames.extract",
        params=params,
    )


def test_processor_extracts_frames_with_legacy_ex_params():
    fake = FakeVideoProcess()
    processor = VideoFrameExtractProcessor(lambda: fake)

    result = processor.process(
        _message(
            {
                "media_url": "https://files.example/video.mp4?signature=secret",
                "ex_params": {"interval": "5"},
            }
        )
    )

    assert fake.calls == [("https://files.example/video.mp4?signature=secret", 5)]
    assert result.payload["interval"] == 5
    assert len(result.payload["imgs"]) == 2
    assert result.artifacts[0]["file_type"] == "COVER"
    assert result.artifacts[0]["extra"]["time"] == 0
    assert fake.http_client.closed is True


def test_processor_defaults_interval_to_five_seconds():
    fake = FakeVideoProcess()
    processor = VideoFrameExtractProcessor(lambda: fake)

    processor.process(_message({"video_url": "https://files.example/video.mp4"}))

    assert fake.calls == [("https://files.example/video.mp4", 5)]


def test_processor_rejects_missing_video_url_before_loading_algorithm():
    processor = VideoFrameExtractProcessor(
        lambda: pytest.fail("参数无效时不应创建视频处理器")
    )

    with pytest.raises(TaskPermanentError, match="缺少video_url"):
        processor.process(_message({"interval": 5}))


def test_processor_rejects_invalid_interval():
    processor = VideoFrameExtractProcessor(
        lambda: pytest.fail("参数无效时不应创建视频处理器")
    )

    with pytest.raises(TaskPermanentError, match="interval 必须是正整数"):
        processor.process(
            _message(
                {
                    "video_url": "https://files.example/video.mp4",
                    "interval": 0,
                }
            )
        )
