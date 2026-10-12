"""连接 context 管理命令。"""

from __future__ import annotations

from typing import Any

import typer

from mediafleet_cli.config import (
    ContextConfig,
    load_config,
    save_config,
    validate_ca_bundle,
    validate_context_name,
    validate_url,
)
from mediafleet_cli.errors import LocalConfigurationError
from mediafleet_cli.output import console, emit_json, invoke, list_table

context_app = typer.Typer(help="管理控制中心连接 context。", no_args_is_help=True)


@context_app.command("list")
def context_list(
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """列出已配置的 context。"""

    config = invoke(load_config, json_output=json_output, no_color=no_color)
    items = [
        {
            "name": name,
            "current": name == config.current_context,
            "url": item.url,
            "ca_bundle": item.ca_bundle,
        }
        for name, item in sorted(config.contexts.items())
    ]
    if json_output:
        emit_json({"current_context": config.current_context, "contexts": items})
        return
    if not items:
        console(no_color=no_color).print("尚未配置 context。")
        return
    list_table(
        "MediaFleet Context",
        [
            ("current", "当前"),
            ("name", "名称"),
            ("url", "控制中心 URL"),
            ("ca_bundle", "CA 文件"),
        ],
        items,
        no_color=no_color,
    )


@context_app.command("set")
def context_set(
    name: str = typer.Argument(..., help="context 名称。"),
    url: str = typer.Option(..., "--url", help="控制中心 URL。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """创建或更新 context，不保存 API Key。"""

    def operation() -> dict[str, Any]:
        normalized_name = validate_context_name(name)
        config = load_config()
        config.contexts[normalized_name] = ContextConfig(
            url=validate_url(url),
            ca_bundle=validate_ca_bundle(ca_bundle),
        )
        if config.current_context is None:
            config.current_context = normalized_name
        path = save_config(config)
        return {
            "name": normalized_name,
            "current": config.current_context == normalized_name,
            "url": config.contexts[normalized_name].url,
            "ca_bundle": config.contexts[normalized_name].ca_bundle,
            "config_file": str(path),
        }

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
    else:
        console(no_color=no_color).print(
            f"已保存 context [bold]{result['name']}[/bold]。"
        )


@context_app.command("use")
def context_use(
    name: str = typer.Argument(..., help="context 名称。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """切换当前 context。"""

    def operation() -> dict[str, Any]:
        normalized_name = validate_context_name(name)
        config = load_config()
        if normalized_name not in config.contexts:
            raise LocalConfigurationError(f"context {normalized_name!r} 不存在")
        config.current_context = normalized_name
        save_config(config)
        return {"current_context": normalized_name}

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
    else:
        console(no_color=no_color).print(
            f"当前 context 已切换为 [bold]{result['current_context']}[/bold]。"
        )


__all__ = ["context_app"]
