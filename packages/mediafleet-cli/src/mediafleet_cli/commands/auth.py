"""API Key 凭据管理命令。"""

from __future__ import annotations

import getpass
import sys
from typing import Any

import typer

from mediafleet_cli.config import load_config
from mediafleet_cli.credentials import delete_api_key, set_api_key
from mediafleet_cli.errors import LocalConfigurationError
from mediafleet_cli.output import console, emit_json, invoke
from mediafleet_cli.runtime import selected_context_name

auth_app = typer.Typer(help="管理 API Key。", no_args_is_help=True)


def _credential_context(explicit_context: str | None) -> str:
    config = load_config()
    context_name = selected_context_name(config, explicit_context)
    if context_name is None:
        raise LocalConfigurationError("请先创建 context，或通过 --context 指定 context")
    if context_name not in config.contexts:
        raise LocalConfigurationError(f"context {context_name!r} 不存在")
    return context_name


@auth_app.command("login")
def auth_login(
    api_key_stdin: bool = typer.Option(
        False,
        "--api-key-stdin",
        help="从标准输入读取 API Key；交互终端中隐藏输入。",
    ),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """将 API Key 写入操作系统凭据库。"""

    def operation() -> dict[str, Any]:
        if not api_key_stdin:
            raise LocalConfigurationError("必须显式传入 --api-key-stdin")
        context_name = _credential_context(context)
        if sys.stdin.isatty():
            api_key = getpass.getpass("API Key: ")
        else:
            api_key = sys.stdin.readline()
        set_api_key(context_name, api_key)
        return {"context": context_name, "stored": True}

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
    else:
        console(no_color=no_color).print(
            f"已将 {result['context']} 的 API Key 写入操作系统凭据库。"
        )


@auth_app.command("logout")
def auth_logout(
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """从操作系统凭据库删除 API Key。"""

    def operation() -> dict[str, Any]:
        context_name = _credential_context(context)
        removed = delete_api_key(context_name)
        return {"context": context_name, "removed": removed}

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
    else:
        message = "已删除" if result["removed"] else "未找到"
        console(no_color=no_color).print(
            f"{message} {result['context']} 的 API Key。"
        )


__all__ = ["auth_app"]
