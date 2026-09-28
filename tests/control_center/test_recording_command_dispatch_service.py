"""录制命令持久意图补发测试。"""

from datetime import timedelta

from media_platform.application import RecorderCommandDispatchService
from media_platform.application.ports import PublishReceipt
from services.control_center.application.recording_command_dispatch_service import (
    DurableRecordingCommandDispatchService,
)
from services.control_center.application.recording_task_state_service import (
    PendingRecordingCommand,
)


class StateService:
    def __init__(self, commands):
        self.commands = commands
        self.published = []
        self.released = []

    def claim_pending_commands(self, instance_id, *, limit, lock_timeout):
        return self.commands[:limit]

    def mark_command_published(self, **kwargs):
        self.published.append(kwargs)
        return True

    def release_command_claim(self, **kwargs):
        self.released.append(kwargs)

    def mark_command_failed(self, **kwargs):
        self.failed = kwargs


class Publisher:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def publish(self, message):
        if self.fail:
            raise RuntimeError("rabbit unavailable")
        self.messages.append(message)
        return PublishReceipt(message_id=message.message_id)


def _command(command="record.start"):
    return PendingRecordingCommand(
        task_id="record-1",
        command=command,
        target_node_id="recorder-a",
        message_id=f"record-1:{command}",
        params={"app": "live", "stream_id": "stream-1", "reason": "manual_stop"},
    )


def test_persisted_start_command_is_published_and_confirmed():
    state = StateService([_command()])
    publisher = Publisher()
    service = DurableRecordingCommandDispatchService(
        state, RecorderCommandDispatchService(publisher)
    )

    result = service.dispatch_batch("center-a", lock_timeout=timedelta(seconds=10))

    assert result.claimed == 1
    assert result.published == 1
    assert publisher.messages[0].message_id == "record-1:record.start"
    assert state.published[0]["task_id"] == "record-1"


def test_publish_failure_releases_claim_for_another_center():
    state = StateService([_command("record.stop")])
    service = DurableRecordingCommandDispatchService(
        state, RecorderCommandDispatchService(Publisher(fail=True))
    )

    result = service.dispatch_batch("center-a")

    assert result.failed_task_ids == ("record-1",)
    assert state.released[0]["instance_id"] == "center-a"


def test_invalid_persisted_command_is_not_retried_forever():
    command = PendingRecordingCommand(
        task_id="record-invalid",
        command="record.start",
        target_node_id="",
        message_id="record-invalid:record.start",
        params={"app": "live"},
    )
    state = StateService([command])
    publisher = Publisher()
    service = DurableRecordingCommandDispatchService(
        state, RecorderCommandDispatchService(publisher)
    )

    result = service.dispatch_batch("center-a")

    assert result.failed_task_ids == ("record-invalid",)
    assert publisher.messages == []
    assert state.failed["task_id"] == "record-invalid"
