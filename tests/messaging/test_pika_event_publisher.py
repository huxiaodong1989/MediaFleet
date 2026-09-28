"""RabbitMQ 媒体事件 Publisher Confirm 适配器测试。"""

import json

from media_platform.contracts.event import MediaEventMessage
from media_platform.contracts.topology import MEDIA_EVENT_EXCHANGE
from media_platform.infrastructure.messaging import (
    PikaMediaEventPublisher,
    RabbitMQPublisherConfig,
)


class FakeChannel:
    def __init__(self, *, fail=False):
        self.is_closed = False
        self.fail = fail
        self.exchange_calls = []
        self.confirmed = False
        self.publish_calls = []

    def exchange_declare(self, **kwargs):
        self.exchange_calls.append(kwargs)

    def confirm_delivery(self):
        self.confirmed = True

    def basic_publish(self, **kwargs):
        self.publish_calls.append(kwargs)
        if self.fail:
            raise OSError("connection reset")
        return True


class FakeConnection:
    def __init__(self, channel):
        self.is_closed = False
        self._channel = channel

    def channel(self):
        return self._channel

    def close(self):
        self.is_closed = True


def _event() -> MediaEventMessage:
    return MediaEventMessage(
        message_id="event-message-1",
        source="media-worker:worker-a",
        event_type="task.completed",
        aggregate_id="task-1",
        task_id="task-1",
        node_id="worker-a",
        status="completed",
        result={"cover_url": "https://files.example/cover.jpg"},
    )


def test_event_publisher_declares_event_exchange_and_uses_event_type(monkeypatch):
    channel = FakeChannel()
    connection = FakeConnection(channel)
    monkeypatch.setattr(
        "pika.BlockingConnection",
        lambda parameters: connection,
    )
    publisher = PikaMediaEventPublisher(RabbitMQPublisherConfig(host="rabbitmq"))

    receipt = publisher.publish(_event())

    assert receipt.confirmed is True
    assert receipt.message_id == "event-message-1"
    assert channel.confirmed is True
    assert channel.exchange_calls == [
        {
            "exchange": MEDIA_EVENT_EXCHANGE,
            "exchange_type": "topic",
            "durable": True,
        }
    ]
    published = channel.publish_calls[0]
    assert published["routing_key"] == "task.completed"
    assert published["mandatory"] is True
    assert published["properties"].delivery_mode == 2
    assert published["properties"].message_id == "event-message-1"
    assert json.loads(published["body"])["task_id"] == "task-1"


def test_event_publisher_reconnect_retry_keeps_message_id(monkeypatch):
    first_channel = FakeChannel(fail=True)
    second_channel = FakeChannel()
    connections = iter(
        [FakeConnection(first_channel), FakeConnection(second_channel)]
    )
    monkeypatch.setattr(
        "pika.BlockingConnection",
        lambda parameters: next(connections),
    )
    publisher = PikaMediaEventPublisher(RabbitMQPublisherConfig(host="rabbitmq"))

    receipt = publisher.publish(_event())

    assert receipt.message_id == "event-message-1"
    first_payload = json.loads(first_channel.publish_calls[0]["body"])
    second_payload = json.loads(second_channel.publish_calls[0]["body"])
    assert first_payload["message_id"] == second_payload["message_id"]
