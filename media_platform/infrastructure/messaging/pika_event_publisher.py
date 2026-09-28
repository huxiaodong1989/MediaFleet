"""基于 pika BlockingConnection 的媒体领域事件发布器。"""

from __future__ import annotations

from threading import Lock

import pika

from media_platform.application.ports import PublishReceipt
from media_platform.contracts.event import MediaEventMessage
from media_platform.contracts.topology import MEDIA_EVENT_EXCHANGE
from media_platform.infrastructure.messaging.pika_task_publisher import (
    PublishNotConfirmedError,
    RabbitMQPublisherConfig,
)


class PikaMediaEventPublisher:
    """线程安全、持久化且启用 Publisher Confirm 的领域事件发布器。"""

    def __init__(self, config: RabbitMQPublisherConfig):
        self.config = config
        self._connection = None
        self._channel = None
        self._lock = Lock()

    def _connect(self) -> None:
        credentials = pika.PlainCredentials(
            self.config.username,
            self.config.password,
        )
        parameters = pika.ConnectionParameters(
            host=self.config.host,
            port=self.config.port,
            virtual_host=self.config.virtual_host,
            credentials=credentials,
            heartbeat=self.config.heartbeat,
            blocked_connection_timeout=self.config.blocked_connection_timeout,
            socket_timeout=self.config.socket_timeout,
        )
        self._connection = pika.BlockingConnection(parameters)
        self._channel = self._connection.channel()
        self._channel.exchange_declare(
            exchange=MEDIA_EVENT_EXCHANGE,
            exchange_type="topic",
            durable=True,
        )
        self._channel.confirm_delivery()

    def _ensure_connection(self) -> None:
        if (
            self._connection is None
            or self._connection.is_closed
            or self._channel is None
            or self._channel.is_closed
        ):
            self.close()
            self._connect()

    def _publish_once(self, message: MediaEventMessage) -> PublishReceipt:
        self._ensure_connection()
        confirmed = self._channel.basic_publish(
            exchange=MEDIA_EVENT_EXCHANGE,
            routing_key=message.event_type,
            body=message.model_dump_json().encode("utf-8"),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json",
                content_encoding="utf-8",
                message_id=message.message_id,
                type=message.event_type,
            ),
            mandatory=True,
        )
        if confirmed is False:
            raise PublishNotConfirmedError(
                f"RabbitMQ事件NACK: message_id={message.message_id}"
            )
        return PublishReceipt(message_id=message.message_id)

    def publish(self, message: MediaEventMessage) -> PublishReceipt:
        """发布失败时重连重试一次；事件 message_id 保持不变。"""

        with self._lock:
            try:
                return self._publish_once(message)
            except (
                PublishNotConfirmedError,
                pika.exceptions.AMQPError,
                OSError,
            ):
                self.close()
                return self._publish_once(message)

    def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            try:
                self._connection.close()
            except pika.exceptions.AMQPError:
                pass
        self._connection = None
        self._channel = None


__all__ = ["PikaMediaEventPublisher"]
