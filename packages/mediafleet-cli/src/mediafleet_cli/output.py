"""CLI 的 Rich 和 JSON 输出。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any, TypeVar

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

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
)

from mediafleet_cli.errors import (
    CredentialStoreError,
    ExitCode,
    LocalConfigurationError,
)

T = TypeVar("T")


def console(*, no_color: bool = False, stderr: bool = False) -> Console:
    return Console(
        stderr=stderr,
        no_color=no_color,
        color_system=None if no_color else "auto",
        highlight=not no_color,
    )


def json_ready(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [json_ready(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def emit_json(value: Any, *, stderr: bool = False) -> None:
    typer.echo(
        json.dumps(json_ready(value), ensure_ascii=False, separators=(",", ":")),
        err=stderr,
    )


def emit_error(exc: Exception, *, json_output: bool, no_color: bool) -> None:
    exit_code, error_type = classify_error(exc)
    payload: dict[str, Any] = {
        "error": {
            "type": error_type,
            "message": str(exc),
        },
        "exit_code": int(exit_code),
    }
    if isinstance(exc, APIError):
        payload["error"]["status_code"] = exc.status_code
        if exc.request_id:
            payload["error"]["request_id"] = exc.request_id
    if json_output:
        emit_json(payload, stderr=True)
    else:
        console(no_color=no_color, stderr=True).print(
            f"[bold red]错误：[/bold red]{exc}"
        )
    raise typer.Exit(code=int(exit_code))


def invoke(
    operation: Callable[[], T],
    *,
    json_output: bool,
    no_color: bool,
) -> T:
    try:
        return operation()
    except (
        APIError,
        ConfigurationError,
        CredentialStoreError,
        LocalConfigurationError,
        ResponseValidationError,
        TimeoutError,
        TransportError,
    ) as exc:
        emit_error(exc, json_output=json_output, no_color=no_color)
        raise AssertionError("unreachable")


def classify_error(exc: Exception) -> tuple[ExitCode, str]:
    if isinstance(exc, (LocalConfigurationError, CredentialStoreError, ConfigurationError)):
        return ExitCode.CONFIGURATION, "configuration_error"
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return ExitCode.AUTHORIZATION, "authorization_error"
    if isinstance(exc, NotFoundError):
        return ExitCode.NOT_FOUND, "not_found"
    if isinstance(exc, ConflictError):
        return ExitCode.CONFLICT, "conflict"
    if isinstance(exc, (TransportError, TimeoutError, RateLimitError, ServiceUnavailableError)):
        return ExitCode.UNAVAILABLE, "service_unavailable"
    if isinstance(exc, ResponseValidationError):
        return ExitCode.RESPONSE_CONTRACT, "response_contract_error"
    return ExitCode.API_ERROR, "api_error"


def detail_table(
    title: str,
    rows: Sequence[tuple[str, Any]],
    *,
    no_color: bool,
) -> None:
    table = Table(title=title, show_header=False, box=None)
    table.add_column("字段", style="bold cyan")
    table.add_column("值")
    for label, value in rows:
        table.add_row(label, display_value(value))
    console(no_color=no_color).print(table)


def list_table(
    title: str,
    columns: Sequence[tuple[str, str]],
    items: Sequence[Any],
    *,
    no_color: bool,
) -> None:
    table = Table(title=title)
    for _, label in columns:
        table.add_column(label)
    for item in items:
        table.add_row(
            *(display_value(_field(item, field)) for field, _ in columns)
        )
    console(no_color=no_color).print(table)


def display_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(json_ready(value), ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _field(item: Any, field: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(field)
    return getattr(item, field, None)


__all__ = [
    "console",
    "detail_table",
    "display_value",
    "emit_error",
    "emit_json",
    "invoke",
    "json_ready",
    "list_table",
]
