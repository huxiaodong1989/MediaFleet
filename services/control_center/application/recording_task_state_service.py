"""调用中心录制任务状态服务。

本服务归属调用中心，负责把“录制命令已经按流绑定下发到哪个 recorder-node”
这类路由事实写入 MySQL。这样 RTC 停止录制时仍保持原来的“只传 task_id”
体验，任意调用中心实例都能按 task_id 找到目标节点，不依赖创建请求所在进程。

注意：这里不执行录制、不读取录像目录、不处理录制结果；这些业务流程归属
recorder-node。公共 `media_platform` 只提供任务表模型和通用仓储能力。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import time
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from media_platform.infrastructure.database.repositories import (
    MediaStreamBindingRepository,
    MediaTaskRepository,
    RecordingServerRepository,
)
from services.control_center.infrastructure import RecordingReservationRepository


RECORDING_TASK_TYPE = "record.stream"
RECORDING_TASK_DEFAULT_SCHOOL_CODE = "SYSTEM"
RECORDING_RESERVATION_MARGIN_SECONDS = 5
RECORDING_RESERVATION_TRANSACTION_ATTEMPTS = 3


class RecordingReservationError(ValueError):
    """录制预约无法建立的业务异常基类。"""


class RecordingReservationCapacityExceededError(RecordingReservationError):
    """绑定所属录制单元在目标时段内已经没有录制容量。"""


class RecordingReservationConflictError(RecordingReservationError):
    """任务号或同一技术流在目标时段内存在冲突。"""


class RecordingReservationTargetUnavailableError(RecordingReservationError):
    """绑定、录制服务器或服务器运维状态不允许建立新预约。"""


@dataclass(frozen=True)
class RecordingTaskContext:
    """停止录制命令所需的最小持久化上下文。"""

    task_id: str
    app: str
    stream_id: str
    target_node_id: str
    binding_id: str | None
    params: dict[str, Any]
    callback_url: str | None


@dataclass(frozen=True)
class PendingRecordingCommand:
    """从 MySQL 领取出的待发布录制命令快照。"""

    task_id: str
    command: str
    target_node_id: str
    message_id: str
    params: dict[str, Any]


class RecordingTaskStateService:
    """保存和读取录制任务事实，支撑多调用中心实例按 task_id 停止录制。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        reservation_margin_seconds: int = RECORDING_RESERVATION_MARGIN_SECONDS,
        transaction_attempts: int = RECORDING_RESERVATION_TRANSACTION_ATTEMPTS,
    ):
        self.session_factory = session_factory
        if reservation_margin_seconds < 0:
            raise ValueError("reservation_margin_seconds 不能小于0")
        if transaction_attempts < 1:
            raise ValueError("transaction_attempts 必须大于0")
        self.reservation_margin = timedelta(seconds=reservation_margin_seconds)
        self.transaction_attempts = transaction_attempts

    @staticmethod
    def _school_code(params: dict[str, Any]) -> str:
        """从业务扩展参数中提取学校码；旧录制接口未显式提供时使用系统占位。"""

        return str(
            params.get("school_code")
            or params.get("xxm")
            or RECORDING_TASK_DEFAULT_SCHOOL_CODE
        ).strip()

    @staticmethod
    def _require_text(value: Any, *, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} 不能为空")
        return normalized

    @staticmethod
    def _datetime_value(value: Any, *, field_name: str) -> datetime | None:
        """读取旧接口透传的 datetime 或无时区时间文本。"""

        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        text_value = str(value).strip()
        try:
            return datetime.fromisoformat(text_value).replace(tzinfo=None)
        except ValueError as exc:
            raise RecordingReservationError(
                f"{field_name} 不是有效日期时间: {text_value}"
            ) from exc

    def _reservation_window(
        self,
        params: dict[str, Any],
        *,
        now: datetime,
    ) -> tuple[datetime, datetime | None]:
        """把原录制计划转换为带固定安全余量的容量半开区间。"""

        requested_start = self._datetime_value(
            params.get("start_time"),
            field_name="start_time",
        )
        requested_end = self._datetime_value(
            params.get("end_time"),
            field_name="end_time",
        )
        effective_start = requested_start or now
        if requested_end is not None and requested_end <= effective_start:
            raise RecordingReservationError("end_time 必须晚于实际录制开始时间")
        return (
            effective_start - self.reservation_margin,
            (
                requested_end + self.reservation_margin
                if requested_end is not None
                else None
            ),
        )

    @staticmethod
    def _retryable_transaction_error(exc: OperationalError) -> bool:
        """识别 MySQL 死锁/锁等待超时和 SQLite 本地并发锁。"""

        original = getattr(exc, "orig", None)
        arguments = getattr(original, "args", ())
        mysql_code = arguments[0] if arguments else None
        message = str(original or exc).lower()
        return mysql_code in {1205, 1213} or "database is locked" in message

    def _save_start_command_once(
        self,
        *,
        task_id: str,
        app: str,
        stream_id: str,
        target_node_id: str,
        binding_id: str,
        params: dict[str, Any],
        callback_url: str | None,
        message_id: str,
        updated_by: str,
    ) -> None:
        """在单个短事务内锁定录制单元、复核容量并写入预约任务。"""

        persisted_params = dict(params or {})
        persisted_params.update(
            {
                "task_id": task_id,
                "app": app,
                "stream_id": stream_id,
                "target_node_id": target_node_id,
                "binding_id": binding_id,
                "command": "record.start",
            }
        )
        school_code = self._school_code(persisted_params)
        now = datetime.now()
        reservation_start_at, reservation_end_at = self._reservation_window(
            persisted_params,
            now=now,
        )

        with self.session_factory() as session:
            with session.begin():
                binding_repository = MediaStreamBindingRepository(session)
                recording_server_repository = RecordingServerRepository(session)
                task_repository = MediaTaskRepository(session)
                reservation_repository = RecordingReservationRepository(session)

                binding = binding_repository.get(binding_id)
                if binding is None or binding.status != "ACTIVE":
                    raise RecordingReservationTargetUnavailableError(
                        "录制使用的流绑定不存在或已释放，无法预约录制容量"
                    )
                if binding.app != app or binding.stream_id != stream_id:
                    raise RecordingReservationConflictError(
                        "录制任务的 app + stream_id 与流绑定不一致"
                    )
                server = recording_server_repository.get_by_recorder_node_id(
                    binding.node_id
                )
                if server is None:
                    raise RecordingReservationTargetUnavailableError(
                        "流绑定所属节点没有登记固定录制服务器"
                    )
                server = reservation_repository.lock_active_server(
                    server_id=server.id,
                    updated_by=updated_by,
                )
                if server is None:
                    raise RecordingReservationTargetUnavailableError(
                        "流绑定所属录制服务器当前不是 ACTIVE，拒绝新录制预约"
                    )

                existing = task_repository.get(task_id)
                retry_task = None
                if existing is not None:
                    requested_start = self._datetime_value(
                        persisted_params.get("start_time"),
                        field_name="start_time",
                    )
                    same_explicit_window = (
                        (
                            requested_start is None
                            or existing.reservation_start_at
                            == requested_start - self.reservation_margin
                        )
                        and existing.reservation_end_at == reservation_end_at
                    )
                    if (
                        existing.task_type == RECORDING_TASK_TYPE
                        and existing.recording_server_id == server.id
                        and existing.recording_app == app
                        and existing.recording_stream_id == stream_id
                        and existing.status
                        in {
                            TaskStatus.PENDING.value,
                            TaskStatus.PENDING.value.upper(),
                            TaskStatus.PROCESSING.value,
                            TaskStatus.PROCESSING.value.upper(),
                        }
                        and same_explicit_window
                    ):
                        return
                    if (
                        existing.task_type == RECORDING_TASK_TYPE
                        and existing.recording_server_id == server.id
                        and existing.recording_app == app
                        and existing.recording_stream_id == stream_id
                        and existing.status
                        in {
                            TaskStatus.FAILED.value,
                            TaskStatus.FAILED.value.upper(),
                        }
                    ):
                        retry_task = existing
                    else:
                        raise RecordingReservationConflictError(
                            f"task_id={task_id} 已被其他任务或已结束录制使用"
                        )

                overlap = reservation_repository.find_overlapping_stream(
                    app=app,
                    stream_id=stream_id,
                    start_at=reservation_start_at,
                    end_at=reservation_end_at,
                )
                if overlap is not None:
                    raise RecordingReservationConflictError(
                        "相同 app + stream_id 已存在重叠录制计划: "
                        f"task_id={overlap.id}"
                    )

                occupied = reservation_repository.count_overlapping(
                    recording_server_id=server.id,
                    start_at=reservation_start_at,
                    end_at=reservation_end_at,
                )
                if occupied >= server.max_recordings:
                    raise RecordingReservationCapacityExceededError(
                        "绑定所属录制服务器在目标时间段录制容量已满: "
                        f"server_code={server.server_code}, "
                        f"max_recordings={server.max_recordings}"
                    )

                task_values = {
                    "school_code": school_code,
                    "task_type": RECORDING_TASK_TYPE,
                    "routing_key": "record.start",
                    "status": TaskStatus.PROCESSING.value,
                    "priority": 0,
                    "progress": 0,
                    "params": persisted_params,
                    "callback_url": callback_url,
                    "executor_node_id": target_node_id,
                    "recording_server_id": server.id,
                    "recording_app": app,
                    "recording_stream_id": stream_id,
                    "reservation_start_at": reservation_start_at,
                    "reservation_end_at": reservation_end_at,
                    "retry_count": 0,
                    "max_retries": 3,
                    "publish_status": PublishStatus.CLAIMED.value,
                    "message_id": message_id,
                    "locked_by": f"api:{message_id}",
                    "locked_at": now,
                    "published_at": None,
                    "started_at": now,
                    "completed_at": None,
                    "error_message": None,
                    "updated_by": updated_by,
                }
                if retry_task is not None:
                    for field_name, field_value in task_values.items():
                        setattr(retry_task, field_name, field_value)
                    session.flush()
                else:
                    task_repository.add(
                        MediaTaskModel(
                            id=task_id,
                            created_by=updated_by,
                            **task_values,
                        )
                    )

    def save_start_command(
        self,
        *,
        task_id: str,
        app: str,
        stream_id: str,
        target_node_id: str,
        binding_id: str,
        params: dict[str, Any],
        callback_url: str | None,
        message_id: str,
        updated_by: str = "control-center",
    ) -> None:
        """发布前持久化开始命令意图，并由 API 暂时领取。"""

        task_id = self._require_text(task_id, field_name="task_id")
        app = self._require_text(app, field_name="app")
        stream_id = self._require_text(stream_id, field_name="stream_id")
        target_node_id = self._require_text(
            target_node_id, field_name="target_node_id"
        )
        binding_id = self._require_text(binding_id, field_name="binding_id")
        message_id = self._require_text(message_id, field_name="message_id")

        for attempt in range(1, self.transaction_attempts + 1):
            try:
                self._save_start_command_once(
                    task_id=task_id,
                    app=app,
                    stream_id=stream_id,
                    target_node_id=target_node_id,
                    binding_id=binding_id,
                    params=params,
                    callback_url=callback_url,
                    message_id=message_id,
                    updated_by=updated_by,
                )
                return
            except OperationalError as exc:
                if (
                    attempt >= self.transaction_attempts
                    or not self._retryable_transaction_error(exc)
                ):
                    raise
                time.sleep(0.05 * attempt)

    def mark_start_dispatch_failed(
        self,
        *,
        task_id: str,
        message_id: str,
        error_message: str,
        updated_by: str = "control-center",
    ) -> None:
        """发布失败后释放命令领取，交给后台循环补发，不结束录制预约。"""

        self.mark_command_dispatch_failed(
            task_id=task_id,
            message_id=message_id,
            error_message=error_message,
            updated_by=updated_by,
        )

    def mark_command_dispatch_failed(
        self,
        *,
        task_id: str,
        message_id: str,
        error_message: str,
        updated_by: str = "control-center",
    ) -> bool:
        """API 直接发布失败后释放对应命令意图，供后台循环立即补发。"""

        task_id = self._require_text(task_id, field_name="task_id")
        message_id = self._require_text(message_id, field_name="message_id")
        with self.session_factory() as session:
            with session.begin():
                result = session.execute(
                    update(MediaTaskModel)
                    .where(
                        MediaTaskModel.id == task_id,
                        MediaTaskModel.task_type == RECORDING_TASK_TYPE,
                        MediaTaskModel.message_id == message_id,
                        MediaTaskModel.locked_by == f"api:{message_id}",
                    )
                    .values(
                        publish_status=PublishStatus.PENDING.value,
                        locked_by=None,
                        locked_at=None,
                        error_message=error_message,
                        updated_by=updated_by,
                    )
                )
                return result.rowcount == 1

    def get_context(self, task_id: str) -> RecordingTaskContext | None:
        """按业务 task_id 读取停止录制所需上下文。"""

        task_id = self._require_text(task_id, field_name="task_id")
        with self.session_factory() as session:
            repository = MediaTaskRepository(session)
            task = repository.get(task_id)
            if task is None:
                return None
            params = dict(task.params or {})
            target_node_id = str(
                task.executor_node_id or params.get("target_node_id") or ""
            ).strip()
            app = str(params.get("app") or "").strip()
            stream_id = str(params.get("stream_id") or "").strip()
            if not target_node_id or not app or not stream_id:
                return None
            return RecordingTaskContext(
                task_id=task.id,
                app=app,
                stream_id=stream_id,
                target_node_id=target_node_id,
                binding_id=params.get("binding_id"),
                params=params,
                callback_url=task.callback_url,
            )

    def save_stop_command(
        self,
        *,
        task_id: str,
        stop_params: dict[str, Any],
        message_id: str,
        updated_by: str = "control-center",
    ) -> None:
        """发布前持久化停止命令意图，并由 API 暂时领取。"""

        task_id = self._require_text(task_id, field_name="task_id")
        message_id = self._require_text(message_id, field_name="message_id")
        with self.session_factory() as session:
            repository = MediaTaskRepository(session)
            task = repository.get(task_id)
            if task is None:
                return
            params = dict(task.params or {})
            params["stop_command"] = dict(stop_params or {})
            params["last_command"] = "record.stop"
            params["last_command_message_id"] = message_id
            task.params = params
            task.routing_key = "record.stop"
            task.message_id = message_id
            task.publish_status = PublishStatus.CLAIMED.value
            task.locked_by = f"api:{message_id}"
            task.locked_at = datetime.now()
            task.published_at = None
            task.updated_by = updated_by
            session.commit()

    def mark_command_published(
        self,
        *,
        task_id: str,
        message_id: str,
        claimed_by: str | None = None,
        updated_by: str = "control-center",
    ) -> bool:
        """Publisher Confirm 后把当前命令意图标记为已发布。"""

        with self.session_factory() as session:
            with session.begin():
                filters = [
                    MediaTaskModel.id == task_id,
                    MediaTaskModel.task_type == RECORDING_TASK_TYPE,
                    MediaTaskModel.message_id == message_id,
                ]
                if claimed_by is not None:
                    filters.append(MediaTaskModel.locked_by == claimed_by)
                result = session.execute(
                    update(MediaTaskModel)
                    .where(*filters)
                    .values(
                        publish_status=PublishStatus.PUBLISHED.value,
                        published_at=datetime.now(),
                        locked_by=None,
                        locked_at=None,
                        error_message=None,
                        updated_by=updated_by,
                    )
                )
                return result.rowcount == 1

    def claim_pending_commands(
        self,
        instance_id: str,
        *,
        limit: int,
        lock_timeout: timedelta,
    ) -> list[PendingRecordingCommand]:
        """多调用中心原子竞争领取待补发或领取超时的录制命令。"""

        now = datetime.now()
        stale_before = now - lock_timeout
        claimable = or_(
            MediaTaskModel.publish_status == PublishStatus.PENDING.value,
            and_(
                MediaTaskModel.publish_status == PublishStatus.CLAIMED.value,
                MediaTaskModel.locked_at < stale_before,
            ),
        )
        commands: list[PendingRecordingCommand] = []
        with self.session_factory() as session:
            with session.begin():
                candidate_ids = list(
                    session.scalars(
                        select(MediaTaskModel.id)
                        .where(
                            MediaTaskModel.task_type == RECORDING_TASK_TYPE,
                            claimable,
                        )
                        .order_by(MediaTaskModel.created_at.asc())
                        .limit(limit * 4)
                    )
                )
                for task_id in candidate_ids:
                    if len(commands) >= limit:
                        break
                    claimed = session.execute(
                        update(MediaTaskModel)
                        .where(MediaTaskModel.id == task_id, claimable)
                        .values(
                            publish_status=PublishStatus.CLAIMED.value,
                            locked_by=instance_id,
                            locked_at=now,
                        )
                    )
                    if claimed.rowcount != 1:
                        continue
                    task = session.get(MediaTaskModel, task_id)
                    params = dict(task.params or {})
                    command = str(params.get("last_command") or params.get("command") or task.routing_key)
                    command_params = (
                        dict(params.get("stop_command") or {})
                        if command == "record.stop"
                        else params
                    )
                    commands.append(
                        PendingRecordingCommand(
                            task_id=task.id,
                            command=command,
                            target_node_id=str(task.executor_node_id or params.get("target_node_id") or ""),
                            message_id=str(task.message_id or ""),
                            params=command_params,
                        )
                    )
        return commands

    def release_command_claim(
        self,
        *,
        task_id: str,
        message_id: str,
        instance_id: str,
        error_message: str,
    ) -> bool:
        """补发失败时释放当前实例领取，供下一轮或其他实例重试。"""

        with self.session_factory() as session:
            with session.begin():
                filters = [
                    MediaTaskModel.id == task_id,
                    MediaTaskModel.locked_by == instance_id,
                ]
                if message_id:
                    filters.append(MediaTaskModel.message_id == message_id)
                result = session.execute(
                    update(MediaTaskModel)
                    .where(*filters)
                    .values(
                        publish_status=PublishStatus.PENDING.value,
                        locked_by=None,
                        locked_at=None,
                        error_message=error_message,
                        updated_by=instance_id,
                    )
                )
                return result.rowcount == 1

    def mark_command_failed(
        self,
        *,
        task_id: str,
        message_id: str,
        instance_id: str,
        error_message: str,
    ) -> bool:
        """把无法发布的非法持久化命令置为失败，避免后台无限重试。"""

        with self.session_factory() as session:
            with session.begin():
                filters = [
                    MediaTaskModel.id == task_id,
                    MediaTaskModel.locked_by == instance_id,
                ]
                if message_id:
                    filters.append(MediaTaskModel.message_id == message_id)
                result = session.execute(
                    update(MediaTaskModel)
                    .where(*filters)
                    .values(
                        publish_status=PublishStatus.FAILED.value,
                        locked_by=None,
                        locked_at=None,
                        error_message=error_message,
                        updated_by=instance_id,
                    )
                )
                return result.rowcount == 1


__all__ = [
    "RECORDING_TASK_TYPE",
    "RecordingReservationCapacityExceededError",
    "RecordingReservationConflictError",
    "RecordingReservationError",
    "RecordingReservationTargetUnavailableError",
    "RecordingTaskContext",
    "PendingRecordingCommand",
    "RecordingTaskStateService",
]
