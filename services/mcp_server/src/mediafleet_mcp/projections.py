"""适合模型上下文的精简、安全响应投影。"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "signature",
    "token",
}
MAX_DEPTH = 4
MAX_ITEMS = 50
MAX_STRING_LENGTH = 1000
URL_PATTERN = re.compile(r"(?:https?|rtsp|rtmp)://[^\s\"'<>]+", re.IGNORECASE)


def health(value: Any) -> dict[str, Any]:
    return _pick(value, "service", "role", "version", "status", "checked_at")


def overview(value: Any) -> dict[str, Any]:
    return {
        "tasks_by_status": safe_value(value.tasks_by_status),
        "tasks_by_type": safe_value(value.tasks_by_type),
        "nodes_by_status": safe_value(value.nodes_by_status),
        "queues": [
            _pick(
                item,
                "queue_name",
                "message_count",
                "consumer_count",
                "status",
                "error_message",
            )
            for item in value.queues[:MAX_ITEMS]
        ],
        "queues_truncated": len(value.queues) > MAX_ITEMS,
    }


def task_page(value: Any) -> dict[str, Any]:
    return {
        "items": [task_summary(item) for item in value.items],
        "total": value.total,
        "page": value.page,
        "page_size": value.page_size,
    }


def task_summary(value: Any) -> dict[str, Any]:
    return _pick(
        value,
        "task_id",
        "school_code",
        "task_type",
        "status",
        "publish_status",
        "progress",
        "executor_node_id",
        "retry_count",
        "max_retries",
        "error_message",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
    )


def task_detail(value: Any) -> dict[str, Any]:
    result = task_summary(value)
    result.update(
        {
            "params": safe_value(value.params),
            "result": safe_value(value.result),
            "message_id": value.message_id,
            "published_at": json_value(value.published_at),
        }
    )
    return result


def task_files(values: list[Any]) -> dict[str, Any]:
    items = [
        {
            **_pick(
                value,
                "file_id",
                "task_id",
                "school_code",
                "file_type",
                "file_name",
                "file_size",
                "mime_type",
                "created_at",
            ),
            "extra_info": safe_value(value.extra_info),
            "download_url_available": bool(value.file_url),
        }
        for value in values[:MAX_ITEMS]
    ]
    return {
        "items": items,
        "total": len(values),
        "returned": len(items),
        "truncated": len(values) > MAX_ITEMS,
    }


def node_page(value: Any) -> dict[str, Any]:
    return {
        "items": [
            {
                **_pick(
                    item,
                    "node_id",
                    "node_code",
                    "node_name",
                    "node_type",
                    "status",
                    "readiness_status",
                    "capabilities",
                    "capacity",
                    "readiness_details",
                    "last_heartbeat_at",
                    "active_bindings",
                    "active_tasks",
                    "recording_server_code",
                    "recording_server_status",
                )
            }
            for item in value.items
        ],
        "total": value.total,
        "page": value.page,
        "page_size": value.page_size,
    }


def binding_page(value: Any) -> dict[str, Any]:
    return {
        "items": [
            _pick(
                item,
                "binding_id",
                "school_code",
                "resource_type",
                "space_id",
                "node_id",
                "node_code",
                "app",
                "stream_id",
                "stream_name",
                "stream_mode",
                "status",
                "version",
                "last_active_at",
            )
            for item in value.items
        ],
        "total": value.total,
        "page": value.page,
        "page_size": value.page_size,
    }


def recording_servers(values: list[Any]) -> dict[str, Any]:
    items = [
        _pick(
            value,
            "server_id",
            "server_code",
            "server_name",
            "status",
            "recorder_node_id",
            "zlm_server_id",
            "max_recordings",
            "max_bindings",
            "occupied_bindings",
        )
        for value in values[:MAX_ITEMS]
    ]
    return {
        "items": items,
        "total": len(values),
        "returned": len(items),
        "truncated": len(values) > MAX_ITEMS,
    }


def safe_value(value: Any, *, key: str | None = None, depth: int = 0) -> Any:
    if key is not None and _sensitive_key(key):
        return "[REDACTED]"
    if depth >= MAX_DEPTH:
        return "[TRUNCATED]"
    value = json_value(value)
    if isinstance(value, dict):
        return {
            str(item_key): safe_value(
                item_value,
                key=str(item_key),
                depth=depth + 1,
            )
            for item_key, item_value in list(value.items())[:MAX_ITEMS]
        }
    if isinstance(value, list):
        return [safe_value(item, depth=depth + 1) for item in value[:MAX_ITEMS]]
    if isinstance(value, str):
        return _safe_string(value)
    return value


def json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _pick(value: Any, *fields: str) -> dict[str, Any]:
    return {
        field: safe_value(getattr(value, field, None), key=field)
        for field in fields
    }


def _sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEYS)


def _safe_string(value: str) -> str:
    candidate = value[:MAX_STRING_LENGTH]
    return URL_PATTERN.sub(lambda match: _sanitize_url(match.group(0)), candidate)


def _sanitize_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[INVALID URL]"
    if parsed.scheme in {"http", "https", "rtsp", "rtmp"} and parsed.hostname:
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = parsed.port
        except ValueError:
            return "[INVALID URL]"
        if port is not None:
            host = f"{host}:{port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    return "[INVALID URL]"


__all__ = [
    "binding_page",
    "health",
    "node_page",
    "overview",
    "recording_servers",
    "safe_value",
    "task_detail",
    "task_files",
    "task_page",
]
