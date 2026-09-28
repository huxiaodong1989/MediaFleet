from threading import Event
from types import SimpleNamespace

from media_platform.contracts.task import TaskDeliveryChannel, TaskDispatchMessage
from media_platform.contracts.topology import (
    CONTENT_ANALYSIS_TASK_DEAD_LETTER_EXCHANGE,
    CONTENT_ANALYSIS_TASK_EXCHANGE,
    CONTENT_ANALYSIS_TASK_QUEUE,
    CONTENT_ANALYSIS_TASK_RETRY_EXCHANGE,
)
from media_platform.infrastructure.messaging import (
    RabbitMQConsumerConfig,
    TaskRetryableError,
)
from services.content_analysis.infrastructure.task_consumer import (
    ContentAnalysisTaskConsumer,
)


class FakeChannel:
    def __init__(self):
        self.acked = []
        self.nacked = []
        self.published = []
        self.publish_confirmed = True
        self.is_closed = False

    def basic_ack(self, **kwargs):
        self.acked.append(kwargs)

    def basic_nack(self, **kwargs):
        self.nacked.append(kwargs)

    def basic_publish(self, **kwargs):
        self.published.append(kwargs)
        return self.publish_confirmed


class FakeConnection:
    def __init__(self):
        self.callbacks = []
        self.callback_ready = Event()
        self.is_closed = False

    def add_callback_threadsafe(self, callback):
        self.callbacks.append(callback)
        self.callback_ready.set()

    def run_callbacks(self):
        callbacks = list(self.callbacks)
        self.callbacks.clear()
        self.callback_ready.clear()
        for callback in callbacks:
            callback()


def _message():
    return TaskDispatchMessage(
        message_id="message-1",
        task_id="task-1",
        business_task_id="business-1",
        school_code="school-1",
        task_type="content.class_evaluation",
        routing_key="content.class_evaluation",
        delivery_channel=TaskDeliveryChannel.CONTENT_ANALYSIS,
        params={},
    )


def _consumer(handler):
    consumer = ContentAnalysisTaskConsumer(
        RabbitMQConsumerConfig(
            host="rabbitmq",
            exchange=CONTENT_ANALYSIS_TASK_EXCHANGE,
            retry_exchange=CONTENT_ANALYSIS_TASK_RETRY_EXCHANGE,
            dead_letter_exchange=CONTENT_ANALYSIS_TASK_DEAD_LETTER_EXCHANGE,
            queue_name=CONTENT_ANALYSIS_TASK_QUEUE,
            routing_keys=("content.#",),
            prefetch_count=1,
            retry_delay_milliseconds=1500,
        ),
        handler,
    )
    consumer._connection = FakeConnection()
    consumer._channel = FakeChannel()
    return consumer


def _deliver(consumer, message):
    consumer._on_message(
        consumer._channel,
        SimpleNamespace(delivery_tag=7),
        SimpleNamespace(message_id=message.message_id),
        message.model_dump_json().encode("utf-8"),
    )


def test_long_evaluation_keeps_io_thread_free_and_acks_after_completion():
    started = Event()
    release = Event()

    def handle(_message):
        started.set()
        assert release.wait(timeout=1)

    consumer = _consumer(handle)
    _deliver(consumer, _message())

    assert started.wait(timeout=1)
    assert consumer._channel.acked == []
    assert consumer._connection.callbacks == []

    release.set()
    assert consumer._connection.callback_ready.wait(timeout=1)
    consumer._connection.run_callbacks()
    assert consumer._channel.acked == [{"delivery_tag": 7}]


def test_retry_is_published_and_acked_on_original_io_channel():
    def handle(_message):
        raise TaskRetryableError("temporary")

    consumer = _consumer(handle)
    _deliver(consumer, _message())

    assert consumer._connection.callback_ready.wait(timeout=1)
    assert consumer._channel.published == []
    consumer._connection.run_callbacks()

    assert consumer._channel.published[0]["exchange"] == (
        CONTENT_ANALYSIS_TASK_RETRY_EXCHANGE
    )
    assert consumer._channel.acked == [{"delivery_tag": 7}]


def test_closed_original_connection_leaves_message_for_broker_redelivery():
    started = Event()
    release = Event()
    finished = Event()

    def handle(_message):
        started.set()
        assert release.wait(timeout=1)
        finished.set()

    consumer = _consumer(handle)
    connection = consumer._connection
    _deliver(consumer, _message())
    assert started.wait(timeout=1)

    connection.is_closed = True
    consumer._channel.is_closed = True
    release.set()

    assert finished.wait(timeout=1)
    assert not connection.callback_ready.wait(timeout=0.1)
    assert consumer._channel.acked == []
    assert consumer._channel.nacked == []
