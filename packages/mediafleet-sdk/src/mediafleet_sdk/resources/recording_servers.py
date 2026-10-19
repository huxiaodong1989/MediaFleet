"""录制服务器只读资源。"""

from __future__ import annotations

from typing import Any

from mediafleet_sdk.models import RecordingServer


class RecordingServersResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    def list(self) -> list[RecordingServer]:
        return self._client._get_model(
            "api/v1/recording-servers", list[RecordingServer]
        )


class AsyncRecordingServersResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def list(self) -> list[RecordingServer]:
        return await self._client._get_model(
            "api/v1/recording-servers", list[RecordingServer]
        )


__all__ = ["AsyncRecordingServersResource", "RecordingServersResource"]
