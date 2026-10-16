"""流绑定响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from mediafleet_sdk.models.base import Page, SDKModel, UtcDateTime


class BindingCreate(SDKModel):
    model_config = ConfigDict(extra="forbid")

    school_code: str = Field(min_length=1, max_length=64, pattern=r"^\d+$")
    resource_type: Literal["CAMERA", "DESKTOP"]
    stream_id: str = Field(min_length=1, max_length=128)
    app: str = Field(min_length=1, max_length=64)
    space_id: str | None = Field(default=None, max_length=64, pattern=r"^\d+$")
    stream_name: str | None = Field(default=None, max_length=255)


class BindingEndpoint(SDKModel):
    stream_id: str
    api_url: str
    server_id: str
    ip: str


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


__all__ = ["BindingCreate", "BindingEndpoint", "BindingPage", "BindingSummary"]
