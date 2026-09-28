"""调用中心 RabbitMQ 消费组件。"""

from services.control_center.consumers.media_event_handler import (
    MediaEventHandler,
)

__all__ = ["MediaEventHandler"]
