"""MCP 进程环境配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

WRITE_TOOL_NAMES = frozenset(
    {
        "mediafleet_create_task",
        "mediafleet_retry_task",
        "mediafleet_retry_task_callback",
        "mediafleet_create_binding",
        "mediafleet_release_binding",
        "mediafleet_start_recording",
        "mediafleet_stop_recording",
    }
)


class MCPConfigurationError(Exception):
    """MCP 运行配置无效。"""


@dataclass(frozen=True, repr=False)
class MCPSettings:
    base_url: str
    api_key: str
    verify: bool | str = True
    timeout: float = 30.0
    write_tools: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> MCPSettings:
        base_url = _required("MEDIAFLEET_URL")
        api_key = _required("MEDIAFLEET_API_KEY")
        ca_bundle = os.getenv("MEDIAFLEET_CA_BUNDLE")
        timeout_value = os.getenv("MEDIAFLEET_TIMEOUT", "30")

        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MCPConfigurationError(
                "MEDIAFLEET_URL 必须是完整的 HTTP 或 HTTPS 地址"
            )
        if parsed.username is not None or parsed.password is not None:
            raise MCPConfigurationError("MEDIAFLEET_URL 不能包含用户名或密码")
        if parsed.query or parsed.fragment:
            raise MCPConfigurationError("MEDIAFLEET_URL 不能包含查询参数或片段")
        if "\r" in api_key or "\n" in api_key:
            raise MCPConfigurationError("MEDIAFLEET_API_KEY 不能包含换行符")

        verify: bool | str = True
        if ca_bundle is not None:
            path = Path(ca_bundle).expanduser().resolve()
            if not path.is_file():
                raise MCPConfigurationError(
                    f"MEDIAFLEET_CA_BUNDLE 指向的文件不存在: {path}"
                )
            verify = str(path)

        try:
            timeout = float(timeout_value)
        except ValueError as exc:
            raise MCPConfigurationError("MEDIAFLEET_TIMEOUT 必须是数字") from exc
        if not 0 < timeout <= 120:
            raise MCPConfigurationError("MEDIAFLEET_TIMEOUT 必须大于 0 且不超过 120 秒")

        return cls(
            base_url=base_url.rstrip("/") + "/",
            api_key=api_key,
            verify=verify,
            timeout=timeout,
            write_tools=cls.write_tools_from_env(),
        )

    @staticmethod
    def write_tools_from_env() -> frozenset[str]:
        raw_value = os.getenv("MEDIAFLEET_MCP_WRITE_TOOLS", "")
        values = frozenset(
            item.strip() for item in raw_value.split(",") if item.strip()
        )
        unknown = values - WRITE_TOOL_NAMES
        if unknown:
            raise MCPConfigurationError(
                "MEDIAFLEET_MCP_WRITE_TOOLS 包含未知工具: "
                + ", ".join(sorted(unknown))
            )
        return values

    def __repr__(self) -> str:
        return (
            "MCPSettings("
            f"base_url={self.base_url!r}, api_key='********', "
            f"verify={self.verify!r}, timeout={self.timeout!r}, "
            f"write_tools={sorted(self.write_tools)!r})"
        )


def _required(name: str) -> str:
    value = os.getenv(name)
    normalized = value.strip() if value else ""
    if not normalized:
        raise MCPConfigurationError(f"缺少必需环境变量 {name}")
    return normalized


__all__ = ["MCPConfigurationError", "MCPSettings", "WRITE_TOOL_NAMES"]
