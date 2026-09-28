"""基于 pika BlockingConnection 的通用媒体任务可靠消费者。

消费者只负责 RabbitMQ 传输语义：声明共享队列、手动 ACK、限制 Prefetch、
延迟重试和最终死信。业务幂等、MySQL 状态迁移和具体媒体处理由注入的
``handler`` 完成，从而避免基础设施层依赖 Worker 的业务实现。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from threading import Event

import pika
from pydantic import ValidationError

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.contracts.topology import (
    MEDIA_TASK_DEAD_LETTER_EXCHANGE,
    MEDIA_TASK_EXCHANGE,
    MEDIA_TASK_RETRY_EXCHANGE,
    normalized_binding_key,
    normalized_rabbitmq_name,
    worker_dead_letter_queue_name,
    worker_retry_queue_name,
)


LOGGER = logging.getLogger(__name__)
TaskMessageHandler = Callable[[TaskDispatchMessage], None]


class TaskRetryableError(RuntimeError):
    """处理失败但允许消耗一次业务重试次数。"""


class TaskBusyError(RuntimeError):
    """任务暂时被其他执行者占用；延迟再投递但不增加业务尝试次数。"""


class TaskPermanentError(RuntimeError):
    """任务参数或业务状态不可恢复，应直接进入死信队列。"""


@dataclass(frozen=True)
class RabbitMQConsumerConfig:
    """通用媒体 Worker 共享任务队列的连接和消费配置。

    所有通用 Worker 默认使用同一个持久化队列 ``media-worker.tasks`` 并绑定
    ``#``。部署多个相同配置的进程时，RabbitMQ 在这些进程间竞争分发任务；
    每条消息只会交给其中一个 Worker，而不会广播给所有实例。
    """

    host: str
    exchange: str = MEDIA_TASK_EXCHANGE
    retry_exchange: str = MEDIA_TASK_RETRY_EXCHANGE
    dead_letter_exchange: str = MEDIA_TASK_DEAD_LETTER_EXCHANGE
    queue_name: str = "media-worker.tasks"
    routing_keys: tuple[str, ...] = ("#",)
    port: int = 5672
    username: str = "guest"
    password: str = "guest"
    virtual_host: str = "/"
    heartbeat: int = 120
    blocked_connection_timeout: int = 300
    socket_timeout: int = 10
    prefetch_count: int = 1
    retry_delay_milliseconds: int = 5_000

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


class PikaTaskConsumer:
    """消费通用 Worker 共享队列，并以至少一次语义驱动任务处理器。

    处理成功后才 ACK。可恢复异常先把更新后的消息可靠发布到延迟重试队列，
    Broker Confirm 成功后再 ACK 原消息；达到最大次数或遇到永久异常时，同理
    先写入 DLQ 再 ACK。若重试或死信发布失败，则原消息 ``requeue=True``，防止
    RabbitMQ 短暂故障造成任务丢失。
    """

    def __init__(
        self,
        config: RabbitMQConsumerConfig,
        handler: TaskMessageHandler,
    ) -> None:
        self.config = config
        self.handler = handler
        self._connection = None
        self._channel = None
        self._stop_requested = Event()

    def _connection_parameters(self) -> pika.ConnectionParameters:
        """根据只读配置构造 pika 连接参数。"""

        return pika.ConnectionParameters(
            host=self.config.host,
            port=self.config.port,
            virtual_host=self.config.virtual_host,
            credentials=pika.PlainCredentials(
                self.config.username, self.config.password
            ),
            heartbeat=self.config.heartbeat,
            blocked_connection_timeout=self.config.blocked_connection_timeout,
            socket_timeout=self.config.socket_timeout,
        )

    def connect(self) -> None:
        """建立消费连接并声明共享主队列、重试队列和死信队列。"""

        self.close()
        self._connection = pika.BlockingConnection(self._connection_parameters())
        self._channel = self._connection.channel()
        self._declare_topology()
        self._channel.confirm_delivery()
        self._channel.basic_qos(prefetch_count=self.config.prefetch_count)

    def _declare_topology(self) -> None:
        """以幂等方式声明共享 Worker 队列需要的可靠投递拓扑。"""

        channel = self._channel
        queue_name = self.config.queue_name
        retry_queue = worker_retry_queue_name(queue_name)
        dead_letter_queue = worker_dead_letter_queue_name(queue_name)

        channel.exchange_declare(
            exchange=self.config.exchange, exchange_type="topic", durable=True
        )
        channel.exchange_declare(
            exchange=self.config.retry_exchange,
            exchange_type="topic",
            durable=True,
        )
        channel.exchange_declare(
            exchange=self.config.dead_letter_exchange,
            exchange_type="direct",
            durable=True,
        )
        channel.queue_declare(
            queue=queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": self.config.dead_letter_exchange,
                "x-dead-letter-routing-key": queue_name,
                "x-max-priority": 255,
            },
        )
        channel.queue_declare(
            queue=retry_queue,
            durable=True,
            arguments={"x-dead-letter-exchange": self.config.exchange},
        )
        channel.queue_declare(queue=dead_letter_queue, durable=True)

        for routing_key in self.config.routing_keys:
            channel.queue_bind(
                exchange=self.config.exchange,
                queue=queue_name,
                routing_key=routing_key,
            )
            # 延迟队列保留原 Routing Key；TTL 到期后 Broker 使用原键回到主 Exchange。
            channel.queue_bind(
                exchange=self.config.retry_exchange,
                queue=retry_queue,
                routing_key=routing_key,
            )
        channel.queue_bind(
            exchange=self.config.dead_letter_exchange,
            queue=dead_letter_queue,
            routing_key=queue_name,
        )

    @staticmethod
    def _decode_message(body: bytes, properties) -> TaskDispatchMessage:
        """校验 UTF-8 JSON 契约以及 AMQP 属性与信封消息编号的一致性。"""

        message = TaskDispatchMessage.model_validate_json(body)
        property_message_id = getattr(properties, "message_id", None)
        if property_message_id and property_message_id != message.message_id:
            raise ValueError("AMQP message_id 与消息信封不一致")
        return message

    @staticmethod
    def _properties(message: TaskDispatchMessage, *, expiration: str | None = None):
        """构造重试或死信消息使用的持久化 AMQP 属性。"""

        return pika.BasicProperties(
            delivery_mode=2,
            content_type="application/json",
            content_encoding="utf-8",
            message_id=message.message_id,
            type=message.task_type,
            priority=message.priority,
            expiration=expiration,
        )

    def _publish_confirmed(
        self,
        *,
        exchange: str,
        routing_key: str,
        message: TaskDispatchMessage,
        expiration: str | None = None,
    ) -> None:
        """发布持久化消息并要求 Broker Confirm，未确认时抛出异常。"""

        confirmed = self._channel.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=message.model_dump_json().encode("utf-8"),
            properties=self._properties(message, expiration=expiration),
            mandatory=True,
        )
        if confirmed is False:
            raise RuntimeError(
                f"RabbitMQ 未确认转发消息: message_id={message.message_id}"
            )

    def _retry(self, message: TaskDispatchMessage, *, increment_attempt: bool) -> None:
        """把任务发送到延迟队列，必要时增加消息中的业务尝试次数。"""

        next_attempt = message.attempt + (1 if increment_attempt else 0)
        retry_message = message.model_copy(update={"attempt": next_attempt})
        self._publish_confirmed(
            exchange=self.config.retry_exchange,
            routing_key=message.routing_key,
            message=retry_message,
            expiration=str(self.config.retry_delay_milliseconds),
        )

    def _dead_letter(self, message: TaskDispatchMessage) -> None:
        """显式发布到共享 Worker 队列的 DLQ，保留完整任务契约供排障。"""

        self._publish_confirmed(
            exchange=self.config.dead_letter_exchange,
            routing_key=self.config.queue_name,
            message=message,
        )

    def _on_message(self, channel, method, properties, body: bytes) -> None:
        """执行单条消息并在每条控制路径上明确 ACK、重试或死信。"""

        delivery_tag = method.delivery_tag
        try:
            message = self._decode_message(body, properties)
        except (ValidationError, ValueError, UnicodeDecodeError):
            LOGGER.exception("收到不符合 media.task 契约的消息，转入队列死信")
            channel.basic_nack(delivery_tag=delivery_tag, requeue=False)
            return
        LOGGER.info(
            "收到媒体任务消息: task_id=%s, message_id=%s, task_type=%s, "
            "routing_key=%s, attempt=%s/%s",
            message.task_id,
            message.message_id,
            message.task_type,
            message.routing_key,
            message.attempt,
            message.max_attempts,
        )

        try:
            self.handler(message)
        except TaskBusyError:
            LOGGER.info("任务正在其他执行者处理中，延迟重新检查: %s", message.task_id)
            self._forward_or_requeue(
                channel, delivery_tag, lambda: self._retry(message, increment_attempt=False)
            )
        except TaskRetryableError:
            next_attempt = message.attempt + 1
            if next_attempt < message.max_attempts:
                LOGGER.warning(
                    "任务处理失败，进入第%s次延迟重试: task_id=%s",
                    next_attempt,
                    message.task_id,
                    exc_info=True,
                )
                self._forward_or_requeue(
                    channel,
                    delivery_tag,
                    lambda: self._retry(message, increment_attempt=True),
                )
            else:
                LOGGER.error(
                    "任务达到最大尝试次数，转入死信: task_id=%s",
                    message.task_id,
                    exc_info=True,
                )
                self._forward_or_requeue(
                    channel, delivery_tag, lambda: self._dead_letter(message)
                )
        except TaskPermanentError:
            LOGGER.error(
                "任务发生不可恢复错误，转入死信: task_id=%s",
                message.task_id,
                exc_info=True,
            )
            self._forward_or_requeue(
                channel, delivery_tag, lambda: self._dead_letter(message)
            )
        except Exception:
            # 未分类异常按可恢复错误处理，避免新增处理器遗漏异常分类时直接丢任务。
            LOGGER.exception("任务处理器出现未分类异常，按有限重试处理")
            next_attempt = message.attempt + 1
            forward = (
                (lambda: self._retry(message, increment_attempt=True))
                if next_attempt < message.max_attempts
                else (lambda: self._dead_letter(message))
            )
            self._forward_or_requeue(channel, delivery_tag, forward)
        else:
            channel.basic_ack(delivery_tag=delivery_tag)
            LOGGER.info(
                "媒体任务消息处理成功并ACK: task_id=%s, message_id=%s",
                message.task_id,
                message.message_id,
            )

    @staticmethod
    def _forward_or_requeue(channel, delivery_tag: int, forward: Callable[[], None]) -> None:
        """先可靠转发再 ACK；转发失败时让 Broker 重新投递原消息。"""

        try:
            forward()
        except Exception:
            LOGGER.exception("重试或死信转发失败，重新入队原消息")
            channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
        else:
            channel.basic_ack(delivery_tag=delivery_tag)

    def start_consuming(self) -> None:
        """连接 RabbitMQ 并阻塞消费，通常在 Worker 专用线程中调用。"""

        self._stop_requested.clear()
        self.connect()
        self._channel.basic_consume(
            queue=self.config.queue_name,
            on_message_callback=self._on_message,
            auto_ack=False,
        )
        LOGGER.info(
            "媒体Worker开始消费: queue=%s, routing_keys=%s, prefetch=%s",
            self.config.queue_name,
            self.config.routing_keys,
            self.config.prefetch_count,
        )
        self._channel.start_consuming()

    def stop(self) -> None:
        """请求停止消费；跨线程调用时通过 pika 的线程安全回调进入 I/O 线程。"""

        self._stop_requested.set()
        connection = self._connection
        channel = self._channel
        if connection is None or connection.is_closed or channel is None:
            return
        connection.add_callback_threadsafe(channel.stop_consuming)

    def close(self) -> None:
        """关闭 RabbitMQ 连接并清空失效的连接、通道引用。"""

        connection = self._connection
        if connection is not None and not connection.is_closed:
            try:
                connection.close()
            except pika.exceptions.AMQPError:
                LOGGER.warning("关闭RabbitMQ消费连接失败", exc_info=True)
        self._connection = None
        self._channel = None


__all__ = [
    "PikaTaskConsumer",
    "RabbitMQConsumerConfig",
    "TaskBusyError",
    "TaskPermanentError",
    "TaskRetryableError",
]
