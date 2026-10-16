"""录制命令与录制服务器响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, model_validator

from mediafleet_sdk.models.base import SDKModel, UtcDateTime


class RecordingStart(SDKModel):
    model_config = ConfigDict(extra="forbid")

    stream_id: str = Field(min_length=1, max_length=128)
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    app: str = Field(default="live", min_length=1, max_length=64)
    start_time: UtcDateTime | None = None
    end_time: UtcDateTime | None = None
    output_format: str = Field(default="mp4", min_length=1, max_length=32)
    callback_url: str | None = Field(default=None, max_length=1000)
    extra_params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_window(self) -> RecordingStart:
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError("end_time 必须晚于 start_time")
        return self


class RecordingCommandResult(SDKModel):
    accepted: bool
    command: str
    task_id: str
    target_node_id: str
    binding_id: str
    app: str
    stream_id: str
    message_id: str
    confirmed: bool


class RecordingServer(SDKModel):
    server_id: str
    server_code: str
    server_name: str
    status: str
    recorder_node_id: str
    zlm_server_id: str
    zlm_api_url: str | None = None
    play_host: str | None = None
    play_port: str | None = None
    play_protocol: str | None = None
    rtmp_port: str | None = None
    rtsp_port: str | None = None
    record_root: str | None = None
    max_recordings: int
    max_bindings: int
    occupied_bindings: int


__all__ = ["RecordingCommandResult", "RecordingServer", "RecordingStart"]
