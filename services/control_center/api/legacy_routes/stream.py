from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Dict, Any
import uuid
import logging
from datetime import datetime

from services.control_center.api.legacy_auth import verify_api_key
from services.control_center.api.legacy_schemas.stream import StreamRecordRequest, StreamRecordResponse
from services.control_center.api.legacy_response import success, error, not_found, ResponseCode
from media_platform.infrastructure.observability.logger import setup_request_id
from media_platform.application import (
    MediaStreamBindingService,
    RecorderCommandDispatchService,
    TaskDispatchService,
    TaskQueryService,
)
from services.control_center.application.recording_task_state_service import (
    RecordingReservationError,
    RecordingTaskStateService,
)
from media_platform.domain.task.legacy_models import TaskType, TaskStatus
from services.control_center.api.dependencies import (
    get_recorder_command_service,
    get_recording_task_state_service,
    get_stream_binding_service,
    get_task_dispatch_service,
    get_task_query_service,
)
from services.control_center.api.recording_targets import recorder_command_target
from services.control_center.api.legacy_routes.media_task_adapter import (
    create_legacy_media_task,
    legacy_status,
)

from services.control_center.api.legacy_schemas.stream import StreamProcessRequest,StreamProcessResponse

router = APIRouter(prefix="/stream", tags=["直播流处理"])

# 定义日志记录器
logger = logging.getLogger("stream_api")

def _legacy_datetime_text(value: datetime | None) -> str | None:
    """保持原接口习惯，传给 recorder-node 的时间为无时区毫秒文本。"""

    if value is None:
        return None
    return value.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _legacy_record_task_id(request: StreamRecordRequest) -> str:
    """旧录制接口 task_id 可选；未传时由服务端生成并返回。"""

    if request.task_id:
        return request.task_id
    return str(uuid.uuid4())


def _record_message_id(*, task_id: str, command: str) -> str:
    """构造录制命令稳定消息编号。"""

    return f"{task_id}:{command}"


def _record_start_params(
    request: StreamRecordRequest,
    *,
    task_id: str,
    target_node_id: str,
    binding_id: str,
) -> dict[str, Any]:
    """把旧录制入参转换为 recorder-node 命令参数。"""

    params = dict(request.extra_params or {})
    params.update(
        {
            "task_id": task_id,
            "app": request.app,
            "stream_id": request.stream_id,
            "start_time": _legacy_datetime_text(request.start_time),
            "end_time": _legacy_datetime_text(request.end_time),
            "output_format": request.output_format,
            "callback_url": request.callback_url,
            "extra_params": dict(request.extra_params or {}),
            "target_node_id": target_node_id,
            "binding_id": binding_id,
            "dispatch_mode": "media.command",
        }
    )
    return params


def _dispatch_record_stop_by_task_id(
    *,
    task_id: str,
    trace_id: str,
    recorder_command_service: RecorderCommandDispatchService,
    recording_task_state_service: RecordingTaskStateService,
) -> dict[str, Any] | None:
    """按原停止录制语义处理：调用方只传 task_id，节点上下文从 MySQL 查询。"""

    context = recording_task_state_service.get_context(task_id)
    if context is None:
        return None

    stop_params = {
        "task_id": task_id,
        "app": context.app,
        "stream_id": context.stream_id,
        "reason": "manual_stop",
    }
    message_id = _record_message_id(task_id=task_id, command="record.stop")
    recording_task_state_service.save_stop_command(
        task_id=task_id,
        stop_params=stop_params,
        message_id=message_id,
        updated_by="original-stream-api",
    )
    try:
        dispatch_result = recorder_command_service.send_record_stop(
            task_id=task_id,
            target_node_id=context.target_node_id,
            delete_from_memory=False,
            params=stop_params,
            message_id=message_id,
            trace_id=trace_id,
            idempotency_key=f"{task_id}:record.stop",
        )
        if not recording_task_state_service.mark_command_published(
            task_id=task_id,
            message_id=dispatch_result.message.message_id,
            updated_by="original-stream-api",
        ):
            raise RuntimeError("停止录制命令已发布但MySQL状态确认失败")
    except Exception as exc:
        recording_task_state_service.mark_command_dispatch_failed(
            task_id=task_id,
            message_id=message_id,
            error_message=f"停止录制命令发布失败: {exc}",
            updated_by="original-stream-api",
        )
        raise
    return {
        "found": True,
        # 保留旧字段，避免 RTC 解析历史响应时出错；真实业务语义是正常提前停止。
        "canceled": True,
        "stop_requested": True,
        "action": "record_stop_dispatched",
        "target_node_id": context.target_node_id,
        "command_message_id": dispatch_result.message.message_id,
        "reason": "停止命令已发送到原录制节点，节点将停止录制并继续完整后处理流程",
    }

