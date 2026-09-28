"""流音频切片处理器的新旧参数兼容测试。"""

import pytest

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.processors.stream import StreamAudioChunkProcessor


class FakeChunker:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False

    def start(self):
        self.started = True

    def get_status(self):
        return {
            "running": self.started,
            "task_started": False,
            "task_completed": False,
            "video_source": self.kwargs["video_source"],
        }


def _message(params, *, callback_url=None):
    return TaskDispatchMessage(
        task_id="task-1",
        school_code="school-1",
        task_type="stream.audio.chunk",
        routing_key="stream.audio.chunk",
        params=params,
        callback_url=callback_url,
    )


def test_processor_starts_background_chunker_with_legacy_ex_params():
    created = []

    def factory(**kwargs):
        chunker = FakeChunker(**kwargs)
        created.append(chunker)
        return chunker

    processor = StreamAudioChunkProcessor(factory)

    result = processor.process(
        _message(
            {
                "stream_url": "rtmp://zl.example/live/stream-1",
                "ex_params": {
                    "api_endpoint": "https://biz.example/audio/chunk",
                    "start_time": "2026-07-24 10:00:00",
                    "end_time": "2026-07-24 11:00:00",
                    "chunk_duration": "10",
                },
            },
            callback_url="https://biz.example/callback",
        )
    )

    assert len(created) == 1
    assert created[0].started is True
    assert created[0].kwargs["api_endpoint"] == "https://biz.example/audio/chunk"
    assert created[0].kwargs["callback_url"] == "https://biz.example/callback"
    assert created[0].kwargs["chunk_duration"] == 10
    assert result.payload["mode"] == "started_background_chunker"
    assert result.payload["status"]["running"] is True


def test_processor_rejects_missing_api_endpoint_before_loading_chunker():
    processor = StreamAudioChunkProcessor(
        lambda **kwargs: pytest.fail("参数无效时不应创建切片器")
    )

    with pytest.raises(TaskPermanentError, match="缺少api_endpoint"):
        processor.process(
            _message({"stream_url": "rtmp://zl.example/live/stream-1"})
        )


def test_processor_rejects_invalid_chunk_duration():
    processor = StreamAudioChunkProcessor(
        lambda **kwargs: pytest.fail("参数无效时不应创建切片器")
    )

    with pytest.raises(TaskPermanentError, match="chunk_duration 必须是正整数"):
        processor.process(
            _message(
                {
                    "stream_url": "rtmp://zl.example/live/stream-1",
                    "api_endpoint": "https://biz.example/audio/chunk",
                    "chunk_duration": "0",
                }
            )
        )
