"""调用中心录制服务器管理服务。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
try:
    from enum import StrEnum
except ImportError:  # Python 3.10 compatibility for the CUDA worker image.
    from enum import Enum

    class StrEnum(str, Enum):
        """Compatibility fallback for Python versions before 3.11."""

        pass

from sqlalchemy.orm import Session

from media_platform.infrastructure.database.repositories import (
    RecordingServerRepository,
)


class RecordingServerStatus(StrEnum):
    """录制服务器运维状态。"""

    ACTIVE = "ACTIVE"
    DRAINING = "DRAINING"
    MAINTENANCE = "MAINTENANCE"
    DISABLED = "DISABLED"


class RecordingServerNotFoundError(LookupError):
    """指定录制服务器不存在。"""


@dataclass(frozen=True)
class RecordingServerView:
    """录制服务器管理只读视图。"""

    server_id: str
    server_code: str
    server_name: str
    status: RecordingServerStatus
    recorder_node_id: str
    zlm_server_id: str
    zlm_api_url: str | None
    play_host: str | None
    play_port: str | None
    play_protocol: str | None
    rtmp_port: str | None
    rtsp_port: str | None
    record_root: str | None
    max_recordings: int
    max_bindings: int
    occupied_bindings: int


class RecordingServerService:
    """管理整机排空、维护和停用意图，不执行本机媒体操作。"""

    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    @staticmethod
    def _to_view(model) -> RecordingServerView:
        return RecordingServerView(
            server_id=model.id,
            server_code=model.server_code,
            server_name=model.server_name,
            status=RecordingServerStatus(model.status),
            recorder_node_id=model.recorder_node_id,
            zlm_server_id=model.zlm_server_id,
            zlm_api_url=model.zlm_api_url,
            play_host=model.play_host,
            play_port=model.play_port,
            play_protocol=model.play_protocol,
            rtmp_port=model.rtmp_port,
            rtsp_port=model.rtsp_port,
            record_root=model.record_root,
            max_recordings=model.max_recordings,
            max_bindings=model.max_bindings,
            occupied_bindings=model.occupied_bindings,
        )

    def list_servers(self) -> tuple[RecordingServerView, ...]:
        with self.session_factory() as session:
            rows = RecordingServerRepository(session).list_all()
            return tuple(self._to_view(row) for row in rows)

    def update_status(
        self,
        *,
        server_code: str,
        status: RecordingServerStatus,
        updated_by: str,
    ) -> RecordingServerView:
        with self.session_factory() as session:
            with session.begin():
                repository = RecordingServerRepository(session)
                server = repository.get_by_code(server_code)
                if server is None:
                    raise RecordingServerNotFoundError(
                        f"录制服务器不存在: server_code={server_code}"
                    )
                repository.update_status(
                    server,
                    status=status.value,
                    updated_by=updated_by,
                )
                return self._to_view(server)


__all__ = [
    "RecordingServerNotFoundError",
    "RecordingServerService",
    "RecordingServerStatus",
    "RecordingServerView",
]
