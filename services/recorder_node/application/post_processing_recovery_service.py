"""录制节点后处理队列恢复服务。

录制结束后的后处理队列是 recorder-node 本机的并发控制设施，不能成为唯一事实来源。
本服务在节点启动时从 MySQL 找回已经进入后处理、或录制窗口已经结束但尚未来得及
进入后处理的本节点任务，并重新提交到本机后处理队列。录像文件仍由 ZLMediaKit
本机目录扫描发现，不要求进程退出前保留内存任务对象。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_platform.domain.task import TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from services.recorder_node.application.task_result_service import (
    RecordingTaskResultService,
)


LOGGER = logging.getLogger(__name__)
RECORDING_TASK_TYPE = "record.stream"


@dataclass(frozen=True)
class PostProcessingRecoveryCandidate:
    """可重新提交到本机后处理队列的持久化任务快照。"""

    task_id: str
    task_status: dict[str, Any]
    result_url: str | None
    recording_window: dict[str, datetime] | None
    recording_error: str | None
    priority: int
    recovery_context: dict[str, Any]


class RecordingPostProcessingRecoveryService:
    """从 MySQL 恢复 recorder-node 本机丢失的后处理队列任务。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        node_id: str,
        post_processing_recoverer: Callable[[str, dict[str, Any]], bool],
        post_processing_tracker: Callable[[str], bool] | None = None,
        task_result_service: RecordingTaskResultService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.node_id = str(node_id or "").strip()
        self.post_processing_recoverer = post_processing_recoverer
        self.post_processing_tracker = post_processing_tracker
        self.task_result_service = task_result_service or RecordingTaskResultService(
            session_factory=session_factory,
            node_id=self.node_id,
        )
        self._recovered_task_ids: set[str] = set()
        if not self.node_id:
            raise ValueError("node_id 不能为空")

    async def recover_pending_post_processing(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        """恢复本节点丢失的后处理任务，并返回成功入队数量。"""

        now = now or datetime.now()
        candidates = await asyncio.to_thread(self._load_candidates, now=now)
        if not candidates:
            LOGGER.debug("录制后处理恢复扫描完成，无待恢复任务: node_id=%s", self.node_id)
            return 0

        recovered = 0
        for snapshot in candidates:
            # 批量扫描得到的是旧快照。逐条处理前必须重读当前事实，防止前面的
            # 长耗时恢复循环把已经 completed 的任务重新写回 post_processing。
            candidate = await asyncio.to_thread(
                self._load_current_candidate,
                task_id=snapshot.task_id,
                now=datetime.now(),
            )
            if candidate is None:
                continue
            if candidate.recovery_context.get("recovery_reason") == "auto_retry_due":
                # 上一次恢复执行失败后会再次写入 auto_retry_waiting。到期时允许同一
                # task_id 进入下一次尝试；普通 recovered_queued 仍由集合防重复扫描。
                self._recovered_task_ids.discard(candidate.task_id)
            if candidate.task_id in self._recovered_task_ids:
                LOGGER.debug(
                    "录制后处理任务已在本进程恢复，跳过重复入队: task_id=%s",
                    candidate.task_id,
                )
                continue
            if self.post_processing_tracker is not None and await asyncio.to_thread(
                self.post_processing_tracker,
                candidate.task_id,
            ):
                LOGGER.debug(
                    "录制后处理任务已在本机队列，跳过恢复: task_id=%s",
                    candidate.task_id,
                )
                continue
            try:
                # 先把可再次恢复的上下文写回 MySQL，再放入内存队列。即使进程在
                # submit_task 前再次退出，下次启动仍能重新发现该任务。
                claimed = await asyncio.to_thread(
                    self.task_result_service.mark_post_processing_if_active,
                    task_id=candidate.task_id,
                    result_url=candidate.result_url,
                    recovery_context=candidate.recovery_context,
                )
                if not claimed:
                    continue
                recovery_params = {
                    "task_status": candidate.task_status,
                    "result_url": candidate.result_url,
                    "priority": candidate.priority,
                    "recording_window": candidate.recovery_context.get(
                        "recording_window"
                    ),
                    "recording_error": candidate.recording_error,
                }
                submitted = await asyncio.to_thread(
                    self.post_processing_recoverer,
                    candidate.task_id,
                    recovery_params,
                )
                if submitted:
                    self._recovered_task_ids.add(candidate.task_id)
                    recovered += 1
                    LOGGER.info(
                        "录制后处理任务已从MySQL恢复入队: task_id=%s, node_id=%s",
                        candidate.task_id,
                        self.node_id,
                    )
                else:
                    LOGGER.error(
                        "录制后处理任务恢复入队失败: task_id=%s, node_id=%s",
                        candidate.task_id,
                        self.node_id,
                    )
            except Exception:
                LOGGER.exception(
                    "录制后处理任务恢复异常: task_id=%s, node_id=%s",
                    candidate.task_id,
                    self.node_id,
                )

        LOGGER.info(
            "录制后处理恢复扫描完成: node_id=%s, candidate_count=%s, recovered_count=%s",
            self.node_id,
            len(candidates),
            recovered,
        )
        return recovered

    def _load_current_candidate(
        self,
        *,
        task_id: str,
        now: datetime,
    ) -> PostProcessingRecoveryCandidate | None:
        """在真正恢复前重读单条任务，拒绝已经进入终态的旧扫描快照。"""

        with self.session_factory() as session:
            task = session.get(MediaTaskModel, task_id)
            if (
                task is None
                or task.task_type != RECORDING_TASK_TYPE
                or task.executor_node_id != self.node_id
            ):
                return None
            return self._candidate_from_task(task, now=now)

    async def retry_failed_post_processing(
        self,
        task_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """无需重启服务，手动把本节点失败的录制后处理任务重新入队。

        该入口不会重新调用 ZL 开始录制，而是按原录制时间窗重新发现本地原片并执行
        完整尾链路。失败任务原片由定时清理器保护，因此只要文件仍在即可恢复。
        """

        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            raise ValueError("task_id 不能为空")
        now = now or datetime.now()
        candidate = await asyncio.to_thread(
            self._load_failed_candidate,
            task_id=normalized_task_id,
            now=now,
        )
        recovery_context = dict(candidate.recovery_context)
        recovery_context.update(
            {
                "post_processing_state": "manual_retry_queued",
                "manual_retry_at": self._format_datetime(now),
                "manual_retry_by_node": self.node_id,
            }
        )
        await asyncio.to_thread(
            self.task_result_service.mark_post_processing,
            task_id=candidate.task_id,
            result_url=candidate.result_url,
            recovery_context=recovery_context,
        )
        recovery_params = {
            "task_status": candidate.task_status,
            "result_url": candidate.result_url,
            "priority": candidate.priority,
            "recording_window": recovery_context.get("recording_window"),
            "recording_error": candidate.recording_error,
        }
        self._recovered_task_ids.discard(candidate.task_id)
        submitted = await asyncio.to_thread(
            self.post_processing_recoverer,
            candidate.task_id,
            recovery_params,
        )
        if not submitted:
            await asyncio.to_thread(
                self.task_result_service.mark_failed,
                task_id=candidate.task_id,
                error_message="录制后处理手动重试入队失败",
                result_payload={
                    "status": "failed",
                    "failed_stage": "retry_enqueue",
                    "post_processing_state": "manual_retry_enqueue_failed",
                    "post_processing_recovery": recovery_context,
                },
            )
            raise RuntimeError("录制后处理手动重试入队失败")
        self._recovered_task_ids.add(candidate.task_id)
        LOGGER.info(
            "录制后处理失败任务已手动恢复入队: task_id=%s, node_id=%s",
            candidate.task_id,
            self.node_id,
        )
        return True

    def _load_failed_candidate(
        self,
        *,
        task_id: str,
        now: datetime,
    ) -> PostProcessingRecoveryCandidate:
        """读取并校验单个失败任务，构造可恢复快照。"""

        with self.session_factory() as session:
            task = session.get(MediaTaskModel, task_id)
            if task is None:
                raise LookupError(f"录制任务不存在: task_id={task_id}")
            if task.task_type != RECORDING_TASK_TYPE:
                raise ValueError(f"任务不是录制任务，不能执行后处理重试: task_id={task_id}")
            if task.executor_node_id != self.node_id:
                raise ValueError(
                    f"任务不属于当前录制节点: task_id={task_id}, "
                    f"task_node={task.executor_node_id}, current_node={self.node_id}"
                )
            if str(task.status or "").lower() != TaskStatus.FAILED.value:
                raise ValueError(
                    f"只有 failed 录制任务可以手动重试后处理: "
                    f"task_id={task_id}, status={task.status}"
                )
            candidate = self._candidate_from_task(
                task,
                now=now,
                allow_failed=True,
            )
        if candidate is None:
            raise ValueError(
                f"失败任务缺少可恢复录制时间窗: task_id={task_id}"
            )
        return candidate

    def _load_candidates(
        self,
        *,
        now: datetime,
    ) -> list[PostProcessingRecoveryCandidate]:
        """读取本节点非终态录制任务，并识别后处理恢复候选。"""

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

        candidates: list[PostProcessingRecoveryCandidate] = []
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
        allow_failed: bool = False,
    ) -> PostProcessingRecoveryCandidate | None:
        """识别后处理状态或已越过录制结束边界的未完成任务。"""

        status = str(task.status or "").lower()
        params = dict(task.params or {})
        result = dict(task.result or {})
        persisted_context = dict(result.get("post_processing_recovery") or {})
        persisted_state = str(
            persisted_context.get("post_processing_state") or ""
        ).lower()
        auto_retry_due = False
        if persisted_state == "auto_retry_waiting":
            next_retry_at = self._parse_datetime(
                persisted_context.get("next_retry_at")
            )
            if next_retry_at is not None and next_retry_at > now:
                return None
            auto_retry_due = True

        should_recover = status == TaskStatus.POST_PROCESSING.value
        if allow_failed and status == TaskStatus.FAILED.value:
            should_recover = True
        if status in {TaskStatus.PENDING.value, TaskStatus.PROCESSING.value}:
            should_recover = self._recording_has_ended(
                task=task,
                params=params,
                now=now,
            )
        if not should_recover:
            return None

        recording_error = self._optional_text(
            persisted_context.get("recording_error")
        )
        recording_window = self._recording_window(
            task=task,
            params=params,
            persisted_context=persisted_context,
        )
        if recording_error is None and recording_window is None:
            LOGGER.warning(
                "录制后处理任务缺少可恢复时间窗，跳过: task_id=%s, node_id=%s",
                task.id,
                self.node_id,
            )
            return None

        priority = self._priority(params, persisted_context)
        task_status = self._task_status(
            task,
            params,
            recording_window,
            persisted_context,
        )
        recovery_context = {
            "recording_window": self._serialize_window(recording_window),
            "recording_error": recording_error,
            "priority": priority,
            "post_processing_state": "recovered_queued",
            "recovered_at": self._format_datetime(now),
            "recovered_by_node": self.node_id,
            "failed_stage": (
                persisted_context.get("failed_stage")
                or result.get("failed_stage")
            ),
            "post_processing_retry_attempt": self._safe_int(
                persisted_context.get("post_processing_retry_attempt"),
                default=0,
            ),
            "post_processing_retry_max_attempts": self._safe_int(
                persisted_context.get("post_processing_retry_max_attempts"),
                default=0,
            ),
            "recovery_reason": (
                "auto_retry_due" if auto_retry_due else "startup_recovery"
            ),
        }
        return PostProcessingRecoveryCandidate(
            task_id=task.id,
            task_status=task_status,
            result_url=self._optional_text(result.get("result_url")),
            recording_window=recording_window,
            recording_error=recording_error,
            priority=priority,
            recovery_context=recovery_context,
        )

    def _recording_has_ended(
        self,
        *,
        task: MediaTaskModel,
        params: dict[str, Any],
        now: datetime,
    ) -> bool:
        """判断任务是否已越过计划结束或已持久化停止请求。"""

        stop_command = dict(params.get("stop_command") or {})
        if str(params.get("last_command") or "") == "record.stop":
            requested_at = (
                self._parse_datetime(stop_command.get("requested_at"))
                or task.updated_at
            )
            return requested_at is not None and requested_at <= now

        end_time = self._parse_datetime(params.get("end_time"))
        return end_time is not None and end_time <= now

    def _recording_window(
        self,
        *,
        task: MediaTaskModel,
        params: dict[str, Any],
        persisted_context: dict[str, Any],
    ) -> dict[str, datetime] | None:
        """从持久化上下文或原任务字段重建本机录像扫描时间范围。"""

        persisted_window = dict(persisted_context.get("recording_window") or {})
        stop_command = dict(params.get("stop_command") or {})
        start_time = (
            self._parse_datetime(persisted_window.get("start_time"))
            or self._parse_datetime(params.get("actual_start_time"))
            or self._parse_datetime(params.get("start_time"))
            or task.started_at
            or task.created_at
        )
        end_time = (
            self._parse_datetime(persisted_window.get("end_time"))
            or self._parse_datetime(stop_command.get("requested_at"))
            or self._parse_datetime(params.get("end_time"))
            or task.reservation_end_at
            or task.updated_at
        )
        stopped_at = (
            self._parse_datetime(persisted_window.get("stopped_at"))
            or end_time
        )
        if start_time is None or end_time is None or stopped_at is None:
            return None
        if end_time < start_time:
            end_time = start_time
        return {
            "start_time": start_time,
            "end_time": end_time,
            "stopped_at": stopped_at,
        }

    def _task_status(
        self,
        task: MediaTaskModel,
        params: dict[str, Any],
        recording_window: dict[str, datetime] | None,
        persisted_context: dict[str, Any],
    ) -> dict[str, Any]:
        """重建后处理各阶段需要的本机任务状态字典。"""

        task_status = dict(params)
        task_status.update(
            {
                "task_id": task.id,
                "app": str(params.get("app") or task.recording_app or "live"),
                "stream_id": str(
                    params.get("stream_id") or task.recording_stream_id or ""
                ),
                "callback_url": params.get("callback_url") or task.callback_url,
                "status": "post_processing",
                "progress": 90.0,
                "post_processing_state": "recovered_queued",
                "errors": list(params.get("errors") or []),
                "post_processing_retry_attempt": self._safe_int(
                    persisted_context.get("post_processing_retry_attempt"),
                    default=0,
                ),
                "post_processing_retry_max_attempts": self._safe_int(
                    persisted_context.get("post_processing_retry_max_attempts"),
                    default=0,
                ),
                "post_processing_failed_stage": persisted_context.get(
                    "failed_stage"
                ),
            }
        )
        extra_params = task_status.get("extra_params")
        if not isinstance(extra_params, dict):
            task_status["extra_params"] = {}
        if recording_window is not None:
            task_status["start_time"] = self._format_datetime(
                recording_window["start_time"]
            )
            task_status["end_time"] = self._format_datetime(
                recording_window["end_time"]
            )
            task_status["actual_end_time"] = self._format_datetime(
                recording_window["stopped_at"]
            )
        return task_status

    @staticmethod
    def _priority(
        params: dict[str, Any],
        persisted_context: dict[str, Any],
    ) -> int:
        value = persisted_context.get("priority")
        if value is None:
            value = (params.get("extra_params") or {}).get("priority", 5)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 5

    @staticmethod
    def _safe_int(value: Any, *, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            return None

    @classmethod
    def _serialize_window(
        cls,
        window: dict[str, datetime] | None,
    ) -> dict[str, str] | None:
        if window is None:
            return None
        return {key: cls._format_datetime(value) for key, value in window.items()}

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


__all__ = [
    "PostProcessingRecoveryCandidate",
    "RecordingPostProcessingRecoveryService",
]
