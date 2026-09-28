"""RabbitMQ 媒体事件消费者传输语义测试。"""

import json
from types import SimpleNamespace

import pika
import pytest

from media_platform.contracts.event import MediaEventMessage
from media_platform.contracts.topology import (
    CONTROL_CENTER_EVENT_QUEUE,
    MEDIA_EVENT_DEAD_LETTER_EXCHANGE,
    MEDIA_EVENT_EXCHANGE,
    MEDIA_EVENT_RETRY_EXCHANGE,
)
from media_platform.infrastructure.messaging import (
    PikaMediaEventConsumer,
    RabbitMQEventConsumerConfig,
    TaskPermanentError,
    TaskRetryableError,
)


class FakeChannel:
    def __init__(self):
        self.acks = []
        self.nacks = []
        self.published = []

    def basic_ack(self, delivery_tag):
        self.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue=False):
        self.nacks.append((delivery_tag, requeue))

    def basic_publish(self, **kwargs):
        self.published.append(kwargs)
        return True


def _event() -> MediaEventMessage:
    return MediaEventMessage(
        message_id="event-message-1",
        source="media-worker:worker-a",
        event_type="task.completed",
        aggregate_id="task-1",
        task_id="task-1",
    )


def _consumer(handler):
    return PikaMediaEventConsumer(
        RabbitMQEventConsumerConfig(
            host="rabbitmq",
            retry_delay_milliseconds=100,
            max_attempts=2,
        ),
        handler,
    )


def _deliver(consumer, channel, event, *, attempt=0):
    properties = pika.BasicProperties(
        message_id=event.message_id,
        headers={"x-attempt": attempt},
    )
    consumer._channel = channel
    consumer._on_message(
        channel,
        SimpleNamespace(delivery_tag=7),
        properties,
        event.model_dump_json().encode("utf-8"),
    )


def test_event_consumer_acks_successful_event():
    calls = []
    channel = FakeChannel()
    consumer = _consumer(lambda event: calls.append(event))

    _deliver(consumer, channel, _event())

    assert calls[0].task_id == "task-1"
    assert channel.acks == [7]
    assert channel.nacks == []


def test_event_consumer_delays_retryable_failure():
    channel = FakeChannel()
    consumer = _consumer(lambda event: (_ for _ in ()).throw(TaskRetryableError("busy")))

    _deliver(consumer, channel, _event(), attempt=0)

    assert channel.acks == [7]
    assert channel.published[0]["exchange"] == MEDIA_EVENT_RETRY_EXCHANGE
    assert channel.published[0]["routing_key"] == "task.completed"
    assert channel.published[0]["properties"].expiration == "100"
    assert channel.published[0]["properties"].headers["x-attempt"] == 1


def test_event_consumer_dead_letters_after_max_attempts():
    channel = FakeChannel()
    consumer = _consumer(lambda event: (_ for _ in ()).throw(TaskRetryableError("busy")))

    _deliver(consumer, channel, _event(), attempt=1)

    assert channel.acks == [7]
    assert channel.published[0]["exchange"] == MEDIA_EVENT_DEAD_LETTER_EXCHANGE
    assert channel.published[0]["routing_key"] == CONTROL_CENTER_EVENT_QUEUE


def test_event_consumer_nacks_invalid_contract():
    channel = FakeChannel()
    consumer = _consumer(lambda event: pytest.fail("非法消息不应进入业务处理"))

    consumer._on_message(
        channel,
        SimpleNamespace(delivery_tag=7),
        pika.BasicProperties(message_id="bad"),
        json.dumps({"event_type": ""}).encode("utf-8"),
    )

    assert channel.nacks == [(7, False)]


def test_event_consumer_declares_event_topology(monkeypatch):
    class TopologyChannel(FakeChannel):
        def __init__(self):
            super().__init__()
            self.is_closed = False
            self.exchange_calls = []
            self.queue_calls = []
            self.bind_calls = []

        def exchange_declare(self, **kwargs):
            self.exchange_calls.append(kwargs)

        def queue_declare(self, **kwargs):
            self.queue_calls.append(kwargs)

        def queue_bind(self, **kwargs):
            self.bind_calls.append(kwargs)

        def confirm_delivery(self):
            pass

        def basic_qos(self, **kwargs):
            pass

    class FakeConnection:
        is_closed = False

        def __init__(self, channel):
            self._channel = channel

        def channel(self):
            return self._channel

        def close(self):
            self.is_closed = True

    channel = TopologyChannel()
    monkeypatch.setattr(
        "pika.BlockingConnection",
        lambda parameters: FakeConnection(channel),
    )

    _consumer(lambda event: None).connect()

    assert {item["exchange"] for item in channel.exchange_calls} == {
        MEDIA_EVENT_EXCHANGE,
        MEDIA_EVENT_RETRY_EXCHANGE,
        MEDIA_EVENT_DEAD_LETTER_EXCHANGE,
    }
    assert {item["queue"] for item in channel.queue_calls} == {
        CONTROL_CENTER_EVENT_QUEUE,
        f"{CONTROL_CENTER_EVENT_QUEUE}.retry",
        f"{CONTROL_CENTER_EVENT_QUEUE}.dlq",
    }
