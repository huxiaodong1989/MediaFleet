"""媒体节点心跳应用服务。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Callable

from sqlalchemy.orm import Session

from media_platform.domain.node import (
    MediaNodeHeartbeat,
    MediaNodeHeartbeatResult,
    MediaNodeStatus,
    MediaNodeType,
)
from media_platform.infrastructure.database.repositories import (
    MediaNodeRepository,
    RecordingServerRepository,
)


LOGGER = logging.getLogger(__name__)


class MediaNodeHeartbeatConflictError(RuntimeError):
    """同一节点编号仍有健康节点在线时拒绝被另一个地址覆盖。"""


class RecordingServerIdentityConflictError(RuntimeError):
    """录制服务器、recorder-node 或 ZLMediaKit 固定身份发生冲突。"""


class MediaNodeHeartbeatService:
    """把录制节点和通用 Worker 的心跳落到 MySQL 节点表。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        identity_conflict_window: timedelta = timedelta(seconds=90),
    ):
        if identity_conflict_window <= timedelta(0):
            raise ValueError("identity_conflict_window 必须大于0")
        self.session_factory = session_factory
        self.identity_conflict_window = identity_conflict_window

    @staticmethod
    def _validate(heartbeat: MediaNodeHeartbeat) -> None:
        if not heartbeat.node_code.strip():
            raise ValueError("node_code 不能为空")
        if heartbeat.weight < 0:
            raise ValueError("weight 不能小于0")
        if heartbeat.node_type == MediaNodeType.RECORDER:
            if not (heartbeat.server_code or "").strip():
                raise ValueError("录制节点必须上报 server_code")
            if not (heartbeat.zlm_server_id or "").strip():
                raise ValueError("录制节点必须上报 zlm_server_id")
            if not (heartbeat.zlm_api_url or "").strip():
                raise ValueError("录制节点必须上报 zlm_api_url")
            if not (heartbeat.play_host or "").strip():
                raise ValueError("录制节点必须上报 play_host")
            if heartbeat.play_protocol not in {"http", "https"}:
                raise ValueError("录制节点 play_protocol 必须是http或https")
            for field_name in ("play_port", "rtmp_port", "rtsp_port"):
                value = str(getattr(heartbeat, field_name) or "").strip()
                if not value.isdigit() or not 1 <= int(value) <= 65535:
                    raise ValueError(
                        f"录制节点 {field_name} 必须是1到65535之间的端口"
                    )
            max_recordings = heartbeat.capacity.get("max_recordings")
            if not isinstance(max_recordings, int) or max_recordings < 1:
                raise ValueError("录制节点 capacity.max_recordings 必须是正整数")
            max_bindings = heartbeat.capacity.get("max_bindings", max_recordings)
            if not isinstance(max_bindings, int) or max_bindings < 1:
                raise ValueError("录制节点 capacity.max_bindings 必须是正整数")

    @staticmethod
    def _upsert_recording_server(
        *,
        repository: RecordingServerRepository,
        node,
        heartbeat: MediaNodeHeartbeat,
    ):
        """注册录制单元，并阻止 recorder 或 ZL 身份被静默换绑。"""

        server_code = str(heartbeat.server_code).strip()
        zlm_server_id = str(heartbeat.zlm_server_id).strip()
        server = repository.get_by_code(server_code)
        node_server = repository.get_by_recorder_node_id(node.id)
        zlm_server = repository.get_by_zlm_server_id(zlm_server_id)

        if server is not None and server.recorder_node_id != node.id:
            raise RecordingServerIdentityConflictError(
                "录制服务器编号已绑定其他 recorder-node: "
                f"server_code={server_code}, node_code={heartbeat.node_code}"
            )
        if node_server is not None and node_server.server_code != server_code:
            raise RecordingServerIdentityConflictError(
                "recorder-node 已绑定其他录制服务器编号: "
                f"node_code={heartbeat.node_code}, server_code={server_code}"
            )
        if zlm_server is not None and zlm_server.server_code != server_code:
            raise RecordingServerIdentityConflictError(
                "ZLMediaKit 服务标识已绑定其他录制服务器: "
                f"zlm_server_id={zlm_server_id}, server_code={server_code}"
            )
        if server is not None and server.zlm_server_id != zlm_server_id:
            raise RecordingServerIdentityConflictError(
                "录制服务器已固定关联其他 ZLMediaKit: "
                f"server_code={server_code}, zlm_server_id={zlm_server_id}"
            )

        max_recordings = int(heartbeat.capacity["max_recordings"])
        reported_max_bindings = heartbeat.capacity.get("max_bindings")
        max_bindings = (
            int(reported_max_bindings)
            if reported_max_bindings is not None
            else (server.max_bindings if server is not None else 300)
        )
        if server is None:
            return repository.create(
                server_code=server_code,
                server_name=heartbeat.server_name or server_code,
                recorder_node_id=node.id,
                zlm_server_id=zlm_server_id,
                zlm_api_url=heartbeat.zlm_api_url,
                play_host=heartbeat.play_host,
                play_port=heartbeat.play_port,
                play_protocol=heartbeat.play_protocol,
                rtmp_port=heartbeat.rtmp_port,
                rtsp_port=heartbeat.rtsp_port,
                record_root=heartbeat.record_root,
                max_recordings=max_recordings,
                max_bindings=max_bindings,
                updated_by=heartbeat.updated_by,
            )
        repository.update_runtime_metadata(
            server,
            server_name=heartbeat.server_name or server.server_name,
            zlm_api_url=heartbeat.zlm_api_url,
            play_host=heartbeat.play_host,
            play_port=heartbeat.play_port,
            play_protocol=heartbeat.play_protocol,
            rtmp_port=heartbeat.rtmp_port,
            rtsp_port=heartbeat.rtsp_port,
            record_root=heartbeat.record_root,
            max_recordings=max_recordings,
            max_bindings=max_bindings,
            updated_by=heartbeat.updated_by,
        )
        return server

    @staticmethod
    def _normalize_datetime(value: datetime | None) -> datetime:
        """数据库字段使用无时区时间；上报带时区时转换为 UTC 后去时区。"""

        if value is None:
            return datetime.now()
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def _validate_node_identity(
        self,
        *,
        existing_node,
        heartbeat: MediaNodeHeartbeat,
        heartbeat_at: datetime,
    ) -> None:
        """防止健康节点编号被另一个节点地址误复用。

        节点编号是调度和任务执行权的稳定身份。相同 `node_code`
        且 `agent_url` 相同，视为同一节点重启或重复心跳；如果旧节点仍在健康
        窗口内但新心跳来自不同 `agent_url`，说明部署配置冲突，应让新节点修改
        自己的节点编号，而不是覆盖原节点事实。
        """

        if existing_node is None:
            return
        if not existing_node.agent_url or not heartbeat.agent_url:
            return
        if existing_node.agent_url.strip() == heartbeat.agent_url.strip():
            return
        if existing_node.status not in {
            MediaNodeStatus.ONLINE.value,
            MediaNodeStatus.DRAINING.value,
        }:
            return
        last_heartbeat_at = existing_node.last_heartbeat_at
        if last_heartbeat_at is None:
            return
        healthy_after = heartbeat_at - self.identity_conflict_window
        if last_heartbeat_at >= healthy_after:
            raise MediaNodeHeartbeatConflictError(
                "节点编号已被其他健康节点使用: "
                f"node_code={heartbeat.node_code}, "
                f"existing_agent_url={existing_node.agent_url}, "
                f"current_agent_url={heartbeat.agent_url}"
            )

    def report(self, heartbeat: MediaNodeHeartbeat) -> MediaNodeHeartbeatResult:
        """处理一次节点心跳并返回节点主键。

        节点以 `node_code` 作为稳定身份。多实例调用中心收到同一节点
        心跳时都会写同一行，后续调度只读取 MySQL 中的节点状态。
        """

        self._validate(heartbeat)
        heartbeat_at = self._normalize_datetime(heartbeat.reported_at)
        with self.session_factory() as session:
            with session.begin():
                repository = MediaNodeRepository(session)
                existing_node = repository.get_by_code(heartbeat.node_code)
                self._validate_node_identity(
                    existing_node=existing_node,
                    heartbeat=heartbeat,
                    heartbeat_at=heartbeat_at,
                )
                node, created = repository.upsert_heartbeat(
                    heartbeat,
                    heartbeat_at=heartbeat_at,
                )
                recording_server = None
                if heartbeat.node_type == MediaNodeType.RECORDER:
                    recording_server = self._upsert_recording_server(
                        repository=RecordingServerRepository(session),
                        node=node,
                        heartbeat=heartbeat,
                    )
                result = MediaNodeHeartbeatResult(
                    node_id=node.id,
                    created=created,
                    status=MediaNodeStatus(node.status),
                    last_heartbeat_at=node.last_heartbeat_at or heartbeat_at,
                    recording_server_id=(
                        recording_server.id if recording_server is not None else None
                    ),
                )
        LOGGER.info(
            "媒体节点心跳已写入MySQL: node_id=%s, node_code=%s, node_type=%s, "
            "status=%s, readiness=%s, recording_server_id=%s, created=%s",
            result.node_id,
            heartbeat.node_code,
            heartbeat.node_type.value,
            result.status.value,
            heartbeat.readiness.value,
            result.recording_server_id,
            result.created,
        )
        return result

    def mark_stale_offline(
        self,
        *,
        heartbeat_timeout: timedelta,
        now: datetime | None = None,
    ) -> int:
        """把超过心跳超时时间的节点标记为离线。

        节点在线状态由调用中心根据 `last_heartbeat_at` 判断。节点能持续心跳即
        ``ONLINE``；停止心跳超过超时时间后由调用中心写入 ``OFFLINE``。节点再次
        启动并上报心跳时会通过 `report()` 自动恢复为 ``ONLINE``。
        """

        if heartbeat_timeout <= timedelta(0):
            raise ValueError("heartbeat_timeout 必须大于0")

        heartbeat_before = (now or datetime.now()) - heartbeat_timeout
        with self.session_factory() as session:
            with session.begin():
                updated_count = MediaNodeRepository(session).mark_stale_offline(
                    heartbeat_before=heartbeat_before,
                )
        if updated_count:
            LOGGER.warning(
                "已将心跳超时媒体节点标记为离线: count=%s, heartbeat_before=%s",
                updated_count,
                heartbeat_before,
            )
        return updated_count


__all__ = [
    "MediaNodeHeartbeatConflictError",
    "MediaNodeHeartbeatService",
    "RecordingServerIdentityConflictError",
]
