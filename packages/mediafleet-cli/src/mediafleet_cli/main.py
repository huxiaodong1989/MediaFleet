"""MediaFleet CLI 入口。"""

from __future__ import annotations

import typer

from mediafleet_cli import __version__
from mediafleet_cli.commands.auth import auth_app
from mediafleet_cli.commands.context import context_app
from mediafleet_cli.commands.query import (
    binding_app,
    health,
    node_app,
    overview,
    recorder_app,
    task_app,
)
from mediafleet_cli.commands.security import security_app
from mediafleet_cli.commands.write import recording_app
from mediafleet_cli.output import console, emit_json

app = typer.Typer(
    name="mediafleet",
    help="通过 MediaFleet SDK 查询和管理控制中心。",
    no_args_is_help=True,
    add_completion=False,
)

app.command("health")(health)
app.command("overview")(overview)
app.add_typer(task_app, name="task")
app.add_typer(node_app, name="node")
app.add_typer(binding_app, name="binding")
app.add_typer(recorder_app, name="recorder")
app.add_typer(recording_app, name="recording")
app.add_typer(context_app, name="context")
app.add_typer(auth_app, name="auth")
app.add_typer(security_app, name="security")


@app.command("version")
def version(
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """显示 CLI 版本。"""

    payload = {"name": "mediafleet-cli", "version": __version__}
    if json_output:
        emit_json(payload)
    else:
        console(no_color=no_color).print(f"mediafleet-cli {__version__}")


def run() -> None:
    app()


if __name__ == "__main__":
    run()


__all__ = ["app", "run"]
