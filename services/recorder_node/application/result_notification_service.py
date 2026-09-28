"""录制结果通知应用服务。

录制完成后的通知链路属于 recorder-node 本机职责：先把最终任务状态和结果写入
国标任务表 ``media_task``，再构造兼容 RTC 业务的结果体，发布录制结果 RabbitMQ
通知，最后按任务传入的 ``callback_url`` 执行业务 HTTP 回调。回调是否成功只记录
到 ``media_task.hdjg``，不改变已经完成的录制结果状态。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import asyncio
import inspect
import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from media_platform.common.redaction import sanitize_url
from media_platform.infrastructure.database.session import SessionLocal
from services.recorder_node.application.task_result_service import (
    RecordingTaskResultService,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecordingCallbackSendResult:
    """一次录制业务 HTTP 回调请求的结果。"""

    success: bool
    status_code: int | None = None
    response_text: str | None = None
    error_message: str | None = None


CallbackSender = Callable[[str, dict[str, Any]], Any]


class RecordingResultNotificationService:
    """协调录制结果 MQ 通知、HTTP 回调和回调结果落库。"""

    def __init__(
        self,
        *,
        payload_builder: Any,
        result_notifier: Any | None,
        session_factory: Callable[[], Session] | None = None,
        callback_sender: CallbackSender | None = None,
        task_result_service: RecordingTaskResultService | None = None,
        node_id: str = "recorder-node",
        callback_max_retries: int = 3,
        callback_retry_interval_seconds: float = 5.0,
        callback_timeout_seconds: float = 30.0,
    ) -> None:
        if callback_max_retries < 1:
            raise ValueError("callback_max_retries 必须大于0")
        if callback_retry_interval_seconds < 0:
            raise ValueError("callback_retry_interval_seconds 不能小于0")
        if callback_timeout_seconds <= 0:
            raise ValueError("callback_timeout_seconds 必须大于0")

        self.payload_builder = payload_builder
        self.result_notifier = result_notifier
        self.session_factory = session_factory or SessionLocal
        self.callback_sender = callback_sender or self._send_http_callback
        self.node_id = node_id
        self.task_result_service = task_result_service or RecordingTaskResultService(
            session_factory=self.session_factory,
            node_id=node_id,
        )
        self.callback_max_retries = callback_max_retries
        self.callback_retry_interval_seconds = callback_retry_interval_seconds
        self.callback_timeout_seconds = callback_timeout_seconds

    async def notify(self, *, task_id: str, task_status: dict[str, Any]) -> bool:
        """发送录制结果通知。

        Returns:
            有 HTTP ``callback_url`` 时返回 HTTP 回调是否最终成功；未配置回调地址时
            返回 ``True``，表示录制结果通知链路已完成可执行部分。
        """

        payload = await self.payload_builder.build(
            task_id=task_id,
            task_status=task_status,
        )
        LOGGER.info("录制结果通知数据已构造: task_id=%s, status=%s", task_id, payload.get("status"))
        self._write_final_task_result(
            task_id=task_id,
            task_status=task_status,
            payload=payload,
        )

        mq_published = self._publish_mq_result(task_id, payload)
        callback_url = str(task_status.get("callback_url") or "").strip()
        if not callback_url:
            LOGGER.info(
                "录制任务未配置HTTP回调URL，跳过HTTP回调: task_id=%s, mq_published=%s",
                task_id,
                mq_published,
            )
            self._write_callback_result(
                task_id,
                {
                    "success": False,
                    "skipped": True,
                    "status_code": None,
                    "response_text": "录制任务未配置callback_url，跳过HTTP回调",
                    "error_message": None,
                    "callback_url": None,
                    "callback_at": datetime.now().isoformat(),
                    "mq_published": mq_published,
                    "callback_payload": payload,
                },
            )
            return True

        callback_success = await self._send_callback_with_retry(
            task_id=task_id,
            callback_url=callback_url,
            payload=payload,
            mq_published=mq_published,
        )
        return callback_success

    def _write_final_task_result(
        self,
        *,
        task_id: str,
        task_status: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """把录制最终状态写入国标任务表。

        录制任务不走通用 Worker 执行领取，所以不能复用
        ``mark_execution_completed`` 的 ``message_id`` 条件更新。这里按
        ``task_id`` 写入最终结果，供调用中心原录制状态接口和统一任务查询接口读取。
        """

        status = str(task_status.get("status") or payload.get("status") or "").lower()
        result_payload = self._recording_result_payload(task_status, payload)
        if status == "completed":
            self.task_result_service.mark_completed(
                task_id=task_id,
                result_payload=result_payload,
            )
            return
        if status == "failed":
            self.task_result_service.mark_failed(
                task_id=task_id,
                error_message=str(
                    task_status.get("error")
                    or task_status.get("error_message")
                    or "录制任务失败"
                )[:2000],
                result_payload=result_payload,
            )
            return
        LOGGER.info(
            "录制任务尚未进入最终状态，跳过最终状态落库: task_id=%s, status=%s",
            task_id,
            status or "<empty>",
        )

    @staticmethod
    def _recording_result_payload(
        task_status: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """构造写入 ``media_task.zxjg`` 的录制结果。

        保留原状态查询常用字段，同时保存 RTC MQ 通知体摘要，方便排障和后续查询
        不再回退到旧 dispatcher 或旧表。
        """

        return {
            "status": str(task_status.get("status") or payload.get("status") or ""),
            "result_url": task_status.get("upload_url") or task_status.get("result_url"),
            "audio_url": task_status.get("audio_result_url"),
            "cover_url": task_status.get("cover_url"),
            "failed_stage": task_status.get("failed_stage"),
            "post_processing_state": task_status.get("post_processing_state"),
            "post_processing_errors": list(
                task_status.get("post_processing_errors") or []
            ),
            "post_processing_recovery": dict(
                task_status.get("post_processing_recovery") or {}
            ),
            "db_save_retry_attempts": task_status.get("db_save_retry_attempts", 0),
            "segments_count": len(task_status.get("segments", [])),
            "duration": sum(
                segment.get("time_len", 0)
                for segment in task_status.get("segments", [])
                if isinstance(segment, dict)
            ),
            "was_canceled": bool(task_status.get("early_termination", False)),
            "cancellation_note": task_status.get("cancellation_note", ""),
            "extra_info": {
                "stream_id": task_status.get("stream_id"),
                "original_stream_id": task_status.get("original_stream_id"),
                "actual_start_time": task_status.get("actual_start_time"),
                "actual_end_time": task_status.get("actual_end_time"),
                "video_file_id": task_status.get("video_file_id"),
                "audio_file_id": task_status.get("audio_file_id"),
                "cover_file_id": task_status.get("cover_file_id"),
                "mq_payload": payload,
            },
        }

    def _publish_mq_result(self, task_id: str, payload: dict[str, Any]) -> bool:
        """发布录制结果 RabbitMQ 通知，供 RTC 业务继续按原链路消费。"""

        if self.result_notifier is None:
            LOGGER.warning("录制结果通知器未初始化，跳过RabbitMQ通知: task_id=%s", task_id)
            return False

        try:
            published = bool(self.result_notifier.publish(task_id=task_id, message_data=payload))
        except Exception:
            LOGGER.exception("录制结果RabbitMQ通知异常: task_id=%s", task_id)
            return False

        if published:
            LOGGER.info("录制结果RabbitMQ通知已发布: task_id=%s", task_id)
        else:
            LOGGER.error("录制结果RabbitMQ通知发布失败: task_id=%s", task_id)
        return published

    async def _send_callback_with_retry(
        self,
        *,
        task_id: str,
        callback_url: str,
        payload: dict[str, Any],
        mq_published: bool,
    ) -> bool:
        """按固定次数发送 HTTP 回调，并记录每次尝试结果。"""

        attempts: list[dict[str, Any]] = []
        send_result = RecordingCallbackSendResult(success=False)
        safe_url = self._safe_url(callback_url)
        for attempt in range(1, self.callback_max_retries + 1):
            LOGGER.info(
                "准备发送录制业务回调: task_id=%s, callback_url=%s, attempt=%s/%s",
                task_id,
                safe_url,
                attempt,
                self.callback_max_retries,
            )
            try:
                send_result = await self._call_callback_sender(callback_url, payload)
            except Exception as exc:
                send_result = RecordingCallbackSendResult(
                    success=False,
                    error_message=str(exc)[:1000],
                )

            attempts.append(
                {
                    "attempt": attempt,
                    "success": send_result.success,
                    "status_code": send_result.status_code,
                    "response_text": send_result.response_text,
                    "error_message": send_result.error_message,
                    "callback_at": datetime.now().isoformat(),
                }
            )
            if send_result.success:
                LOGGER.info(
                    "录制业务回调发送成功: task_id=%s, status_code=%s, attempt=%s/%s",
                    task_id,
                    send_result.status_code,
                    attempt,
                    self.callback_max_retries,
                )
                break

            LOGGER.warning(
                "录制业务回调尝试失败: task_id=%s, attempt=%s/%s, status_code=%s, "
                "error=%s, response=%s",
                task_id,
                attempt,
                self.callback_max_retries,
                send_result.status_code,
                send_result.error_message,
                send_result.response_text,
            )
            if attempt < self.callback_max_retries:
                await asyncio.sleep(self.callback_retry_interval_seconds)

        callback_result = {
            "success": send_result.success,
            "skipped": False,
            "status_code": send_result.status_code,
            "response_text": send_result.response_text,
            "error_message": send_result.error_message,
            "callback_url": safe_url,
            "callback_at": datetime.now().isoformat(),
            "attempt_count": len(attempts),
            "max_retries": self.callback_max_retries,
            "attempts": attempts,
            "mq_published": mq_published,
            "callback_payload": payload,
        }
        self._write_callback_result(task_id, callback_result)

        if not send_result.success:
            LOGGER.error(
                "录制业务回调最终失败: task_id=%s, status_code=%s, error=%s, response=%s",
                task_id,
                send_result.status_code,
                send_result.error_message,
                send_result.response_text,
            )
        return send_result.success

    async def _call_callback_sender(
        self,
        callback_url: str,
        payload: dict[str, Any],
    ) -> RecordingCallbackSendResult:
        """兼容同步或异步测试替身的回调发送器。"""

        result = self.callback_sender(callback_url, payload)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, RecordingCallbackSendResult):
            return result
        if isinstance(result, dict):
            return RecordingCallbackSendResult(**result)
        raise TypeError("callback_sender 必须返回 RecordingCallbackSendResult 或等价字典")

    async def _send_http_callback(
        self,
        callback_url: str,
        payload: dict[str, Any],
    ) -> RecordingCallbackSendResult:
        """异步发送录制业务 HTTP 回调。"""

        try:
            async with httpx.AsyncClient(timeout=self.callback_timeout_seconds) as client:
                response = await client.post(
                    callback_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
            return RecordingCallbackSendResult(
                success=response.status_code < 300,
                status_code=response.status_code,
                response_text=response.text[:1000],
            )
        except Exception as exc:
            return RecordingCallbackSendResult(
                success=False,
                error_message=str(exc)[:1000],
            )

    def _write_callback_result(
        self,
        task_id: str,
        callback_result: dict[str, Any],
    ) -> None:
        """把录制业务回调执行结果写入国标任务表。"""

        if self.session_factory is None:
            LOGGER.warning("未启用同步数据库会话，跳过录制回调结果落库: task_id=%s", task_id)
            return

        try:
            updated = self.task_result_service.mark_callback_result(
                task_id=task_id,
                callback_result=callback_result,
            )
            if not updated:
                return
        except Exception:
            LOGGER.exception("录制回调结果写入MySQL异常: task_id=%s", task_id)
            return

        LOGGER.info(
            "录制回调结果已写入MySQL: task_id=%s, success=%s, skipped=%s, "
            "status_code=%s, attempt_count=%s",
            task_id,
            callback_result.get("success"),
            callback_result.get("skipped"),
            callback_result.get("status_code"),
            callback_result.get("attempt_count"),
        )

    @staticmethod
    def _safe_url(value: str | None) -> str | None:
        """输出可排障但不包含认证信息的 URL。"""

        return sanitize_url(value)


__all__ = ["RecordingCallbackSendResult", "RecordingResultNotificationService"]
