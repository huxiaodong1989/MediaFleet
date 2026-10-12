"""CLI 本地错误和稳定退出码。"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    CONFIGURATION = 2
    AUTHORIZATION = 3
    NOT_FOUND = 4
    CONFLICT = 5
    UNAVAILABLE = 6
    RESPONSE_CONTRACT = 7
    CANCELLED = 8
    API_ERROR = 10


class CLIError(Exception):
    """CLI 可预期本地错误。"""


class LocalConfigurationError(CLIError):
    """本地配置文件或参数无效。"""


class CredentialStoreError(CLIError):
    """操作系统凭据库不可用。"""


__all__ = [
    "CLIError",
    "CredentialStoreError",
    "ExitCode",
    "LocalConfigurationError",
]
