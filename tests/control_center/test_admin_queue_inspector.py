"""RabbitMQ 队列总览的 Channel 隔离测试。"""

from types import SimpleNamespace

from services.control_center.application import admin_service as admin_module
from services.control_center.application.admin_service import RabbitQueueInspector
from media_platform.infrastructure.messaging import RabbitMQPublisherConfig


class FakeChannel:
    def __init__(self) -> None:
        self.is_closed = False

    def queue_declare(self, *, queue: str, passive: bool):
        assert passive is True
        if queue == "optional.missing":
            self.is_closed = True
            raise RuntimeError("queue does not exist")
        return SimpleNamespace(
            method=SimpleNamespace(message_count=2, consumer_count=1)
        )

    def close(self) -> None:
        self.is_closed = True


class FakeConnection:
    def __init__(self, _parameters) -> None:
        self.is_closed = False
        self.channels: list[FakeChannel] = []

    def channel(self) -> FakeChannel:
        channel = FakeChannel()
        self.channels.append(channel)
        return channel

    def close(self) -> None:
        self.is_closed = True


def test_missing_queue_does_not_poison_following_queue(monkeypatch):
    connections: list[FakeConnection] = []

    def connect(parameters):
        connection = FakeConnection(parameters)
        connections.append(connection)
        return connection

    monkeypatch.setattr(admin_module.pika, "BlockingConnection", connect)
    inspector = RabbitQueueInspector(
        RabbitMQPublisherConfig(
            host="rabbitmq",
            port=5672,
            username="mediafleet",
            password="test-password",
            virtual_host="/",
        )
    )

    result = inspector.inspect(["optional.missing", "media-worker.tasks"])

    assert result[0].status == "UNAVAILABLE"
    assert result[1].status == "AVAILABLE"
    assert result[1].message_count == 2
    assert result[1].consumer_count == 1
    assert len(connections[0].channels) == 2
    assert all(channel.is_closed for channel in connections[0].channels)
    assert connections[0].is_closed is True
