"""按任务投递通道选择 RabbitMQ 发布器。"""

from __future__ import annotations

from collections.abc import Mapping

from media_platform.application.ports import PublishReceipt, TaskPublisher
from media_platform.contracts.task import TaskDeliveryChannel, TaskDispatchMessage


class ChannelTaskPublisher:
    """保持任务发布应用服务稳定，同时隔离媒体与内容分析 Exchange。"""

    def __init__(self, publishers: Mapping[TaskDeliveryChannel, TaskPublisher]) -> None:
        self._publishers = dict(publishers)
        missing = set(TaskDeliveryChannel) - set(self._publishers)
        if missing:
            raise ValueError(f"缺少任务投递通道发布器: {sorted(item.value for item in missing)}")

    def publish(self, message: TaskDispatchMessage) -> PublishReceipt:
        channel = TaskDeliveryChannel(message.delivery_channel)
        return self._publishers[channel].publish(message)

    def close(self) -> None:
        closed: set[int] = set()
        for publisher in self._publishers.values():
            identity = id(publisher)
            if identity in closed:
                continue
            closed.add(identity)
            close = getattr(publisher, "close", None)
            if callable(close):
                close()


__all__ = ["ChannelTaskPublisher"]
