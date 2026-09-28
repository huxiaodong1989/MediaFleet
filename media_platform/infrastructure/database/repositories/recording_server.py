"""媒体录制服务器表仓储。"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from media_platform.infrastructure.database.models import RecordingServerModel


class RecordingServerRepository:
    """维护录制单元稳定身份和 recorder/ZL 固定归属。"""

    def __init__(self, session: Session):
        self.session = session

    def get_by_code(self, server_code: str) -> RecordingServerModel | None:
        return self.session.scalar(
            select(RecordingServerModel).where(
                RecordingServerModel.server_code == server_code
            )
        )

    def get_by_recorder_node_id(
        self, recorder_node_id: str
    ) -> RecordingServerModel | None:
        return self.session.scalar(
            select(RecordingServerModel).where(
                RecordingServerModel.recorder_node_id == recorder_node_id
            )
        )

    def get_by_zlm_server_id(
        self, zlm_server_id: str
    ) -> RecordingServerModel | None:
        return self.session.scalar(
            select(RecordingServerModel).where(
                RecordingServerModel.zlm_server_id == zlm_server_id
            )
        )

    def list_all(self) -> list[RecordingServerModel]:
        """按稳定编号返回全部录制服务器。"""

        return list(
            self.session.scalars(
                select(RecordingServerModel).order_by(
                    RecordingServerModel.server_code.asc()
                )
            )
        )

    def update_status(
        self,
        server: RecordingServerModel,
        *,
        status: str,
        updated_by: str,
    ) -> None:
        """更新运维意图；节点心跳不会调用本方法。"""

        server.status = status
        server.updated_by = updated_by
        self.session.flush()

    def create(
        self,
        *,
        server_code: str,
        server_name: str,
        recorder_node_id: str,
        zlm_server_id: str,
        zlm_api_url: str | None,
        play_host: str | None,
        play_port: str | None,
        play_protocol: str | None,
        rtmp_port: str | None,
        rtsp_port: str | None,
        record_root: str | None,
        max_recordings: int,
        max_bindings: int,
        updated_by: str,
    ) -> RecordingServerModel:
        server = RecordingServerModel(
            id=str(uuid4()),
            server_code=server_code,
            server_name=server_name,
            recorder_node_id=recorder_node_id,
            zlm_server_id=zlm_server_id,
            zlm_api_url=zlm_api_url,
            play_host=play_host,
            play_port=play_port,
            play_protocol=play_protocol,
            rtmp_port=rtmp_port,
            rtsp_port=rtsp_port,
            record_root=record_root,
            max_recordings=max_recordings,
            max_bindings=max_bindings,
            occupied_bindings=0,
            created_by=updated_by,
            updated_by=updated_by,
        )
        self.session.add(server)
        self.session.flush()
        return server

    def update_runtime_metadata(
        self,
        server: RecordingServerModel,
        *,
        server_name: str,
        zlm_api_url: str | None,
        play_host: str | None,
        play_port: str | None,
        play_protocol: str | None,
        rtmp_port: str | None,
        rtsp_port: str | None,
        record_root: str | None,
        max_recordings: int,
        max_bindings: int,
        updated_by: str,
    ) -> None:
        """更新地址和容量，不改变服务器运维状态与固定身份。"""

        server.server_name = server_name
        server.zlm_api_url = zlm_api_url
        server.play_host = play_host
        server.play_port = play_port
        server.play_protocol = play_protocol
        server.rtmp_port = rtmp_port
        server.rtsp_port = rtsp_port
        server.record_root = record_root
        server.max_recordings = max_recordings
        server.max_bindings = max_bindings
        server.updated_by = updated_by
        self.session.flush()

    def try_reserve_binding_slot(
        self,
        *,
        recorder_node_id: str,
        updated_by: str,
    ) -> bool:
        """原子占用一个流绑定名额。

        条件更新由数据库保证并发正确性：多个调用中心即使同时看到最后一个名额，
        也只有一个事务能把 ``yzbds`` 加一。服务器非 ACTIVE 或容量已满时返回 False。
        """

        result = self.session.execute(
            update(RecordingServerModel)
            .where(
                RecordingServerModel.recorder_node_id == recorder_node_id,
                RecordingServerModel.status == "ACTIVE",
                RecordingServerModel.occupied_bindings
                < RecordingServerModel.max_bindings,
            )
            .values(
                occupied_bindings=RecordingServerModel.occupied_bindings + 1,
                updated_by=updated_by,
            )
        )
        return result.rowcount == 1

    def release_binding_slot(
        self,
        *,
        recorder_node_id: str,
        updated_by: str,
    ) -> bool:
        """原子释放一个已占流绑定名额，计数不会减到零以下。"""

        result = self.session.execute(
            update(RecordingServerModel)
            .where(
                RecordingServerModel.recorder_node_id == recorder_node_id,
                RecordingServerModel.occupied_bindings > 0,
            )
            .values(
                occupied_bindings=RecordingServerModel.occupied_bindings - 1,
                updated_by=updated_by,
            )
        )
        return result.rowcount == 1


__all__ = ["RecordingServerRepository"]
