"""任务消息发布端口。"""

from dataclasses import dataclass
from typing import Protocol

from media_platform.contracts.task import TaskDispatchMessage


@dataclass(frozen=True)
class PublishReceipt:
    message_id: str
    confirmed: bool = True


class TaskPublisher(Protocol):
    def publish(self, message: TaskDispatchMessage) -> PublishReceipt:
        """持久化发布消息并等待 Broker Confirm。"""


__all__ = ["PublishReceipt", "TaskPublisher"]
