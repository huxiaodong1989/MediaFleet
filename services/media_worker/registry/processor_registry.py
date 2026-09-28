"""通用媒体 Worker 的任务处理器注册表。

注册表替代旧 ``node/main.py`` 中持续增长的 ``if/elif`` 分支。每种任务类型只
注册一个处理器；具体处理器可以继续复用现有视频、音频和识别算法，迁移时无需
改变 RabbitMQ 消费器或服务入口。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from media_platform.contracts.task import TaskDispatchMessage


@dataclass(frozen=True)
class ProcessorResult:
    """媒体处理器返回的标准结果。

    ``payload`` 保存可序列化业务结果；``artifacts`` 保存视频、音频、封面等
    产物描述。Worker 执行服务会把二者写入 MySQL，并据此构造业务回调体。
    """

    payload: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[Mapping[str, Any], ...] = ()


class MediaTaskProcessor(Protocol):
    """单一媒体任务类型处理器必须实现的同步端口。"""

    def process(self, message: TaskDispatchMessage) -> ProcessorResult:
        """执行任务并返回标准结果；失败时抛出具有业务含义的异常。"""


class DuplicateProcessorError(ValueError):
    """同一任务类型被重复注册。"""


class UnsupportedTaskTypeError(LookupError):
    """Worker 收到未注册的任务类型。"""


class TaskProcessorRegistry:
    """按精确 ``task_type`` 查找媒体处理器的可变注册表。"""

    def __init__(self) -> None:
        self._processors: dict[str, MediaTaskProcessor] = {}

    @staticmethod
    def _normalize_task_type(task_type: str) -> str:
        """清理任务类型并拒绝空值，防止注册和查找使用不同键。"""

        normalized = task_type.strip()
        if not normalized:
            raise ValueError("task_type 不能为空")
        return normalized

    def register(
        self,
        task_type: str,
        processor: MediaTaskProcessor,
        *,
        replace: bool = False,
    ) -> None:
        """注册处理器；默认禁止静默覆盖已有实现。

        ``replace`` 只用于测试注入或显式灰度切换。生产装配若意外重复注册，应
        在进程启动阶段立即失败，而不是根据导入顺序选择不确定的实现。
        """

        key = self._normalize_task_type(task_type)
        if key in self._processors and not replace:
            raise DuplicateProcessorError(f"任务类型已注册处理器: {key}")
        self._processors[key] = processor

    def get(self, task_type: str) -> MediaTaskProcessor:
        """返回任务处理器；不支持的任务类型抛出明确异常。"""

        key = self._normalize_task_type(task_type)
        try:
            return self._processors[key]
        except KeyError as exc:
            raise UnsupportedTaskTypeError(
                f"当前Worker未注册任务处理器: {key}"
            ) from exc

    def process(self, message: TaskDispatchMessage) -> ProcessorResult:
        """根据消息中的任务类型选择处理器并执行。"""

        return self.get(message.task_type).process(message)

    @property
    def task_types(self) -> tuple[str, ...]:
        """按名称排序返回已注册任务类型，便于健康检查和启动日志展示。"""

        return tuple(sorted(self._processors))


__all__ = [
    "DuplicateProcessorError",
    "MediaTaskProcessor",
    "ProcessorResult",
    "TaskProcessorRegistry",
    "UnsupportedTaskTypeError",
]
