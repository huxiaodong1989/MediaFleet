"""离线结果发布只走独立exchange，且必须持久化/确认/有路由。"""

import json

import pytest

from services.image_analysis.infrastructure.result_publisher import (
    ImageResultPublisherConfig,
    PikaImageResultPublisher,
)


class FakeChannel:
    is_closed = False

    def __init__(self, confirmed=True):
        self.confirmed = confirmed
        self.calls = []

    def basic_publish(self, **kwargs):
        self.calls.append(kwargs)
        return self.confirmed


class FakeConnection:
    is_closed = False

    def close(self):
        self.is_closed = True


def test_offline_result_uses_stable_event_id_and_new_confirmed_exchange():
    config = ImageResultPublisherConfig(host="local", username="test", password="test")
    publisher = PikaImageResultPublisher(config)
    channel = FakeChannel()
    publisher._connection = FakeConnection()
    publisher._channel = channel
    payload = {
        "id": "message-1",
        "eventId": "sample-event-1",
        "eventType": "SAMPLE_RESULT",
        "scheduleTime": "2026-09-30 10:00:00",
    }
    publisher.publish(event_id="sample-event-1", payload=payload)
    sent = channel.calls[0]
    assert sent["exchange"] == "image.analysis.result"
    assert sent["routing_key"] == "image.analysis.sample"
    assert sent["mandatory"] is True
    assert sent["properties"].delivery_mode == 2
    assert sent["properties"].message_id == "sample-event-1"
    assert sent["properties"].type == "image.analysis.sample"
    assert json.loads(sent["body"]) == payload


def test_task_terminal_event_uses_same_exchange_and_routing_key():
    publisher = PikaImageResultPublisher(
        ImageResultPublisherConfig(host="local", username="test", password="test")
    )
    channel = FakeChannel()
    publisher._connection = FakeConnection()
    publisher._channel = channel
    payload = {
        "id": "message-1",
        "eventId": "task-terminal-1",
        "eventType": "TASK_TERMINATED",
        "status": "PARTIAL",
    }
    publisher.publish(event_id="task-terminal-1", payload=payload)
    sent = channel.calls[0]
    assert sent["exchange"] == "image.analysis.result"
    assert sent["routing_key"] == "image.analysis.sample"
    assert sent["properties"].type == "image.analysis.task.terminated"


def test_nack_or_event_mismatch_is_not_success():
    publisher = PikaImageResultPublisher(
        ImageResultPublisherConfig(host="local", username="test", password="test")
    )
    publisher._connection = FakeConnection()
    publisher._channel = FakeChannel(confirmed=False)
    with pytest.raises(ValueError):
        publisher.publish(event_id="expected", payload={"eventId": "other"})
    with pytest.raises(RuntimeError, match="未获Broker确认"):
        publisher.publish(
            event_id="expected",
            payload={"eventId": "expected", "eventType": "SAMPLE_RESULT"},
        )
    assert publisher._connection is None
