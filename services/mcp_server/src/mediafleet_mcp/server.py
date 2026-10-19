"""MediaFleet 默认只读、写工具逐项允许的 MCP Server。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
import json
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from mediafleet_sdk import AsyncMediaFleetClient
from mediafleet_sdk.models import BindingCreate, RecordingStart, TaskCreate

from mediafleet_mcp import __version__
from mediafleet_mcp.errors import (
    EXPECTED_ERRORS,
    MCPConfirmationRequiredError,
    MCPInputValidationError,
    failure,
    success,
)
from mediafleet_mcp.projections import (
    binding_changed,
    binding_page,
    callback_retried,
    health,
    node_page,
    overview,
    recording_command,
    recording_servers,
    task_created,
    task_detail,
    task_files,
    task_page,
    task_retried,
)
from mediafleet_mcp.settings import MCPSettings

Operation = Callable[[AsyncMediaFleetClient], Awaitable[Any]]
Projector = Callable[[Any], Any]
MAX_WRITE_PAYLOAD_BYTES = 64 * 1024

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

NON_IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

DESTRUCTIVE_IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)

server = MCPServer(
    name="mediafleet",
    title="MediaFleet",
    description="通过 MediaFleet SDK 查询控制中心，并按显式允许列表执行受控写操作。",
    instructions=(
        "默认只注册读取工具。写工具必须由 MEDIAFLEET_MCP_WRITE_TOOLS 逐项启用，"
        "调用时显式确认，并继续经过控制中心范围化授权与可信审计。列表工具必须使用有界分页；"
        "服务端不会提供任意 HTTP、SQL、Shell、消息发布、凭据管理、节点状态修改或文件访问能力。"
    ),
    version=__version__,
    log_level="ERROR",
)


async def _execute(
    operation: Operation,
    projector: Projector,
    *,
    required_write_tool: str | None = None,
) -> dict[str, Any]:
    try:
        settings = MCPSettings.from_env()
        if (
            required_write_tool is not None
            and required_write_tool not in settings.write_tools
        ):
            raise MCPInputValidationError(
                f"写工具未在 MEDIAFLEET_MCP_WRITE_TOOLS 中启用: {required_write_tool}"
            )
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


def _require_confirmation(confirmed: bool, action: str) -> None:
    if not confirmed:
        raise MCPConfirmationRequiredError(
            f"{action}需要调用方在确认影响后将 confirmed 设置为 true"
        )


def _validate_write_payload(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded) > MAX_WRITE_PAYLOAD_BYTES:
        raise MCPInputValidationError(
            f"写工具请求不能超过 {MAX_WRITE_PAYLOAD_BYTES // 1024} KiB"
        )


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


async def mediafleet_create_task(
    school_code: Annotated[str, Field(min_length=1, max_length=64)],
    task_type: Annotated[str, Field(min_length=1, max_length=64)],
    callback_url: Annotated[str, Field(min_length=1, max_length=1000)],
    idempotency_key: Annotated[
        str,
        Field(
            min_length=8,
            max_length=128,
            description="同一次业务请求及其重试必须复用的稳定幂等键",
        ),
    ],
    confirmed: Annotated[
        bool,
        Field(description="确认创建任务并可能消耗媒体处理资源"),
    ] = False,
    priority: Annotated[int, Field(ge=0, le=255)] = 0,
    max_retries: Annotated[int, Field(ge=1, le=100)] = 3,
    params: Annotated[
        dict[str, Any] | None,
        Field(description="对应 task_type 的任务参数，最多 64 KiB"),
    ] = None,
    target_node_id: Annotated[str | None, Field(max_length=36)] = None,
) -> dict[str, Any]:
    """创建通用媒体任务；要求 task:write 凭据和稳定幂等键。"""

    async def operation(client: AsyncMediaFleetClient) -> Any:
        _require_confirmation(confirmed, "创建媒体任务")
        payload = {
            "school_code": school_code,
            "task_type": task_type,
            "callback_url": callback_url,
            "priority": priority,
            "max_retries": max_retries,
            "params": params or {},
            "target_node_id": target_node_id,
        }
        _validate_write_payload(payload)
        request = TaskCreate(**payload)
        return await client.tasks.create(
            request,
            idempotency_key=idempotency_key,
        )

    return await _execute(
        operation,
        task_created,
        required_write_tool="mediafleet_create_task",
    )


async def mediafleet_retry_task(
    task_id: Annotated[str, Field(min_length=1, max_length=256)],
    confirmed: Annotated[
        bool,
        Field(description="确认重新投递失败或已取消的任务"),
    ] = False,
) -> dict[str, Any]:
    """重新投递失败或已取消的任务；要求 task:write 凭据。"""

    async def operation(client: AsyncMediaFleetClient) -> Any:
        _require_confirmation(confirmed, "任务重试")
        return await client.tasks.retry(task_id)

    return await _execute(
        operation,
        task_retried,
        required_write_tool="mediafleet_retry_task",
    )


async def mediafleet_retry_task_callback(
    task_id: Annotated[str, Field(min_length=1, max_length=256)],
    confirmed: Annotated[
        bool,
        Field(description="确认再次调用任务配置的外部业务回调"),
    ] = False,
) -> dict[str, Any]:
    """补发终态任务的业务回调；不会重新执行媒体任务。"""

    async def operation(client: AsyncMediaFleetClient) -> Any:
        _require_confirmation(confirmed, "业务回调补发")
        return await client.tasks.retry_callback(task_id)

    return await _execute(
        operation,
        callback_retried,
        required_write_tool="mediafleet_retry_task_callback",
    )


async def mediafleet_create_binding(
    school_code: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern=r"^\d+$"),
    ],
    resource_type: Literal["CAMERA", "DESKTOP"],
    stream_id: Annotated[str, Field(min_length=1, max_length=128)],
    app: Annotated[str, Field(min_length=1, max_length=64)],
    confirmed: Annotated[
        bool,
        Field(description="确认创建或复用流绑定并占用录制节点容量"),
    ] = False,
    space_id: Annotated[
        str | None,
        Field(max_length=64, pattern=r"^\d+$"),
    ] = None,
    stream_name: Annotated[str | None, Field(max_length=255)] = None,
) -> dict[str, Any]:
    """创建或复用 RTC 流绑定；响应不向模型暴露内部流媒体地址。"""

    async def operation(client: AsyncMediaFleetClient) -> Any:
        _require_confirmation(confirmed, "创建流绑定")
        request = BindingCreate(
            school_code=school_code,
            resource_type=resource_type,
            stream_id=stream_id,
            app=app,
            space_id=space_id,
            stream_name=stream_name,
        )
        return await client.bindings.create(request)

    return await _execute(
        operation,
        binding_changed,
        required_write_tool="mediafleet_create_binding",
    )


async def mediafleet_release_binding(
    binding_id: Annotated[str, Field(min_length=1, max_length=256)],
    confirmed: Annotated[
        bool,
        Field(description="确认释放粘性流绑定并归还容量；临时断流不应调用"),
    ] = False,
) -> dict[str, Any]:
    """释放流绑定；仅用于资源删除、明确解绑或迁移。"""

    async def operation(client: AsyncMediaFleetClient) -> Any:
        _require_confirmation(confirmed, "释放流绑定")
        return await client.bindings.release(binding_id)

    return await _execute(
        operation,
        binding_changed,
        required_write_tool="mediafleet_release_binding",
    )


async def mediafleet_start_recording(
    stream_id: Annotated[str, Field(min_length=1, max_length=128)],
    task_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=128,
            description="稳定录制任务 ID，重复请求必须复用",
        ),
    ],
    confirmed: Annotated[
        bool,
        Field(description="确认在已有流绑定上开始录制"),
    ] = False,
    app: Annotated[str, Field(min_length=1, max_length=64)] = "live",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    output_format: Annotated[str, Field(min_length=1, max_length=32)] = "mp4",
    callback_url: Annotated[str | None, Field(max_length=1000)] = None,
    extra_params: Annotated[
        dict[str, Any] | None,
        Field(description="录制扩展参数，最多 64 KiB"),
    ] = None,
) -> dict[str, Any]:
    """在已有流绑定上开始录制；要求 recording:write 凭据。"""

    async def operation(client: AsyncMediaFleetClient) -> Any:
        _require_confirmation(confirmed, "开始录制")
        payload = {
            "stream_id": stream_id,
            "task_id": task_id,
            "app": app,
            "start_time": start_time,
            "end_time": end_time,
            "output_format": output_format,
            "callback_url": callback_url,
            "extra_params": extra_params or {},
        }
        _validate_write_payload(payload)
        return await client.recordings.start(RecordingStart(**payload))

    return await _execute(
        operation,
        recording_command,
        required_write_tool="mediafleet_start_recording",
    )


async def mediafleet_stop_recording(
    task_id: Annotated[str, Field(min_length=1, max_length=128)],
    confirmed: Annotated[
        bool,
        Field(description="确认提前停止录制并继续后处理"),
    ] = False,
) -> dict[str, Any]:
    """按稳定任务 ID 停止录制并继续后处理。"""

    async def operation(client: AsyncMediaFleetClient) -> Any:
        _require_confirmation(confirmed, "停止录制")
        return await client.recordings.stop(task_id)

    return await _execute(
        operation,
        recording_command,
        required_write_tool="mediafleet_stop_recording",
    )


WRITE_TOOL_REGISTRATIONS = {
    "mediafleet_create_task": (
        "创建 MediaFleet 媒体任务",
        IDEMPOTENT_WRITE,
        mediafleet_create_task,
    ),
    "mediafleet_retry_task": (
        "重新投递失败的媒体任务",
        NON_IDEMPOTENT_WRITE,
        mediafleet_retry_task,
    ),
    "mediafleet_retry_task_callback": (
        "补发媒体任务业务回调",
        NON_IDEMPOTENT_WRITE,
        mediafleet_retry_task_callback,
    ),
    "mediafleet_create_binding": (
        "创建或复用流绑定",
        IDEMPOTENT_WRITE,
        mediafleet_create_binding,
    ),
    "mediafleet_release_binding": (
        "释放流绑定",
        DESTRUCTIVE_IDEMPOTENT_WRITE,
        mediafleet_release_binding,
    ),
    "mediafleet_start_recording": (
        "开始录制",
        IDEMPOTENT_WRITE,
        mediafleet_start_recording,
    ),
    "mediafleet_stop_recording": (
        "停止录制并继续后处理",
        DESTRUCTIVE_IDEMPOTENT_WRITE,
        mediafleet_stop_recording,
    ),
}


def register_write_tools(
    target_server: MCPServer,
    enabled_tools: frozenset[str],
) -> None:
    """只注册部署配置逐项允许的写工具。"""

    for name in sorted(enabled_tools):
        title, annotations, function = WRITE_TOOL_REGISTRATIONS[name]
        target_server.tool(
            name=name,
            title=title,
            annotations=annotations,
            structured_output=True,
        )(function)


def run() -> None:
    """使用 stdio 传输启动 MCP Server。"""

    register_write_tools(server, MCPSettings.write_tools_from_env())
    server.run(transport="stdio")


if __name__ == "__main__":
    run()


__all__ = ["register_write_tools", "run", "server"]
