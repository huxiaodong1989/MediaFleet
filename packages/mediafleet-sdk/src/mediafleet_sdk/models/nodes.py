"""媒体节点响应模型。"""

from __future__ import annotations

from typing import Any

from mediafleet_sdk.models.base import Page, SDKModel, UtcDateTime


class NodeSummary(SDKModel):
    node_id: str
    node_code: str
    node_name: str
    node_type: str
    status: str
    readiness_status: str
    agent_url: str | None
    zlm_api_url: str | None
    capabilities: list[str]
    capacity: dict[str, Any]
    readiness_details: dict[str, Any]
    last_heartbeat_at: UtcDateTime | None
    active_bindings: int
    active_tasks: int
    recording_server_code: str | None
    recording_server_status: str | None


class NodePage(Page[NodeSummary]):
    pass


__all__ = ["NodePage", "NodeSummary"]
