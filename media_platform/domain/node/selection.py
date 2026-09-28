"""媒体节点查询与调度选择领域对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from media_platform.domain.node.heartbeat import (
    MediaNodeReadiness,
    MediaNodeStatus,
    MediaNodeType,
)


@dataclass(frozen=True)
class MediaNodeView:
    """调用中心可用于调度决策的节点快照。"""

    node_id: str
    node_code: str
    node_name: str
    node_type: MediaNodeType
    status: MediaNodeStatus
    agent_url: str | None = None
    zlm_api_url: str | None = None
    zlm_server_id: str | None = None
    play_host: str | None = None
    play_port: str | None = None
    play_protocol: str | None = None
    rtmp_port: str | None = None
    rtsp_port: str | None = None
    record_root: str | None = None
    weight: int = 100
    capabilities: tuple[str, ...] = ()
    capacity: dict[str, Any] = field(default_factory=dict)
    readiness: MediaNodeReadiness = MediaNodeReadiness.NOT_READY
    readiness_details: dict[str, Any] = field(default_factory=dict)
    recording_server_id: str | None = None
    recording_server_code: str | None = None
    recording_server_status: str | None = None
    last_heartbeat_at: datetime | None = None


@dataclass(frozen=True)
class NodeSelectionCriteria:
    """节点选择条件。"""

    node_type: MediaNodeType
    capability: str | None = None
    heartbeat_timeout: timedelta = timedelta(seconds=90)
    max_disk_usage_percent: float = 90.0


@dataclass(frozen=True)
class NodeSelectionCandidate:
    """单个候选节点和调度评分。"""

    node: MediaNodeView
    score: float
    reason: str


@dataclass(frozen=True)
class NodeSelectionResult:
    """节点选择结果。"""

    selected: NodeSelectionCandidate | None
    candidates: tuple[NodeSelectionCandidate, ...]


__all__ = [
    "MediaNodeView",
    "NodeSelectionCandidate",
    "NodeSelectionCriteria",
    "NodeSelectionResult",
]
