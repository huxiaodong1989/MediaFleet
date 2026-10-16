"""调用方凭据、权限范围和可信审计命令。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import typer

from mediafleet_cli.errors import LocalConfigurationError
from mediafleet_cli.output import detail_table, emit_json, invoke, list_table
from mediafleet_cli.runtime import ConnectionOptions, create_client
from mediafleet_sdk.models import CredentialCreate


security_app = typer.Typer(help="管理范围化 API 凭据和审计。", no_args_is_help=True)
credential_app = typer.Typer(help="创建、查询、轮换和吊销 API 凭据。", no_args_is_help=True)
security_app.add_typer(credential_app, name="credential")


def _connection(url: str | None, context: str | None, ca_bundle: str | None) -> ConnectionOptions:
    return ConnectionOptions(url=url, context=context, ca_bundle=ca_bundle)


def _ensure_yes(value: bool, operation: str) -> None:
    if not value:
        raise LocalConfigurationError(f"{operation}会立即改变凭据状态，请添加 --yes")


@security_app.command("scopes")
def scopes(
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """查询服务端允许分配的权限范围。"""

    def operation() -> Any:
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.security.scopes()

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
    else:
        detail_table("API 权限范围", [("范围", result.scopes)], no_color=no_color)


@credential_app.command("list")
def credential_list(
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """查询凭据元数据；不会返回任何密钥明文。"""

    def operation() -> Any:
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.security.list_credentials()

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
        return
    list_table(
        "API 凭据",
        [
            ("credential_id", "凭据 ID"),
            ("caller_id", "调用方"),
            ("credential_name", "名称"),
            ("scopes", "范围"),
            ("status", "状态"),
            ("expires_at", "过期时间"),
            ("last_used_at", "最后使用"),
        ],
        result,
        no_color=no_color,
    )


@credential_app.command("create")
def credential_create(
    caller_id: str = typer.Option(..., "--caller-id", help="稳定调用方标识。"),
    display_name: str = typer.Option(..., "--display-name", help="调用方显示名称。"),
    credential_name: str = typer.Option(..., "--name", help="凭据名称。"),
    scope: list[str] = typer.Option(..., "--scope", help="权限范围，可重复。"),
    expires_at: datetime | None = typer.Option(None, "--expires-at", help="可选过期时间。"),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key", help="请求追踪幂等键。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """创建凭据；API Key 只在本次输出中出现。"""

    def operation() -> Any:
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.security.create_credential(
                CredentialCreate(
                    caller_id=caller_id,
                    display_name=display_name,
                    credential_name=credential_name,
                    scopes=scope,
                    expires_at=expires_at,
                ),
                idempotency_key=(
                    idempotency_key
                    or f"credential-create:{caller_id}:{credential_name}"
                ),
            )

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
        return
    detail_table(
        "API 凭据已创建（请立即保存密钥）",
        [
            ("凭据 ID", result.credential_id),
            ("调用方", result.caller_id),
            ("范围", result.scopes),
            ("API Key", result.api_key),
        ],
        no_color=no_color,
    )


@credential_app.command("rotate")
def credential_rotate(
    credential_id: str = typer.Argument(..., help="凭据 ID。"),
    yes: bool = typer.Option(False, "--yes", help="确认旧密钥立即失效。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """轮换凭据；旧密钥立即失效，新密钥只返回一次。"""

    def operation() -> Any:
        _ensure_yes(yes, "轮换")
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.security.rotate_credential(credential_id)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
    else:
        detail_table(
            "API 凭据已轮换（请立即保存新密钥）",
            [("凭据 ID", result.credential_id), ("API Key", result.api_key)],
            no_color=no_color,
        )


@credential_app.command("revoke")
def credential_revoke(
    credential_id: str = typer.Argument(..., help="凭据 ID。"),
    yes: bool = typer.Option(False, "--yes", help="确认永久吊销凭据。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """永久吊销凭据。"""

    def operation() -> Any:
        _ensure_yes(yes, "吊销")
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.security.revoke_credential(credential_id)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
    else:
        detail_table(
            "API 凭据已吊销",
            [("凭据 ID", result.credential_id), ("状态", result.status)],
            no_color=no_color,
        )


@security_app.command("audit")
def audit_list(
    limit: int = typer.Option(100, "--limit", min=1, max=200, help="返回数量。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """查询最近写请求审计事件。"""

    def operation() -> Any:
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.security.list_audit_events(limit=limit)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
        return
    list_table(
        "API 写请求审计",
        [
            ("started_at", "时间"),
            ("caller_id", "调用方"),
            ("method", "方法"),
            ("path", "路径"),
            ("result", "结果"),
            ("http_status", "HTTP"),
        ],
        result,
        no_color=no_color,
    )


__all__ = ["security_app"]