# @mcp.tool(name="create stream record task",description="创建流录制任务")
@router.post("/record", response_model=StreamRecordResponse, summary="创建直播录制任务", description="创建一个新的直播流录制任务，支持定时录制和格式转换")
async def start_stream_recording(
    request: StreamRecordRequest,
    req: Request,
    stream_binding_service: MediaStreamBindingService = Depends(
        get_stream_binding_service
    ),
    recorder_command_service: RecorderCommandDispatchService = Depends(
        get_recorder_command_service
    ),
    recording_task_state_service: RecordingTaskStateService = Depends(
        get_recording_task_state_service
    ),
    api_key: Dict[str, str] = Depends(verify_api_key)
):
    """
    开始录制直播流

    - **task_id**: 任务ID，如果指定则使用已有的任务ID
    - **app**: 应用名称，例如：live、classCard等
    - **stream_id**: 流ID，不包含文件扩展名
    - **start_time**: 录制开始时间，不指定则立即开始
    - **end_time**: 录制结束时间，不指定则持续录制
    - **output_format**: 输出格式，默认mp4
    - **callback_url**: 任务完成回调地址
    - **extra_params**: 额外参数，如是否提取音频、音频格式等

    返回统一的响应格式：
    - code: 状态码
    - msg: 提示信息
    - data: 任务信息
    - traceId: 请求跟踪ID
    """
    # 设置请求跟踪ID
    trace_id = setup_request_id()
    logger.info(
        "收到直播流录制请求: app=%s, stream_id=%s, traceId=%s",
        request.app,
        request.stream_id,
        trace_id,
    )

    reservation_saved = False
    command_published = False
    try:
        binding = stream_binding_service.get_active_by_app_stream(
            app=request.app,
            stream_id=request.stream_id,
        )
        if binding is None:
            raise ValueError(
                "当前 app + stream_id 未找到有效流绑定，无法确定录像所在录制节点"
            )

        task_id = _legacy_record_task_id(request)
        target_node_id = recorder_command_target(binding)
        message_id = _record_message_id(task_id=task_id, command="record.start")
        params = _record_start_params(
            request,
            task_id=task_id,
            target_node_id=target_node_id,
            binding_id=binding.binding_id,
        )
        recording_task_state_service.save_start_command(
            task_id=task_id,
            app=request.app,
            stream_id=request.stream_id,
            target_node_id=target_node_id,
            binding_id=binding.binding_id,
            params=params,
            callback_url=request.callback_url,
            message_id=message_id,
            updated_by="original-stream-api",
        )
        reservation_saved = True
        dispatch_result = recorder_command_service.send_record_start(
            task_id=task_id,
            target_node_id=target_node_id,
            app=request.app,
            stream_id=request.stream_id,
            params=params,
            message_id=message_id,
            trace_id=trace_id,
            idempotency_key=f"{task_id}:record.start",
        )
        if not recording_task_state_service.mark_command_published(
            task_id=task_id,
            message_id=dispatch_result.message.message_id,
            updated_by="original-stream-api",
        ):
            raise RuntimeError("开始录制命令已发布但MySQL状态确认失败")
        command_published = True
        response_data = StreamRecordResponse(
            task_id=task_id,
            status=TaskStatus.PENDING.value,
            message="录制任务已创建",
            created_at=datetime.now(),
        )

        logger.info(
            "录制任务命令发布完成: task_id=%s, binding_node_id=%s, "
            "target_node_id=%s, message_id=%s, traceId=%s",
            task_id,
            binding.node_id,
            target_node_id,
            dispatch_result.message.message_id,
            trace_id,
        )

        # 上报流程轨迹：任务创建
        classroom_id = (request.extra_params or {}).get("classroom_id")
        if classroom_id:
            try:
                from media_platform.infrastructure.observability.flow_trace_client import (
                    FlowTraceStatus,
                    FlowTraceStepCode,
                    get_flow_trace_client,
                )
                flow_trace = get_flow_trace_client()
                await flow_trace.report_step(
                    classroom_id=classroom_id,
                    process_type=(request.extra_params or {}).get("process_type", "stream_record"),
                    step_code=FlowTraceStepCode.TASK_CREATED,
                    status=FlowTraceStatus.COMPLETED,
                    task_id=task_id,
                    trace_id=trace_id,
                    step_output_data={
                        "task_id": task_id,
                        "app": request.app,
                        "stream_id": request.stream_id,
                        "binding_node_id": binding.node_id,
                        "target_node_id": target_node_id,
                        "command_message_id": dispatch_result.message.message_id,
                    },
                )
            except Exception as ft_err:
                logger.warning(f"流程轨迹上报异常（不影响主流程）: {str(ft_err)}")

        return response_data

    except RecordingReservationError as e:
        logger.warning(f"录制容量预约被拒绝: {str(e)}, traceId: {trace_id}")
        raise HTTPException(status_code=409, detail=str(e))

    except ValueError as e:
        # 处理参数验证错误
        logger.warning(f"参数验证错误: {str(e)}, traceId: {trace_id}")
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        if reservation_saved and not command_published:
            try:
                recording_task_state_service.mark_start_dispatch_failed(
                    task_id=task_id,
                    message_id=message_id,
                    error_message=f"开始录制命令发布失败: {e}",
                    updated_by="original-stream-api",
                )
            except Exception:
                logger.exception(
                    "开始命令发布失败后释放命令领取失败: task_id=%s, traceId=%s",
                    task_id,
                    trace_id,
                )
        # 处理其他异常
        logger.error(f"处理请求异常: {str(e)}, traceId: {trace_id}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")

