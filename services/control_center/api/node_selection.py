"""调用中心媒体节点查询与选择 API。"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Query

from media_platform.application import MediaNodeSelectionService
from media_platform.domain.node import (
    MediaNodeType,
    NodeSelectionCandidate,
    NodeSelectionCriteria,
)
from services.control_center.api.dependencies import (
    get_node_selection_service,
    verify_internal_api_key,
)
from services.control_center.api.schemas import (
    NodeCandidateResponse,
    NodeSelectRequest,
    NodeSelectResponse,
)


router = APIRouter(
    prefix="/api/v1/nodes",
    tags=["media-nodes"],
    dependencies=[Depends(verify_internal_api_key)],
)


def _criteria(
    *,
    node_type: str,
    capability: str | None,
    heartbeat_timeout_seconds: int,
    max_disk_usage_percent: float,
) -> NodeSelectionCriteria:
    """把 API 入参转换为节点选择条件。"""

    return NodeSelectionCriteria(
        node_type=MediaNodeType(node_type),
        capability=capability,
        heartbeat_timeout=timedelta(seconds=heartbeat_timeout_seconds),
        max_disk_usage_percent=max_disk_usage_percent,
    )


def _candidate_response(candidate: NodeSelectionCandidate) -> NodeCandidateResponse:
    """把节点候选对象转换为 API 响应。"""

    node = candidate.node
    return NodeCandidateResponse(
        node_id=node.node_id,
        node_code=node.node_code,
        node_name=node.node_name,
        node_type=node.node_type.value,
        status=node.status.value,
        agent_url=node.agent_url,
        zlm_api_url=node.zlm_api_url,
        zlm_server_id=node.zlm_server_id,
        record_root=node.record_root,
        weight=node.weight,
        capabilities=list(node.capabilities),
        capacity=node.capacity,
        readiness=node.readiness.value,
        readiness_details=node.readiness_details,
        recording_server_id=node.recording_server_id,
        recording_server_code=node.recording_server_code,
        recording_server_status=node.recording_server_status,
        last_heartbeat_at=node.last_heartbeat_at,
        score=candidate.score,
        reason=candidate.reason,
    )


@router.get(
    "/candidates",
    response_model=list[NodeCandidateResponse],
    summary="查询可调度媒体节点候选",
    description=(
        "从 MySQL 读取 ONLINE 且心跳未过期的节点。RECORDER 还必须 READY，"
        "且所属录制服务器为 ACTIVE，再按能力和容量阈值过滤。该接口不操作 ZLMediaKit。"
    ),
)
def list_node_candidates(
    node_type: str = Query(pattern="^(RECORDER|WORKER)$"),
    capability: str | None = Query(default=None, max_length=128),
    heartbeat_timeout_seconds: int = Query(default=90, ge=1, le=3600),
    max_disk_usage_percent: float = Query(default=90, ge=1, le=100),
    service: MediaNodeSelectionService = Depends(get_node_selection_service),
) -> list[NodeCandidateResponse]:
    """返回当前可参与调度的节点候选。"""

    candidates = service.list_candidates(
        _criteria(
            node_type=node_type,
            capability=capability,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            max_disk_usage_percent=max_disk_usage_percent,
        )
    )
    return [_candidate_response(candidate) for candidate in candidates]


@router.post(
    "/select",
    response_model=NodeSelectResponse,
    summary="选择一个最佳媒体节点",
    description=(
        "基于节点心跳、依赖就绪、服务器运维状态、能力和容量快照选择最佳节点。"
        "RTC 摄像头/桌面绑定接口后续会复用该选择器。"
    ),
)
def select_node(
    request: NodeSelectRequest,
    service: MediaNodeSelectionService = Depends(get_node_selection_service),
) -> NodeSelectResponse:
    """返回当前最佳节点和完整候选列表。"""

    result = service.select_best(
        _criteria(
            node_type=request.node_type,
            capability=request.capability,
            heartbeat_timeout_seconds=request.heartbeat_timeout_seconds,
            max_disk_usage_percent=request.max_disk_usage_percent,
        )
    )
    candidates = [_candidate_response(candidate) for candidate in result.candidates]
    return NodeSelectResponse(
        selected=_candidate_response(result.selected) if result.selected else None,
        candidates=candidates,
    )


__all__ = ["list_node_candidates", "router", "select_node"]
