"""录像命令处理器测试。"""

from __future__ import annotations

import asyncio
from threading import Event

import pytest

from media_platform.contracts.command import (
    RecorderCommandMessage,
    RecorderCommandType,
)
from media_platform.infrastructure.messaging import TaskPermanentError
from services.recorder_node.commands import (
    RecordingCommandHandler,
    RecordingCommandHandlerConfig,
)


class FakeRecorder:
    """模拟异步录制器，验证命令处理器的异步 loop 语义。"""

    def __init__(self) -> None:
        self.start_calls = []
        self.cancel_calls = []
        self.stop_calls = []
        self.background_task_finished = Event()
        self.recording_tasks = {}
        self.post_processing_calls = []

    async def start_recording(self, task_id, params):
        self.start_calls.append((task_id, params))
        self.recording_tasks[task_id] = {"status": "pending"}
        asyncio.create_task(self._background_marker())
        return {"task_id": task_id, "status": "pending"}

    async def _background_marker(self):
        await asyncio.sleep(0.02)
        self.background_task_finished.set()

    async def cancel_task(self, task_id, delete_from_memory=False):
        self.cancel_calls.append((task_id, delete_from_memory))
        return task_id == "known-task"

    async def stop_recording(self, task_id, params=None):
        self.stop_calls.append((task_id, params or {}))
        return task_id == "known-task" or task_id in self.recording_tasks

    async def recover_post_processing(self, task_id, params):
        self.post_processing_calls.append((task_id, params))
        return True


def _message(command, *, task_id="task-1", params=None):
    return RecorderCommandMessage(
        task_id=task_id,
        target_node_id="recorder-a",
        command=command,
        params=params or {"app": "live", "stream_id": "stream-1"},
    )


def test_record_start_keeps_background_task_running_after_ack() -> None:
    recorder = FakeRecorder()
    handler = RecordingCommandHandler(
        recorder_factory=lambda: recorder,
        config=RecordingCommandHandlerConfig(accept_timeout_seconds=1),
    )

    try:
        handler.handle(_message(RecorderCommandType.RECORD_START))

        assert recorder.start_calls == [
            ("task-1", {"app": "live", "stream_id": "stream-1"})
        ]
        assert recorder.background_task_finished.wait(1)
    finally:
        handler.close()


def test_record_start_rejects_missing_required_params() -> None:
    handler = RecordingCommandHandler(
        recorder_factory=FakeRecorder,
        config=RecordingCommandHandlerConfig(accept_timeout_seconds=1),
    )

    try:
        with pytest.raises(TaskPermanentError, match="缺少 stream_id"):
            handler.handle(
                _message(
                    RecorderCommandType.RECORD_START,
                    params={"app": "live"},
                )
            )
    finally:
        handler.close()


def test_record_stop_uses_normal_stop_semantics() -> None:
    recorder = FakeRecorder()
    handler = RecordingCommandHandler(
        recorder_factory=lambda: recorder,
        config=RecordingCommandHandlerConfig(accept_timeout_seconds=1),
    )

    try:
        handler.handle(
            _message(
                RecorderCommandType.RECORD_STOP,
                task_id="known-task",
                params={
                    "delete_from_memory": True,
                    "reason": "manual_stop",
                    "stop_time": "2026-07-27T10:00:00",
                },
            )
        )

        assert recorder.stop_calls == [
            (
                "known-task",
                {
                    "delete_from_memory": True,
                    "reason": "manual_stop",
                    "stop_time": "2026-07-27T10:00:00",
                },
            )
        ]
        assert recorder.cancel_calls == []
    finally:
        handler.close()


def test_record_stop_is_idempotent_when_local_task_missing() -> None:
    recorder = FakeRecorder()
    handler = RecordingCommandHandler(
        recorder_factory=lambda: recorder,
        config=RecordingCommandHandlerConfig(accept_timeout_seconds=1),
    )

    try:
        handler.handle(
            _message(
                RecorderCommandType.RECORD_STOP,
                task_id="missing-task",
                params={"delete_from_memory": True},
            )
        )

        assert recorder.stop_calls == [("missing-task", {"delete_from_memory": True})]
    finally:
        handler.close()


