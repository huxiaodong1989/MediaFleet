"""媒体领域事件发布端口。"""

from typing import Protocol

from media_platform.application.ports.task_publisher import PublishReceipt
from media_platform.contracts.event import MediaEventMessage


class MediaEventPublisher(Protocol):
    """发布媒体领域事件并等待 Broker Confirm。"""

    def publish(self, message: MediaEventMessage) -> PublishReceipt:
        """发布领域事件。"""


__all__ = ["MediaEventPublisher"]
