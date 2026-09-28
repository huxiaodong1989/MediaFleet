"""RTC 摄像头和桌面流绑定 API。"""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status

from media_platform.application import MediaStreamBindingService
from media_platform.domain.stream import (
    MediaStreamBindingCommand,
    MediaStreamBindingResult,
    StreamBindingConflictError,
    StreamBindingNodeUnavailableError,
    StreamResourceType,
)
from services.control_center.api.dependencies import (
    get_stream_binding_service,
    verify_internal_api_key,
)
from services.control_center.api.schemas import (
    StreamBindingCreateRequest,
    StreamBindingResponse,
)


router = APIRouter(
    prefix="/api/v1/rtc/stream-bindings",
    tags=["rtc-stream-bindings"],
    dependencies=[Depends(verify_internal_api_key)],
)


def _to_command(request: StreamBindingCreateRequest) -> MediaStreamBindingCommand:
    """把 RTC API 入参转换为应用服务命令。"""

    return MediaStreamBindingCommand(
        school_code=request.school_code,
        resource_type=StreamResourceType(request.resource_type),
        space_id=request.space_id,
        stream_id=request.stream_id,
        stream_name=request.stream_name,
        app=request.app,
    )


def _to_response(result: MediaStreamBindingResult) -> StreamBindingResponse:
    """把应用服务结果转换为 RTC 可用响应。"""

    node = result.node
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="绑定节点不存在，无法返回流媒体端点",
        )
    required_endpoint_fields = {
        "api_url": node.zlm_api_url,
        "server_id": node.zlm_server_id,
    }
    missing_fields = [
        field_name
        for field_name, value in required_endpoint_fields.items()
        if not value
    ]
    if missing_fields:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "绑定节点缺少流媒体端点配置: "
                + ", ".join(missing_fields)
            ),
        )
    api_url = str(node.zlm_api_url).strip()
    ip = urlsplit(api_url).hostname
    if not ip:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="绑定节点 api_url 无法解析流媒体 IP",
        )
    return StreamBindingResponse(
        stream_id=result.stream_id,
        api_url=api_url,
        server_id=str(node.zlm_server_id),
        ip=ip,
    )


@router.post(
    "",
    response_model=StreamBindingResponse,
    summary="创建或获取 RTC 流绑定",
    description=(
        "RTC 为摄像头或桌面技术流申请 ZLMediaKit 节点时调用。"
        "调用中心只做节点选择和 media_stream_binding 绑定持久化，不调用 ZLMediaKit，"
        "不接收 RTSP 地址。相同 app + stream_id 重复请求直接返回已有绑定；"
        "传入 space_id 时，相同 school_code + space_id 下的新流优先复用同一个健康"
        "录制节点/ZLMediaKit；未传空间时按健康度、容量和权重选择最佳节点。"
    ),
)
def bind_stream(
    request: StreamBindingCreateRequest,
    service: MediaStreamBindingService = Depends(get_stream_binding_service),
) -> StreamBindingResponse:
    """创建或复用摄像头/桌面技术流绑定。"""

    try:
        result = service.bind_stream(_to_command(request))
    except StreamBindingConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except StreamBindingNodeUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return _to_response(result)


@router.delete(
    "/{binding_id}",
    response_model=StreamBindingResponse,
    summary="释放 RTC 流绑定",
    description=(
        "仅在摄像头/桌面资源删除、明确解绑或迁移时调用。临时断流不应释放粘性绑定。"
        "接口幂等；重复释放不会重复归还录制服务器绑定容量。"
    ),
)
def release_stream_binding(
    binding_id: str,
    service: MediaStreamBindingService = Depends(get_stream_binding_service),
) -> StreamBindingResponse:
    """释放绑定并归还录制服务器容量名额。"""

    try:
        result = service.release_binding(binding_id)
    except StreamBindingConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"流绑定不存在: binding_id={binding_id}",
        )
    return _to_response(result)


__all__ = ["bind_stream", "release_stream_binding", "router"]
