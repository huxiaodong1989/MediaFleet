"""录制节点命令处理器注册表。

注册表替代后续可能出现的大型 ``if/elif`` 命令分发。每个命令类型只能注册一个
处理器；未注册命令会明确失败并进入命令消费者的死信路径，避免录制节点误 ACK
尚未实现的录像、本地后处理或安全清理命令。
"""

from __future__ import annotations

from typing import Protocol

from media_platform.contracts.command import (
    RecorderCommandMessage,
    RecorderCommandType,
)


class RecorderCommandHandler(Protocol):
    """录制节点单一命令处理器端口。"""

    def handle(self, message: RecorderCommandMessage) -> None:
        """执行录制节点命令；失败时抛出具有业务含义的异常。"""


class DuplicateCommandHandlerError(ValueError):
    """同一录制命令被重复注册。"""


class UnsupportedRecorderCommandError(LookupError):
    """录制节点收到尚未注册处理器的命令。"""


class RecorderCommandRegistry:
    """按精确命令类型查找录制节点命令处理器。"""

    def __init__(self) -> None:
        self._handlers: dict[str, RecorderCommandHandler] = {}

    @staticmethod
    def _normalize_command(command: RecorderCommandType | str) -> str:
        """清理命令类型并拒绝空值。"""

        normalized = str(command.value if isinstance(command, RecorderCommandType) else command).strip()
        if not normalized:
            raise ValueError("command 不能为空")
        return normalized

    def register(
        self,
        command: RecorderCommandType | str,
        handler: RecorderCommandHandler,
        *,
        replace: bool = False,
    ) -> None:
        """注册命令处理器；默认禁止静默覆盖已有实现。"""

        key = self._normalize_command(command)
        if key in self._handlers and not replace:
            raise DuplicateCommandHandlerError(f"录制命令已注册处理器: {key}")
        self._handlers[key] = handler

    def get(self, command: RecorderCommandType | str) -> RecorderCommandHandler:
        """返回命令处理器；不支持的命令抛出明确异常。"""

        key = self._normalize_command(command)
        try:
            return self._handlers[key]
        except KeyError as exc:
            raise UnsupportedRecorderCommandError(
                f"当前录制节点未注册命令处理器: {key}"
            ) from exc

    def handle(self, message: RecorderCommandMessage) -> None:
        """根据消息中的命令类型选择处理器并执行。"""

        self.get(message.command).handle(message)

    @property
    def commands(self) -> tuple[str, ...]:
        """按名称排序返回已注册命令类型，便于启动日志展示。"""

        return tuple(sorted(self._handlers))


__all__ = [
    "DuplicateCommandHandlerError",
    "RecorderCommandHandler",
    "RecorderCommandRegistry",
    "UnsupportedRecorderCommandError",
]
