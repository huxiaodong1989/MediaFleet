"""调用中心媒体事件处理服务。

通用媒体 Worker 和录制节点通过 ``media.event`` 发布领域事件。调用中心消费事件
后读取 MySQL 任务事实记录，并在需要时执行业务回调。该服务不执行 FFmpeg、
不读取本地录像目录，也不依赖创建任务的调用中心实例内存状态。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from media_platform.common.redaction import sanitize_url
from media_platform.contracts.event import MediaEventMessage
from media_platform.domain.task import TaskStatus
from media_platform.infrastructure.database.repositories import MediaTaskRepository
from media_platform.infrastructure.messaging import (
    TaskPermanentError,
    TaskRetryableError,
)


LOGGER = logging.getLogger(__name__)


class MediaEventHandler:
    """处理媒体领域事件并执行调用中心侧业务回调。"""

    _SUPPORTED_EVENTS = {"task.completed", "task.failed"}

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        http_client_factory: Callable[[], httpx.Client] | None = None,
        callback_timeout_seconds: float = 10.0,
        instance_id: str = "control-center",
    ) -> None:
        if callback_timeout_seconds <= 0:
            raise ValueError("callback_timeout_seconds 必须大于0")
        self.session_factory = session_factory
        self.http_client_factory = http_client_factory
        self.callback_timeout_seconds = callback_timeout_seconds
        self.instance_id = instance_id

    @staticmethod
    def _callback_success(callback_result: Any) -> bool:
        return isinstance(callback_result, dict) and callback_result.get("success") is True

    @staticmethod
    def _callback_payload(task, event: MediaEventMessage) -> dict[str, Any]:
        """构造发送给业务系统的标准回调体。"""

        return {
            "task_id": task.id,
            "request_id": task.request_id,
            "idempotency_key": task.idempotency_key,
            "school_code": task.school_code,
            "task_type": task.task_type,
            "status": task.status,
            "progress": task.progress,
            "result": task.result or event.result or {},
            "error_message": task.error_message or event.error_message,
            "event_type": event.event_type,
            "event_message_id": event.message_id,
            "worker_node_id": event.node_id,
        }

    @staticmethod
    def _callback_result(
        *,
        success: bool,
        event: MediaEventMessage,
        status_code: int | None = None,
        response_text: str | None = None,
        error_message: str | None = None,
        skipped: bool = False,
    ) -> dict[str, Any]:
        """生成可写入 ``media_task.hdjg`` 的回调结果。"""

        if response_text and len(response_text) > 1000:
            response_text = response_text[:1000]
        if error_message and len(error_message) > 1000:
            error_message = error_message[:1000]
        return {
            "success": success,
            "skipped": skipped,
            "event_type": event.event_type,
            "event_message_id": event.message_id,
            "status_code": status_code,
            "response_text": response_text,
            "error_message": error_message,
            "handled_at": datetime.now(timezone.utc).isoformat(),
        }

    def _load_task(self, event: MediaEventMessage):
        with self.session_factory() as session:
            repository = MediaTaskRepository(session)
            task = repository.get(event.task_id or "")
            if task is None:
                raise TaskPermanentError(
                    f"事件对应任务不存在: task_id={event.task_id}"
                )
            session.expunge(task)
            return task

    def _write_callback_result(
        self,
        task_id: str,
        callback_result: dict[str, Any],
    ) -> None:
        with self.session_factory() as session:
            with session.begin():
                updated = MediaTaskRepository(session).mark_callback_result(
                    task_id,
                    callback_result,
                    updated_by=self.instance_id,
                )
                if not updated:
                    raise TaskRetryableError(
                        f"回调结果写入失败: task_id={task_id}"
                    )
        LOGGER.info(
            "业务回调结果已写入MySQL: task_id=%s, success=%s, skipped=%s, "
            "status_code=%s",
            task_id,
            callback_result.get("success"),
            callback_result.get("skipped"),
            callback_result.get("status_code"),
        )

    def _post_callback(
        self,
        callback_url: str,
        payload: dict[str, Any],
    ) -> tuple[int, str]:
        client_factory = self.http_client_factory or (
            lambda: httpx.Client(timeout=self.callback_timeout_seconds)
        )
        with client_factory() as client:
            response = client.post(callback_url, json=payload)
        return response.status_code, response.text

    def handle(self, event: MediaEventMessage) -> None:
        """处理一条媒体事件。

        ``TaskRetryableError`` 交给 RabbitMQ 事件消费者做延迟重试；不可恢复契约
        错误进入 DLQ。重复成功事件通过 ``callback_result.success`` 幂等跳过。
        """

        if event.event_type not in self._SUPPORTED_EVENTS:
            LOGGER.info("忽略暂不处理的媒体事件: event_type=%s", event.event_type)
            return
        if not event.task_id:
            raise TaskPermanentError("任务事件缺少task_id")

        LOGGER.info(
            "收到媒体任务事件: event_type=%s, task_id=%s, event_message_id=%s, "
            "source=%s",
            event.event_type,
            event.task_id,
            event.message_id,
            event.source,
        )

        task = self._load_task(event)
        if self._callback_success(task.callback_result):
            LOGGER.info("业务回调已成功，跳过重复事件: task_id=%s", task.id)
            return

        task_status = str(task.status or "").lower()
        if (
            event.event_type == "task.completed"
            and task_status != TaskStatus.COMPLETED.value
        ):
            raise TaskRetryableError(
                f"任务尚未完成，等待MySQL状态同步: task_id={task.id}"
            )
        if (
            event.event_type == "task.failed"
            and task_status != TaskStatus.FAILED.value
        ):
            raise TaskRetryableError(
                f"任务尚未最终失败，等待MySQL状态同步: task_id={task.id}"
            )

        if not task.callback_url:
            self._write_callback_result(
                task.id,
                self._callback_result(
                    success=True,
                    skipped=True,
                    event=event,
                    response_text="任务未配置callback_url，跳过业务回调",
                ),
            )
            LOGGER.info("任务未配置业务回调地址，已标记跳过: task_id=%s", task.id)
            return

        payload = self._callback_payload(task, event)
        try:
            LOGGER.info(
                "准备发送业务回调: task_id=%s, callback_url=%s, event_type=%s",
                task.id,
                sanitize_url(task.callback_url),
                event.event_type,
            )
            status_code, response_text = self._post_callback(
                task.callback_url,
                payload,
            )
        except Exception as exc:
            self._write_callback_result(
                task.id,
                self._callback_result(
                    success=False,
                    event=event,
                    error_message=str(exc),
                ),
            )
            LOGGER.warning(
                "业务回调请求异常，等待事件重试: task_id=%s, error=%s",
                task.id,
                exc,
            )
            raise TaskRetryableError(f"业务回调请求异常: {exc}") from exc

        success = 200 <= status_code < 300
        self._write_callback_result(
            task.id,
            self._callback_result(
                success=success,
                event=event,
                status_code=status_code,
                response_text=response_text,
                error_message=None if success else response_text,
            ),
        )
        if not success:
            LOGGER.warning(
                "业务回调响应失败，等待事件重试: task_id=%s, status_code=%s, "
                "response=%s",
                task.id,
                status_code,
                response_text[:500] if response_text else "",
            )
            raise TaskRetryableError(
                f"业务回调响应失败: task_id={task.id}, status_code={status_code}"
            )
        LOGGER.info("业务回调成功: task_id=%s, status_code=%s", task.id, status_code)

    def retry_callback(self, task_id: str) -> None:
        """手动补发已完成或最终失败任务的业务回调，不重新执行媒体任务。"""

        with self.session_factory() as session:
            task = MediaTaskRepository(session).get(task_id)
            if task is None:
                raise LookupError(f"任务不存在: {task_id}")
            session.expunge(task)

        task_status = str(task.status or "").lower()
        if task_status not in {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value}:
            raise TaskRetryableError("只有已完成或最终失败任务允许补发回调")
        self.handle(
            MediaEventMessage(
                source="control-center-admin",
                event_type=(
                    "task.completed"
                    if task_status == TaskStatus.COMPLETED.value
                    else "task.failed"
                ),
                aggregate_id=task.id,
                task_id=task.id,
                node_id=task.executor_node_id,
                status=task.status,
                result=task.result or {},
                error_message=task.error_message,
            )
        )


__all__ = ["MediaEventHandler"]