# @mcp.tool(name="get stream record task status",description="获取流录制任务状态")
@router.get("/record/{task_id}", summary="获取录制任务状态", description="通过任务ID获取直播录制任务的详细状态信息")
async def get_stream_recording_status(
    task_id: str  ,
    req: Request,
    query_service: TaskQueryService = Depends(get_task_query_service),
    api_key: Dict[str, str] = Depends(verify_api_key)
):
    """
    获取录制任务状态
     - **task_id**: 任务ID，通过创建录制任务接口返回的唯一标识

    返回统一的响应格式：
    - code: 状态码
    - msg: 提示信息
    - **data**: 任务状态信息，包含进度、结果URL等
    - traceId: 请求跟踪ID
    """
    # 设置请求跟踪ID
    trace_id = setup_request_id()
    logger.info(f"查询录制任务状态: {task_id}, traceId: {trace_id}")

    try:
        task = query_service.get_task(task_id)
        if not task:
            logger.warning(f"任务不存在: {task_id}, traceId: {trace_id}")
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

       # 提取额外参数
        extra_params = None
        if task.result:
            extra_params = {}
            # 如果有音频文件，添加到额外参数
            if "audio_url" in task.result:
                extra_params["audio_url"] = task.result["audio_url"]
            # 添加其他可能的扩展信息
            if "extra_info" in task.result:
                extra_params.update(task.result["extra_info"])

        # 转换任务数据为响应格式
        status_data = {
            "task_id": task.task_id,
            "status": legacy_status(task.status).value,
            "progress": task.progress,
            "message": f"任务状态: {legacy_status(task.status).value}",
            "result_url": task.result.get("result_url") if task.result else None,
            "start_time": task.started_at if task.started_at else task.created_at,
            "end_time": task.completed_at,
            "updated_at": task.updated_at,
            "extra_params": extra_params
        }

        return success(
            data=status_data,
            msg="获取任务状态成功",
            traceId=trace_id
        )

    except HTTPException:
        raise
    except Exception as e:
        # 处理异常
        logger.error(f"查询任务状态异常: {str(e)}, traceId: {trace_id}", exc_info=True)
        return error(
            code=ResponseCode.SERVER_ERROR,
            msg=f"服务器错误: {str(e)}",
            traceId=trace_id
        )

