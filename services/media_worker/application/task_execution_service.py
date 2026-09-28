"""通用媒体任务的 MySQL 幂等执行服务。

RabbitMQ 采用至少一次投递，因此同一个 ``message_id`` 可能重复到达。该服务在
调用媒体算法前先通过 ``media_task`` 条件更新领取执行权，并在同一事实表中
记录成功、等待重试或最终失败状态，不依赖进程内集合判断任务是否处理过。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from media_platform.common.redaction import sanitize_url
from media_platform.contracts.task import TaskDispatchMessage
from media_platform.domain.task import TaskStatus
from media_platform.infrastructure.database.models import MediaFileModel
from media_platform.infrastructure.database.repositories import (
    MediaTaskFileRepository,
    MediaTaskRepository,
)
from media_platform.infrastructure.messaging import (
    TaskBusyError,
    TaskPermanentError,
    TaskRetryableError,
)
from services.media_worker.registry import (
    ProcessorResult,
    TaskProcessorRegistry,
    UnsupportedTaskTypeError,
)


LOGGER = logging.getLogger(__name__)

_KNOWN_ARTIFACT_FIELDS = {
    "bucket",
    "bucket_name",
    "cttmc",
    "extra",
    "extra_info",
    "file_name",
    "file_size",
    "file_type",
    "file_url",
    "mime_type",
    "relative_path",
}

_SUPPORTED_FILE_TYPES = {"VIDEO", "AUDIO", "COVER", "SUBTITLE", "OTHER"}


@dataclass(frozen=True)
class CallbackSendResult:
    """一次业务回调 HTTP 请求的结果。"""

    success: bool
    status_code: int | None = None
    response_text: str | None = None
    error_message: str | None = None


CallbackSender = Callable[[str, dict[str, Any]], CallbackSendResult]


class _ExecutionLeaseRenewer:
    """在媒体处理器运行期间续租数据库执行权。

    续租线程只负责延长当前任务租约，不执行任何业务逻辑；数据库仍是执行权
    的唯一事实来源。续租失败时停止继续续租，最终写回会再次用执行代次和租约
    条件校验，避免旧 Worker 覆盖接管者的结果。
    """

    def __init__(
        self,
        service: "TaskExecutionService",
        message: TaskDispatchMessage,
        execution_generation: int,
    ) -> None:
        self.service = service
        self.message = message
        self.execution_generation = execution_generation
        self.stop_event = Event()
        self.thread: Thread | None = None

    def start(self) -> None:
        self.thread = Thread(
            target=self._run,
            name=f"media-worker-lease-{self.message.task_id}",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(
                timeout=min(self.service.lease_renew_interval.total_seconds(), 5.0)
                + 1.0
            )

    def _run(self) -> None:
        while not self.stop_event.wait(
            self.service.lease_renew_interval.total_seconds()
        ):
            try:
                with self.service.session_factory() as session:
                    with session.begin():
                        renewed = MediaTaskRepository(session).renew_execution_lease(
                            self.message.task_id,
                            self.message.message_id,
                            self.service.worker_id,
                            self.execution_generation,
                            lease_timeout=self.service.lease_timeout,
                        )
                if not renewed:
                    LOGGER.warning(
                        "媒体任务执行租约续租失败，停止续租并等待最终写回校验: "
                        "task_id=%s, execution_generation=%s, worker_id=%s",
                        self.message.task_id,
                        self.execution_generation,
                        self.service.worker_id,
                    )
                    return
                LOGGER.debug(
                    "媒体任务执行租约已续租: task_id=%s, execution_generation=%s",
                    self.message.task_id,
                    self.execution_generation,
                )
            except Exception:
                # 数据库短暂不可用时继续尝试；真正失去租约由最终条件更新判定。
                LOGGER.exception(
                    "媒体任务执行租约续租异常: task_id=%s, execution_generation=%s",
                    self.message.task_id,
                    self.execution_generation,
                )


class TaskExecutionService:
    """协调数据库任务状态与媒体处理器执行。

    Args:
        session_factory: 创建同步 SQLAlchemy Session 的工厂。
        registry: 按 ``task_type`` 保存媒体处理器的注册表。
        worker_id: 当前 Worker 实例唯一标识，用于任务执行所有权校验。
        execution_timeout: ``PROCESSING`` 状态超过该时长后允许其他实例恢复。
        callback_timeout_seconds: 业务回调 HTTP 超时时间；回调失败只记录到
            `media_task.hdjg`，不改变媒体处理任务的最终状态。
        callback_max_retries: 业务回调最大尝试次数，默认 3 次。
        callback_retry_interval_seconds: 两次业务回调尝试之间的等待时间。
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        registry: TaskProcessorRegistry,
        worker_id: str,
        *,
        execution_timeout: timedelta = timedelta(minutes=30),
        lease_timeout: timedelta = timedelta(minutes=30),
        lease_renew_interval: timedelta | None = None,
        callback_timeout_seconds: float = 10.0,
        callback_max_retries: int = 3,
        callback_retry_interval_seconds: float = 5.0,
        callback_sender: CallbackSender | None = None,
    ) -> None:
        worker_id = worker_id.strip()
        if not worker_id:
            raise ValueError("worker_id 不能为空")
        if execution_timeout <= timedelta(0):
            raise ValueError("execution_timeout 必须大于0")
        if lease_timeout <= timedelta(0):
            raise ValueError("lease_timeout 必须大于0")
        if lease_renew_interval is not None and lease_renew_interval <= timedelta(0):
            raise ValueError("lease_renew_interval 必须大于0")
        if callback_timeout_seconds <= 0:
            raise ValueError("callback_timeout_seconds 必须大于0")
        if callback_max_retries < 1:
            raise ValueError("callback_max_retries 必须大于0")
        if callback_retry_interval_seconds < 0:
            raise ValueError("callback_retry_interval_seconds 不能小于0")

        self.session_factory = session_factory
        self.registry = registry
        self.worker_id = worker_id
        self.execution_timeout = execution_timeout
        self.lease_timeout = lease_timeout
        self.lease_renew_interval = lease_renew_interval or (
            lease_timeout / 3
        )
        if self.lease_renew_interval >= lease_timeout:
            raise ValueError("lease_renew_interval 必须小于 lease_timeout")
        self.callback_timeout_seconds = callback_timeout_seconds
        self.callback_max_retries = callback_max_retries
        self.callback_retry_interval_seconds = callback_retry_interval_seconds
        self.callback_sender = callback_sender or self._send_http_callback
        self._processing_count = 0
        self._processing_count_lock = Lock()

    @property
    def processing_count(self) -> int:
        """返回当前进程正在执行的媒体任务数量。"""

        with self._processing_count_lock:
            return self._processing_count

    def _increase_processing_count(self) -> None:
        with self._processing_count_lock:
            self._processing_count += 1

    def _decrease_processing_count(self) -> None:
        with self._processing_count_lock:
            self._processing_count = max(0, self._processing_count - 1)

    def _claim(self, message: TaskDispatchMessage) -> int | None:
        """校验消息和任务事实记录，并尝试领取执行权。

        Returns:
            ``True`` 表示当前实例获得执行权；``False`` 表示任务已经完成或取消，
            调用方可以安全 ACK 当前重复消息。

        Raises:
            TaskPermanentError: 任务不存在、消息编号不匹配或契约与数据库冲突。
            TaskBusyError: 任务仍由另一个未超时的 Worker 执行。
        """

        now = datetime.now()
        stale_before = now - self.execution_timeout
        with self.session_factory() as session:
            with session.begin():
                repository = MediaTaskRepository(session)
                task = repository.get(message.task_id)
                if task is None:
                    raise TaskPermanentError(
                        f"数据库不存在媒体任务: task_id={message.task_id}"
                    )
                if task.message_id != message.message_id:
                    raise TaskPermanentError(
                        "任务消息编号与数据库不一致: "
                        f"task_id={message.task_id}, message_id={message.message_id}"
                    )
                if task.task_type != message.task_type:
                    raise TaskPermanentError(
                        "任务类型与数据库不一致: "
                        f"task_id={message.task_id}, task_type={message.task_type}"
                    )
                task_status = str(task.status or "").lower()
                terminal_statuses = {
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                }
                if task_status in terminal_statuses:
                    return None

                execution_generation = repository.claim_execution_with_lease(
                    message.task_id,
                    message.message_id,
                    self.worker_id,
                    attempt=message.attempt,
                    stale_before=stale_before,
                    started_at=now,
                    lease_timeout=self.lease_timeout,
                )
                if execution_generation is not None:
                    LOGGER.info(
                        "Worker已领取媒体任务执行权: task_id=%s, "
                        "message_id=%s, worker_id=%s, attempt=%s/%s, "
                        "execution_generation=%s, lease_expires_in=%ss",
                        message.task_id,
                        message.message_id,
                        self.worker_id,
                        message.attempt,
                        message.max_attempts,
                        execution_generation,
                        int(self.lease_timeout.total_seconds()),
                    )
                    return execution_generation

                # 条件更新失败通常表示另一个 Worker 已经先取得执行权。重新读取
                # 最新状态，避免把并发完成的重复消息误判为异常。
                session.expire_all()
                latest = repository.get(message.task_id)
                latest_status = str(latest.status or "").lower() if latest else ""
                if latest is not None and latest_status in terminal_statuses:
                    return False
                if latest is not None:
                    LOGGER.warning(
                        "媒体任务执行权领取失败，任务仍由其他Worker处理: "
                        "task_id=%s, status=%s, publish_status=%s, "
                        "executor_node_id=%s, started_at=%s, retry_count=%s, "
                        "message_id=%s, current_worker_id=%s",
                        latest.id,
                        latest.status,
                        latest.publish_status,
                        latest.executor_node_id,
                        latest.started_at,
                        latest.retry_count,
                        latest.message_id,
                        self.worker_id,
                    )
                raise TaskBusyError(
                    f"任务正在其他Worker执行: task_id={message.task_id}"
                )

    @staticmethod
    def _result_payload(result: ProcessorResult) -> dict:
        """把处理器标准结果转换为可持久化 JSON 对象。"""

        payload = dict(result.payload)
        if result.artifacts:
            payload["artifacts"] = [dict(item) for item in result.artifacts]
        return payload

    @staticmethod
    def _safe_url(value: str | None) -> str | None:
        """输出可排障但不包含认证信息的 URL。"""

        return sanitize_url(value)

    @staticmethod
    def _artifact_file_type(artifact: dict[str, Any]) -> str:
        """把处理器产物类型归一到国标文件表支持的枚举范围。"""

        raw_type = str(artifact.get("file_type") or "OTHER").strip().upper()
        if raw_type in _SUPPORTED_FILE_TYPES:
            return raw_type
        return "OTHER"

    @staticmethod
    def _artifact_file_name(
        artifact: dict[str, Any],
        *,
        task_id: str,
        index: int,
    ) -> str:
        """从产物描述或 URL 中推导文件名，避免数据库非空字段缺失。"""

        configured = artifact.get("file_name")
        if isinstance(configured, str) and configured.strip():
            return configured.strip()[:255]

        file_url = str(artifact.get("file_url") or "")
        try:
            path = unquote(urlsplit(file_url).path)
        except ValueError:
            path = ""
        name = path.rsplit("/", maxsplit=1)[-1].strip()
        if name:
            return name[:255]
        return f"{task_id}-{index + 1}"[:255]

    @staticmethod
    def _artifact_file_size(artifact: dict[str, Any]) -> int:
        """清理文件大小；未知大小按 0 写入，保持查询接口可用。"""

        value = artifact.get("file_size")
        if value is None:
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _artifact_mime_type(artifact: dict[str, Any]) -> str:
        """清理 MIME 类型；处理器未提供时使用通用二进制类型。"""

        value = artifact.get("mime_type")
        if isinstance(value, str) and value.strip():
            return value.strip()[:128]
        return "application/octet-stream"

    @staticmethod
    def _artifact_extra_info(artifact: dict[str, Any], file_type: str) -> dict[str, Any] | None:
        """合并处理器额外元数据，保留原始未知产物类型便于排障。"""

        extra_info: dict[str, Any] = {}
        explicit_extra = artifact.get("extra_info")
        if isinstance(explicit_extra, dict):
            extra_info.update(explicit_extra)
        legacy_extra = artifact.get("extra")
        if isinstance(legacy_extra, dict):
            extra_info.update(legacy_extra)

        raw_type = str(artifact.get("file_type") or "").strip().upper()
        if raw_type and raw_type != file_type:
            extra_info["source_file_type"] = raw_type

        for key, value in artifact.items():
            if key not in _KNOWN_ARTIFACT_FIELDS:
                extra_info[key] = value
        return extra_info or None

    def _artifact_models(
        self,
        message: TaskDispatchMessage,
        result: ProcessorResult,
    ) -> list[MediaFileModel]:
        """把处理器产物描述转换为国标媒体文件表记录。"""

        models: list[MediaFileModel] = []
        for index, item in enumerate(result.artifacts):
            artifact = dict(item)
            file_url = artifact.get("file_url")
            if not isinstance(file_url, str) or not file_url.strip():
                LOGGER.warning(
                    "跳过缺少file_url的媒体产物: task_id=%s, index=%s",
                    message.task_id,
                    index,
                )
                continue

            file_type = self._artifact_file_type(artifact)
            models.append(
                MediaFileModel(
                    id=str(uuid4()),
                    task_id=message.task_id,
                    file_type=file_type,
                    file_name=self._artifact_file_name(
                        artifact,
                        task_id=message.task_id,
                        index=index,
                    ),
                    file_url=file_url.strip(),
                    relative_path=(
                        str(artifact["relative_path"]).strip()
                        if artifact.get("relative_path") is not None
                        else None
                    ),
                    bucket_name=(
                        str(
                            artifact.get("bucket_name")
                            or artifact.get("bucket")
                            or artifact.get("cttmc")
                        ).strip()
                        if (
                            artifact.get("bucket_name")
                            or artifact.get("bucket")
                            or artifact.get("cttmc")
                        )
                        else None
                    ),
                    file_size=self._artifact_file_size(artifact),
                    mime_type=self._artifact_mime_type(artifact),
                    extra_info=self._artifact_extra_info(artifact, file_type),
                    created_by=self.worker_id,
                    updated_by=self.worker_id,
                    school_code=message.school_code,
                )
            )
        return models

    def _mark_completed(
        self,
        message: TaskDispatchMessage,
        result: ProcessorResult,
        execution_generation: int,
    ) -> None:
        """持久化完成结果和产物文件，并校验当前 Worker 仍拥有执行权。"""

        result_payload = self._result_payload(result)
        LOGGER.info(
            "准备写入媒体任务完成结果: task_id=%s, worker_id=%s, "
            "artifact_count=%s",
            message.task_id,
            self.worker_id,
            len(result.artifacts),
        )
        with self.session_factory() as session:
            with session.begin():
                updated = MediaTaskRepository(session).mark_execution_completed(
                    message.task_id,
                    message.message_id,
                    self.worker_id,
                    result_payload,
                    execution_generation=execution_generation,
                )
                if not updated:
                    raise TaskRetryableError(
                        f"任务完成结果写入失败: task_id={message.task_id}"
                    )
                file_models = self._artifact_models(message, result)
                MediaTaskFileRepository(session).add_many(file_models)
                if file_models:
                    LOGGER.info(
                        "媒体任务产物已写入MySQL: task_id=%s, file_count=%s",
                        message.task_id,
                        len(file_models),
                    )
                    for file_model in file_models:
                        LOGGER.info(
                            "媒体任务产物详情: task_id=%s, file_type=%s, "
                            "bucket=%s, file_name=%s, file_url=%s",
                            message.task_id,
                            file_model.file_type,
                            file_model.bucket_name,
                            file_model.file_name,
                            self._safe_url(file_model.file_url),
                        )
        LOGGER.info(
            "媒体任务完成结果已写入MySQL: task_id=%s, worker_id=%s, "
            "status=%s",
            message.task_id,
            self.worker_id,
            TaskStatus.COMPLETED.value,
        )

    def _send_http_callback(
        self,
        callback_url: str,
        payload: dict[str, Any],
    ) -> CallbackSendResult:
        """同步发送业务回调。

        Worker 消费线程本身是同步模型，因此这里使用同步 httpx Client。请求异常
        会转换成结构化结果并写入数据库，不向外抛出影响 RabbitMQ ACK。
        """

        try:
            with httpx.Client(timeout=self.callback_timeout_seconds) as client:
                response = client.post(
                    callback_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
            return CallbackSendResult(
                success=response.status_code < 300,
                status_code=response.status_code,
                response_text=response.text[:1000],
            )
        except Exception as exc:
            return CallbackSendResult(
                success=False,
                error_message=str(exc)[:1000],
            )

    def _callback_payload(
        self,
        message: TaskDispatchMessage,
        *,
        status: TaskStatus,
        result_payload: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """构造业务回调体，保持调用方能直接定位任务和处理结果。"""

        return {
            "task_id": message.task_id,
            "request_id": message.trace_id,
            "idempotency_key": message.idempotency_key,
            "school_code": message.school_code,
            "task_type": message.task_type,
            "status": status.value,
            "progress": 100 if status == TaskStatus.COMPLETED else 0,
            "result": result_payload or {},
            "error_message": error_message,
            "executor_node_id": self.worker_id,
        }

    def _write_callback_result(
        self,
        task_id: str,
        callback_result: dict[str, Any],
    ) -> None:
        """把业务回调执行结果写入国标任务表。"""

        with self.session_factory() as session:
            with session.begin():
                updated = MediaTaskRepository(session).mark_callback_result(
                    task_id,
                    callback_result,
                    updated_by=self.worker_id,
                )
                if not updated:
                    LOGGER.error("业务回调结果写入失败，任务不存在: task_id=%s", task_id)
                    return
        LOGGER.info(
            "业务回调结果已写入MySQL: task_id=%s, success=%s, skipped=%s, "
            "status_code=%s",
            task_id,
            callback_result.get("success"),
            callback_result.get("skipped"),
            callback_result.get("status_code"),
        )

    def _handle_business_callback(
        self,
        message: TaskDispatchMessage,
        *,
        status: TaskStatus,
        result_payload: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        """由当前执行 Worker 直接处理业务回调并记录完整过程。

        回调是任务完成后的通知步骤。媒体处理结果已经写入 MySQL 后，即使回调
        地址不可达，也不应把已完成的媒体任务改回失败；失败原因记录到 `hdjg`，
        供 Swagger 查询和后续人工/接口补偿。
        """

        callback_url = (message.callback_url or "").strip()
        payload = self._callback_payload(
            message,
            status=status,
            result_payload=result_payload,
            error_message=error_message,
        )
        if not callback_url:
            callback_result = {
                "success": False,
                "skipped": True,
                "status_code": None,
                "response_text": "任务未配置callback_url，跳过业务回调",
                "error_message": None,
                "callback_url": None,
                "callback_at": datetime.now().isoformat(),
                "callback_payload": payload,
            }
            LOGGER.warning(
                "任务未配置业务回调地址，已记录跳过: task_id=%s",
                message.task_id,
            )
            self._write_callback_result(message.task_id, callback_result)
            return

        attempts: list[dict[str, Any]] = []
        send_result = CallbackSendResult(success=False)
        for attempt in range(1, self.callback_max_retries + 1):
            LOGGER.info(
                "准备发送业务回调: task_id=%s, callback_url=%s, status=%s, "
                "attempt=%s/%s",
                message.task_id,
                self._safe_url(callback_url),
                status.value,
                attempt,
                self.callback_max_retries,
            )
            try:
                send_result = self.callback_sender(callback_url, payload)
            except Exception as exc:
                send_result = CallbackSendResult(
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
                break

            LOGGER.warning(
                "业务回调尝试失败: task_id=%s, attempt=%s/%s, "
                "status_code=%s, error=%s, response=%s",
                message.task_id,
                attempt,
                self.callback_max_retries,
                send_result.status_code,
                send_result.error_message,
                send_result.response_text,
            )
            if attempt < self.callback_max_retries:
                time.sleep(self.callback_retry_interval_seconds)

        callback_result = {
            "success": send_result.success,
            "skipped": False,
            "status_code": send_result.status_code,
            "response_text": send_result.response_text,
            "error_message": send_result.error_message,
            "callback_url": self._safe_url(callback_url),
            "callback_at": datetime.now().isoformat(),
            "attempt_count": len(attempts),
            "max_retries": self.callback_max_retries,
            "attempts": attempts,
            "callback_payload": payload,
        }
        self._write_callback_result(message.task_id, callback_result)
        if send_result.success:
            LOGGER.info(
                "业务回调成功: task_id=%s, status_code=%s",
                message.task_id,
                send_result.status_code,
            )
        else:
            LOGGER.error(
                "业务回调失败: task_id=%s, status_code=%s, error=%s, "
                "response=%s",
                message.task_id,
                send_result.status_code,
                send_result.error_message,
                send_result.response_text,
            )

    def _mark_failed(
        self,
        message: TaskDispatchMessage,
        error: Exception,
        *,
        retryable: bool,
        execution_generation: int,
    ) -> None:
        """根据剩余次数记录等待重试或最终失败状态。"""

        error_message = str(error)[:4000]
        next_attempt = message.attempt + 1
        should_retry = retryable and next_attempt < message.max_attempts
        should_callback_failure = False
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
                        execution_generation=execution_generation,
                    )
                    if updated:
                        LOGGER.warning(
                            "媒体任务处理失败，已恢复为待重试: task_id=%s, "
                            "worker_id=%s, next_attempt=%s/%s, error=%s",
                            message.task_id,
                            self.worker_id,
                            next_attempt,
                            message.max_attempts,
                            error_message,
                        )
                else:
                    updated = repository.mark_execution_failed(
                        message.task_id,
                        message.message_id,
                        self.worker_id,
                        error_message,
                        execution_generation=execution_generation,
                    )
                    if updated:
                        LOGGER.error(
                            "媒体任务处理失败并进入最终失败: task_id=%s, "
                            "worker_id=%s, error=%s",
                            message.task_id,
                            self.worker_id,
                            error_message,
                        )
                        should_callback_failure = True
                if not updated:
                    LOGGER.error(
                        "任务失败状态写入未命中当前执行权: task_id=%s, worker_id=%s",
                        message.task_id,
                        self.worker_id,
                    )
        if should_callback_failure:
            self._handle_business_callback(
                message,
                status=TaskStatus.FAILED,
                error_message=error_message,
            )

    def handle(self, message: TaskDispatchMessage) -> None:
        """幂等执行一条媒体任务消息，供 RabbitMQ 消费者直接调用。

        已完成或已取消任务直接返回，让消费者 ACK 重复消息。处理器可恢复失败
        转换为 ``TaskRetryableError``，永久错误转换为 ``TaskPermanentError``，
        由消息消费者决定进入延迟重试队列还是最终 DLQ。
        """

        execution_generation = self._claim(message)
        if execution_generation is None:
            LOGGER.info("跳过已结束任务的重复消息: task_id=%s", message.task_id)
            return

        LOGGER.info(
            "开始执行媒体任务处理器: task_id=%s, task_type=%s, worker_id=%s",
            message.task_id,
            message.task_type,
            self.worker_id,
        )
        lease_renewer = _ExecutionLeaseRenewer(
            self,
            message,
            execution_generation,
        )
        lease_renewer.start()
        self._increase_processing_count()
        try:
            try:
                result = self.registry.process(message)
            except Exception as exc:
                self._handle_processor_error(
                    message,
                    exc,
                    execution_generation=execution_generation,
                )
                raise AssertionError("媒体处理异常未按预期抛出")
            self._mark_completed(message, result, execution_generation)
            self._handle_business_callback(
                message,
                status=TaskStatus.COMPLETED,
                result_payload=self._result_payload(result),
            )
        finally:
            self._decrease_processing_count()
            lease_renewer.stop()
        LOGGER.info(
            "媒体任务处理完成、结果落库并已处理业务回调: "
            "task_id=%s, task_type=%s, worker_id=%s",
            message.task_id,
            message.task_type,
            self.worker_id,
        )

    def _handle_processor_error(
        self,
        message: TaskDispatchMessage,
        error: Exception,
        *,
        execution_generation: int,
    ) -> None:
        """按异常类型写入任务失败状态并抛出消费者可识别的异常。"""

        if isinstance(error, UnsupportedTaskTypeError):
            permanent_error = TaskPermanentError(str(error))
            self._mark_failed(
                message,
                permanent_error,
                retryable=False,
                execution_generation=execution_generation,
            )
            raise permanent_error from error
        if isinstance(error, TaskPermanentError):
            self._mark_failed(
                message,
                error,
                retryable=False,
                execution_generation=execution_generation,
            )
            raise error
        self._mark_failed(
            message,
            error,
            retryable=True,
            execution_generation=execution_generation,
        )
        raise TaskRetryableError(str(error)) from error


__all__ = ["CallbackSendResult", "TaskExecutionService"]
