"""通过 SDK 执行受控业务写操作。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import typer
from pydantic import BaseModel, ValidationError

from mediafleet_cli.commands.query import binding_app, recorder_app, task_app
from mediafleet_cli.errors import LocalConfigurationError
from mediafleet_cli.output import detail_table, emit_json, invoke
from mediafleet_cli.runtime import ConnectionOptions, create_client
from mediafleet_sdk.models import BindingCreate, RecordingStart, TaskCreate


MAX_REQUEST_BYTES = 1024 * 1024
RequestModel = TypeVar("RequestModel", bound=BaseModel)

recording_app = typer.Typer(help="开始或停止录制。", no_args_is_help=True)


def _connection(
    url: str | None,
    context: str | None,
    ca_bundle: str | None,
) -> ConnectionOptions:
    return ConnectionOptions(url=url, context=context, ca_bundle=ca_bundle)


def _ensure_yes(value: bool, operation: str) -> None:
    if not value:
        raise LocalConfigurationError(f"{operation}会改变服务端状态，请添加 --yes")


def _read_request(file: Path | None, stdin_input: bool) -> str:
    if (file is None) == (not stdin_input):
        raise LocalConfigurationError("必须且只能选择 --file FILE 或 --stdin")
    if stdin_input:
        content = typer.get_text_stream("stdin").read(MAX_REQUEST_BYTES + 1)
    else:
        assert file is not None
        try:
            with file.open("rb") as handle:
                raw_content = handle.read(MAX_REQUEST_BYTES + 1)
            if len(raw_content) > MAX_REQUEST_BYTES:
                raise LocalConfigurationError("请求 JSON 不能超过 1 MiB")
            content = raw_content.decode("utf-8")
        except LocalConfigurationError:
            raise
        except (OSError, UnicodeError) as exc:
            raise LocalConfigurationError(f"无法读取请求文件: {file}") from exc
    if len(content.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise LocalConfigurationError("请求 JSON 不能超过 1 MiB")
    return content


def _load_request(
    model: type[RequestModel],
    *,
    file: Path | None,
    stdin_input: bool,
) -> RequestModel:
    raw = _read_request(file, stdin_input)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalConfigurationError(
            f"请求文件不是有效 JSON（第 {exc.lineno} 行，第 {exc.colno} 列）"
        ) from exc
    if not isinstance(payload, dict):
        raise LocalConfigurationError("请求 JSON 顶层必须是对象")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        fields = [".".join(str(item) for item in error["loc"]) for error in exc.errors()]
        field_text = ", ".join(dict.fromkeys(fields)) or "请求内容"
        raise LocalConfigurationError(f"请求 JSON 字段无效: {field_text}") from exc


def _emit_result(
    result: Any,
    *,
    title: str,
    json_output: bool,
    no_color: bool,
) -> None:
    if json_output:
        emit_json(result)
        return
    detail_table(
        title,
        [(name, value) for name, value in result.model_dump().items()],
        no_color=no_color,
    )


@task_app.command("create")
def task_create(
    file: Path | None = typer.Option(None, "--file", help="UTF-8 JSON 请求文件。"),
    stdin_input: bool = typer.Option(False, "--stdin", help="从标准输入读取 JSON。"),
    idempotency_key: str = typer.Option(
        ...,
        "--idempotency-key",
        help="稳定业务幂等键；重试同一请求时必须复用。",
    ),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """创建通用媒体任务。"""

    def operation() -> Any:
        normalized_key = idempotency_key.strip()
        if not 8 <= len(normalized_key) <= 128:
            raise LocalConfigurationError("--idempotency-key 长度必须在 8 到 128 之间")
        request = _load_request(TaskCreate, file=file, stdin_input=stdin_input)
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.tasks.create(request, idempotency_key=normalized_key)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    _emit_result(
        result,
        title="媒体任务已提交",
        json_output=json_output,
        no_color=no_color,
    )


@task_app.command("retry")
def task_retry(
    task_id: str = typer.Argument(..., help="失败或已取消的任务 ID。"),
    yes: bool = typer.Option(False, "--yes", help="确认重新投递任务。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """重新投递失败或已取消的通用媒体任务。"""

    def operation() -> Any:
        _ensure_yes(yes, "任务重试")
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.tasks.retry(task_id)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    _emit_result(
        result,
        title="媒体任务已重新投递",
        json_output=json_output,
        no_color=no_color,
    )


@task_app.command("callback-retry")
def task_callback_retry(
    task_id: str = typer.Argument(..., help="终态任务 ID。"),
    yes: bool = typer.Option(False, "--yes", help="确认重新发送业务回调。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """重新发送终态任务的业务回调，不重新执行媒体任务。"""

    def operation() -> Any:
        _ensure_yes(yes, "回调补发")
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.tasks.retry_callback(task_id)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    _emit_result(
        result,
        title="业务回调已补发",
        json_output=json_output,
        no_color=no_color,
    )


@binding_app.command("create")
def binding_create(
    file: Path | None = typer.Option(None, "--file", help="UTF-8 JSON 请求文件。"),
    stdin_input: bool = typer.Option(False, "--stdin", help="从标准输入读取 JSON。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """创建或获取幂等流绑定。"""

    def operation() -> Any:
        request = _load_request(BindingCreate, file=file, stdin_input=stdin_input)
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.bindings.create(request)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    _emit_result(
        result,
        title="流绑定已就绪",
        json_output=json_output,
        no_color=no_color,
    )


@binding_app.command("release")
def binding_release(
    binding_id: str = typer.Argument(..., help="流绑定 ID。"),
    yes: bool = typer.Option(False, "--yes", help="确认释放绑定和容量名额。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """释放流绑定并归还录制服务器容量名额。"""

    def operation() -> Any:
        _ensure_yes(yes, "释放流绑定")
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.bindings.release(binding_id)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    _emit_result(
        result,
        title="流绑定已释放",
        json_output=json_output,
        no_color=no_color,
    )


@recording_app.command("start")
def recording_start(
    file: Path | None = typer.Option(None, "--file", help="UTF-8 JSON 请求文件。"),
    stdin_input: bool = typer.Option(False, "--stdin", help="从标准输入读取 JSON。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """按已有流绑定开始录制。"""

    def operation() -> Any:
        request = _load_request(RecordingStart, file=file, stdin_input=stdin_input)
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.recordings.start(request)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    _emit_result(
        result,
        title="开始录制命令已确认",
        json_output=json_output,
        no_color=no_color,
    )


@recording_app.command("stop")
def recording_stop(
    task_id: str = typer.Argument(..., help="开始录制时使用的任务 ID。"),
    yes: bool = typer.Option(False, "--yes", help="确认停止录制并继续后处理。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """停止录制并让录制节点继续后处理。"""

    def operation() -> Any:
        _ensure_yes(yes, "停止录制")
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.recordings.stop(task_id)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    _emit_result(
        result,
        title="停止录制命令已确认",
        json_output=json_output,
        no_color=no_color,
    )


@recorder_app.command("set-status")
def recorder_set_status(
    server_code: str = typer.Argument(..., help="录制服务器稳定编号。"),
    status: str = typer.Argument(
        ...,
        help="目标状态：ACTIVE、DRAINING、MAINTENANCE 或 DISABLED。",
    ),
    yes: bool = typer.Option(False, "--yes", help="确认更新录制服务器状态。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """更新录制服务器运维状态。"""

    def operation() -> Any:
        _ensure_yes(yes, "更新录制服务器状态")
        normalized_status = status.strip().upper()
        allowed = {"ACTIVE", "DRAINING", "MAINTENANCE", "DISABLED"}
        if normalized_status not in allowed:
            raise LocalConfigurationError(
                "状态必须是 ACTIVE、DRAINING、MAINTENANCE 或 DISABLED"
            )
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.recording_servers.update_status(
                server_code,
                normalized_status,
            )

    result = invoke(operation, json_output=json_output, no_color=no_color)
    _emit_result(
        result,
        title="录制服务器状态已更新",
        json_output=json_output,
        no_color=no_color,
    )


__all__ = ["recording_app"]
