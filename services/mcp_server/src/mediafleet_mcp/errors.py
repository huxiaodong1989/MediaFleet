"""SDK 错误到 MCP 结构化错误的映射。"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError

from mediafleet_sdk import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ResponseValidationError,
    ServiceUnavailableError,
    TimeoutError,
    TransportError,
    ValidationError,
)

from mediafleet_mcp.settings import MCPConfigurationError


class MCPConfirmationRequiredError(Exception):
    """调用方尚未显式确认写操作。"""


class MCPInputValidationError(Exception):
    """MCP 写工具输入超出额外安全约束。"""


def success(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def failure(exc: Exception) -> dict[str, Any]:
    code, retryable = classify_error(exc)
    error: dict[str, Any] = {
        "code": code,
        "message": _safe_message(exc),
        "retryable": retryable,
    }
    if isinstance(exc, APIError):
        error["http_status"] = exc.status_code
        if exc.request_id:
            error["request_id"] = exc.request_id
    return {"ok": False, "data": None, "error": error}


def _safe_message(exc: Exception) -> str:
    if not isinstance(exc, PydanticValidationError):
        return str(exc)

    messages: list[str] = []
    for detail in exc.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in detail.get("loc", ()))
        message = str(detail.get("msg", "输入无效"))
        messages.append(f"{location}: {message}" if location else message)
    return "; ".join(messages) or "输入校验失败"


def classify_error(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, MCPConfirmationRequiredError):
        return "confirmation_required", False
    if isinstance(exc, (MCPInputValidationError, PydanticValidationError)):
        return "validation_error", False
    if isinstance(exc, (MCPConfigurationError, ConfigurationError)):
        return "configuration_error", False
    if isinstance(exc, AuthenticationError):
        return "authentication_error", False
    if isinstance(exc, PermissionDeniedError):
        return "permission_denied", False
    if isinstance(exc, NotFoundError):
        return "not_found", False
    if isinstance(exc, ConflictError):
        return "conflict", False
    if isinstance(exc, ValidationError):
        return "validation_error", False
    if isinstance(exc, RateLimitError):
        return "rate_limited", True
    if isinstance(exc, ServiceUnavailableError):
        return "service_unavailable", True
    if isinstance(exc, TimeoutError):
        return "timeout", True
    if isinstance(exc, TransportError):
        return "transport_error", True
    if isinstance(exc, ResponseValidationError):
        return "response_contract_error", False
    return "api_error", isinstance(exc, APIError) and exc.status_code >= 500


EXPECTED_ERRORS = (
    APIError,
    ConfigurationError,
    MCPConfirmationRequiredError,
    MCPConfigurationError,
    MCPInputValidationError,
    PydanticValidationError,
    ResponseValidationError,
    TimeoutError,
    TransportError,
)


__all__ = [
    "EXPECTED_ERRORS",
    "MCPConfirmationRequiredError",
    "MCPInputValidationError",
    "classify_error",
    "failure",
    "success",
]
