"""只读查询命令。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import typer

from mediafleet_cli.output import (
    detail_table,
    emit_json,
    invoke,
    list_table,
)
from mediafleet_cli.runtime import ConnectionOptions, create_client

task_app = typer.Typer(help="查询媒体任务和产物。", no_args_is_help=True)
node_app = typer.Typer(help="查询媒体节点。", no_args_is_help=True)
binding_app = typer.Typer(help="查询流绑定。", no_args_is_help=True)
recorder_app = typer.Typer(help="查询录制服务器。", no_args_is_help=True)


def _connection(url: str | None, context: str | None, ca_bundle: str | None) -> ConnectionOptions:
    return ConnectionOptions(url=url, context=context, ca_bundle=ca_bundle)


def health(
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """检查控制中心健康状态。"""

    def operation() -> Any:
        with create_client(
            _connection(url, context, ca_bundle), require_api_key=False
        ) as client:
            return client.system.health()

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
        return
    detail_table(
        "MediaFleet 健康状态",
        [
            ("服务", result.service),
            ("角色", result.role),
            ("版本", result.version),
            ("状态", result.status),
            ("检查时间", result.checked_at),
        ],
        no_color=no_color,
    )


def overview(
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """显示任务、节点和队列总览。"""

    def operation() -> Any:
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.admin.overview()

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
        return
    detail_table(
        "MediaFleet 总览",
        [
            ("任务状态", result.tasks_by_status),
            ("任务类型", result.tasks_by_type),
            ("节点状态", result.nodes_by_status),
        ],
        no_color=no_color,
    )
    list_table(
        "队列状态",
        [
            ("queue_name", "队列"),
            ("message_count", "消息"),
            ("consumer_count", "消费者"),
            ("status", "状态"),
            ("error_message", "错误"),
        ],
        result.queues,
        no_color=no_color,
    )


@task_app.command("list")
def task_list(
    page: int = typer.Option(1, "--page", min=1, help="页码。"),
    page_size: int = typer.Option(20, "--page-size", min=1, max=100, help="每页数量。"),
    task_type: str | None = typer.Option(None, "--task-type", help="任务类型。"),
    status: str | None = typer.Option(None, "--status", help="任务状态。"),
    node_id: str | None = typer.Option(None, "--node-id", help="执行节点 ID。"),
    school_code: str | None = typer.Option(None, "--school-code", help="业务租户代码。"),
    created_from: datetime | None = typer.Option(None, "--created-from", help="创建时间下限。"),
    created_to: datetime | None = typer.Option(None, "--created-to", help="创建时间上限。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """分页查询媒体任务。"""

    def operation() -> Any:
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.tasks.list(
                page=page,
                page_size=page_size,
                task_type=task_type,
                status=status,
                node_id=node_id,
                school_code=school_code,
                created_from=created_from,
                created_to=created_to,
            )

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
        return
    list_table(
        f"媒体任务（第 {result.page} 页，共 {result.total} 条）",
        [
            ("task_id", "任务 ID"),
            ("task_type", "类型"),
            ("status", "状态"),
            ("progress", "进度"),
            ("executor_node_id", "执行节点"),
            ("created_at", "创建时间"),
        ],
        result.items,
        no_color=no_color,
    )


@task_app.command("get")
def task_get(
    task_id: str = typer.Argument(..., help="任务 ID。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """查询单个媒体任务。"""

    def operation() -> Any:
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.tasks.get(task_id)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
        return
    detail_table(
        f"媒体任务 {result.task_id}",
        [(name, value) for name, value in result.model_dump().items()],
        no_color=no_color,
    )


@task_app.command("files")
def task_files(
    task_id: str = typer.Argument(..., help="任务 ID。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """查询任务产物。"""

    def operation() -> Any:
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.tasks.list_files(task_id)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
        return
    list_table(
        f"任务 {task_id} 的产物",
        [
            ("file_id", "文件 ID"),
            ("file_type", "类型"),
            ("file_name", "名称"),
            ("file_size", "大小"),
            ("mime_type", "MIME"),
            ("created_at", "创建时间"),
        ],
        result,
        no_color=no_color,
    )


@node_app.command("list")
def node_list(
    page: int = typer.Option(1, "--page", min=1, help="页码。"),
    page_size: int = typer.Option(20, "--page-size", min=1, max=100, help="每页数量。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """分页查询媒体节点。"""

    def operation() -> Any:
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.nodes.list(page=page, page_size=page_size)

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
        return
    list_table(
        f"媒体节点（第 {result.page} 页，共 {result.total} 条）",
        [
            ("node_code", "节点"),
            ("node_type", "类型"),
            ("status", "状态"),
            ("readiness_status", "就绪"),
            ("active_tasks", "任务"),
            ("active_bindings", "绑定"),
            ("last_heartbeat_at", "最后心跳"),
        ],
        result.items,
        no_color=no_color,
    )


@binding_app.command("list")
def binding_list(
    page: int = typer.Option(1, "--page", min=1, help="页码。"),
    page_size: int = typer.Option(20, "--page-size", min=1, max=100, help="每页数量。"),
    node_id: str | None = typer.Option(None, "--node-id", help="节点 ID。"),
    status: str | None = typer.Option(None, "--status", help="绑定状态。"),
    resource_type: str | None = typer.Option(None, "--resource-type", help="资源类型。"),
    space_id: str | None = typer.Option(None, "--space-id", help="空间 ID。"),
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """分页查询流绑定。"""

    def operation() -> Any:
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.bindings.list(
                page=page,
                page_size=page_size,
                node_id=node_id,
                status=status,
                resource_type=resource_type,
                space_id=space_id,
            )

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
        return
    list_table(
        f"流绑定（第 {result.page} 页，共 {result.total} 条）",
        [
            ("binding_id", "绑定 ID"),
            ("resource_type", "资源类型"),
            ("node_code", "节点"),
            ("app", "应用"),
            ("stream_id", "流 ID"),
            ("status", "状态"),
            ("last_active_at", "最后活动"),
        ],
        result.items,
        no_color=no_color,
    )


@recorder_app.command("list")
def recorder_list(
    url: str | None = typer.Option(None, "--url", help="控制中心 URL。"),
    context: str | None = typer.Option(None, "--context", help="使用指定 context。"),
    ca_bundle: str | None = typer.Option(None, "--ca-bundle", help="自定义 CA 文件。"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON。"),
    no_color: bool = typer.Option(False, "--no-color", help="禁用彩色输出。"),
) -> None:
    """查询录制服务器和容量。"""

    def operation() -> Any:
        with create_client(_connection(url, context, ca_bundle)) as client:
            return client.recording_servers.list()

    result = invoke(operation, json_output=json_output, no_color=no_color)
    if json_output:
        emit_json(result)
        return
    list_table(
        "录制服务器",
        [
            ("server_code", "服务器"),
            ("server_name", "名称"),
            ("status", "状态"),
            ("recorder_node_id", "录制节点"),
            ("occupied_bindings", "已占绑定"),
            ("max_bindings", "绑定上限"),
            ("max_recordings", "录制上限"),
        ],
        result,
        no_color=no_color,
    )


__all__ = [
    "binding_app",
    "health",
    "node_app",
    "overview",
    "recorder_app",
    "task_app",
]
