"""录制节点命令 Publisher Confirm 适配器测试。"""

import json

import pytest

from media_platform.contracts.command import (
    RecorderCommandMessage,
    RecorderCommandType,
)
from media_platform.contracts.topology import MEDIA_COMMAND_EXCHANGE
from media_platform.infrastructure.messaging import (
    PikaRecorderCommandPublisher,
    RabbitMQPublisherConfig,
)
from media_platform.infrastructure.messaging.pika_task_publisher import (
    PublishNotConfirmedError,
)


class FakeChannel:
    """记录 pika Channel 调用，避免测试连接真实 RabbitMQ。"""

    def __init__(self, *, fail=False, nack=False):
        self.is_closed = False
        self.fail = fail
        self.nack = nack
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
        if self.nack:
            return False
        return True


class FakeConnection:
    def __init__(self, channel):
        self.is_closed = False
        self._channel = channel

    def channel(self):
        return self._channel

    def close(self):
        self.is_closed = True


def _message() -> RecorderCommandMessage:
    return RecorderCommandMessage(
        message_id="command-message-1",
        task_id="task-1",
        target_node_id="recorder-a",
        command=RecorderCommandType.RECORD_START,
        params={"app": "live", "stream_id": "stream-1"},
    )


def test_command_publisher_declares_direct_exchange_and_routes_to_target_node(
    monkeypatch,
):
    channel = FakeChannel()
    monkeypatch.setattr(
        "pika.BlockingConnection",
        lambda parameters: FakeConnection(channel),
    )
    publisher = PikaRecorderCommandPublisher(
        RabbitMQPublisherConfig(host="rabbitmq"),
    )

    receipt = publisher.publish(_message())

    assert receipt.confirmed is True
    assert receipt.message_id == "command-message-1"
    assert channel.confirmed is True
    assert channel.exchange_calls == [
        {
            "exchange": MEDIA_COMMAND_EXCHANGE,
            "exchange_type": "direct",
            "durable": True,
        }
    ]
    published = channel.publish_calls[0]
    assert published["exchange"] == MEDIA_COMMAND_EXCHANGE
    assert published["routing_key"] == "recorder.recorder-a"
    assert published["mandatory"] is True
    assert published["properties"].delivery_mode == 2
    assert published["properties"].content_type == "application/json"
    assert published["properties"].message_id == "command-message-1"
    assert published["properties"].type == "record.start"
    payload = json.loads(published["body"])
    assert payload["target_node_id"] == "recorder-a"
    assert payload["command"] == "record.start"


def test_command_publisher_reconnect_retry_keeps_same_message_id(monkeypatch):
    first_channel = FakeChannel(fail=True)
    second_channel = FakeChannel()
    connections = iter(
        [FakeConnection(first_channel), FakeConnection(second_channel)]
    )
    monkeypatch.setattr(
        "pika.BlockingConnection",
        lambda parameters: next(connections),
    )
    publisher = PikaRecorderCommandPublisher(
        RabbitMQPublisherConfig(host="rabbitmq"),
    )

    receipt = publisher.publish(_message())

    assert receipt.message_id == "command-message-1"
    first_payload = json.loads(first_channel.publish_calls[0]["body"])
    second_payload = json.loads(second_channel.publish_calls[0]["body"])
    assert first_payload["message_id"] == second_payload["message_id"]


def test_command_publisher_raises_when_retry_is_not_confirmed(monkeypatch):
    channels = iter([FakeChannel(nack=True), FakeChannel(nack=True)])
    monkeypatch.setattr(
        "pika.BlockingConnection",
        lambda parameters: FakeConnection(next(channels)),
    )
    publisher = PikaRecorderCommandPublisher(
        RabbitMQPublisherConfig(host="rabbitmq"),
    )

    with pytest.raises(PublishNotConfirmedError, match="录制命令NACK"):
        publisher.publish(_message())
