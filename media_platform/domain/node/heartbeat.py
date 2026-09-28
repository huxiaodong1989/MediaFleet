"""媒体节点心跳领域对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
try:
    from enum import StrEnum
except ImportError:  # Python 3.10 compatibility for the CUDA worker image.
    from enum import Enum

    class StrEnum(str, Enum):
        """Compatibility fallback for Python versions before 3.11."""

        pass
from typing import Any


class MediaNodeType(StrEnum):
    """可参与调度或任务执行的媒体节点类型。"""

    RECORDER = "RECORDER"
    WORKER = "WORKER"
    CONTENT_ANALYSIS = "CONTENT_ANALYSIS"


class MediaNodeStatus(StrEnum):
    """调用中心记录的节点运行状态。"""

    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DRAINING = "DRAINING"
    DISABLED = "DISABLED"


class MediaNodeReadiness(StrEnum):
    """节点真实依赖是否满足接单条件。"""

    READY = "READY"
    NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class MediaNodeHeartbeat:
    """一次节点心跳上报的标准化内容。

    心跳只表达节点当前事实，不直接触发 FFmpeg、ZLMediaKit 或模型推理动作。
    动态容量先进入 `media_node.nlpz` JSON，避免首期为了监控指标扩出多张表。
    """

    node_code: str
    node_type: MediaNodeType
    status: MediaNodeStatus = MediaNodeStatus.ONLINE
    node_name: str | None = None
    server_code: str | None = None
    server_name: str | None = None
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
    reported_at: datetime | None = None
    updated_by: str = "node-heartbeat"


@dataclass(frozen=True)
class MediaNodeHeartbeatResult:
    """调用中心处理节点心跳后的结果。"""

    node_id: str
    created: bool
    status: MediaNodeStatus
    last_heartbeat_at: datetime
    recording_server_id: str | None = None


__all__ = [
    "MediaNodeHeartbeat",
    "MediaNodeHeartbeatResult",
    "MediaNodeReadiness",
    "MediaNodeStatus",
    "MediaNodeType",
]
