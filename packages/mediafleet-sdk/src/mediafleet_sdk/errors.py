"""MediaFleet SDK 公共异常类型。"""

from __future__ import annotations

from typing import Any


class MediaFleetError(Exception):
    """SDK 所有可预期错误的基类。"""


class ConfigurationError(MediaFleetError):
    """本地客户端配置无效。"""


class TransportError(MediaFleetError):
    """无法连接控制中心。"""

    def __init__(self, path: str, message: str = "连接控制中心失败") -> None:
        self.path = path
        super().__init__(f"{message}: {path}")


class TimeoutError(TransportError):
    """请求控制中心超时。"""

    def __init__(self, path: str) -> None:
        super().__init__(path, "请求控制中心超时")


class APIError(MediaFleetError):
    """控制中心返回非成功 HTTP 状态。"""

    def __init__(
        self,
        *,
        status_code: int,
        detail: Any,
        path: str,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        self.path = path
        self.request_id = request_id
        request_suffix = f"，request_id={request_id}" if request_id else ""
        super().__init__(
            f"MediaFleet API 返回 {status_code}: {detail} "
            f"(path={path}{request_suffix})"
        )


class AuthenticationError(APIError):
    """API Key 缺失或无效。"""


class PermissionDeniedError(APIError):
    """调用方没有所需权限。"""


class NotFoundError(APIError):
    """请求的资源不存在。"""


class ConflictError(APIError):
    """资源状态与请求冲突。"""


class ValidationError(APIError):
    """请求参数未通过服务端校验。"""


class RateLimitError(APIError):
    """请求超过服务端限流阈值。"""


class ServiceUnavailableError(APIError):
    """控制中心或其上游暂时不可用。"""


class ResponseValidationError(MediaFleetError):
    """服务端响应不是合法 JSON，或不符合 SDK 契约。"""

    def __init__(self, *, path: str, issues: list[dict[str, Any]]) -> None:
        self.path = path
        self.issues = issues
        super().__init__(f"MediaFleet API 响应不符合 SDK 契约: {path}")


__all__ = [
    "APIError",
    "AuthenticationError",
    "ConfigurationError",
    "ConflictError",
    "MediaFleetError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "ResponseValidationError",
    "ServiceUnavailableError",
    "TimeoutError",
    "TransportError",
    "ValidationError",
]
