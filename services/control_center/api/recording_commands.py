"""调用中心录制命令 API。

本路由只负责根据 RTC 已建立的流绑定定位 recorder-node，并向 RabbitMQ
`media.command` 定向发布录制命令。调用中心不执行 FFmpeg、不读取录像目录、
不调用 ZLMediaKit 拉流或断流接口。
"""

from __future__ import annotations

import logging
from typing import Any
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from media_platform.application import (
    MediaStreamBindingService,
    RecorderCommandDispatchResult,
    RecorderCommandDispatchService,
)
from media_platform.domain.stream import MediaStreamBindingResult
from services.control_center.api.dependencies import (
    get_recorder_command_service,
    get_recording_task_state_service,
    get_stream_binding_service,
    verify_internal_api_key,
)
from services.control_center.api.recording_targets import recorder_command_target
from services.control_center.application.recording_task_state_service import (
    RecordingReservationError,
)
from services.control_center.api.schemas import (
    RecordingCommandResponse,
    RecordingCommandStartRequest,
    RecordingCommandStopRequest,
)


LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/recordings",
    tags=["recording-commands"],
    dependencies=[Depends(verify_internal_api_key)],
)


def _default_active_record_task_id(*, app: str, stream_id: str) -> str:
    """按流生成默认活跃录制任务号，降低 RTC 调用负担。"""

    return f"record-{app}-{stream_id}"


def _resolve_task_id(
    request: RecordingCommandStartRequest,
) -> str:
    """解析录制任务编号。

    RTC 已有录制计划任务号时直接复用，便于后续状态查询、停止录制和业务回调
    继续使用同一个任务 ID。不传时才按 `app + stream_id` 生成开放式活跃录制
    任务号，兼容手工 Swagger 测试。
    """

    if request.task_id:
        return request.task_id
    return _default_active_record_task_id(app=request.app, stream_id=request.stream_id)


def _message_id(
    *,
    task_id: str,
    command: str,
) -> str:
    """构造稳定命令消息编号，便于 Broker、日志和 recorder-node 幂等排查。"""

    return f"{task_id}:{command}"


def _resolve_idempotency_key(
    request: RecordingCommandStartRequest,
    *,
    command: str,
    task_id: str,
) -> str:
    """生成服务端内部命令幂等键。

    该值用于 MQ 和 recorder-node 排重，不要求 RTC 理解或传入。
    """

    return f"{command}:{request.app}:{request.stream_id}:{task_id}"


def _stop_idempotency_key(*, task_id: str) -> str:
    """停止录制只按业务任务号幂等，不要求 RTC 再传流参数。"""

    return f"record.stop:{task_id}"


def _active_binding_or_409(
    *,
    service: MediaStreamBindingService,
    app: str,
    stream_id: str,
) -> MediaStreamBindingResult:
    """按 app + stream_id 获取有效绑定；不存在时拒绝录制命令。"""

    binding = service.get_active_by_app_stream(app=app, stream_id=stream_id)
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "未找到 app + stream_id 对应的有效流绑定，无法确定录像所在 "
                "recorder-node；请先通过 RTC 流绑定接口建立绑定"
            ),
        )
    return binding


def _recording_params(
    request: RecordingCommandStartRequest,
) -> dict[str, Any]:
    """复制扩展参数，避免路由层修改请求对象。"""

    return dict(request.extra_params or {})


def _datetime_to_legacy_text(value: datetime | None) -> str | None:
    """按原录制接口习惯输出无时区毫秒时间字符串。"""

    if value is None:
        return None
    return value.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _to_response(
    *,
    command: str,
    dispatch_result: RecorderCommandDispatchResult,
    binding_id: str,
    app: str,
    stream_id: str,
) -> RecordingCommandResponse:
    """把应用服务发布结果转换为 Swagger/API 响应。"""

    return RecordingCommandResponse(
        accepted=True,
        command=command,  # type: ignore[arg-type]
        task_id=dispatch_result.message.task_id,
        target_node_id=dispatch_result.message.target_node_id,
        binding_id=binding_id,
        app=app,
        stream_id=stream_id,
        message_id=dispatch_result.receipt.message_id,
        confirmed=dispatch_result.receipt.confirmed,
    )


