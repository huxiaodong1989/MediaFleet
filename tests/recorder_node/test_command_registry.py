"""录制节点命令注册表测试。"""

import pytest

from media_platform.contracts.command import (
    RecorderCommandMessage,
    RecorderCommandType,
)
from services.recorder_node.commands import (
    DuplicateCommandHandlerError,
    RecorderCommandRegistry,
    UnsupportedRecorderCommandError,
)


class FakeHandler:
    def __init__(self):
        self.calls = []

    def handle(self, message):
        self.calls.append(message)


def _message(command=RecorderCommandType.RECORD_START):
    return RecorderCommandMessage(
        task_id="task-1",
        target_node_id="recorder-a",
        command=command,
        params={"stream_id": "stream-1"},
    )


def test_registry_dispatches_registered_command():
    registry = RecorderCommandRegistry()
    handler = FakeHandler()
    registry.register(RecorderCommandType.RECORD_START, handler)

    message = _message()
    registry.handle(message)

    assert handler.calls == [message]
    assert registry.commands == ("record.start",)


def test_registry_rejects_duplicate_command_by_default():
    registry = RecorderCommandRegistry()
    registry.register("record.start", FakeHandler())

    with pytest.raises(DuplicateCommandHandlerError):
        registry.register(RecorderCommandType.RECORD_START, FakeHandler())


def test_registry_reports_unsupported_command():
    registry = RecorderCommandRegistry()

    with pytest.raises(UnsupportedRecorderCommandError, match="未注册命令处理器"):
        registry.handle(_message())
