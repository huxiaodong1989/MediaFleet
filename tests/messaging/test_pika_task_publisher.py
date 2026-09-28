"""RabbitMQ Publisher Confirm 适配器测试。"""

import json

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.contracts.topology import MEDIA_TASK_EXCHANGE
from media_platform.infrastructure.messaging import (
    PikaTaskPublisher,
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


def _message() -> TaskDispatchMessage:
    return TaskDispatchMessage(
        message_id="message-1",
        task_id="task-1",
        school_code="school-1",
        task_type="video.cover.extract",
        routing_key="video.cover.extract",
        params={"source": "video.mp4"},
    )


def test_publisher_declares_exchange_and_waits_for_confirm(monkeypatch):
    channel = FakeChannel()
    connection = FakeConnection(channel)
    monkeypatch.setattr(
        "pika.BlockingConnection", lambda parameters: connection
    )
    publisher = PikaTaskPublisher(RabbitMQPublisherConfig(host="rabbitmq"))

    receipt = publisher.publish(_message())

    assert receipt.confirmed is True
    assert receipt.message_id == "message-1"
    assert channel.confirmed is True
    assert channel.exchange_calls == [
        {
            "exchange": MEDIA_TASK_EXCHANGE,
            "exchange_type": "topic",
            "durable": True,
        }
    ]
    published = channel.publish_calls[0]
    assert published["mandatory"] is True
    assert published["properties"].delivery_mode == 2
    assert published["properties"].message_id == "message-1"
    assert json.loads(published["body"])["school_code"] == "school-1"


def test_reconnect_retry_keeps_same_message_id(monkeypatch):
    first_channel = FakeChannel(fail=True)
    second_channel = FakeChannel()
    connections = iter(
        [FakeConnection(first_channel), FakeConnection(second_channel)]
    )
    monkeypatch.setattr(
        "pika.BlockingConnection", lambda parameters: next(connections)
    )
    publisher = PikaTaskPublisher(RabbitMQPublisherConfig(host="rabbitmq"))

    receipt = publisher.publish(_message())

    assert receipt.message_id == "message-1"
    first_payload = json.loads(first_channel.publish_calls[0]["body"])
    second_payload = json.loads(second_channel.publish_calls[0]["body"])
    assert first_payload["message_id"] == second_payload["message_id"]
