"""国标媒体节点表仓储。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from media_platform.domain.node import MediaNodeHeartbeat
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    RecordingServerModel,
)


class MediaNodeRepository:
    """只读写 `media_node`，作为节点身份、能力和容量事实来源。"""

    def __init__(self, session: Session):
        self.session = session

    def get(self, node_id: str) -> MediaNodeModel | None:
        """按主键查询媒体节点。"""

        return self.session.get(MediaNodeModel, node_id)

    def get_by_code(self, node_code: str) -> MediaNodeModel | None:
        """按节点编号查询唯一媒体节点。"""

        return self.session.scalar(
            select(MediaNodeModel).where(
                MediaNodeModel.node_code == node_code,
            )
        )

    def list_by_type(
        self,
        *,
        node_type: str,
        statuses: tuple[str, ...] = ("ONLINE",),
        heartbeat_after: datetime | None = None,
        require_ready: bool = False,
        recording_server_statuses: tuple[str, ...] | None = None,
    ) -> list[MediaNodeModel]:
        """按节点类型查询调度候选。

        JSON 容量判断保留在应用服务中，仓储只做数据库可高效处理的基础过滤。
        """

        statement = select(MediaNodeModel).where(
            MediaNodeModel.node_type == node_type,
            MediaNodeModel.status.in_(statuses),
        )
        if require_ready:
            statement = statement.where(
                MediaNodeModel.readiness_status == "READY"
            )
        if recording_server_statuses is not None:
            statement = statement.join(
                RecordingServerModel,
                RecordingServerModel.recorder_node_id == MediaNodeModel.id,
            ).where(RecordingServerModel.status.in_(recording_server_statuses))
        if heartbeat_after is not None:
            statement = statement.where(
                MediaNodeModel.last_heartbeat_at.is_not(None),
                MediaNodeModel.last_heartbeat_at >= heartbeat_after,
            )
        statement = statement.order_by(
            MediaNodeModel.weight.desc(),
            MediaNodeModel.last_heartbeat_at.desc(),
            MediaNodeModel.node_code.asc(),
        )
        return list(self.session.scalars(statement))

    def upsert_heartbeat(
        self,
        heartbeat: MediaNodeHeartbeat,
        *,
        heartbeat_at: datetime,
    ) -> tuple[MediaNodeModel, bool]:
        """新增或更新节点心跳。

        Returns:
            `(node, created)`，`created=True` 表示本次心跳首次注册该节点。
        """

        node = self.get_by_code(heartbeat.node_code)
        created = node is None
        if node is None:
            node = MediaNodeModel(
                id=str(uuid4()),
                node_code=heartbeat.node_code,
                created_by=heartbeat.updated_by,
            )
            self.session.add(node)

        node.node_name = heartbeat.node_name or heartbeat.node_code
        node.node_type = heartbeat.node_type.value
        node.status = heartbeat.status.value
        node.agent_url = heartbeat.agent_url
        node.zlm_api_url = heartbeat.zlm_api_url
        node.zlm_server_id = heartbeat.zlm_server_id
        node.record_root = heartbeat.record_root
        node.weight = heartbeat.weight
        node.capabilities = list(heartbeat.capabilities)
        node.capacity_config = dict(heartbeat.capacity)
        node.readiness_status = heartbeat.readiness.value
        node.readiness_details = dict(heartbeat.readiness_details)
        node.last_heartbeat_at = heartbeat_at
        node.updated_by = heartbeat.updated_by
        self.session.flush()
        return node, created

    def mark_stale_offline(
        self,
        *,
        heartbeat_before: datetime,
        updated_by: str = "control-center-health-check",
    ) -> int:
        """将心跳超时的节点标记为离线。

        节点进程停止后不会再主动上报 ``OFFLINE``。调用中心必须依据最后心跳时间
        维护数据库事实状态，避免运维查看 `media_node` 时仍看到已经停掉的节点为
        ``ONLINE``。
        """

        result = self.session.execute(
            update(MediaNodeModel)
            .where(
                MediaNodeModel.status.in_(("ONLINE", "DRAINING")),
                MediaNodeModel.last_heartbeat_at.is_not(None),
                MediaNodeModel.last_heartbeat_at < heartbeat_before,
            )
            .values(
                status="OFFLINE",
                updated_by=updated_by,
            )
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)


__all__ = ["MediaNodeRepository"]
