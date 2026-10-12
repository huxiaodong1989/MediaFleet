"""媒体节点只读资源。"""

from __future__ import annotations

from typing import Any

from mediafleet_sdk.models import NodePage
from mediafleet_sdk.resources._params import compact_params


class NodesResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    def list(self, *, page: int = 1, page_size: int = 20) -> NodePage:
        return self._client._get_model(
            "api/v1/admin/nodes/page",
            NodePage,
            params=compact_params(page=page, page_size=page_size),
        )


class AsyncNodesResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def list(self, *, page: int = 1, page_size: int = 20) -> NodePage:
        return await self._client._get_model(
            "api/v1/admin/nodes/page",
            NodePage,
            params=compact_params(page=page, page_size=page_size),
        )


__all__ = ["AsyncNodesResource", "NodesResource"]
