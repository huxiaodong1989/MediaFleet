"""离线图像任务 RabbitMQ 消费器：业务线程运行，原连接I/O线程结算ACK。"""

from __future__ import annotations

from functools import partial
import logging
from threading import Thread

from pydantic import ValidationError

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import (
    PikaTaskConsumer,
    TaskBusyError,
    TaskPermanentError,
)


LOGGER = logging.getLogger(__name__)


class ImageAnalysisTaskConsumer(PikaTaskConsumer):
    def _settle(
        self,
        channel,
        delivery_tag: int,
        message: TaskDispatchMessage,
        error: Exception | None,
    ) -> None:
        if channel.is_closed:
            return  # 原连接关闭后由Broker重新投递，不在新通道复用delivery tag。
        if error is None:
            channel.basic_ack(delivery_tag=delivery_tag)
            return
        if isinstance(error, TaskBusyError):
            forward = partial(self._retry, message, increment_attempt=False)
            LOGGER.info("图像任务执行租约冲突，延迟重新检查: task_id=%s", message.task_id)
        elif (
            isinstance(error, TaskPermanentError)
            or message.attempt + 1 >= message.max_attempts
        ):
            forward = partial(self._dead_letter, message)
            LOGGER.error("图像课堂处理最终失败，转入死信: task_id=%s, error_type=%s", message.task_id, type(error).__name__)
        else:
            forward = partial(self._retry, message, increment_attempt=True)
            LOGGER.warning("图像课堂处理失败，进入延迟重试: task_id=%s, error_type=%s, next_attempt=%s", message.task_id, type(error).__name__, message.attempt + 1)
        self._forward_or_requeue(channel, delivery_tag, forward)

    def _run_handler(
        self, connection, channel, delivery_tag: int, message: TaskDispatchMessage
    ) -> None:
        error = None
        try:
            self.handler(message)
        except Exception as exc:  # noqa: BLE001 - RabbitMQ统一结算错误分类
            error = exc
        if connection is None or connection.is_closed:
            return
        try:
            connection.add_callback_threadsafe(
                partial(self._settle, channel, delivery_tag, message, error)
            )
        except Exception:
            LOGGER.exception("图像消息结算调度失败: task_id=%s", message.task_id)

    def _on_message(self, channel, method, properties, body: bytes) -> None:
        try:
            message = self._decode_message(body, properties)
        except (ValidationError, ValueError, UnicodeDecodeError):
            LOGGER.warning("无效图像任务消息转入死信")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        Thread(
            target=self._run_handler,
            args=(self._connection, channel, method.delivery_tag, message),
            name=f"image-task-{message.task_id}",
            daemon=True,
        ).start()


__all__ = ["ImageAnalysisTaskConsumer"]
