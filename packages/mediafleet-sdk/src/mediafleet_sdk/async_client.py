"""异步 MediaFleet SDK 客户端。"""

from __future__ import annotations

import asyncio
from os import PathLike
from typing import Any

import httpx

from mediafleet_sdk._http import (
    RETRYABLE_STATUS_CODES,
    api_error,
    parse_response,
    retry_delay,
    should_retry,
)
from mediafleet_sdk.config import ClientConfig, RetryPolicy
from mediafleet_sdk.errors import TimeoutError, TransportError
from mediafleet_sdk.resources import (
    AsyncAdminResource,
    AsyncBindingsResource,
    AsyncNodesResource,
    AsyncRecordingServersResource,
    AsyncRecordingsResource,
    AsyncSecurityResource,
    AsyncSystemResource,
    AsyncTasksResource,
)


class AsyncMediaFleetClient:
    """通过控制中心 REST API 异步查询和管理 MediaFleet。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float | httpx.Timeout = 30.0,
        verify: bool | str | PathLike[str] = True,
        user_agent: str = "mediafleet-sdk/0.1.0",
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = ClientConfig.create(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            verify=verify,
            user_agent=user_agent,
            retry=retry,
        )
        self._http = httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout,
            verify=self.config.verify,
            transport=transport,
            headers={"User-Agent": self.config.user_agent},
        )
        self.system = AsyncSystemResource(self)
        self.admin = AsyncAdminResource(self)
        self.tasks = AsyncTasksResource(self)
        self.nodes = AsyncNodesResource(self)
        self.bindings = AsyncBindingsResource(self)
        self.recording_servers = AsyncRecordingServersResource(self)
        self.recordings = AsyncRecordingsResource(self)
        self.security = AsyncSecurityResource(self)

    @property
    def is_closed(self) -> bool:
        return self._http.is_closed

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncMediaFleetClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()

    async def _get_model(
        self,
        path: str,
        model: Any,
        *,
        params: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> Any:
        return await self._request_model(
            "GET", path, model, params=params, auth=auth
        )

    async def _request_model(
        self,
        method: str,
        path: str,
        model: Any,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        extra_headers: dict[str, str] | None = None,
        auth: bool = True,
    ) -> Any:
        normalized_method = method.upper()
        headers = {"X-API-Key": self.config.api_key} if auth else {}
        if extra_headers:
            headers.update(extra_headers)
        safe_request_path = "/" + path.lstrip("/").split("?", 1)[0]
        for attempt in range(1, self.config.retry.max_attempts + 1):
            failure: str | None = None
            try:
                response = await self._http.request(
                    normalized_method,
                    path.lstrip("/"),
                    params=params,
                    json=json,
                    headers=headers,
                )
            except httpx.TimeoutException:
                if should_retry(normalized_method, attempt, self.config.retry):
                    await asyncio.sleep(
                        retry_delay(attempt=attempt, policy=self.config.retry)
                    )
                    continue
                failure = "timeout"
            except httpx.RequestError:
                if should_retry(normalized_method, attempt, self.config.retry):
                    await asyncio.sleep(
                        retry_delay(attempt=attempt, policy=self.config.retry)
                    )
                    continue
                failure = "transport"

            if failure == "timeout":
                raise TimeoutError(safe_request_path)
            if failure == "transport":
                raise TransportError(safe_request_path)

            if response.status_code in RETRYABLE_STATUS_CODES and should_retry(
                normalized_method, attempt, self.config.retry
            ):
                await asyncio.sleep(
                    retry_delay(
                        attempt=attempt,
                        policy=self.config.retry,
                        response=response,
                    )
                )
                continue
            if response.is_error:
                raise api_error(response, self.config.api_key)
            return parse_response(response, model, self.config.api_key)

        raise TransportError(safe_request_path)


__all__ = ["AsyncMediaFleetClient"]
