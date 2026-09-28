"""录制结果 RabbitMQ 通知器。

录制完成后的结果通知属于 recorder-node 本机链路：录制节点在本机完成停止 ZL、
查找碎片、合并、上传和落库后，把结果通过 RabbitMQ 通知 RTC 业务。调用中心不
参与该链路。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from media_platform.infrastructure.messaging.legacy_rabbitmq import RabbitMQMaster

LOGGER = logging.getLogger(__name__)


class RecorderResultNotifier:
    """负责把 recorder-node 录制结果发布到 RabbitMQ。

    当前仍复用历史 ``RabbitMQMaster.publish_to_fanout_exchange`` 适配器，保持
    既有交换机、TTL 和消息体不变。该类把旧适配器隔离在 recorder-node 私有组件中，
    后续替换为新的消息契约时只需要改这里，不再让 ``StreamRecorder`` 大类直接持有
    RabbitMQ 连接细节。
    """

    def __init__(
        self,
        *,
        exchange_name: str,
        ttl_milliseconds: int,
        publisher: Any | None,
    ) -> None:
        self.exchange_name = exchange_name
        self.ttl_milliseconds = ttl_milliseconds
        self.publisher = publisher

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        publisher_factory: Callable[..., Any] = RabbitMQMaster,
    ) -> "RecorderResultNotifier":
        """按运行配置创建通知器。

        RabbitMQ 适配器保持懒连接；初始化失败时返回禁用状态的通知器，避免影响
        录制完成后的 HTTP 回调和本地状态收尾。
        """

        exchange_name = settings.rabbitmq.record_result_exchange
        ttl_milliseconds = settings.rabbitmq.record_result_ttl
        try:
            publisher = publisher_factory(
                host=settings.rabbitmq.host,
                port=settings.rabbitmq.port,
                username=settings.rabbitmq.username,
                password=settings.rabbitmq.password,
                exchange_name=exchange_name,
                exchange_type="fanout",
            )
            LOGGER.info(
                "录制结果 RabbitMQ 通知器已初始化（懒连接）: exchange=%s",
                exchange_name,
            )
        except Exception as exc:
            LOGGER.error("录制结果 RabbitMQ 通知器初始化失败: %s", exc)
            publisher = None

        return cls(
            exchange_name=exchange_name,
            ttl_milliseconds=ttl_milliseconds,
            publisher=publisher,
        )

    def publish(self, *, task_id: str, message_data: dict[str, Any]) -> bool:
        """发布录制结果消息。

        :return: True 表示已交给 RabbitMQ 适配器；False 表示未发布或发布失败。
        """

        if self.publisher is None:
            LOGGER.warning("RabbitMQ 通知器不可用，跳过录制结果消息: task_id=%s", task_id)
            return False

        try:
            self.publisher.publish_to_fanout_exchange(
                exchange_name=self.exchange_name,
                message_data=message_data,
                ttl_milliseconds=self.ttl_milliseconds,
            )
            LOGGER.info(
                "录制结果已发送到 RabbitMQ: task_id=%s, exchange=%s, ttl=%sms",
                task_id,
                self.exchange_name,
                self.ttl_milliseconds,
            )
            return True
        except Exception as exc:
            LOGGER.error("发送录制结果到 RabbitMQ 失败: task_id=%s, error=%s", task_id, exc)
            return False
