"""通用媒体任务消费者的拓扑、ACK、重试与死信测试。"""

from types import SimpleNamespace

import pytest

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.contracts.topology import (
    MEDIA_TASK_DEAD_LETTER_EXCHANGE,
    MEDIA_TASK_RETRY_EXCHANGE,
)
from media_platform.infrastructure.messaging import (
    PikaTaskConsumer,
    RabbitMQConsumerConfig,
    TaskBusyError,
    TaskPermanentError,
    TaskRetryableError,
)


class FakeChannel:
    """记录 pika Channel 调用，避免单元测试连接真实 RabbitMQ。"""

    def __init__(self):
        self.exchange_declarations = []
        self.queue_declarations = []
        self.bindings = []
        self.published = []
        self.acked = []
        self.nacked = []
        self.publish_confirmed = True

    def exchange_declare(self, **kwargs):
        self.exchange_declarations.append(kwargs)

    def queue_declare(self, **kwargs):
        self.queue_declarations.append(kwargs)

    def queue_bind(self, **kwargs):
        self.bindings.append(kwargs)

    def basic_publish(self, **kwargs):
        self.published.append(kwargs)
        return self.publish_confirmed

    def basic_ack(self, **kwargs):
        self.acked.append(kwargs)

    def basic_nack(self, **kwargs):
        self.nacked.append(kwargs)


def _config(**overrides):
    values = {
        "host": "rabbitmq",
        "prefetch_count": 4,
        "retry_delay_milliseconds": 1500,
    }
    values.update(overrides)
    return RabbitMQConsumerConfig(**values)


def _message(*, attempt=0, max_attempts=3):
    return TaskDispatchMessage(
        message_id="message-1",
        task_id="task-1",
        school_code="school-1",
        task_type="video.cover.extract",
        routing_key="video.cover.extract",
        params={"video_url": "https://example.invalid/video.mp4"},
        attempt=attempt,
        max_attempts=max_attempts,
    )


def _delivery(message):
    return (
        SimpleNamespace(delivery_tag=7),
        SimpleNamespace(message_id=message.message_id),
        message.model_dump_json().encode("utf-8"),
    )


def _consumer(handler):
    consumer = PikaTaskConsumer(_config(), handler)
    consumer._channel = FakeChannel()
    return consumer


def test_declares_shared_worker_retry_and_dead_letter_queues():
    consumer = _consumer(lambda message: None)

    consumer._declare_topology()

    queue_names = {
        declaration["queue"] for declaration in consumer._channel.queue_declarations
    }
    assert queue_names == {
        "media-worker.tasks",
        "media-worker.tasks.retry",
        "media-worker.tasks.dlq",
    }
    assert {
        declaration["exchange"]
        for declaration in consumer._channel.exchange_declarations
    } >= {MEDIA_TASK_RETRY_EXCHANGE, MEDIA_TASK_DEAD_LETTER_EXCHANGE}


    assert {
        (binding["exchange"], binding["queue"], binding["routing_key"])
        for binding in consumer._channel.bindings
    } >= {
        ("media.task", "media-worker.tasks", "#"),
        ("media.task.retry", "media-worker.tasks.retry", "#"),
    }


def test_shared_worker_queue_uses_catch_all_binding_by_default():
    config = _config()

    assert config.queue_name == "media-worker.tasks"
    assert config.routing_keys == ("#",)


def test_acknowledges_only_after_handler_success():
    handled = []
    consumer = _consumer(handled.append)
    message = _message()
    method, properties, body = _delivery(message)

    consumer._on_message(consumer._channel, method, properties, body)

    assert handled == [message]
    assert consumer._channel.acked == [{"delivery_tag": 7}]
    assert consumer._channel.nacked == []


@pytest.mark.parametrize(
    ("error", "expected_attempt"),
    [
        (TaskRetryableError("temporary"), 1),
        (TaskBusyError("owned by another worker"), 0),
    ],
)
def test_retryable_and_busy_tasks_use_confirmed_delay_queue(
    error, expected_attempt
):
    def fail(_message):
        raise error

    consumer = _consumer(fail)
    message = _message()
    method, properties, body = _delivery(message)

    consumer._on_message(consumer._channel, method, properties, body)

    publish = consumer._channel.published[0]
    retry_message = TaskDispatchMessage.model_validate_json(publish["body"])
    assert publish["exchange"] == MEDIA_TASK_RETRY_EXCHANGE
    assert publish["properties"].expiration == "1500"
    assert retry_message.attempt == expected_attempt
    assert consumer._channel.acked == [{"delivery_tag": 7}]


@pytest.mark.parametrize(
    "error",
    [TaskPermanentError("bad params"), TaskRetryableError("last attempt")],
)
def test_permanent_or_exhausted_task_is_written_to_dlq(error):
    def fail(_message):
        raise error

    consumer = _consumer(fail)
    message = _message(attempt=2, max_attempts=3)
    method, properties, body = _delivery(message)

    consumer._on_message(consumer._channel, method, properties, body)

    assert consumer._channel.published[0]["exchange"] == (
        MEDIA_TASK_DEAD_LETTER_EXCHANGE
    )
    assert consumer._channel.acked == [{"delivery_tag": 7}]


def test_forward_failure_requeues_original_message_without_ack():
    def fail(_message):
        raise TaskRetryableError("temporary")

    consumer = _consumer(fail)
    consumer._channel.publish_confirmed = False
    message = _message()
    method, properties, body = _delivery(message)

    consumer._on_message(consumer._channel, method, properties, body)

    assert consumer._channel.acked == []
    assert consumer._channel.nacked == [
        {"delivery_tag": 7, "requeue": True}
    ]


def test_invalid_contract_is_rejected_to_broker_dead_letter_path():
    consumer = _consumer(lambda message: None)
    method = SimpleNamespace(delivery_tag=9)
    properties = SimpleNamespace(message_id="invalid")

    consumer._on_message(consumer._channel, method, properties, b"not-json")

    assert consumer._channel.acked == []
    assert consumer._channel.nacked == [
        {"delivery_tag": 9, "requeue": False}
    ]
