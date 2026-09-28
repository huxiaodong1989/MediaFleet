"""录制节点启动恢复服务。

recorder-node 重启后，本机内存中的 ``recording_tasks`` 会丢失，但 MySQL
中的录制任务事实仍然存在。本服务只归属 recorder-node 应用层，负责按当前
``RECORDER_NODE_ID`` 找回仍处于正常录制时间窗口内的录制任务，并交回本机
``StreamRecorder`` 继续等待、录制、停止和后处理。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_platform.domain.task import TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel


LOGGER = logging.getLogger(__name__)
RECORDING_TASK_TYPE = "record.stream"
OPEN_ENDED_RECOVERY_WINDOW = timedelta(hours=24)
RECOVERABLE_STATUSES = {
    TaskStatus.PENDING.value,
    TaskStatus.PROCESSING.value,
    "waiting",
    "recording",
    "stopping",
}
TERMINAL_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.POST_PROCESSING.value,
}


@dataclass(frozen=True)
class RecordingRecoveryCandidate:
    """可恢复录制任务的最小信息。"""

    task_id: str
    params: dict[str, Any]


class RecordingTaskRecoveryService:
    """按节点编号恢复本机仍应继续执行的录制任务。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        node_id: str,
        recording_starter: Callable[[str, dict[str, Any]], bool],
        open_ended_window: timedelta = OPEN_ENDED_RECOVERY_WINDOW,
    ) -> None:
        self.session_factory = session_factory
        self.node_id = str(node_id or "").strip()
        self.recording_starter = recording_starter
        self.open_ended_window = open_ended_window
        if not self.node_id:
            raise ValueError("node_id 不能为空")
        if self.open_ended_window <= timedelta(0):
            raise ValueError("open_ended_window 必须大于0")

    def recover_active_recordings(self, *, now: datetime | None = None) -> int:
        """恢复当前节点在正常录制窗口内的任务。

        恢复规则：
        - 有开始时间和结束时间：当前时间不超过结束时间，即可恢复；过了开始时间但
          没到结束时间也属于正常录制。
        - 有开始时间但没有结束时间：当前时间不超过开始时间后 24 小时，即可恢复；
          该规则用于服务更新、重启期间恢复开放式录制。
        - 已完成、失败、取消或已经进入后处理的任务不恢复。
        """

        now = now or datetime.now()
        candidates = self._load_candidates(now=now)
        recovered = 0
        for candidate in candidates:
            try:
                if self.recording_starter(candidate.task_id, candidate.params):
                    recovered += 1
            except Exception:
                LOGGER.exception(
                    "录制任务恢复启动异常: task_id=%s, node_id=%s",
                    candidate.task_id,
                    self.node_id,
                )
        LOGGER.info(
            "录制节点启动恢复完成: node_id=%s, candidate_count=%s, recovered_count=%s",
            self.node_id,
            len(candidates),
            recovered,
        )
        return recovered

    def load_recovery_params(
        self,
        task_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """按任务号读取单条可恢复录制上下文。

        该方法用于 ``record.stop`` 到达本节点但本机内存未命中任务时的兜底恢复。
        只有任务属于当前节点且仍处于恢复窗口内，才返回可交给
        ``StreamRecorder.start_recording`` 的参数。
        """

        task_id = str(task_id or "").strip()
        if not task_id:
            return None
        now = now or datetime.now()
        with self.session_factory() as session:
            task = session.get(MediaTaskModel, task_id)
            if task is None:
                return None
        candidate = self._candidate_from_task(task, now=now)
        if candidate is None:
            return None
        return candidate.params

    def _load_candidates(self, *, now: datetime) -> list[RecordingRecoveryCandidate]:
        """从 MySQL 读取候选任务，并在应用层按录制窗口过滤。"""

        with self.session_factory() as session:
            tasks = list(
                session.scalars(
                    select(MediaTaskModel)
                    .where(
                        MediaTaskModel.task_type == RECORDING_TASK_TYPE,
                        MediaTaskModel.executor_node_id == self.node_id,
                    )
                    .order_by(MediaTaskModel.created_at.asc())
                )
            )

        candidates: list[RecordingRecoveryCandidate] = []
        for task in tasks:
            candidate = self._candidate_from_task(task, now=now)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _candidate_from_task(
        self,
        task: MediaTaskModel,
        *,
        now: datetime,
    ) -> RecordingRecoveryCandidate | None:
        """把任务表记录转换为可恢复候选。"""

        if task.task_type != RECORDING_TASK_TYPE:
            return None
        if str(task.executor_node_id or "").strip() != self.node_id:
            return None
        status = str(task.status or "").lower()
        if status in TERMINAL_STATUSES or status not in RECOVERABLE_STATUSES:
            return None
        params = self._build_recovery_params(task)
        start_time = self._resolve_start_time(task, params)
        end_time = self._resolve_end_time(params)
        if start_time is None:
            LOGGER.warning(
                "录制任务缺少开始时间，跳过恢复: task_id=%s, node_id=%s",
                task.id,
                self.node_id,
            )
            return None
        if not self._is_in_recovery_window(
            now=now,
            start_time=start_time,
            end_time=end_time,
        ):
            return None
        params["start_time"] = self._format_datetime(start_time)
        params["end_time"] = self._format_datetime(end_time) if end_time else None
        params["recovered_by_node"] = self.node_id
        params["recovered_at"] = self._format_datetime(now)
        return RecordingRecoveryCandidate(
            task_id=task.id,
            params=params,
        )

    @staticmethod
    def _build_recovery_params(task: MediaTaskModel) -> dict[str, Any]:
        """补齐 ``StreamRecorder.start_recording`` 需要的入参。"""

        params = dict(task.params or {})
        params["task_id"] = task.id
        if task.callback_url and not params.get("callback_url"):
            params["callback_url"] = task.callback_url
        params.setdefault("output_format", "mp4")
        extra_params = params.get("extra_params")
        if not isinstance(extra_params, dict):
            params["extra_params"] = {}
        return params

    @classmethod
    def _resolve_start_time(
        cls,
        task: MediaTaskModel,
        params: dict[str, Any],
    ) -> datetime | None:
        """解析恢复用开始时间，兼容旧接口未显式传开始时间的立即录制任务。"""

        return (
            cls._parse_datetime(params.get("start_time"))
            or task.started_at
            or task.created_at
        )

    @classmethod
    def _resolve_end_time(cls, params: dict[str, Any]) -> datetime | None:
        """解析恢复用结束时间。"""

        return cls._parse_datetime(params.get("end_time"))

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        """解析常见无时区时间文本。"""

        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            LOGGER.warning("录制恢复时间格式非法，已忽略: value=%s", text)
            return None

    def _is_in_recovery_window(
        self,
        *,
        now: datetime,
        start_time: datetime,
        end_time: datetime | None,
    ) -> bool:
        """判断任务是否仍处于允许恢复的录制窗口。"""

        if end_time is not None:
            return now <= end_time
        return now <= start_time + self.open_ended_window

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        """按原录制接口格式输出无时区毫秒时间。"""

        return value.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


__all__ = [
    "OPEN_ENDED_RECOVERY_WINDOW",
    "RecordingRecoveryCandidate",
    "RecordingTaskRecoveryService",
]