@router.post(
    "/start",
    response_model=RecordingCommandResponse,
    summary="按已有流绑定下发开始录制命令",
    description=(
        "业务端或 RTC 已通过 /api/v1/rtc/stream-bindings 获取并持久化流绑定后，"
        "调用本接口开始录制。调用中心只按 app + stream_id 查询 media_stream_binding，"
        "并把 record.start 命令投递到绑定的 recorder-node；如果绑定不存在或已释放，"
        "接口直接返回 409，不重新选择其他健康节点。"
    ),
)
def start_recording(
    request: RecordingCommandStartRequest,
    stream_binding_service: MediaStreamBindingService = Depends(
        get_stream_binding_service,
    ),
    command_service: RecorderCommandDispatchService = Depends(
        get_recorder_command_service,
    ),
    recording_task_state_service=Depends(get_recording_task_state_service),
) -> RecordingCommandResponse:
    """下发开始录制命令。"""

    binding = _active_binding_or_409(
        service=stream_binding_service,
        app=request.app,
        stream_id=request.stream_id,
    )
    task_id = _resolve_task_id(request)
    params = _recording_params(request)
    params["task_id"] = task_id
    start_time_text = _datetime_to_legacy_text(request.start_time)
    end_time_text = _datetime_to_legacy_text(request.end_time)
    if start_time_text:
        params["start_time"] = start_time_text
    if end_time_text:
        params["end_time"] = end_time_text
    params["output_format"] = request.output_format
    if request.callback_url:
        params["callback_url"] = request.callback_url
    idempotency_key = _resolve_idempotency_key(
        request,
        command="record.start",
        task_id=task_id,
    )
    message_id = _message_id(
        task_id=task_id,
        command="record.start",
    )
    target_node_id = recorder_command_target(binding)

    LOGGER.info(
        "准备下发开始录制命令: task_id=%s, app=%s, stream_id=%s, "
        "binding_id=%s, binding_node_id=%s, target_node_id=%s",
        task_id,
        request.app,
        request.stream_id,
        binding.binding_id,
        binding.node_id,
        target_node_id,
    )
    reservation_saved = False
    try:
        recording_task_state_service.save_start_command(
            task_id=task_id,
            app=request.app,
            stream_id=request.stream_id,
            target_node_id=target_node_id,
            binding_id=binding.binding_id,
            params=params,
            callback_url=request.callback_url,
            message_id=message_id,
        )
        reservation_saved = True
        dispatch_result = command_service.send_record_start(
            task_id=task_id,
            target_node_id=target_node_id,
            app=request.app,
            stream_id=request.stream_id,
            params=params,
            message_id=message_id,
            trace_id=task_id,
            idempotency_key=idempotency_key,
        )
        if not recording_task_state_service.mark_command_published(
            task_id=task_id,
            message_id=dispatch_result.message.message_id,
        ):
            raise RuntimeError("开始录制命令已发布但MySQL状态确认失败")
    except RecordingReservationError as exc:
        LOGGER.warning("录制容量预约被拒绝: task_id=%s, reason=%s", task_id, exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        if reservation_saved:
            try:
                recording_task_state_service.mark_start_dispatch_failed(
                    task_id=task_id,
                    message_id=message_id,
                    error_message=f"开始录制命令发布失败: {exc}",
                )
            except Exception:
                LOGGER.exception(
                    "开始命令发布失败后释放命令领取失败: task_id=%s",
                    task_id,
                )
        LOGGER.exception("开始录制命令发布失败: task_id=%s", task_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"开始录制命令发布失败: {exc}",
        ) from exc
    return _to_response(
        command="record.start",
        dispatch_result=dispatch_result,
        binding_id=binding.binding_id,
        app=binding.app,
        stream_id=binding.stream_id,
    )


@router.post(
    "/stop",
    response_model=RecordingCommandResponse,
    summary="按 task_id 下发停止录制命令",
    description=(
        "RTC 停止录制只需要传开始录制时的同一个 task_id。调用中心从 MySQL "
        "录制任务上下文中读取 app、stream_id 和目标 recorder-node，并把 "
        "record.stop 命令投递到同一录制节点。停止录制表示提前结束并继续后处理，"
        "不是取消并丢弃结果。"
    ),
)
def stop_recording(
    request: RecordingCommandStopRequest,
    command_service: RecorderCommandDispatchService = Depends(
        get_recorder_command_service,
    ),
    recording_task_state_service=Depends(get_recording_task_state_service),
) -> RecordingCommandResponse:
    """下发停止录制命令。"""

    task_id = str(request.task_id).strip()
    context = recording_task_state_service.get_context(task_id)
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "未找到 task_id 对应的录制任务上下文，无法确定 recorder-node；"
                "请确认开始录制已成功下发并写入任务事实"
            ),
        )
    params: dict[str, Any] = {
        "task_id": task_id,
        "app": context.app,
        "stream_id": context.stream_id,
        "reason": "manual_stop",
    }
    params["task_id"] = task_id
    idempotency_key = _stop_idempotency_key(task_id=task_id)
    message_id = _message_id(task_id=task_id, command="record.stop")

    LOGGER.info(
        "准备下发停止录制命令: task_id=%s, app=%s, stream_id=%s, "
        "binding_id=%s, target_node_id=%s",
        task_id,
        context.app,
        context.stream_id,
        context.binding_id,
        context.target_node_id,
    )
    try:
        recording_task_state_service.save_stop_command(
            task_id=task_id,
            stop_params=params,
            message_id=message_id,
        )
        dispatch_result = command_service.send_record_stop(
            task_id=task_id,
            target_node_id=context.target_node_id,
            delete_from_memory=False,
            params=params,
            message_id=message_id,
            trace_id=task_id,
            idempotency_key=idempotency_key,
        )
        if not recording_task_state_service.mark_command_published(
            task_id=task_id,
            message_id=dispatch_result.message.message_id,
        ):
            raise RuntimeError("停止录制命令已发布但MySQL状态确认失败")
    except Exception as exc:
        try:
            recording_task_state_service.mark_command_dispatch_failed(
                task_id=task_id,
                message_id=message_id,
                error_message=f"停止录制命令发布失败: {exc}",
            )
        except Exception:
            LOGGER.exception(
                "停止命令发布失败后释放命令领取失败: task_id=%s",
                task_id,
            )
        LOGGER.exception("停止录制命令发布失败: task_id=%s", task_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"停止录制命令发布失败: {exc}",
        ) from exc
    return _to_response(
        command="record.stop",
        dispatch_result=dispatch_result,
        binding_id=context.binding_id or "",
        app=context.app,
        stream_id=context.stream_id,
    )


__all__ = ["router", "start_recording", "stop_recording"]
