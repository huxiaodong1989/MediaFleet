"""直播流封面处理器的新旧参数兼容测试。"""

from types import SimpleNamespace

import pytest

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.processors.stream import LiveCoverExtractProcessor


class FakeHttpClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class FakeVideoProcess:
    def __init__(self):
        self.http_client = FakeHttpClient()
        self.calls = []

    async def live_extract_cover_url(self, stream_url, strategy, cover_info):
        self.calls.append((stream_url, strategy, cover_info))
        return SimpleNamespace(file_url="https://files.example/live-cover.jpg")


def _message(params):
    return TaskDispatchMessage(
        task_id="task-1",
        school_code="school-1",
        task_type="stream.cover.extract",
        routing_key="stream.cover.extract",
        params=params,
    )


def test_processor_extracts_live_cover_with_legacy_ex_params():
    fake = FakeVideoProcess()
    processor = LiveCoverExtractProcessor(lambda: fake)

    result = processor.process(
        _message(
            {
                "video_url": "rtmp://zl.example/live/stream-1",
                "ex_params": {
                    "cover_strategy": "timestamp",
                    "cover_info": {"start_time": 0},
                },
            }
        )
    )

    assert fake.calls == [
        (
            "rtmp://zl.example/live/stream-1",
            "timestamp",
            {"start_time": 0},
        )
    ]
    assert result.payload["cover_url"].endswith("live-cover.jpg")
    assert result.artifacts[0]["file_type"] == "COVER"
    assert fake.http_client.closed is True


def test_processor_accepts_stream_url_and_default_strategy():
    fake = FakeVideoProcess()
    processor = LiveCoverExtractProcessor(lambda: fake)

    processor.process(
        _message({"stream_url": "rtsp://camera.example/stream-1"})
    )

    assert fake.calls == [("rtsp://camera.example/stream-1", "timestamp", {})]


def test_processor_rejects_missing_stream_url_before_loading_algorithm():
    processor = LiveCoverExtractProcessor(
        lambda: pytest.fail("参数无效时不应创建视频处理器")
    )

    with pytest.raises(TaskPermanentError, match="缺少stream_url"):
        processor.process(_message({"cover_strategy": "timestamp"}))
