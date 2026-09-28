"""视频片段提取处理器的新旧参数兼容测试。"""

from types import SimpleNamespace

import pytest

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.processors.video import VideoClipExtractProcessor


class FakeHttpClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class FakeVideoProcess:
    def __init__(self):
        self.http_client = FakeHttpClient()
        self.calls = []

    async def video_extract_clips_url(self, video_url, clips):
        self.calls.append((video_url, clips))
        return {
            "video_url": video_url,
            "total_clips": len(clips),
            "success_clips": len(clips),
            "clips": [
                {
                    "buss_id": clips[0].get("buss_id"),
                    "start_time": clips[0]["start_time"],
                    "end_time": clips[0]["end_time"],
                    "duration": 20.0,
                    "clip_url": "https://files.example/clip.mp4",
                    "cover_url": "https://files.example/clip-cover.jpg",
                }
            ],
        }


def _message(params):
    return TaskDispatchMessage(
        task_id="task-1",
        school_code="school-1",
        task_type="video.clip.extract",
        routing_key="video.clip.extract",
        params=params,
    )


def test_processor_accepts_legacy_nested_params_and_closes_http_client():
    fake = FakeVideoProcess()
    processor = VideoClipExtractProcessor(lambda: fake)

    result = processor.process(
        _message(
            {
                "media_url": "https://files.example/video.mp4?signature=secret",
                "params": {
                    "clips": [
                        {
                            "start_time": "00:00:10.500",
                            "end_time": "00:00:30.500",
                            "buss_id": "clip-1",
                        }
                    ]
                },
            }
        )
    )

    assert fake.calls == [
        (
            "https://files.example/video.mp4?signature=secret",
            [
                {
                    "start_time": "00:00:10.500",
                    "end_time": "00:00:30.500",
                    "buss_id": "clip-1",
                }
            ],
        )
    ]
    assert result.payload["success_clips"] == 1
    assert result.artifacts[0]["file_type"] == "VIDEO"
    assert result.artifacts[1]["file_type"] == "COVER"
    assert fake.http_client.closed is True


def test_processor_accepts_direct_clips_with_number_time():
    fake = FakeVideoProcess()
    processor = VideoClipExtractProcessor(lambda: fake)

    result = processor.process(
        _message(
            {
                "video_url": "https://files.example/video.mp4",
                "clips": [
                    {
                        "start_time": 10.5,
                        "end_time": 30.5,
                        "buss_id": "clip-1",
                    }
                ],
            }
        )
    )

    assert result.payload["clips"][0]["clip_url"].endswith("clip.mp4")


def test_processor_rejects_missing_clips_before_loading_algorithm():
    processor = VideoClipExtractProcessor(
        lambda: pytest.fail("参数无效时不应创建旧视频处理器")
    )

    with pytest.raises(TaskPermanentError, match="缺少clips数组"):
        processor.process(
            _message({"video_url": "https://files.example/video.mp4"})
        )


def test_processor_rejects_invalid_clip_item():
    processor = VideoClipExtractProcessor(
        lambda: pytest.fail("参数无效时不应创建旧视频处理器")
    )

    with pytest.raises(TaskPermanentError, match="必须包含start_time和end_time"):
        processor.process(
            _message(
                {
                    "video_url": "https://files.example/video.mp4",
                    "clips": [{"start_time": 10}],
                }
            )
        )
