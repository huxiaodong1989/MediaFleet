"""录制节点命令发布端口。"""

from typing import Protocol

from media_platform.application.ports.task_publisher import PublishReceipt
from media_platform.contracts.command import RecorderCommandMessage


class RecorderCommandPublisher(Protocol):
    """发布录制节点定向命令并等待 Broker Confirm。"""

    def publish(self, message: RecorderCommandMessage) -> PublishReceipt:
        """发布命令消息。"""


__all__ = ["RecorderCommandPublisher"]
