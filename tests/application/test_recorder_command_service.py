"""调用中心录制命令下发服务测试。"""

import pytest

from media_platform.application import RecorderCommandDispatchService
from media_platform.application.ports import PublishReceipt


class FakeRecorderCommandPublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)
        return PublishReceipt(message_id=message.message_id)


def test_record_start_command_keeps_target_node_and_required_record_params():
    publisher = FakeRecorderCommandPublisher()
    service = RecorderCommandDispatchService(publisher)

    result = service.send_record_start(
        task_id="task-1",
        target_node_id="recorder-a",
        app="live",
        stream_id="stream-1",
        params={"record_seconds": 30},
        message_id="command-message-1",
        idempotency_key="idem-1",
    )

    assert result.receipt.message_id == "command-message-1"
    message = publisher.messages[0]
    assert message.command == "record.start"
    assert message.task_id == "task-1"
    assert message.target_node_id == "recorder-a"
    assert message.params == {
        "app": "live",
        "stream_id": "stream-1",
        "record_seconds": 30,
    }
    assert message.idempotency_key == "idem-1"


def test_record_stop_command_is_targeted_and_idempotent_by_task_id():
    publisher = FakeRecorderCommandPublisher()
    service = RecorderCommandDispatchService(publisher)

    service.send_record_stop(
        task_id="task-1",
        target_node_id="recorder-a",
        delete_from_memory=False,
        params={"reason": "manual"},
        message_id="command-message-2",
    )

    message = publisher.messages[0]
    assert message.command == "record.stop"
    assert message.task_id == "task-1"
    assert message.target_node_id == "recorder-a"
    assert message.params == {
        "reason": "manual",
        "delete_from_memory": False,
    }


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    [
        ("task_id", {"task_id": ""}),
        ("target_node_id", {"target_node_id": ""}),
        ("app", {"app": ""}),
        ("stream_id", {"stream_id": ""}),
    ],
)
def test_record_start_rejects_blank_required_fields(field_name, kwargs):
    publisher = FakeRecorderCommandPublisher()
    service = RecorderCommandDispatchService(publisher)
    values = {
        "task_id": "task-1",
        "target_node_id": "recorder-a",
        "app": "live",
        "stream_id": "stream-1",
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match=field_name):
        service.send_record_start(**values)
