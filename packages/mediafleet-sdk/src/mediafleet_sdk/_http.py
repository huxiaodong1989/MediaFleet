"""同步和异步客户端共用的 HTTP 安全处理。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from mediafleet_sdk.config import RetryPolicy
from mediafleet_sdk.errors import (
    APIError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ResponseValidationError,
    ServiceUnavailableError,
    ValidationError,
)


RETRYABLE_METHODS = frozenset({"GET", "HEAD"})
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_ERROR_TYPES: dict[int, type[APIError]] = {
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: RateLimitError,
    502: ServiceUnavailableError,
    503: ServiceUnavailableError,
    504: ServiceUnavailableError,
}


def safe_path(request: httpx.Request) -> str:
    """只保留路径，避免查询参数和用户信息进入异常。"""

    return request.url.path or "/"


def parse_response(response: httpx.Response, model: Any, api_key: str) -> Any:
    path = safe_path(response.request)
    payload: Any = None
    json_invalid = False
    try:
        payload = response.json()
    except ValueError:
        json_invalid = True
    if json_invalid:
        raise ResponseValidationError(
            path=path,
            issues=[{"loc": [], "type": "json_invalid"}],
        )
    issues: list[dict[str, Any]] | None = None
    try:
        return TypeAdapter(model).validate_python(payload)
    except PydanticValidationError as exc:
        issues = exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    if issues is not None:
        raise ResponseValidationError(
            path=path,
            issues=_sanitize_value(issues, api_key),
        )
    raise ResponseValidationError(path=path, issues=[])


def api_error(response: httpx.Response, api_key: str) -> APIError:
    path = safe_path(response.request)
    detail: Any = response.reason_phrase or "请求失败"
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and "detail" in payload:
        detail = payload["detail"]
    detail = _sanitize_value(detail, api_key)
    request_id = response.headers.get("x-request-id") or response.headers.get(
        "x-correlation-id"
    )
    request_id = _sanitize_text(request_id, api_key) if request_id else None
    error_type = _ERROR_TYPES.get(response.status_code, APIError)
    return error_type(
        status_code=response.status_code,
        detail=detail,
        path=path,
        request_id=request_id,
    )


def should_retry(method: str, attempt: int, policy: RetryPolicy) -> bool:
    return method.upper() in RETRYABLE_METHODS and attempt < policy.max_attempts


def retry_delay(
    *,
    attempt: int,
    policy: RetryPolicy,
    response: httpx.Response | None = None,
) -> float:
    retry_after = _retry_after_seconds(response) if response is not None else None
    if retry_after is not None:
        return min(max(0.0, retry_after), policy.max_delay)
    exponential = policy.backoff_factor * (2 ** max(0, attempt - 1))
    return min(exponential, policy.max_delay)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw_value = response.headers.get("retry-after")
    if not raw_value:
        return None
    try:
        return float(raw_value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw_value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return (retry_at - datetime.now(timezone.utc)).total_seconds()


def _sanitize_value(value: Any, api_key: str) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value, api_key)
    if isinstance(value, list):
        return [_sanitize_value(item, api_key) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item, api_key) for item in value)
    if isinstance(value, dict):
        return {
            _sanitize_text(str(key), api_key): _sanitize_value(item, api_key)
            for key, item in value.items()
        }
    return value


def _sanitize_text(value: str, api_key: str) -> str:
    result = value.replace(api_key, "[REDACTED]") if api_key else value
    return _URL_PATTERN.sub(_safe_url, result)


def _safe_url(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    trailing = ""
    while raw_url and raw_url[-1] in ".,;:)]}":
        trailing = raw_url[-1] + trailing
        raw_url = raw_url[:-1]
    try:
        parsed = urlsplit(raw_url)
        host = parsed.hostname or ""
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        sanitized = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        sanitized = "[REDACTED_URL]"
    return sanitized + trailing


__all__ = [
    "RETRYABLE_STATUS_CODES",
    "api_error",
    "parse_response",
    "retry_delay",
    "safe_path",
    "should_retry",
]
