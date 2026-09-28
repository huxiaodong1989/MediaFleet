"""调用中心录制命令下发服务。

本服务只负责构造跨进程命令契约并交给发布端口，不导入 recorder-node 的任何
运行时实现。录制节点收到命令后，才在本机执行 StreamRecorder 等本地能力。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from media_platform.application.ports import PublishReceipt, RecorderCommandPublisher
from media_platform.contracts.command import (
    RecorderCommandMessage,
    RecorderCommandType,
)


@dataclass(frozen=True)
class RecorderCommandDispatchResult:
    """一次录制命令下发结果。"""

    message: RecorderCommandMessage
    receipt: PublishReceipt


class RecorderCommandDispatchService:
    """构造并发布必须定向到指定 recorder-node 的录制命令。"""

    def __init__(self, publisher: RecorderCommandPublisher):
        self.publisher = publisher

    @staticmethod
    def _require_text(value: str, *, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} 不能为空")
        return normalized

    def _dispatch(
        self,
        *,
        task_id: str,
        target_node_id: str,
        command: RecorderCommandType,
        params: dict[str, Any] | None = None,
        message_id: str | None = None,
        trace_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> RecorderCommandDispatchResult:
        message_values: dict[str, Any] = {
            "task_id": self._require_text(task_id, field_name="task_id"),
            "target_node_id": self._require_text(
                target_node_id,
                field_name="target_node_id",
            ),
            "command": command,
            "params": params or {},
            "trace_id": trace_id,
            "idempotency_key": idempotency_key,
        }
        if message_id is not None:
            message_values["message_id"] = self._require_text(
                message_id,
                field_name="message_id",
            )

        message = RecorderCommandMessage(**message_values)
        receipt = self.publisher.publish(message)
        return RecorderCommandDispatchResult(message=message, receipt=receipt)

    def send_record_start(
        self,
        *,
        task_id: str,
        target_node_id: str,
        app: str,
        stream_id: str,
        params: dict[str, Any] | None = None,
        message_id: str | None = None,
        trace_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> RecorderCommandDispatchResult:
        """下发开始录制命令。

        ``app`` 和 ``stream_id`` 是 recorder-node 录制器启动所需的最小参数。
        额外录制参数继续放在 ``params`` 中透传，便于旧 API 分批迁移。
        """

        payload = dict(params or {})
        payload["app"] = self._require_text(app, field_name="app")
        payload["stream_id"] = self._require_text(
            stream_id,
            field_name="stream_id",
        )
        return self._dispatch(
            task_id=task_id,
            target_node_id=target_node_id,
            command=RecorderCommandType.RECORD_START,
            params=payload,
            message_id=message_id,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
        )

    def send_record_stop(
        self,
        *,
        task_id: str,
        target_node_id: str,
        delete_from_memory: bool = False,
        params: dict[str, Any] | None = None,
        message_id: str | None = None,
        trace_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> RecorderCommandDispatchResult:
        """下发停止录制命令。

        停止命令按 ``task_id`` 定位 recorder-node 本机录制任务。它表示“提前结束
        正在录制的流并完成后续处理”，适用于计划录制中途终止和开放式录制手动停止。
        recorder-node 会继续执行停止 ZL 录制、查找碎片、合并和后处理，不把任务
        当作“取消并丢弃结果”。不存在本地任务时，recorder-node 处理器会按幂等
        成功处理，避免重复停止造成业务失败。
        """

        payload = dict(params or {})
        payload["delete_from_memory"] = bool(delete_from_memory)
        return self._dispatch(
            task_id=task_id,
            target_node_id=target_node_id,
            command=RecorderCommandType.RECORD_STOP,
            params=payload,
            message_id=message_id,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
        )


__all__ = [
    "RecorderCommandDispatchResult",
    "RecorderCommandDispatchService",
]
