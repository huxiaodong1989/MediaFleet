"""系统只读资源。"""

from __future__ import annotations

from typing import Any

from mediafleet_sdk.models import ServiceHealth, ServiceInfo


class SystemResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    def health(self) -> ServiceHealth:
        return self._client._get_model("health", ServiceHealth, auth=False)

    def service_info(self) -> ServiceInfo:
        return self._client._get_model(
            "api/v1/service-info", ServiceInfo, auth=False
        )


class AsyncSystemResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def health(self) -> ServiceHealth:
        return await self._client._get_model("health", ServiceHealth, auth=False)

    async def service_info(self) -> ServiceInfo:
        return await self._client._get_model(
            "api/v1/service-info", ServiceInfo, auth=False
        )


__all__ = ["AsyncSystemResource", "SystemResource"]
