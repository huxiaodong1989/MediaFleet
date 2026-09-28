"""AI 评课长任务专用 RabbitMQ 消费器。"""

from __future__ import annotations

from functools import partial
import logging
import sys
from threading import Thread
from types import TracebackType

from pydantic import ValidationError

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import (
    PikaTaskConsumer,
    TaskBusyError,
    TaskPermanentError,
    TaskRetryableError,
)


LOGGER = logging.getLogger(__name__)


class ContentAnalysisTaskConsumer(PikaTaskConsumer):
    """让数分钟评课任务脱离 Pika I/O 线程，保持消费连接心跳。"""

    @staticmethod
    def _channel_is_closed(channel) -> bool:
        return bool(getattr(channel, "is_closed", False))

    def _publish_on_channel(
        self,
        channel,
        *,
        exchange: str,
        routing_key: str,
        message: TaskDispatchMessage,
        expiration: str | None = None,
    ) -> None:
        confirmed = channel.basic_publish(
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

    def _retry_on_channel(
        self,
        channel,
        message: TaskDispatchMessage,
        *,
        increment_attempt: bool,
    ) -> None:
        retry_message = message.model_copy(
            update={
                "attempt": message.attempt + (1 if increment_attempt else 0)
            }
        )
        self._publish_on_channel(
            channel,
            exchange=self.config.retry_exchange,
            routing_key=message.routing_key,
            message=retry_message,
            expiration=str(self.config.retry_delay_milliseconds),
        )

    def _dead_letter_on_channel(
        self,
        channel,
        message: TaskDispatchMessage,
    ) -> None:
        self._publish_on_channel(
            channel,
            exchange=self.config.dead_letter_exchange,
            routing_key=self.config.queue_name,
            message=message,
        )

    def _settle_handler_result(
        self,
        channel,
        delivery_tag: int,
        message: TaskDispatchMessage,
        error: Exception | None,
        exc_info: tuple[type[BaseException], BaseException, TracebackType] | None,
    ) -> None:
        """在原 Pika I/O 线程中 ACK、重试或死信。"""

        if self._channel_is_closed(channel):
            LOGGER.warning(
                "AI评课结束时RabbitMQ通道已关闭，等待Broker重新投递: "
                "task_id=%s, message_id=%s",
                message.task_id,
                message.message_id,
            )
            return
        if error is None:
            channel.basic_ack(delivery_tag=delivery_tag)
            LOGGER.info(
                "AI评课消息处理成功并ACK: task_id=%s, message_id=%s",
                message.task_id,
                message.message_id,
            )
            return
        if isinstance(error, TaskBusyError):
            LOGGER.info("AI评课任务被其他实例持有，延迟检查: %s", message.task_id)
            self._forward_or_requeue(
                channel,
                delivery_tag,
                partial(
                    self._retry_on_channel,
                    channel,
                    message,
                    increment_attempt=False,
                ),
            )
            return
        if isinstance(error, TaskRetryableError):
            next_attempt = message.attempt + 1
            if next_attempt < message.max_attempts:
                LOGGER.warning(
                    "AI评课任务失败，进入第%s次延迟重试: task_id=%s",
                    next_attempt,
                    message.task_id,
                    exc_info=exc_info,
                )
                forward = partial(
                    self._retry_on_channel,
                    channel,
                    message,
                    increment_attempt=True,
                )
            else:
                LOGGER.error(
                    "AI评课任务达到最大尝试次数，转入死信: task_id=%s",
                    message.task_id,
                    exc_info=exc_info,
                )
                forward = partial(
                    self._dead_letter_on_channel,
                    channel,
                    message,
                )
            self._forward_or_requeue(channel, delivery_tag, forward)
            return
        if isinstance(error, TaskPermanentError):
            LOGGER.error(
                "AI评课任务发生不可恢复错误，转入死信: task_id=%s",
                message.task_id,
                exc_info=exc_info,
            )
            self._forward_or_requeue(
                channel,
                delivery_tag,
                partial(self._dead_letter_on_channel, channel, message),
            )
            return

        LOGGER.error(
            "AI评课处理器出现未分类异常，按有限重试处理: task_id=%s",
            message.task_id,
            exc_info=exc_info,
        )
        if message.attempt + 1 < message.max_attempts:
            forward = partial(
                self._retry_on_channel,
                channel,
                message,
                increment_attempt=True,
            )
        else:
            forward = partial(self._dead_letter_on_channel, channel, message)
        self._forward_or_requeue(channel, delivery_tag, forward)

    def _schedule_settlement(
        self,
        connection,
        channel,
        delivery_tag: int,
        message: TaskDispatchMessage,
        error: Exception | None,
        exc_info: tuple[type[BaseException], BaseException, TracebackType] | None,
    ) -> None:
        if connection is None or bool(getattr(connection, "is_closed", False)):
            LOGGER.warning(
                "AI评课结束时RabbitMQ连接已关闭，无法ACK并等待重新投递: "
                "task_id=%s, message_id=%s",
                message.task_id,
                message.message_id,
            )
            return
        try:
            connection.add_callback_threadsafe(
                partial(
                    self._settle_handler_result,
                    channel,
                    delivery_tag,
                    message,
                    error,
                    exc_info,
                )
            )
        except Exception:
            LOGGER.exception(
                "调度AI评课消息结算失败，等待Broker重新投递: "
                "task_id=%s, message_id=%s",
                message.task_id,
                message.message_id,
            )

    def _run_handler(
        self,
        connection,
        channel,
        delivery_tag: int,
        message: TaskDispatchMessage,
    ) -> None:
        error: Exception | None = None
        exc_info: tuple[type[BaseException], BaseException, TracebackType] | None = None
        try:
            self.handler(message)
        except Exception as exc:  # noqa: BLE001 - 统一在 I/O 线程分类结算
            error = exc
            captured = sys.exc_info()
            if all(item is not None for item in captured):
                exc_info = captured  # type: ignore[assignment]
        finally:
            self._schedule_settlement(
                connection,
                channel,
                delivery_tag,
                message,
                error,
                exc_info,
            )

    def _on_message(self, channel, method, properties, body: bytes) -> None:
        delivery_tag = method.delivery_tag
        try:
            message = self._decode_message(body, properties)
        except (ValidationError, ValueError, UnicodeDecodeError):
            LOGGER.exception("收到无效AI评课消息，转入队列死信")
            channel.basic_nack(delivery_tag=delivery_tag, requeue=False)
            return

        LOGGER.info(
            "收到AI评课消息: task_id=%s, message_id=%s, attempt=%s/%s",
            message.task_id,
            message.message_id,
            message.attempt,
            message.max_attempts,
        )
        Thread(
            target=self._run_handler,
            args=(self._connection, channel, delivery_tag, message),
            name=f"content-analysis-handler-{message.task_id}",
            daemon=True,
        ).start()


__all__ = ["ContentAnalysisTaskConsumer"]
