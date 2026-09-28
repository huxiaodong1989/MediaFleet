"""录制命令持久意图补发服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from media_platform.application import RecorderCommandDispatchService
from services.control_center.application.recording_task_state_service import (
    RecordingTaskStateService,
)


LOGGER = logging.getLogger(__name__)


class InvalidRecordingCommandIntentError(ValueError):
    """MySQL 中的录制命令意图缺少不可恢复的必要字段。"""


@dataclass(frozen=True)
class RecordingCommandDispatchBatchResult:
    claimed: int
    published: int
    failed_task_ids: tuple[str, ...] = ()


class DurableRecordingCommandDispatchService:
    """从 MySQL 领取录制命令意图并按稳定消息编号补发。"""

    def __init__(
        self,
        state_service: RecordingTaskStateService,
        command_service: RecorderCommandDispatchService,
    ) -> None:
        self.state_service = state_service
        self.command_service = command_service

    @staticmethod
    def _error_message(exc: Exception) -> str:
        """生成不会为空的发布错误信息，便于后台排障。"""

        message = str(exc).strip()
        if message:
            return message
        return f"{type(exc).__name__}: {exc!r}"

    @staticmethod
    def _validate_command(command) -> None:
        """发布前校验持久化意图，区分数据错误和临时 MQ 故障。"""

        if command.command not in {"record.start", "record.stop"}:
            raise InvalidRecordingCommandIntentError(
                f"不支持的录制命令: {command.command or '<empty>'}"
            )
        if not str(command.target_node_id or "").strip():
            raise InvalidRecordingCommandIntentError("target_node_id 不能为空")
        if not str(command.message_id or "").strip():
            raise InvalidRecordingCommandIntentError("message_id 不能为空")
        if command.command == "record.start":
            if not str(command.params.get("app") or "").strip():
                raise InvalidRecordingCommandIntentError("record.start 缺少 app")
            if not str(command.params.get("stream_id") or "").strip():
                raise InvalidRecordingCommandIntentError("record.start 缺少 stream_id")

    def dispatch_batch(
        self,
        instance_id: str,
        *,
        limit: int = 10,
        lock_timeout: timedelta = timedelta(seconds=10),
    ) -> RecordingCommandDispatchBatchResult:
        commands = self.state_service.claim_pending_commands(
            instance_id, limit=limit, lock_timeout=lock_timeout
        )
        published = 0
        failed: list[str] = []
        for command in commands:
            try:
                self._validate_command(command)
                common = dict(
                    task_id=command.task_id,
                    target_node_id=command.target_node_id,
                    params=command.params,
                    message_id=command.message_id,
                    trace_id=command.task_id,
                    idempotency_key=f"{command.task_id}:{command.command}",
                )
                if command.command == "record.start":
                    self.command_service.send_record_start(
                        app=str(command.params.get("app") or ""),
                        stream_id=str(command.params.get("stream_id") or ""),
                        **common,
                    )
                elif command.command == "record.stop":
                    self.command_service.send_record_stop(
                        delete_from_memory=False,
                        **common,
                    )
                else:
                    raise ValueError(f"不支持的录制命令: {command.command}")
                if not self.state_service.mark_command_published(
                    task_id=command.task_id,
                    message_id=command.message_id,
                    claimed_by=instance_id,
                    updated_by=instance_id,
                ):
                    raise RuntimeError("录制命令发布成功但MySQL确认失败")
                published += 1
            except InvalidRecordingCommandIntentError as exc:
                LOGGER.error(
                    "录制命令持久化数据非法，停止自动补发: task_id=%s, error=%s",
                    command.task_id,
                    exc,
                )
                failed.append(command.task_id)
                self.state_service.mark_command_failed(
                    task_id=command.task_id,
                    message_id=command.message_id,
                    instance_id=instance_id,
                    error_message=str(exc),
                )
            except Exception as exc:
                LOGGER.exception("录制命令补发失败: task_id=%s", command.task_id)
                failed.append(command.task_id)
                self.state_service.release_command_claim(
                    task_id=command.task_id,
                    message_id=command.message_id,
                    instance_id=instance_id,
                    error_message=self._error_message(exc),
                )
        return RecordingCommandDispatchBatchResult(
            claimed=len(commands),
            published=published,
            failed_task_ids=tuple(failed),
        )


__all__ = [
    "DurableRecordingCommandDispatchService",
    "InvalidRecordingCommandIntentError",
    "RecordingCommandDispatchBatchResult",
]
