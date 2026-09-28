"""基于 pika BlockingConnection 的媒体事件可靠消费者。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from threading import Event

import pika
from pydantic import ValidationError

from media_platform.contracts.event import MediaEventMessage
from media_platform.contracts.topology import (
    MEDIA_EVENT_DEAD_LETTER_EXCHANGE,
    MEDIA_EVENT_EXCHANGE,
    MEDIA_EVENT_RETRY_EXCHANGE,
    normalized_binding_key,
    normalized_rabbitmq_name,
    queue_dead_letter_queue_name,
    queue_retry_queue_name,
)
from media_platform.infrastructure.messaging.pika_task_consumer import (
    TaskPermanentError,
    TaskRetryableError,
)


LOGGER = logging.getLogger(__name__)
EventMessageHandler = Callable[[MediaEventMessage], None]


@dataclass(frozen=True)
class RabbitMQEventConsumerConfig:
    """调用中心媒体事件队列的连接和消费配置。"""

    host: str
    queue_name: str = "control-center.media-events"
    routing_keys: tuple[str, ...] = ("task.completed", "task.failed")
    port: int = 5672
    username: str = "guest"
    password: str = "guest"
    virtual_host: str = "/"
    heartbeat: int = 120
    blocked_connection_timeout: int = 300
    socket_timeout: int = 10
    prefetch_count: int = 10
    retry_delay_milliseconds: int = 5_000
    max_attempts: int = 3

    def __post_init__(self) -> None:
        normalized_rabbitmq_name(self.queue_name, field_name="queue_name")
        if not self.routing_keys:
            raise ValueError("routing_keys 至少配置一个路由键")
        for routing_key in self.routing_keys:
            normalized_binding_key(routing_key)
        if self.prefetch_count < 1:
            raise ValueError("prefetch_count 必须大于0")
        if self.retry_delay_milliseconds < 1:
            raise ValueError("retry_delay_milliseconds 必须大于0")
        if self.max_attempts < 1:
            raise ValueError("max_attempts 必须大于0")


class PikaMediaEventConsumer:
    """消费媒体领域事件，并提供手动 ACK、延迟重试和 DLQ。"""

    def __init__(
        self,
        config: RabbitMQEventConsumerConfig,
        handler: EventMessageHandler,
    ) -> None:
        self.config = config
        self.handler = handler
        self._connection = None
        self._channel = None
        self._stop_requested = Event()

    def _connection_parameters(self) -> pika.ConnectionParameters:
        return pika.ConnectionParameters(
            host=self.config.host,
            port=self.config.port,
            virtual_host=self.config.virtual_host,
            credentials=pika.PlainCredentials(
                self.config.username,
                self.config.password,
            ),
            heartbeat=self.config.heartbeat,
            blocked_connection_timeout=self.config.blocked_connection_timeout,
            socket_timeout=self.config.socket_timeout,
        )

    def connect(self) -> None:
        """建立连接并声明调用中心事件队列、重试队列和死信队列。"""

        self.close()
        self._connection = pika.BlockingConnection(self._connection_parameters())
        self._channel = self._connection.channel()
        self._declare_topology()
        self._channel.confirm_delivery()
        self._channel.basic_qos(prefetch_count=self.config.prefetch_count)

    def _declare_topology(self) -> None:
        channel = self._channel
        queue_name = self.config.queue_name
        retry_queue = queue_retry_queue_name(queue_name)
        dead_letter_queue = queue_dead_letter_queue_name(queue_name)

        channel.exchange_declare(
            exchange=MEDIA_EVENT_EXCHANGE,
            exchange_type="topic",
            durable=True,
        )
        channel.exchange_declare(
            exchange=MEDIA_EVENT_RETRY_EXCHANGE,
            exchange_type="topic",
            durable=True,
        )
        channel.exchange_declare(
            exchange=MEDIA_EVENT_DEAD_LETTER_EXCHANGE,
            exchange_type="direct",
            durable=True,
        )
        channel.queue_declare(
            queue=queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": MEDIA_EVENT_DEAD_LETTER_EXCHANGE,
                "x-dead-letter-routing-key": queue_name,
            },
        )
        channel.queue_declare(
            queue=retry_queue,
            durable=True,
            arguments={"x-dead-letter-exchange": MEDIA_EVENT_EXCHANGE},
        )
        channel.queue_declare(queue=dead_letter_queue, durable=True)

        for routing_key in self.config.routing_keys:
            channel.queue_bind(
                exchange=MEDIA_EVENT_EXCHANGE,
                queue=queue_name,
                routing_key=routing_key,
            )
            channel.queue_bind(
                exchange=MEDIA_EVENT_RETRY_EXCHANGE,
                queue=retry_queue,
                routing_key=routing_key,
            )
        channel.queue_bind(
            exchange=MEDIA_EVENT_DEAD_LETTER_EXCHANGE,
            queue=dead_letter_queue,
            routing_key=queue_name,
        )

    @staticmethod
    def _decode_message(body: bytes, properties) -> MediaEventMessage:
        event = MediaEventMessage.model_validate_json(body)
        property_message_id = getattr(properties, "message_id", None)
        if property_message_id and property_message_id != event.message_id:
            raise ValueError("AMQP message_id 与事件信封不一致")
        return event

    @staticmethod
    def _attempt(properties) -> int:
        headers = getattr(properties, "headers", None) or {}
        try:
            return int(headers.get("x-attempt", 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _properties(event: MediaEventMessage, *, attempt: int, expiration: str | None = None):
        return pika.BasicProperties(
            delivery_mode=2,
            content_type="application/json",
            content_encoding="utf-8",
            message_id=event.message_id,
            type=event.event_type,
            expiration=expiration,
            headers={"x-attempt": attempt},
        )

    def _publish_confirmed(
        self,
        *,
        exchange: str,
        routing_key: str,
        event: MediaEventMessage,
        attempt: int,
        expiration: str | None = None,
    ) -> None:
        confirmed = self._channel.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=event.model_dump_json().encode("utf-8"),
            properties=self._properties(
                event,
                attempt=attempt,
                expiration=expiration,
            ),
            mandatory=True,
        )
        if confirmed is False:
            raise RuntimeError(
                f"RabbitMQ 未确认转发事件: message_id={event.message_id}"
            )

    def _retry(self, event: MediaEventMessage, *, attempt: int) -> None:
        self._publish_confirmed(
            exchange=MEDIA_EVENT_RETRY_EXCHANGE,
            routing_key=event.event_type,
            event=event,
            attempt=attempt + 1,
            expiration=str(self.config.retry_delay_milliseconds),
        )

    def _dead_letter(self, event: MediaEventMessage, *, attempt: int) -> None:
        self._publish_confirmed(
            exchange=MEDIA_EVENT_DEAD_LETTER_EXCHANGE,
            routing_key=self.config.queue_name,
            event=event,
            attempt=attempt,
        )

    def _on_message(self, channel, method, properties, body: bytes) -> None:
        delivery_tag = method.delivery_tag
        try:
            event = self._decode_message(body, properties)
        except (ValidationError, ValueError, UnicodeDecodeError):
            LOGGER.exception("收到不符合 media.event 契约的消息，转入队列死信")
            channel.basic_nack(delivery_tag=delivery_tag, requeue=False)
            return

        attempt = self._attempt(properties)
        try:
            self.handler(event)
        except TaskRetryableError:
            if attempt + 1 < self.config.max_attempts:
                LOGGER.warning(
                    "媒体事件处理失败，进入延迟重试: event_type=%s, task_id=%s, attempt=%s/%s",
                    event.event_type,
                    event.task_id,
                    attempt + 1,
                    self.config.max_attempts,
                    exc_info=True,
                )
                self._forward_or_requeue(
                    channel,
                    delivery_tag,
                    lambda: self._retry(event, attempt=attempt),
                )
            else:
                LOGGER.error(
                    "媒体事件达到最大尝试次数，转入死信: event_type=%s, task_id=%s",
                    event.event_type,
                    event.task_id,
                    exc_info=True,
                )
                self._forward_or_requeue(
                    channel,
                    delivery_tag,
                    lambda: self._dead_letter(event, attempt=attempt),
                )
        except TaskPermanentError:
            LOGGER.error(
                "媒体事件发生不可恢复错误，转入死信: event_type=%s, task_id=%s",
                event.event_type,
                event.task_id,
                exc_info=True,
            )
            self._forward_or_requeue(
                channel,
                delivery_tag,
                lambda: self._dead_letter(event, attempt=attempt),
            )
        except Exception:
            LOGGER.exception("媒体事件处理器出现未分类异常，按有限重试处理")
            if attempt + 1 < self.config.max_attempts:
                forward = lambda: self._retry(event, attempt=attempt)
            else:
                forward = lambda: self._dead_letter(event, attempt=attempt)
            self._forward_or_requeue(channel, delivery_tag, forward)
        else:
            channel.basic_ack(delivery_tag=delivery_tag)
            LOGGER.info(
                "媒体事件处理成功并ACK: event_type=%s, task_id=%s, message_id=%s",
                event.event_type,
                event.task_id,
                event.message_id,
            )

    @staticmethod
    def _forward_or_requeue(channel, delivery_tag: int, forward: Callable[[], None]) -> None:
        try:
            forward()
        except Exception:
            LOGGER.exception("事件重试或死信转发失败，重新入队原消息")
            channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
        else:
            channel.basic_ack(delivery_tag=delivery_tag)

    def start_consuming(self) -> None:
        self._stop_requested.clear()
        self.connect()
        self._channel.basic_consume(
            queue=self.config.queue_name,
            on_message_callback=self._on_message,
            auto_ack=False,
        )
        LOGGER.info(
            "调用中心开始消费媒体事件: queue=%s, routing_keys=%s, prefetch=%s",
            self.config.queue_name,
            self.config.routing_keys,
            self.config.prefetch_count,
        )
        self._channel.start_consuming()

    def stop(self) -> None:
        self._stop_requested.set()
        connection = self._connection
        channel = self._channel
        if connection is None or connection.is_closed or channel is None:
            return
        connection.add_callback_threadsafe(channel.stop_consuming)

    def close(self) -> None:
        connection = self._connection
        if connection is not None and not connection.is_closed:
            try:
                connection.close()
            except pika.exceptions.AMQPError:
                LOGGER.warning("关闭RabbitMQ事件消费连接失败", exc_info=True)
        self._connection = None
        self._channel = None


__all__ = [
    "PikaMediaEventConsumer",
    "RabbitMQEventConsumerConfig",
]
