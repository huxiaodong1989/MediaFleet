"""流绑定只读资源。"""

from __future__ import annotations

from typing import Any

from mediafleet_sdk.models import BindingPage
from mediafleet_sdk.resources._params import compact_params


class BindingsResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        node_id: str | None = None,
        status: str | None = None,
        resource_type: str | None = None,
        space_id: str | None = None,
    ) -> BindingPage:
        return self._client._get_model(
            "api/v1/admin/bindings/page",
            BindingPage,
            params=compact_params(
                page=page,
                page_size=page_size,
                node_id=node_id,
                status=status,
                resource_type=resource_type,
                space_id=space_id,
            ),
        )


class AsyncBindingsResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        node_id: str | None = None,
        status: str | None = None,
        resource_type: str | None = None,
        space_id: str | None = None,
    ) -> BindingPage:
        return await self._client._get_model(
            "api/v1/admin/bindings/page",
            BindingPage,
            params=compact_params(
                page=page,
                page_size=page_size,
                node_id=node_id,
                status=status,
                resource_type=resource_type,
                space_id=space_id,
            ),
        )


__all__ = ["AsyncBindingsResource", "BindingsResource"]
