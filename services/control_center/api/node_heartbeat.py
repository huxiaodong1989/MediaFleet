"""调用中心媒体节点心跳 API。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from media_platform.application import (
    MediaNodeHeartbeatConflictError,
    MediaNodeHeartbeatService,
    RecordingServerIdentityConflictError,
)
from media_platform.domain.node import (
    MediaNodeHeartbeat,
    MediaNodeReadiness,
    MediaNodeStatus,
    MediaNodeType,
)
from services.control_center.api.dependencies import (
    get_node_heartbeat_service,
    verify_internal_api_key,
)
from services.control_center.api.schemas import (
    NodeHeartbeatRequest,
    NodeHeartbeatResponse,
)


LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/nodes",
    tags=["media-nodes"],
    dependencies=[Depends(verify_internal_api_key)],
)


@router.post(
    "/heartbeat",
    response_model=NodeHeartbeatResponse,
    summary="上报媒体节点心跳和容量",
    description=(
        "录制节点和通用媒体 Worker 周期性调用该接口，调用中心把节点状态、能力和"
        "容量快照写入 MySQL 国标节点表 media_node。录制节点还会注册固定录制服务器"
        "并写入 ZL、录像目录和磁盘的真实就绪检查；调用中心不执行本地探测或媒体处理。"
    ),
)
def report_node_heartbeat(
    request: NodeHeartbeatRequest,
    service: MediaNodeHeartbeatService = Depends(get_node_heartbeat_service),
) -> NodeHeartbeatResponse:
    """接收节点心跳并持久化到 MySQL。"""

    LOGGER.info(
        "收到媒体节点心跳: node_code=%s, node_type=%s, status=%s",
        request.node_code,
        request.node_type,
        request.status,
    )
    try:
        result = service.report(
            MediaNodeHeartbeat(
                node_code=request.node_code,
                node_name=request.node_name,
                node_type=MediaNodeType(request.node_type),
                status=MediaNodeStatus(request.status),
                server_code=request.server_code,
                server_name=request.server_name,
                agent_url=request.agent_url,
                zlm_api_url=request.zlm_api_url,
                zlm_server_id=request.zlm_server_id,
                play_host=request.play_host,
                play_port=request.play_port,
                play_protocol=request.play_protocol,
                rtmp_port=request.rtmp_port,
                rtsp_port=request.rtsp_port,
                record_root=request.record_root,
                weight=request.weight,
                capabilities=tuple(request.capabilities),
                capacity=request.capacity,
                readiness=MediaNodeReadiness(
                    request.readiness
                    or ("READY" if request.node_type == "WORKER" else "NOT_READY")
                ),
                readiness_details=request.readiness_details,
                reported_at=request.reported_at,
                updated_by=request.updated_by,
            )
        )
    except (
        MediaNodeHeartbeatConflictError,
        RecordingServerIdentityConflictError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return NodeHeartbeatResponse(
        node_id=result.node_id,
        created=result.created,
        status=result.status.value,
        last_heartbeat_at=result.last_heartbeat_at,
        recording_server_id=result.recording_server_id,
    )


__all__ = ["router"]
