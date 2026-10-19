"""运维总览只读资源。"""

from __future__ import annotations

from typing import Any

from mediafleet_sdk.models import AdminOverview


class AdminResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    def overview(self) -> AdminOverview:
        return self._client._get_model("api/v1/admin/overview", AdminOverview)


class AsyncAdminResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def overview(self) -> AdminOverview:
        return await self._client._get_model("api/v1/admin/overview", AdminOverview)


__all__ = ["AdminResource", "AsyncAdminResource"]
