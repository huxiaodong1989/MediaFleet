"""调用中心录制服务器管理 API。"""

from fastapi import APIRouter, Depends, HTTPException, status

from services.control_center.api.dependencies import (
    get_recording_server_service,
    verify_internal_api_key,
)
from services.control_center.api.schemas import (
    RecordingServerResponse,
    RecordingServerStatusUpdateRequest,
)
from services.control_center.application.recording_server_service import (
    RecordingServerNotFoundError,
    RecordingServerService,
    RecordingServerStatus,
)


router = APIRouter(
    prefix="/api/v1/recording-servers",
    tags=["recording-servers"],
    dependencies=[Depends(verify_internal_api_key)],
)


def _response(view) -> RecordingServerResponse:
    return RecordingServerResponse(
        server_id=view.server_id,
        server_code=view.server_code,
        server_name=view.server_name,
        status=view.status.value,
        recorder_node_id=view.recorder_node_id,
        zlm_server_id=view.zlm_server_id,
        zlm_api_url=view.zlm_api_url,
        play_host=view.play_host,
        play_port=view.play_port,
        play_protocol=view.play_protocol,
        rtmp_port=view.rtmp_port,
        rtsp_port=view.rtsp_port,
        record_root=view.record_root,
        max_recordings=view.max_recordings,
        max_bindings=view.max_bindings,
        occupied_bindings=view.occupied_bindings,
    )


@router.get(
    "",
    response_model=list[RecordingServerResponse],
    summary="查询录制服务器",
    description="查询 recorder-node 心跳注册的固定 ZL/recorder/录像盘录制单元。",
)
def list_recording_servers(
    service: RecordingServerService = Depends(get_recording_server_service),
) -> list[RecordingServerResponse]:
    return [_response(view) for view in service.list_servers()]


@router.patch(
    "/{server_code}/status",
    response_model=RecordingServerResponse,
    summary="更新录制服务器运维状态",
    description=(
        "DRAINING、MAINTENANCE、DISABLED 会立即阻止新绑定；心跳只更新运行事实，"
        "不会把运维状态改回 ACTIVE。"
    ),
)
def update_recording_server_status(
    server_code: str,
    request: RecordingServerStatusUpdateRequest,
    service: RecordingServerService = Depends(get_recording_server_service),
) -> RecordingServerResponse:
    try:
        view = service.update_status(
            server_code=server_code,
            status=RecordingServerStatus(request.status),
            updated_by=request.updated_by,
        )
    except RecordingServerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _response(view)


__all__ = ["router"]
