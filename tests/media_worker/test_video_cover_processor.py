"""视频封面处理器的新旧参数兼容测试。"""

from types import SimpleNamespace

import pytest

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.processors.video import VideoCoverExtractProcessor


class FakeHttpClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class FakeVideoProcess:
    def __init__(self):
        self.http_client = FakeHttpClient()
        self.calls = []

    async def video_extract_cover_url(self, video_url, strategy, cover_info):
        self.calls.append((video_url, strategy, cover_info))
        return SimpleNamespace(file_url="https://files.example/cover.jpg")


def _message(params):
    return TaskDispatchMessage(
        task_id="task-1",
        school_code="school-1",
        task_type="video.cover.extract",
        routing_key="video.cover.extract",
        params=params,
    )


def test_processor_accepts_legacy_ex_params_and_closes_http_client():
    fake = FakeVideoProcess()
    processor = VideoCoverExtractProcessor(lambda: fake)

    result = processor.process(
        _message(
            {
                "video_url": "https://files.example/video.mp4",
                "ex_params": {
                    "cover_strategy": "timestamp",
                    "cover_info": {"start_time": 3},
                },
            }
        )
    )

    assert fake.calls == [
        (
            "https://files.example/video.mp4",
            "timestamp",
            {"start_time": 3},
        )
    ]
    assert result.payload["cover_url"].endswith("cover.jpg")
    assert result.artifacts[0]["file_type"] == "COVER"
    assert fake.http_client.closed is True


def test_processor_rejects_missing_video_url_before_loading_algorithm():
    processor = VideoCoverExtractProcessor(
        lambda: pytest.fail("参数无效时不应创建旧视频处理器")
    )

    with pytest.raises(TaskPermanentError, match="缺少video_url"):
        processor.process(_message({"cover_strategy": "timestamp"}))


def test_video_process_core_keeps_legacy_import_path():
    """视频处理真实实现归属 media-worker 新目录。"""

    from services.media_worker.processors.video.video_process import VideoProcess

    assert VideoProcess.__module__.startswith("services.media_worker.processors.video")
