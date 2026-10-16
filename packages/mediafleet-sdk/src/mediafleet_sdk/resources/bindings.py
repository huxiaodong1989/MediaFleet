"""流绑定只读资源。"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from mediafleet_sdk.models import BindingCreate, BindingEndpoint, BindingPage
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

    def create(self, binding: BindingCreate) -> BindingEndpoint:
        return self._client._request_model(
            "POST",
            "api/v1/rtc/stream-bindings",
            BindingEndpoint,
            json=binding.model_dump(mode="json", exclude_none=True),
        )

    def release(self, binding_id: str) -> BindingEndpoint:
        return self._client._request_model(
            "DELETE",
            f"api/v1/rtc/stream-bindings/{quote(binding_id, safe='')}",
            BindingEndpoint,
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

    async def create(self, binding: BindingCreate) -> BindingEndpoint:
        return await self._client._request_model(
            "POST",
            "api/v1/rtc/stream-bindings",
            BindingEndpoint,
            json=binding.model_dump(mode="json", exclude_none=True),
        )

    async def release(self, binding_id: str) -> BindingEndpoint:
        return await self._client._request_model(
            "DELETE",
            f"api/v1/rtc/stream-bindings/{quote(binding_id, safe='')}",
            BindingEndpoint,
        )


__all__ = ["AsyncBindingsResource", "BindingsResource"]
