"""录制节点命令消费者的拓扑、ACK、重试与死信测试。"""

import json
from types import SimpleNamespace

import pika
import pytest

from media_platform.contracts.command import (
    RecorderCommandMessage,
    RecorderCommandType,
)
from media_platform.contracts.topology import (
    MEDIA_COMMAND_DEAD_LETTER_EXCHANGE,
    MEDIA_COMMAND_EXCHANGE,
    MEDIA_COMMAND_RETRY_EXCHANGE,
)
from media_platform.infrastructure.messaging import (
    PikaRecorderCommandConsumer,
    RabbitMQCommandConsumerConfig,
    TaskPermanentError,
    TaskRetryableError,
)


class FakeChannel:
    """记录 pika Channel 调用，避免单元测试连接真实 RabbitMQ。"""

    def __init__(self):
        self.acks = []
        self.nacks = []
        self.published = []
        self.exchange_calls = []
        self.queue_calls = []
        self.bind_calls = []
        self.publish_confirmed = True

    def basic_ack(self, delivery_tag):
        self.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue=False):
        self.nacks.append((delivery_tag, requeue))

    def basic_publish(self, **kwargs):
        self.published.append(kwargs)
        return self.publish_confirmed

    def exchange_declare(self, **kwargs):
        self.exchange_calls.append(kwargs)

    def queue_declare(self, **kwargs):
        self.queue_calls.append(kwargs)

    def queue_bind(self, **kwargs):
        self.bind_calls.append(kwargs)


def _config(**overrides):
    values = {
        "host": "rabbitmq",
        "node_id": "recorder-a",
        "retry_delay_milliseconds": 100,
        "max_attempts": 2,
    }
    values.update(overrides)
    return RabbitMQCommandConsumerConfig(**values)


def _command(**overrides):
    values = {
        "message_id": "command-message-1",
        "task_id": "task-1",
        "target_node_id": "recorder-a",
        "command": RecorderCommandType.RECORD_START,
        "params": {"app": "live", "stream_id": "stream-1"},
    }
    values.update(overrides)
    return RecorderCommandMessage(**values)


def _consumer(handler, **config_overrides):
    consumer = PikaRecorderCommandConsumer(_config(**config_overrides), handler)
    consumer._channel = FakeChannel()
    return consumer


def _deliver(consumer, channel, command, *, attempt=0):
    properties = pika.BasicProperties(
        message_id=command.message_id,
        headers={"x-attempt": attempt},
    )
    consumer._channel = channel
    consumer._on_message(
        channel,
        SimpleNamespace(delivery_tag=7),
        properties,
        command.model_dump_json().encode("utf-8"),
    )


def test_command_consumer_declares_node_directed_topology():
    consumer = _consumer(lambda command: None)

    consumer._declare_topology()

    assert {item["exchange"] for item in consumer._channel.exchange_calls} == {
        MEDIA_COMMAND_EXCHANGE,
        MEDIA_COMMAND_RETRY_EXCHANGE,
        MEDIA_COMMAND_DEAD_LETTER_EXCHANGE,
    }
    assert {item["queue"] for item in consumer._channel.queue_calls} == {
        "recorder.recorder-a.commands",
        "recorder.recorder-a.commands.retry",
        "recorder.recorder-a.commands.dlq",
    }
    assert (
        {
            (item["exchange"], item["queue"], item["routing_key"])
            for item in consumer._channel.bind_calls
        }
        >= {
            (
                MEDIA_COMMAND_EXCHANGE,
                "recorder.recorder-a.commands",
                "recorder.recorder-a",
            ),
            (
                MEDIA_COMMAND_RETRY_EXCHANGE,
                "recorder.recorder-a.commands.retry",
                "recorder.recorder-a",
            ),
        }
    )


def test_command_consumer_acks_successful_command():
    calls = []
    channel = FakeChannel()
    consumer = _consumer(lambda command: calls.append(command))

    _deliver(consumer, channel, _command())

    assert calls[0].task_id == "task-1"
    assert channel.acks == [7]
    assert channel.nacks == []


def test_command_consumer_delays_retryable_failure():
    channel = FakeChannel()
    consumer = _consumer(
        lambda command: (_ for _ in ()).throw(TaskRetryableError("busy"))
    )

    _deliver(consumer, channel, _command(), attempt=0)

    assert channel.acks == [7]
    assert channel.published[0]["exchange"] == MEDIA_COMMAND_RETRY_EXCHANGE
    assert channel.published[0]["routing_key"] == "recorder.recorder-a"
    assert channel.published[0]["properties"].expiration == "100"
    assert channel.published[0]["properties"].headers["x-attempt"] == 1


def test_command_consumer_dead_letters_after_max_attempts():
    channel = FakeChannel()
    consumer = _consumer(
        lambda command: (_ for _ in ()).throw(TaskRetryableError("busy"))
    )

    _deliver(consumer, channel, _command(), attempt=1)

    assert channel.acks == [7]
    assert channel.published[0]["exchange"] == MEDIA_COMMAND_DEAD_LETTER_EXCHANGE
    assert channel.published[0]["routing_key"] == "recorder.recorder-a.commands"


def test_command_consumer_dead_letters_permanent_failure():
    channel = FakeChannel()
    consumer = _consumer(
        lambda command: (_ for _ in ()).throw(TaskPermanentError("bad command"))
    )

    _deliver(consumer, channel, _command())

    assert channel.acks == [7]
    assert channel.published[0]["exchange"] == MEDIA_COMMAND_DEAD_LETTER_EXCHANGE


def test_command_consumer_dead_letters_wrong_target_node():
    channel = FakeChannel()
    consumer = _consumer(lambda command: pytest.fail("目标节点不一致时不应处理"))

    _deliver(consumer, channel, _command(target_node_id="recorder-b"))

    assert channel.acks == [7]
    assert channel.published[0]["exchange"] == MEDIA_COMMAND_DEAD_LETTER_EXCHANGE


def test_command_consumer_requeues_original_when_forward_fails():
    channel = FakeChannel()
    channel.publish_confirmed = False
    consumer = _consumer(
        lambda command: (_ for _ in ()).throw(TaskRetryableError("busy"))
    )

    _deliver(consumer, channel, _command())

    assert channel.acks == []
    assert channel.nacks == [(7, True)]


def test_command_consumer_nacks_invalid_contract():
    channel = FakeChannel()
    consumer = _consumer(lambda command: pytest.fail("非法消息不应进入业务处理"))

    consumer._on_message(
        channel,
        SimpleNamespace(delivery_tag=7),
        pika.BasicProperties(message_id="bad"),
        json.dumps({"command": ""}).encode("utf-8"),
    )

    assert channel.acks == []
    assert channel.nacks == [(7, False)]
