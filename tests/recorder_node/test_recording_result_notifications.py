"""录制结果通知组件测试。"""

from __future__ import annotations

from datetime import datetime

import pytest

from services.recorder_node.notifications import (
    RecorderResultNotifier,
    RecorderResultPayloadBuilder,
)
from services.recorder_node.recorder.stream_recorder import StreamRecorder


class FakePublisher:
    """记录 RabbitMQ 适配器发布调用。"""

    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.published = []

    def publish_to_fanout_exchange(self, **kwargs) -> None:
        self.published.append(kwargs)


class FailingPublisher(FakePublisher):
    """模拟 RabbitMQ 发布失败。"""

    def publish_to_fanout_exchange(self, **kwargs) -> None:
        raise RuntimeError("broker down")


class FakeStorageSettings:
    type = "COS"
    audio_type = "MINIO"


class FakeSettings:
    storage = FakeStorageSettings()


class FakeAudioInfoExtractor:
    async def get_audio_info(self, audio_local_path):
        return {
            "duration": 8.5,
            "file_size": 1024,
            "audio": {"codec": "mp3"},
        }


class FakeCoverInfoExtractor:
    async def get_cover_info_from_url(self, cover_url, cover_local_path):
        return {
            "file_size": 512,
            "width": 1920,
            "height": 1080,
            "format": "jpeg",
            "mode": "RGB",
        }


def test_recorder_result_notifier_publishes_to_configured_exchange() -> None:
    publisher = FakePublisher()
    notifier = RecorderResultNotifier(
        exchange_name="record.result",
        ttl_milliseconds=3600000,
        publisher=publisher,
    )

    assert notifier.publish(
        task_id="record-task-1",
        message_data={"task_id": "record-task-1", "status": "completed"},
    )
    assert publisher.published == [
        {
            "exchange_name": "record.result",
            "message_data": {
                "task_id": "record-task-1",
                "status": "completed",
            },
            "ttl_milliseconds": 3600000,
        }
    ]


def test_recorder_result_notifier_failure_does_not_raise() -> None:
    notifier = RecorderResultNotifier(
        exchange_name="record.result",
        ttl_milliseconds=3600000,
        publisher=FailingPublisher(),
    )

    assert notifier.publish(
        task_id="record-task-1",
        message_data={"task_id": "record-task-1"},
    ) is False


@pytest.mark.asyncio
async def test_result_payload_builder_keeps_compatible_result_shape(tmp_path) -> None:
    audio_file = tmp_path / "record.mp3"
    audio_file.write_bytes(b"fake-audio")
    builder = RecorderResultPayloadBuilder(
        settings=FakeSettings(),
        audio_info_extractor=FakeAudioInfoExtractor(),
        cover_info_extractor=FakeCoverInfoExtractor(),
        now_factory=lambda: datetime(2026, 7, 27, 12, 0, 0),
    )

    payload = await builder.build(
        task_id="record-task-1",
        task_status={
            "status": "completed",
            "upload_url": "https://object.example/record.mp4",
            "video_file_id": "video-file-1",
            "audio_result_url": "https://object.example/record.mp3",
            "audio_file_id": "audio-file-1",
            "audio_local_path": str(audio_file),
            "cover_url": "https://object.example/cover.jpg",
            "cover_file_id": "cover-file-1",
            "video_bucket": "video-bucket",
            "video_key": "record.mp4",
            "audio_bucket": "audio-bucket",
            "audio_key": "record.mp3",
            "cover_bucket": "cover-bucket",
            "cover_key": "cover.jpg",
            "result_url": "E:/record.mp4",
            "video_info": {
                "duration": 30.123,
                "size": 2048,
                "width": 1280,
                "height": 720,
                "video_codec": "h264",
                "video_bitrate": 1000,
                "frame_rate": 25.0,
                "audio_codec": "aac",
                "audio_bitrate": 128,
                "audio_sample_rate": 44100,
                "audio_channels": 2,
            },
            "segments": [{"time_len": 30.123, "file_size": 2048}],
            "actual_start_time": "2026-07-27 10:00:00.000",
            "actual_end_time": "2026-07-27 10:30:00.000",
            "stream_id": "rtc-camera-stream-001",
            "original_stream_id": "rtc-camera-stream-001",
        },
    )

    assert payload["task_id"] == "record-task-1"
    assert payload["video_file_id"] == "video-file-1"
    assert payload["audio_file_id"] == "audio-file-1"
    assert payload["cover_file_id"] == "cover-file-1"
    assert payload["created_at"] == "2026-07-27 12:00:00.000"
    assert payload["summary"]["video_info"]["storge_type"] == "cos"
    assert payload["summary"]["audio_info"]["storge_type"] == "minio"
    assert payload["summary"]["cover_info"]["resolution"] == {
        "width": 1920,
        "height": 1080,
    }
    assert payload["summary"]["stream_id"] == "rtc-camera-stream-001"


@pytest.mark.asyncio
async def test_stream_recorder_publishes_mq_result_without_http_callback() -> None:
    """没有 HTTP callback_url 时，仍要通知 RTC 业务 RabbitMQ。"""

    class FakeResultNotifier:
        def __init__(self) -> None:
            self.messages = []

        def publish(self, *, task_id, message_data):
            self.messages.append((task_id, message_data))
            return True

    class FakeResultPayloadBuilder:
        async def build(self, *, task_id, task_status):
            return {
                "task_id": task_id,
                "status": task_status["status"],
                "summary": {
                    "stream_id": task_status["stream_id"],
                },
            }

    recorder = StreamRecorder.__new__(StreamRecorder)
    notifier = FakeResultNotifier()

    class FakeResultNotificationService:
        async def notify(self, *, task_id, task_status):
            payload = await FakeResultPayloadBuilder().build(
                task_id=task_id,
                task_status=task_status,
            )
            notifier.publish(task_id=task_id, message_data=payload)
            return True

    recorder.result_notification_service = FakeResultNotificationService()

    task_status = {
        "status": "completed",
        "actual_start_time": "2026-07-27 10:00:00.000",
        "actual_end_time": "2026-07-27 10:30:00.000",
        "stream_id": "rtc-camera-stream-001",
        "original_stream_id": "rtc-camera-stream-001",
        "segments": [],
    }

    assert await recorder._send_callback_notification("record-task-1", task_status)
    assert notifier.messages[0][0] == "record-task-1"
    assert notifier.messages[0][1]["task_id"] == "record-task-1"
    assert notifier.messages[0][1]["status"] == "completed"
    assert notifier.messages[0][1]["summary"]["stream_id"] == "rtc-camera-stream-001"
