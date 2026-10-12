"""图像结果独立RabbitMQ出口；不声明或投递历史实时hook队列。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from threading import Lock

import pika

from media_platform.contracts.topology import normalized_rabbitmq_name


@dataclass(frozen=True)
class ImageResultPublisherConfig:
    host: str
    username: str
    password: str
    exchange: str = "image.analysis.result"
    routing_key: str = "image.analysis.sample"
    port: int = 5672
    virtual_host: str = "/"
    heartbeat: int = 60
    socket_timeout: int = 10
    blocked_connection_timeout: int = 300

    def __post_init__(self) -> None:
        normalized_rabbitmq_name(self.exchange, field_name="exchange")
        normalized_rabbitmq_name(self.routing_key, field_name="routing_key")


class PikaImageResultPublisher:
    """持久消息、Publisher Confirm和mandatory保证无路由不当成功。"""

    def __init__(self, config: ImageResultPublisherConfig):
        self.config = config
        self._connection = None
        self._channel = None
        self._lock = Lock()

    def _connect(self) -> None:
        config = self.config
        parameters = pika.ConnectionParameters(
            host=config.host,
            port=config.port,
            virtual_host=config.virtual_host,
            credentials=pika.PlainCredentials(config.username, config.password),
            heartbeat=config.heartbeat,
            socket_timeout=config.socket_timeout,
            blocked_connection_timeout=config.blocked_connection_timeout,
        )
        self._connection = pika.BlockingConnection(parameters)
        self._channel = self._connection.channel()
        self._channel.exchange_declare(
            exchange=config.exchange,
            exchange_type="direct",
            durable=True,
        )
        self._channel.confirm_delivery()

    def publish(self, *, event_id: str, payload: dict) -> None:
        if not event_id or payload.get("eventId") != event_id:
            raise ValueError("图像通知事件编号不匹配")
        event_type = payload.get("eventType")
        if event_type not in {"SAMPLE_RESULT", "TASK_TERMINATED"}:
            raise ValueError("图像通知事件类型无效")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        with self._lock:
            if (
                self._connection is None
                or self._connection.is_closed
                or self._channel is None
                or self._channel.is_closed
            ):
                self.close()
                self._connect()
            try:
                confirmed = self._channel.basic_publish(
                    exchange=self.config.exchange,
                    routing_key=self.config.routing_key,
                    body=body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        content_type="application/json",
                        content_encoding="utf-8",
                        message_id=event_id,
                        type=(
                            "image.analysis.task.terminated"
                            if event_type == "TASK_TERMINATED"
                            else "image.analysis.sample"
                        ),
                    ),
                    mandatory=True,
                )
                if confirmed is False:
                    raise RuntimeError("图像通知发布未获Broker确认")
            except Exception:
                self.close()
                raise

    def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            try:
                self._connection.close()
            except pika.exceptions.AMQPError:
                pass
        self._connection = None
        self._channel = None


__all__ = ["ImageResultPublisherConfig", "PikaImageResultPublisher"]
