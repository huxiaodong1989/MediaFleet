"""AI 评课任务的租约、重试、执行代次和最终写回协调。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from threading import Event, Lock, Thread
from time import perf_counter

from pydantic import ValidationError
from sqlalchemy.orm import Session

from media_platform.contracts.task import TaskDeliveryChannel, TaskDispatchMessage
from media_platform.domain.task import TaskStatus
from media_platform.infrastructure.database.repositories import MediaTaskRepository
from media_platform.infrastructure.messaging import (
    TaskBusyError,
    TaskPermanentError,
    TaskRetryableError,
)
from services.content_analysis.application.workflow import (
    ClassEvaluationWorkflow,
    LeaseLostError,
)
from services.content_analysis.application.log_sanitizer import sanitize_log_value
from services.content_analysis.infrastructure.repositories import (
    ContentEvaluationRepository,
)


LOGGER = logging.getLogger(__name__)


class _CallbackDeliveryError(RuntimeError):
    """评课已完成，但终态业务回调尚未成功。"""


class _LeaseRenewer:
    def __init__(
        self,
        service: "ContentTaskExecutionService",
        message: TaskDispatchMessage,
        generation: int,
    ) -> None:
        self.service = service
        self.message = message
        self.generation = generation
        self.stop_event = Event()
        self.lease_lost = Event()
        self.thread: Thread | None = None

    def start(self) -> None:
        self.thread = Thread(
            target=self._run,
            name=f"content-analysis-lease-{self.message.task_id}",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=5)

    def _run(self) -> None:
        interval = self.service.lease_renew_interval.total_seconds()
        while not self.stop_event.wait(interval):
            try:
                with self.service.session_factory() as session:
                    with session.begin():
                        renewed = MediaTaskRepository(session).renew_execution_lease(
                            self.message.task_id,
                            self.message.message_id,
                            self.service.worker_id,
                            self.generation,
                            lease_timeout=self.service.lease_timeout,
                        )
                if not renewed:
                    self.lease_lost.set()
                    LOGGER.warning(
                        "AI评课任务执行租约已丢失: task_id=%s, generation=%s",
                        self.message.task_id,
                        self.generation,
                    )
                    return
            except Exception:
                LOGGER.exception(
                    "AI评课任务续租异常，将继续尝试: task_id=%s",
                    self.message.task_id,
                )


class ContentTaskExecutionService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        workflow: ClassEvaluationWorkflow,
        worker_id: str,
        *,
        execution_timeout: timedelta = timedelta(minutes=30),
        lease_timeout: timedelta = timedelta(minutes=30),
        lease_renew_interval: timedelta | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        self.session_factory = session_factory
        self.workflow = workflow
        self.worker_id = worker_id.strip()
        self.execution_timeout = execution_timeout
        self.lease_timeout = lease_timeout
        self.lease_renew_interval = lease_renew_interval or lease_timeout / 3
        if self.lease_renew_interval >= self.lease_timeout:
            raise ValueError("lease_renew_interval 必须小于 lease_timeout")
        self._processing_count = 0
        self._processing_lock = Lock()

    @property
    def processing_count(self) -> int:
        with self._processing_lock:
            return self._processing_count

    def _claim(self, message: TaskDispatchMessage) -> int | None:
        if message.delivery_channel != TaskDeliveryChannel.CONTENT_ANALYSIS:
            raise TaskPermanentError("收到非内容分析通道任务")
        if message.task_type != "content.class_evaluation":
            raise TaskPermanentError(f"不支持的内容任务类型: {message.task_type}")
        now = datetime.now()
        with self.session_factory() as session:
            with session.begin():
                repository = MediaTaskRepository(session)
                task = repository.get(message.task_id)
                if task is None:
                    raise TaskPermanentError("数据库不存在AI评课任务")
                if task.message_id != message.message_id:
                    raise TaskPermanentError("AI评课任务消息编号与数据库不一致")
                status = str(task.status or "").lower()
                if status in {
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                }:
                    LOGGER.info(
                        "AI评课任务已是终态，跳过重复消息: task_id=%s, "
                        "business_task_id=%s, status=%s, worker_id=%s",
                        message.task_id,
                        sanitize_log_value(message.business_task_id),
                        status,
                        self.worker_id,
                    )
                    return None
                generation = repository.claim_execution_with_lease(
                    message.task_id,
                    message.message_id,
                    self.worker_id,
                    attempt=message.attempt,
                    stale_before=now - self.execution_timeout,
                    started_at=now,
                    lease_timeout=self.lease_timeout,
                )
                if generation is not None:
                    return generation
                session.expire_all()
                latest = repository.get(message.task_id)
                if latest is not None and str(latest.status or "").lower() in {
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                }:
                    return None
        raise TaskBusyError(f"AI评课任务正在其他实例执行: {message.task_id}")

    def _mark_failure(
        self,
        message: TaskDispatchMessage,
        generation: int,
        error: Exception,
        *,
        retryable: bool,
    ) -> None:
        next_attempt = message.attempt + 1
        should_retry = retryable and next_attempt < message.max_attempts
        error_message = str(error)[:4000]
        with self.session_factory() as session:
            with session.begin():
                repository = MediaTaskRepository(session)
                if should_retry:
                    updated = repository.mark_execution_retry(
                        message.task_id,
                        message.message_id,
                        self.worker_id,
                        next_attempt=next_attempt,
                        error_message=error_message,
                        execution_generation=generation,
                    )
                    evaluation_status = "pending"
                else:
                    updated = repository.mark_execution_failed(
                        message.task_id,
                        message.message_id,
                        self.worker_id,
                        error_message,
                        execution_generation=generation,
                    )
                    evaluation_status = "failed"
                if updated:
                    ContentEvaluationRepository(session).finish(
                        task_id=message.task_id,
                        execution_generation=generation,
                        status=evaluation_status,
                        result={},
                        error_message=error_message,
                        operator=self.worker_id,
                    )
        LOGGER.warning(
            "AI评课任务执行失败已写回: task_id=%s, business_task_id=%s, "
            "generation=%s, attempt=%s/%s, will_retry=%s, error_type=%s, error=%s",
            message.task_id,
            sanitize_log_value(message.business_task_id),
            generation,
            message.attempt + 1,
            message.max_attempts,
            should_retry,
            type(error).__name__,
            sanitize_log_value(error, max_length=500),
        )

    def _write_callback(self, task_id: str, result: dict) -> None:
        with self.session_factory() as session:
            with session.begin():
                MediaTaskRepository(session).mark_callback_result(
                    task_id,
                    result,
                    updated_by=self.worker_id,
                )

    def _recover_terminal_callback(self, message: TaskDispatchMessage) -> bool:
        """在重复投递时补偿“终态已落库、回调尚未完成”的崩溃窗口。"""

        with self.session_factory() as session:
            task = MediaTaskRepository(session).get(message.task_id)
            if task is None:
                return False
            if message.delivery_channel != TaskDeliveryChannel.CONTENT_ANALYSIS:
                raise TaskPermanentError("收到非内容分析通道任务")
            if message.task_type != "content.class_evaluation":
                raise TaskPermanentError(f"不支持的内容任务类型: {message.task_type}")
            if task.message_id != message.message_id:
                raise TaskPermanentError("AI评课任务消息编号与数据库不一致")
            status = str(task.status or "").lower()
            if status not in {
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            }:
                return False
            callback_result = task.callback_result
            if isinstance(callback_result, dict) and (
                callback_result.get("success") is True
                or callback_result.get("skipped") is True
            ):
                LOGGER.info(
                    "AI评课终态回调已完成，跳过重复消息: task_id=%s, "
                    "business_task_id=%s, status=%s",
                    message.task_id,
                    sanitize_log_value(message.business_task_id),
                    status,
                )
                return True
            params = dict(task.params or {})
            callback_url = task.callback_url
            result = dict(task.result or {})
            error_message = task.error_message

        if status != TaskStatus.COMPLETED.value:
            result = {
                "taskId": message.business_task_id or params.get("taskId"),
                "classroomId": params.get("classroomId"),
                "status": status.upper(),
                "progress": 1.0,
                "errorMessage": error_message,
            }
        callback_token = params.get("webhookToken") or params.get("webhook_token")
        if not callback_url:
            self._write_callback(
                message.task_id,
                {
                    "success": False,
                    "skipped": True,
                    "callback_at": datetime.now().isoformat(),
                },
            )
            LOGGER.info(
                "AI评课终态回调无需发送: task_id=%s, business_task_id=%s, status=%s",
                message.task_id,
                sanitize_log_value(message.business_task_id),
                status,
            )
            return True

        callback_success = asyncio.run(
            self.workflow.progress_callback.notify(
                callback_url,
                result,
                callback_token,
            )
        )
        self._write_callback(
            message.task_id,
            {
                "success": callback_success,
                "skipped": False,
                "callback_at": datetime.now().isoformat(),
                "recovered": True,
            },
        )
        if not callback_success:
            LOGGER.warning(
                "AI评课终态回调补偿失败，将通过消息重试: task_id=%s, "
                "business_task_id=%s, status=%s",
                message.task_id,
                sanitize_log_value(message.business_task_id),
                status,
            )
            raise TaskRetryableError("AI评课终态业务回调失败")
        LOGGER.info(
            "AI评课终态回调补偿成功: task_id=%s, business_task_id=%s, status=%s",
            message.task_id,
            sanitize_log_value(message.business_task_id),
            status,
        )
        return True

    def handle(self, message: TaskDispatchMessage) -> None:
        if self._recover_terminal_callback(message):
            return
        generation = self._claim(message)
        if generation is None:
            return
        started_at = perf_counter()
        LOGGER.info(
            "AI评课任务开始执行: task_id=%s, business_task_id=%s, school_code=%s, "
            "worker_id=%s, generation=%s, attempt=%s/%s",
            message.task_id,
            sanitize_log_value(message.business_task_id),
            sanitize_log_value(message.school_code),
            self.worker_id,
            generation,
            message.attempt + 1,
            message.max_attempts,
        )
        renewer = _LeaseRenewer(self, message, generation)
        renewer.start()
        with self._processing_lock:
            self._processing_count += 1
        try:
            try:
                outcome = asyncio.run(
                    self.workflow.execute(message, generation, renewer.lease_lost)
                )
                if renewer.lease_lost.is_set():
                    raise LeaseLostError("最终写回前AI评课执行租约已丢失")
                with self.session_factory() as session:
                    with session.begin():
                        updated = MediaTaskRepository(session).mark_execution_completed(
                            message.task_id,
                            message.message_id,
                            self.worker_id,
                            outcome.result,
                            execution_generation=generation,
                        )
                if not updated:
                    raise LeaseLostError("AI评课最终结果写回被执行代次校验拒绝")
                LOGGER.info(
                    "AI评课任务结果已写回: task_id=%s, business_task_id=%s, "
                    "generation=%s, worker_id=%s",
                    message.task_id,
                    sanitize_log_value(outcome.business_task_id),
                    generation,
                    self.worker_id,
                )
                callback_success = asyncio.run(
                    self.workflow.progress_callback.notify(
                        outcome.callback_url,
                        outcome.result,
                        outcome.callback_token,
                    )
                )
                self._write_callback(
                    message.task_id,
                    {
                        "success": callback_success,
                        "skipped": not bool(outcome.callback_url),
                        "callback_at": datetime.now().isoformat(),
                    },
                )
                if outcome.callback_url and not callback_success:
                    raise _CallbackDeliveryError("AI评课终态业务回调失败")
                LOGGER.info(
                    "AI评课任务执行成功: task_id=%s, business_task_id=%s, "
                    "worker_id=%s, generation=%s, callback=%s, duration_ms=%s",
                    message.task_id,
                    sanitize_log_value(outcome.business_task_id),
                    self.worker_id,
                    generation,
                    (
                        "skipped"
                        if not outcome.callback_url
                        else "success" if callback_success else "failed"
                    ),
                    round((perf_counter() - started_at) * 1000),
                )
            except _CallbackDeliveryError as exc:
                LOGGER.warning(
                    "AI评课任务结果已完成但业务回调失败，将仅重试回调: "
                    "task_id=%s, business_task_id=%s, worker_id=%s, generation=%s",
                    message.task_id,
                    sanitize_log_value(message.business_task_id),
                    self.worker_id,
                    generation,
                )
                raise TaskRetryableError(str(exc)) from exc
            except LeaseLostError as exc:
                LOGGER.warning(
                    "AI评课任务执行权丢失: task_id=%s, business_task_id=%s, "
                    "worker_id=%s, generation=%s, error=%s",
                    message.task_id,
                    sanitize_log_value(message.business_task_id),
                    self.worker_id,
                    generation,
                    sanitize_log_value(exc, max_length=500),
                )
                raise TaskBusyError(str(exc)) from exc
            except ValidationError as exc:
                self._mark_failure(message, generation, exc, retryable=False)
                raise TaskPermanentError(str(exc)) from exc
            except Exception as exc:
                self._mark_failure(message, generation, exc, retryable=True)
                raise TaskRetryableError(str(exc)) from exc
        finally:
            with self._processing_lock:
                self._processing_count = max(0, self._processing_count - 1)
            renewer.stop()


__all__ = ["ContentTaskExecutionService"]
