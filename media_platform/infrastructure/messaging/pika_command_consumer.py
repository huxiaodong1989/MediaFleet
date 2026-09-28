"""基于 pika BlockingConnection 的录制节点定向命令消费者。

录制命令必须投递到指定录制节点，不能像通用媒体任务一样广播或共享竞争消费。
本消费者声明 ``media.command`` direct exchange、节点专属持久化队列、延迟重试
队列和最终 DLQ，并在业务处理成功后手动 ACK。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from threading import Event

import pika
from pydantic import ValidationError

from media_platform.contracts.command import RecorderCommandMessage
from media_platform.contracts.topology import (
    MEDIA_COMMAND_DEAD_LETTER_EXCHANGE,
    MEDIA_COMMAND_EXCHANGE,
    MEDIA_COMMAND_RETRY_EXCHANGE,
    normalized_rabbitmq_name,
    queue_dead_letter_queue_name,
    queue_retry_queue_name,
    recorder_command_queue_name,
    recorder_command_routing_key,
)
from media_platform.infrastructure.messaging.pika_task_consumer import (
    TaskPermanentError,
    TaskRetryableError,
)


LOGGER = logging.getLogger(__name__)
RecorderCommandHandler = Callable[[RecorderCommandMessage], None]


@dataclass(frozen=True)
class RabbitMQCommandConsumerConfig:
    """录制节点定向命令队列的连接和消费配置。"""

    host: str
    node_id: str
    port: int = 5672
    username: str = "guest"
    password: str = "guest"
    virtual_host: str = "/"
    heartbeat: int = 120
    blocked_connection_timeout: int = 300
    socket_timeout: int = 10
    prefetch_count: int = 1
    retry_delay_milliseconds: int = 5_000
    max_attempts: int = 3

    def __post_init__(self) -> None:
        normalized_rabbitmq_name(self.node_id, field_name="node_id")
        if self.prefetch_count < 1:
            raise ValueError("prefetch_count 必须大于0")
        if self.retry_delay_milliseconds < 1:
            raise ValueError("retry_delay_milliseconds 必须大于0")
        if self.max_attempts < 1:
            raise ValueError("max_attempts 必须大于0")

    @property
    def queue_name(self) -> str:
        """当前录制节点专属命令队列名。"""

        return recorder_command_queue_name(self.node_id)

    @property
    def routing_key(self) -> str:
        """当前录制节点专属命令 Routing Key。"""

        return recorder_command_routing_key(self.node_id)


class PikaRecorderCommandConsumer:
    """消费录制节点专属命令，并提供手动 ACK、延迟重试和 DLQ。"""

    def __init__(
        self,
        config: RabbitMQCommandConsumerConfig,
        handler: RecorderCommandHandler,
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
        """建立连接并声明录制节点命令队列、重试队列和死信队列。"""

        self.close()
        self._connection = pika.BlockingConnection(self._connection_parameters())
        self._channel = self._connection.channel()
        self._declare_topology()
        self._channel.confirm_delivery()
        self._channel.basic_qos(prefetch_count=self.config.prefetch_count)

    def _declare_topology(self) -> None:
        """以幂等方式声明录制节点定向命令可靠投递拓扑。"""

        channel = self._channel
        queue_name = self.config.queue_name
        retry_queue = queue_retry_queue_name(queue_name)
        dead_letter_queue = queue_dead_letter_queue_name(queue_name)

        channel.exchange_declare(
            exchange=MEDIA_COMMAND_EXCHANGE,
            exchange_type="direct",
            durable=True,
        )
        channel.exchange_declare(
            exchange=MEDIA_COMMAND_RETRY_EXCHANGE,
            exchange_type="direct",
            durable=True,
        )
        channel.exchange_declare(
            exchange=MEDIA_COMMAND_DEAD_LETTER_EXCHANGE,
            exchange_type="direct",
            durable=True,
        )
        channel.queue_declare(
            queue=queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": MEDIA_COMMAND_DEAD_LETTER_EXCHANGE,
                "x-dead-letter-routing-key": queue_name,
            },
        )
        channel.queue_declare(
            queue=retry_queue,
            durable=True,
            arguments={
                "x-dead-letter-exchange": MEDIA_COMMAND_EXCHANGE,
                "x-dead-letter-routing-key": self.config.routing_key,
            },
        )
        channel.queue_declare(queue=dead_letter_queue, durable=True)
        channel.queue_bind(
            exchange=MEDIA_COMMAND_EXCHANGE,
            queue=queue_name,
            routing_key=self.config.routing_key,
        )
        channel.queue_bind(
            exchange=MEDIA_COMMAND_RETRY_EXCHANGE,
            queue=retry_queue,
            routing_key=self.config.routing_key,
        )
        channel.queue_bind(
            exchange=MEDIA_COMMAND_DEAD_LETTER_EXCHANGE,
            queue=dead_letter_queue,
            routing_key=queue_name,
        )

    @staticmethod
    def _decode_message(body: bytes, properties) -> RecorderCommandMessage:
        """校验 JSON 契约和 AMQP message_id。"""

        command = RecorderCommandMessage.model_validate_json(body)
        property_message_id = getattr(properties, "message_id", None)
        if property_message_id and property_message_id != command.message_id:
            raise ValueError("AMQP message_id 与命令信封不一致")
        return command

    @staticmethod
    def _attempt(properties) -> int:
        headers = getattr(properties, "headers", None) or {}
        try:
            return int(headers.get("x-attempt", 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _properties(
        command: RecorderCommandMessage,
        *,
        attempt: int,
        expiration: str | None = None,
    ):
        return pika.BasicProperties(
            delivery_mode=2,
            content_type="application/json",
            content_encoding="utf-8",
            message_id=command.message_id,
            type=command.command,
            expiration=expiration,
            headers={"x-attempt": attempt},
        )

    def _publish_confirmed(
        self,
        *,
        exchange: str,
        routing_key: str,
        command: RecorderCommandMessage,
        attempt: int,
        expiration: str | None = None,
    ) -> None:
        confirmed = self._channel.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=command.model_dump_json().encode("utf-8"),
            properties=self._properties(
                command,
                attempt=attempt,
                expiration=expiration,
            ),
            mandatory=True,
        )
        if confirmed is False:
            raise RuntimeError(
                f"RabbitMQ 未确认转发录制命令: message_id={command.message_id}"
            )

    def _retry(self, command: RecorderCommandMessage, *, attempt: int) -> None:
        self._publish_confirmed(
            exchange=MEDIA_COMMAND_RETRY_EXCHANGE,
            routing_key=self.config.routing_key,
            command=command,
            attempt=attempt + 1,
            expiration=str(self.config.retry_delay_milliseconds),
        )

    def _dead_letter(self, command: RecorderCommandMessage, *, attempt: int) -> None:
        self._publish_confirmed(
            exchange=MEDIA_COMMAND_DEAD_LETTER_EXCHANGE,
            routing_key=self.config.queue_name,
            command=command,
            attempt=attempt,
        )

    def _on_message(self, channel, method, properties, body: bytes) -> None:
        delivery_tag = method.delivery_tag
        try:
            command = self._decode_message(body, properties)
        except (ValidationError, ValueError, UnicodeDecodeError):
            LOGGER.exception("收到不符合 media.command 契约的消息，转入队列死信")
            channel.basic_nack(delivery_tag=delivery_tag, requeue=False)
            return

        attempt = self._attempt(properties)
        if command.target_node_id != self.config.node_id:
            LOGGER.error(
                "命令目标节点与当前消费者不一致，转入死信: target=%s, current=%s",
                command.target_node_id,
                self.config.node_id,
            )
            self._forward_or_requeue(
                channel,
                delivery_tag,
                lambda: self._dead_letter(command, attempt=attempt),
            )
            return

        try:
            self.handler(command)
        except TaskRetryableError:
            if attempt + 1 < self.config.max_attempts:
                LOGGER.warning(
                    "录制命令处理失败，进入延迟重试: command=%s, task_id=%s, attempt=%s/%s",
                    command.command,
                    command.task_id,
                    attempt + 1,
                    self.config.max_attempts,
                    exc_info=True,
                )
                self._forward_or_requeue(
                    channel,
                    delivery_tag,
                    lambda: self._retry(command, attempt=attempt),
                )
            else:
                LOGGER.error(
                    "录制命令达到最大尝试次数，转入死信: command=%s, task_id=%s",
                    command.command,
                    command.task_id,
                    exc_info=True,
                )
                self._forward_or_requeue(
                    channel,
                    delivery_tag,
                    lambda: self._dead_letter(command, attempt=attempt),
                )
        except TaskPermanentError:
            LOGGER.error(
                "录制命令发生不可恢复错误，转入死信: command=%s, task_id=%s",
                command.command,
                command.task_id,
                exc_info=True,
            )
            self._forward_or_requeue(
                channel,
                delivery_tag,
                lambda: self._dead_letter(command, attempt=attempt),
            )
        except Exception:
            LOGGER.exception("录制命令处理器出现未分类异常，按有限重试处理")
            forward = (
                (lambda: self._retry(command, attempt=attempt))
                if attempt + 1 < self.config.max_attempts
                else (lambda: self._dead_letter(command, attempt=attempt))
            )
            self._forward_or_requeue(channel, delivery_tag, forward)
        else:
            channel.basic_ack(delivery_tag=delivery_tag)
            LOGGER.info(
                "录制命令处理成功并ACK: command=%s, task_id=%s, message_id=%s",
                command.command,
                command.task_id,
                command.message_id,
            )

    @staticmethod
    def _forward_or_requeue(channel, delivery_tag: int, forward: Callable[[], None]) -> None:
        try:
            forward()
        except Exception:
            LOGGER.exception("录制命令重试或死信转发失败，重新入队原消息")
            channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
        else:
            channel.basic_ack(delivery_tag=delivery_tag)

    def start_consuming(self) -> None:
        """连接 RabbitMQ 并阻塞消费当前录制节点专属命令队列。"""

        self._stop_requested.clear()
        self.connect()
        self._channel.basic_consume(
            queue=self.config.queue_name,
            on_message_callback=self._on_message,
            auto_ack=False,
        )
        LOGGER.info(
            "录制节点开始消费命令: node_id=%s, queue=%s, routing_key=%s, prefetch=%s",
            self.config.node_id,
            self.config.queue_name,
            self.config.routing_key,
            self.config.prefetch_count,
        )
        self._channel.start_consuming()

    def stop(self) -> None:
        """请求停止消费；跨线程调用时通过 pika 线程安全回调进入 I/O 线程。"""

        self._stop_requested.set()
        connection = self._connection
        channel = self._channel
        if connection is None or connection.is_closed or channel is None:
            return
        connection.add_callback_threadsafe(channel.stop_consuming)

    def close(self) -> None:
        """关闭 RabbitMQ 连接并清空失效引用。"""

        connection = self._connection
        if connection is not None and not connection.is_closed:
            try:
                connection.close()
            except pika.exceptions.AMQPError:
                LOGGER.warning("关闭RabbitMQ命令消费连接失败", exc_info=True)
        self._connection = None
        self._channel = None


__all__ = [
    "PikaRecorderCommandConsumer",
    "RabbitMQCommandConsumerConfig",
]
