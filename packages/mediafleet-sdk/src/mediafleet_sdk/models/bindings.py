"""流绑定响应模型。"""

from __future__ import annotations

from mediafleet_sdk.models.base import Page, SDKModel, UtcDateTime


class BindingSummary(SDKModel):
    binding_id: str
    school_code: str
    resource_type: str
    space_id: str | None
    node_id: str
    node_code: str | None
    app: str
    stream_id: str
    stream_name: str | None
    stream_mode: str
    status: str
    version: int
    last_active_at: UtcDateTime | None


class BindingPage(Page[BindingSummary]):
    pass


__all__ = ["BindingPage", "BindingSummary"]
