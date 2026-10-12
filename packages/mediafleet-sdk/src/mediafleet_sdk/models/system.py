"""系统信息响应模型。"""

from __future__ import annotations

from mediafleet_sdk.models.base import SDKModel, UtcDateTime


class ServiceHealth(SDKModel):
    service: str
    role: str
    version: str
    status: str
    checked_at: UtcDateTime


class ServiceInfo(SDKModel):
    name: str
    role: str
    version: str
    description: str
    compatibility_entrypoint: str | None = None


__all__ = ["ServiceHealth", "ServiceInfo"]
