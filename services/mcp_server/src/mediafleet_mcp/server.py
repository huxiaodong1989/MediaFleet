"""MediaFleet 只读 MCP Server。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from mediafleet_sdk import AsyncMediaFleetClient

from mediafleet_mcp import __version__
from mediafleet_mcp.errors import EXPECTED_ERRORS, failure, success
from mediafleet_mcp.projections import (
    binding_page,
    health,
    node_page,
    overview,
    recording_servers,
    task_detail,
    task_files,
    task_page,
)
from mediafleet_mcp.settings import MCPSettings

ReadOperation = Callable[[AsyncMediaFleetClient], Awaitable[Any]]
Projector = Callable[[Any], Any]

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

server = MCPServer(
    name="mediafleet",
    title="MediaFleet",
    description="通过 MediaFleet SDK 查询控制中心的只读 MCP Server。",
    instructions=(
        "这些工具只读取 MediaFleet 状态。列表工具必须使用有界分页；"
        "服务端不会提供任意 HTTP、SQL、Shell、消息发布或文件访问能力。"
    ),
    version=__version__,
    log_level="ERROR",
)


async def _execute(operation: ReadOperation, projector: Projector) -> dict[str, Any]:
    try:
        settings = MCPSettings.from_env()
        async with AsyncMediaFleetClient(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=settings.timeout,
            verify=settings.verify,
            user_agent=f"mediafleet-mcp/{__version__}",
        ) as client:
            value = await operation(client)
        return success(projector(value))
    except EXPECTED_ERRORS as exc:
        return failure(exc)


@server.tool(
    name="mediafleet_health",
    title="检查 MediaFleet 健康状态",
    annotations=READ_ONLY,
    structured_output=True,
)
async def mediafleet_health() -> dict[str, Any]:
    """检查控制中心健康状态、角色和版本。"""

    return await _execute(lambda client: client.system.health(), health)


@server.tool(
    name="mediafleet_get_overview",
    title="获取 MediaFleet 运维总览",
    annotations=READ_ONLY,
    structured_output=True,
)
async def mediafleet_get_overview() -> dict[str, Any]:
    """获取任务、节点和队列的聚合状态。"""

    return await _execute(lambda client: client.admin.overview(), overview)


@server.tool(
    name="mediafleet_list_tasks",
    title="分页查询媒体任务",
    annotations=READ_ONLY,
    structured_output=True,
)
async def mediafleet_list_tasks(
    page: Annotated[int, Field(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Field(ge=1, le=100, description="每页数量，最多 100")] = 20,
    task_type: Annotated[str | None, Field(max_length=128)] = None,
    status: Annotated[str | None, Field(max_length=64)] = None,
    node_id: Annotated[str | None, Field(max_length=128)] = None,
    school_code: Annotated[str | None, Field(max_length=128)] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> dict[str, Any]:
    """按状态、类型、节点、租户和创建时间分页查询媒体任务。"""

    return await _execute(
        lambda client: client.tasks.list(
            page=page,
            page_size=page_size,
            task_type=task_type,
            status=status,
            node_id=node_id,
            school_code=school_code,
            created_from=created_from,
            created_to=created_to,
        ),
        task_page,
    )


@server.tool(
    name="mediafleet_get_task",
    title="查询单个媒体任务",
    annotations=READ_ONLY,
    structured_output=True,
)
async def mediafleet_get_task(
    task_id: Annotated[str, Field(min_length=1, max_length=256)],
) -> dict[str, Any]:
    """按任务 ID 查询状态、时间、参数和结果；敏感字段会脱敏。"""

    return await _execute(
        lambda client: client.tasks.get(task_id),
        task_detail,
    )


@server.tool(
    name="mediafleet_list_task_files",
    title="查询任务媒体产物",
    annotations=READ_ONLY,
    structured_output=True,
)
async def mediafleet_list_task_files(
    task_id: Annotated[str, Field(min_length=1, max_length=256)],
) -> dict[str, Any]:
    """查询任务产物元数据，不向模型上下文返回签名下载 URL。"""

    return await _execute(
        lambda client: client.tasks.list_files(task_id),
        task_files,
    )


@server.tool(
    name="mediafleet_list_nodes",
    title="分页查询媒体节点",
    annotations=READ_ONLY,
    structured_output=True,
)
async def mediafleet_list_nodes(
    page: Annotated[int, Field(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Field(ge=1, le=100, description="每页数量，最多 100")] = 20,
) -> dict[str, Any]:
    """分页查询节点状态、能力、容量和最后心跳。"""

    return await _execute(
        lambda client: client.nodes.list(page=page, page_size=page_size),
        node_page,
    )


@server.tool(
    name="mediafleet_list_bindings",
    title="分页查询流绑定",
    annotations=READ_ONLY,
    structured_output=True,
)
async def mediafleet_list_bindings(
    page: Annotated[int, Field(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[int, Field(ge=1, le=100, description="每页数量，最多 100")] = 20,
    node_id: Annotated[str | None, Field(max_length=128)] = None,
    status: Annotated[str | None, Field(max_length=64)] = None,
    resource_type: Annotated[str | None, Field(max_length=128)] = None,
    space_id: Annotated[str | None, Field(max_length=128)] = None,
) -> dict[str, Any]:
    """按节点、状态、资源类型和空间分页查询流绑定。"""

    return await _execute(
        lambda client: client.bindings.list(
            page=page,
            page_size=page_size,
            node_id=node_id,
            status=status,
            resource_type=resource_type,
            space_id=space_id,
        ),
        binding_page,
    )


@server.tool(
    name="mediafleet_list_recording_servers",
    title="查询录制服务器",
    annotations=READ_ONLY,
    structured_output=True,
)
async def mediafleet_list_recording_servers() -> dict[str, Any]:
    """查询录制服务器状态和容量，不返回内部地址或本地目录。"""

    return await _execute(
        lambda client: client.recording_servers.list(),
        recording_servers,
    )


def run() -> None:
    """使用 stdio 传输启动 MCP Server。"""

    server.run(transport="stdio")


if __name__ == "__main__":
    run()


__all__ = ["run", "server"]
