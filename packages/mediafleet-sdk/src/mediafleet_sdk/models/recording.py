"""录制服务器响应模型。"""

from __future__ import annotations

from mediafleet_sdk.models.base import SDKModel


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


__all__ = ["RecordingServer"]
