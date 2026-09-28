"""基于 pika BlockingConnection 的录制节点定向命令发布器。"""

from __future__ import annotations

import logging
from threading import Lock

import pika

from media_platform.application.ports import PublishReceipt
from media_platform.contracts.command import RecorderCommandMessage
from media_platform.contracts.topology import (
    MEDIA_COMMAND_EXCHANGE,
    recorder_command_routing_key,
)
from media_platform.infrastructure.messaging.pika_task_publisher import (
    PublishNotConfirmedError,
    RabbitMQPublisherConfig,
)


LOGGER = logging.getLogger(__name__)


class PikaRecorderCommandPublisher:
    """线程安全、持久化且启用 Publisher Confirm 的录制命令发布器。

    调用中心只声明稳定的 ``media.command`` direct exchange，然后按目标
    ``target_node_id`` 路由到 ``recorder.{node_id}``。录制节点消费者负责声明
    自己的专属队列、重试队列和 DLQ。
    """

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
            exchange=MEDIA_COMMAND_EXCHANGE,
            exchange_type="direct",
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

    def _publish_once(self, message: RecorderCommandMessage) -> PublishReceipt:
        self._ensure_connection()
        confirmed = self._channel.basic_publish(
            exchange=MEDIA_COMMAND_EXCHANGE,
            routing_key=recorder_command_routing_key(message.target_node_id),
            body=message.model_dump_json().encode("utf-8"),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json",
                content_encoding="utf-8",
                message_id=message.message_id,
                type=str(message.command),
            ),
            mandatory=True,
        )
        if confirmed is False:
            raise PublishNotConfirmedError(
                f"RabbitMQ录制命令NACK: message_id={message.message_id}"
            )
        return PublishReceipt(message_id=message.message_id)

    def publish(self, message: RecorderCommandMessage) -> PublishReceipt:
        """发布失败时重连重试一次；message_id 保持不变以支持消费幂等。"""

        with self._lock:
            try:
                return self._publish_once(message)
            except (
                PublishNotConfirmedError,
                pika.exceptions.AMQPError,
                OSError,
            ):
                LOGGER.warning(
                    "RabbitMQ录制命令发布异常，使用相同message_id重连重试: %s",
                    message.message_id,
                )
                self.close()
                return self._publish_once(message)

    def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            try:
                self._connection.close()
            except pika.exceptions.AMQPError:
                LOGGER.warning("关闭RabbitMQ录制命令发布连接失败", exc_info=True)
        self._connection = None
        self._channel = None


__all__ = ["PikaRecorderCommandPublisher"]
