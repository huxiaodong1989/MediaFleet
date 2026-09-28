from datetime import timezone

import pytest
from pydantic import ValidationError

from media_platform.contracts import (
    MEDIA_COMMAND_DEAD_LETTER_EXCHANGE,
    MEDIA_COMMAND_EXCHANGE,
    MEDIA_COMMAND_RETRY_EXCHANGE,
    MEDIA_EVENT_EXCHANGE,
    MEDIA_TASK_EXCHANGE,
    MediaEventMessage,
    RecorderCommandMessage,
    RecorderCommandType,
    TaskDispatchMessage,
    recorder_command_queue_name,
    recorder_command_routing_key,
)


def test_task_dispatch_message_round_trip() -> None:
    message = TaskDispatchMessage(
        task_id="task-001",
        school_code="school-001",
        task_type="video.cover.extract",
        routing_key="video.cover.extract",
        idempotency_key="request-001",
        params={"media_url": "https://example.test/video.mp4"},
    )

    restored = TaskDispatchMessage.model_validate_json(message.model_dump_json())

    assert restored == message
    assert restored.message_id
    assert restored.created_at.tzinfo is not None
    assert restored.created_at.utcoffset() == timezone.utc.utcoffset(None)


def test_recorder_command_requires_target_node() -> None:
    with pytest.raises(ValidationError):
        RecorderCommandMessage(
            task_id="task-002",
            target_node_id="",
            command=RecorderCommandType.RECORD_START,
        )


def test_recorder_command_serializes_enum_value() -> None:
    message = RecorderCommandMessage(
        task_id="task-003",
        target_node_id="media-node-01",
        command=RecorderCommandType.RECORD_STOP,
        params={"stream_id": "stream-001"},
    )

    payload = message.model_dump(mode="json")

    assert payload["command"] == "record.stop"
    assert payload["target_node_id"] == "media-node-01"


def test_media_event_round_trip() -> None:
    event = MediaEventMessage(
        source="media-worker-01",
        event_type="task.completed",
        aggregate_id="task-004",
        task_id="task-004",
        node_id="media-worker-01",
        status="completed",
        result={"cover_url": "https://example.test/cover.jpg"},
    )

    restored = MediaEventMessage.model_validate(event.model_dump())

    assert restored.result["cover_url"].endswith("cover.jpg")
    assert restored.status == "completed"


def test_contract_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        TaskDispatchMessage(
            task_id="task-005",
            school_code="school-001",
            task_type="video.audio.extract",
            routing_key="video.audio.extract",
            unknown_field=True,
        )


def test_rabbitmq_topology_names() -> None:
    assert MEDIA_TASK_EXCHANGE == "media.task"
    assert MEDIA_COMMAND_EXCHANGE == "media.command"
    assert MEDIA_COMMAND_RETRY_EXCHANGE == "media.command.retry"
    assert MEDIA_COMMAND_DEAD_LETTER_EXCHANGE == "media.command.dlx"
    assert MEDIA_EVENT_EXCHANGE == "media.event"
    assert recorder_command_routing_key("node-01") == "recorder.node-01"
    assert (
        recorder_command_queue_name("node-01")
        == "recorder.node-01.commands"
    )

    with pytest.raises(ValueError):
        recorder_command_queue_name("node 01")