def test_record_stop_recovers_mysql_context_when_local_task_missing() -> None:
    recorder = FakeRecorder()
    handler = RecordingCommandHandler(
        recorder_factory=lambda: recorder,
        recording_recovery_loader=lambda task_id: {
            "task_id": task_id,
            "app": "live",
            "stream_id": "stream-recovered",
            "start_time": "2026-07-30 10:00:00.000",
            "end_time": "2026-07-30 11:00:00.000",
            "output_format": "mp4",
            "extra_params": {"extract_cover": True},
        },
        config=RecordingCommandHandlerConfig(accept_timeout_seconds=1),
    )

    try:
        handler.handle(
            _message(
                RecorderCommandType.RECORD_STOP,
                task_id="missing-task",
                params={"reason": "manual_stop"},
            )
        )

        assert recorder.start_calls == [
            (
                "missing-task",
                {
                    "task_id": "missing-task",
                    "app": "live",
                    "stream_id": "stream-recovered",
                    "start_time": "2026-07-30 10:00:00.000",
                    "end_time": "2026-07-30 11:00:00.000",
                    "output_format": "mp4",
                    "extra_params": {"extract_cover": True},
                },
            )
        ]
        assert recorder.stop_calls == [
            ("missing-task", {"reason": "manual_stop"}),
            ("missing-task", {"reason": "manual_stop"}),
        ]
    finally:
        handler.close()


def test_active_recording_count_only_counts_non_terminal_tasks() -> None:
    recorder = FakeRecorder()
    recorder.recording_tasks = {
        "recording": {"status": "recording"},
        "stopping": {"status": "stopping"},
        "waiting": {"status": "waiting"},
        "post-processing": {"status": "post_processing"},
        "completed": {"status": "completed"},
        "failed": {"status": "failed"},
    }
    handler = RecordingCommandHandler(
        recorder_factory=lambda: recorder,
        config=RecordingCommandHandlerConfig(accept_timeout_seconds=1),
    )

    try:
        handler._recorder_instance()
        assert handler.active_recording_count() == 3
    finally:
        handler.close()


def test_post_processing_recovery_uses_the_long_lived_recorder_loop() -> None:
    created = []

    def recorder_factory():
        recorder = FakeRecorder()
        recorder.owner_loop = asyncio.get_running_loop()
        created.append(recorder)
        return recorder

    handler = RecordingCommandHandler(
        recorder_factory=recorder_factory,
        config=RecordingCommandHandlerConfig(accept_timeout_seconds=1),
    )

    try:
        assert handler.recover_post_processing(
            "record-task-1",
            {"task_status": {"task_id": "record-task-1"}},
        ) is True
        recorder = created[0]
        assert recorder.owner_loop.is_running()
        assert recorder.post_processing_calls == [
            ("record-task-1", {"task_status": {"task_id": "record-task-1"}})
        ]
    finally:
        handler.close()


def test_recording_recovery_creates_recorder_inside_long_lived_loop() -> None:
    created = []

    def recorder_factory():
        recorder = FakeRecorder()
        recorder.owner_loop = asyncio.get_running_loop()
        created.append(recorder)
        return recorder

    handler = RecordingCommandHandler(
        recorder_factory=recorder_factory,
        config=RecordingCommandHandlerConfig(accept_timeout_seconds=1),
    )

    try:
        assert handler.recover_recording(
            "record-task-2",
            {"app": "live", "stream_id": "stream-2"},
        ) is True
        recorder = created[0]
        assert recorder.owner_loop.is_running()
        assert recorder.start_calls == [
            ("record-task-2", {"app": "live", "stream_id": "stream-2"})
        ]
    finally:
        handler.close()