# @mcp.tool(name="cancel stream record task",description="取消流录制任务")
@router.get(
    "/record_cancel/{task_id}",
    summary="提前停止录制任务",
    description="兼容原 RTC 路径；按 task_id 提前停止录制，并继续录像后处理、上传和通知",
)
async def cancel_stream_recording(
    task_id: str,
    req: Request,
    recorder_command_service: RecorderCommandDispatchService = Depends(
        get_recorder_command_service
    ),
    recording_task_state_service: RecordingTaskStateService = Depends(
        get_recording_task_state_service
    ),
    api_key: Dict[str, str] = Depends(verify_api_key)
):
    """
    提前停止录制任务。

    对于不同状态的任务采用不同策略：
    - 未开始的任务：直接删除
    - 等待中的任务：终止等待并删除
    - 录制中的任务：提前结束录制但完成后续流程

    返回统一的响应格式：
    - code: 状态码
    - msg: 提示信息
    - data: 操作结果
    - traceId: 请求跟踪ID
    """
    # 设置请求跟踪ID
    trace_id = setup_request_id()
    logger.info(f"提前停止录制任务: {task_id}, traceId: {trace_id}")

    try:
        result = _dispatch_record_stop_by_task_id(
            task_id=task_id,
            trace_id=trace_id,
            recorder_command_service=recorder_command_service,
            recording_task_state_service=recording_task_state_service,
        )
        if result is None:
            return not_found(msg=f"任务不存在: {task_id}", traceId=trace_id)
        return success(
            data=result,
            msg="停止命令已发送，录制节点将停止录制并继续后处理",
            traceId=trace_id,
        )

    except Exception as e:
        # 处理异常
        logger.error(f"停止录制任务异常: {str(e)}, traceId: {trace_id}", exc_info=True)
        return error(
            code=ResponseCode.SERVER_ERROR,
            msg=f"服务器错误: {str(e)}",
            traceId=trace_id
        )


@router.get(
    "/mcp/record_cancel/{task_id}",
    summary="提前停止录制任务",
    description="兼容原 MCP 路径；按 task_id 提前停止录制并继续完整后处理",
)
async def cancel_stream_recording_for_mcp(
    task_id: str,
    recorder_command_service: RecorderCommandDispatchService = Depends(
        get_recorder_command_service
    ),
    recording_task_state_service: RecordingTaskStateService = Depends(
        get_recording_task_state_service
    ),
    api_key: Dict[str, str] = Depends(verify_api_key),
):
    """保留 `stream_gpu` 原 MCP 停止录制路径；停止入参仍只有 task_id。"""

    trace_id = setup_request_id()
    logger.info(f"MCP 提前停止录制任务: {task_id}, traceId: {trace_id}")

    try:
        result = _dispatch_record_stop_by_task_id(
            task_id=task_id,
            trace_id=trace_id,
            recorder_command_service=recorder_command_service,
            recording_task_state_service=recording_task_state_service,
        )
        if result is None:
            return not_found(msg=f"任务不存在: {task_id}", traceId=trace_id)
        return success(
            data=result,
            msg="停止命令已发送，录制节点将停止录制并继续后处理",
            traceId=trace_id,
        )
    except Exception as e:
        logger.error(f"MCP 停止录制任务异常: {str(e)}, traceId: {trace_id}", exc_info=True)
        return error(
            code=ResponseCode.SERVER_ERROR,
            msg=f"服务器错误: {str(e)}",
            traceId=trace_id,
        )


@router.post("/process",response_model=StreamProcessResponse, summary="视频流处理接口", description="视频流处理接口，支持提取音频")
async def process_video(
    request: StreamProcessRequest,
    task_service: TaskDispatchService = Depends(get_task_dispatch_service),
    api_key: dict = Depends(verify_api_key)
):
    """视频处理接口"""
    # 检查请求参数
    if request.operations is None or request.operations is TaskType.NONE:
        raise HTTPException(status_code=400, detail="缺少处理操作")
    if request.stream_url is None:
        raise HTTPException(status_code=400, detail="缺少视频源地址")

    try:
        task_id = create_legacy_media_task(
            service=task_service,
            task_type=request.operations,
            params={
                "callback_url": request.callback_url,
                "stream_url": request.stream_url,
                "api_endpoint": request.api_endpoint,
                "start_time": _legacy_datetime_text(request.start_time),
                "end_time": _legacy_datetime_text(request.end_time),
                "chunk_duration": request.chunk_duration,
                "ex_params": request.params or {},
            },
            callback_url=request.callback_url,
            priority=0,
        )
        return {
            "task_id": task_id,
            "status": TaskStatus.PENDING,
            "message": "直播流处理任务已创建"
        }

    except Exception as e:
        logger.error(f"创建任务失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建任务失败: {str(e)}")
