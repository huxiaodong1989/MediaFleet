"""基于 pika BlockingConnection 的可靠任务发布器。"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from threading import Lock

import pika

from media_platform.application.ports import PublishReceipt
from media_platform.contracts.task import TaskDispatchMessage
from media_platform.contracts.topology import MEDIA_TASK_EXCHANGE


LOGGER = logging.getLogger(__name__)


class PublishNotConfirmedError(RuntimeError):
    """RabbitMQ 未确认消息发布。"""


@dataclass(frozen=True)
class RabbitMQPublisherConfig:
    host: str
    exchange: str = MEDIA_TASK_EXCHANGE
    port: int = 5672
    username: str = "guest"
    password: str = "guest"
    virtual_host: str = "/"
    heartbeat: int = 60
    blocked_connection_timeout: int = 300
    socket_timeout: int = 10


class PikaTaskPublisher:
    """线程安全、持久化且启用 Publisher Confirm 的任务发布器。"""

    def __init__(self, config: RabbitMQPublisherConfig):
        self.config = config
        self._connection = None
        self._channel = None
        self._lock = Lock()

    def _connect(self) -> None:
        credentials = pika.PlainCredentials(
            self.config.username, self.config.password
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
            exchange=self.config.exchange,
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

    def _publish_once(self, message: TaskDispatchMessage) -> PublishReceipt:
        self._ensure_connection()
        confirmed = self._channel.basic_publish(
            exchange=self.config.exchange,
            routing_key=message.routing_key,
            body=message.model_dump_json().encode("utf-8"),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json",
                content_encoding="utf-8",
                message_id=message.message_id,
                type=message.task_type,
                priority=message.priority,
            ),
            mandatory=True,
        )
        if confirmed is False:
            raise PublishNotConfirmedError(
                f"RabbitMQ NACK: message_id={message.message_id}"
            )
        return PublishReceipt(message_id=message.message_id)

    def publish(self, message: TaskDispatchMessage) -> PublishReceipt:
        """发布失败时重连重试一次；消息ID保持不变以支持消费幂等。"""

        with self._lock:
            try:
                return self._publish_once(message)
            except (
                PublishNotConfirmedError,
                pika.exceptions.AMQPError,
                OSError,
            ):
                LOGGER.warning(
                    "RabbitMQ发布连接异常，使用相同message_id重连重试: %s",
                    message.message_id,
                )
                self.close()
                return self._publish_once(message)

    def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            try:
                self._connection.close()
            except pika.exceptions.AMQPError:
                LOGGER.warning("关闭RabbitMQ发布连接失败", exc_info=True)
        self._connection = None
        self._channel = None


__all__ = [
    "PikaTaskPublisher",
    "PublishNotConfirmedError",
    "RabbitMQPublisherConfig",
]
