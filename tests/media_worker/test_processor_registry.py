"""媒体处理器注册表测试。"""

import pytest

from media_platform.contracts.task import TaskDispatchMessage
from services.media_worker.registry import (
    DuplicateProcessorError,
    ProcessorResult,
    TaskProcessorRegistry,
    UnsupportedTaskTypeError,
)


class FakeProcessor:
    """返回任务编号，证明注册表把完整消息交给了目标处理器。"""

    def process(self, message):
        return ProcessorResult(payload={"task_id": message.task_id})


def _message(task_type="video.cover.extract"):
    return TaskDispatchMessage(
        task_id="task-1",
        school_code="school-1",
        task_type=task_type,
        routing_key=task_type,
    )


def test_registry_dispatches_by_exact_task_type():
    registry = TaskProcessorRegistry()
    registry.register("video.cover.extract", FakeProcessor())

    result = registry.process(_message())

    assert result.payload == {"task_id": "task-1"}
    assert registry.task_types == ("video.cover.extract",)


def test_registry_rejects_duplicate_registration_by_default():
    registry = TaskProcessorRegistry()
    registry.register("video.cover.extract", FakeProcessor())

    with pytest.raises(DuplicateProcessorError):
        registry.register("video.cover.extract", FakeProcessor())


def test_registry_reports_unsupported_task_type_clearly():
    registry = TaskProcessorRegistry()

    with pytest.raises(UnsupportedTaskTypeError, match="video.audio.extract"):
        registry.process(_message("video.audio.extract"))
