"""录制开始与停止命令资源。"""

from __future__ import annotations

from typing import Any

from mediafleet_sdk.models import RecordingCommandResult, RecordingStart


class RecordingsResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    def start(self, request: RecordingStart) -> RecordingCommandResult:
        return self._client._request_model(
            "POST",
            "api/v1/recordings/start",
            RecordingCommandResult,
            json=request.model_dump(mode="json", exclude_none=True),
        )

    def stop(self, task_id: str) -> RecordingCommandResult:
        return self._client._request_model(
            "POST",
            "api/v1/recordings/stop",
            RecordingCommandResult,
            json={"task_id": task_id},
        )


class AsyncRecordingsResource:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def start(self, request: RecordingStart) -> RecordingCommandResult:
        return await self._client._request_model(
            "POST",
            "api/v1/recordings/start",
            RecordingCommandResult,
            json=request.model_dump(mode="json", exclude_none=True),
        )

    async def stop(self, task_id: str) -> RecordingCommandResult:
        return await self._client._request_model(
            "POST",
            "api/v1/recordings/stop",
            RecordingCommandResult,
            json={"task_id": task_id},
        )


__all__ = ["AsyncRecordingsResource", "RecordingsResource"]
