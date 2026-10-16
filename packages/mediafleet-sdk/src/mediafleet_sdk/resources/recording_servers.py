"""录制服务器只读资源。"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from mediafleet_sdk.models import RecordingServer


class RecordingServersResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    def list(self) -> list[RecordingServer]:
        return self._client._get_model(
            "api/v1/recording-servers", list[RecordingServer]
        )

    def update_status(self, server_code: str, status: str) -> RecordingServer:
        return self._client._request_model(
            "PATCH",
            f"api/v1/recording-servers/{quote(server_code, safe='')}/status",
            RecordingServer,
            json={"status": status},
        )


class AsyncRecordingServersResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def list(self) -> list[RecordingServer]:
        return await self._client._get_model(
            "api/v1/recording-servers", list[RecordingServer]
        )

    async def update_status(self, server_code: str, status: str) -> RecordingServer:
        return await self._client._request_model(
            "PATCH",
            f"api/v1/recording-servers/{quote(server_code, safe='')}/status",
            RecordingServer,
            json={"status": status},
        )


__all__ = ["AsyncRecordingServersResource", "RecordingServersResource"]
