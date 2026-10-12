"""SDK 错误到 MCP 结构化错误的映射。"""

from __future__ import annotations

from typing import Any

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


def success(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def failure(exc: Exception) -> dict[str, Any]:
    code, retryable = classify_error(exc)
    error: dict[str, Any] = {
        "code": code,
        "message": str(exc),
        "retryable": retryable,
    }
    if isinstance(exc, APIError):
        error["http_status"] = exc.status_code
        if exc.request_id:
            error["request_id"] = exc.request_id
    return {"ok": False, "data": None, "error": error}


def classify_error(exc: Exception) -> tuple[str, bool]:
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
    MCPConfigurationError,
    ResponseValidationError,
    TimeoutError,
    TransportError,
)


__all__ = ["EXPECTED_ERRORS", "classify_error", "failure", "success"]
