"""媒体节点查询与调度选择应用服务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from media_platform.domain.node import (
    MediaNodeReadiness,
    MediaNodeStatus,
    MediaNodeType,
    MediaNodeView,
    NodeSelectionCandidate,
    NodeSelectionCriteria,
    NodeSelectionResult,
)
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    RecordingServerModel,
)
from media_platform.infrastructure.database.repositories import (
    MediaNodeRepository,
    RecordingServerRepository,
)


class MediaNodeSelectionService:
    """基于 `media_node` 的节点查询和最小调度选择器。"""

    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    @staticmethod
    def _to_view(
        model: MediaNodeModel,
        recording_server: RecordingServerModel | None = None,
    ) -> MediaNodeView:
        """把 ORM 模型转换为调度只读视图。"""

        capacity = dict(model.capacity_config or {})
        if recording_server is not None:
            # 绑定容量是录制服务器表中的事务事实，覆盖心跳 JSON 中可能过期的同名值。
            capacity["max_bindings"] = recording_server.max_bindings
            capacity["current_bindings"] = recording_server.occupied_bindings

        return MediaNodeView(
            node_id=model.id,
            node_code=model.node_code,
            node_name=model.node_name,
            node_type=MediaNodeType(model.node_type),
            status=MediaNodeStatus(model.status),
            agent_url=model.agent_url,
            zlm_api_url=model.zlm_api_url,
            zlm_server_id=model.zlm_server_id,
            play_host=(recording_server.play_host if recording_server else None),
            play_port=(recording_server.play_port if recording_server else None),
            play_protocol=(
                recording_server.play_protocol if recording_server else None
            ),
            rtmp_port=(recording_server.rtmp_port if recording_server else None),
            rtsp_port=(recording_server.rtsp_port if recording_server else None),
            record_root=model.record_root,
            weight=model.weight,
            capabilities=tuple(model.capabilities or ()),
            capacity=capacity,
            readiness=MediaNodeReadiness(model.readiness_status),
            readiness_details=dict(model.readiness_details or {}),
            recording_server_id=(
                recording_server.id if recording_server else None
            ),
            recording_server_code=(
                recording_server.server_code if recording_server else None
            ),
            recording_server_status=(
                recording_server.status if recording_server else None
            ),
            last_heartbeat_at=model.last_heartbeat_at,
        )

    @staticmethod
    def _number(capacity: dict[str, Any], key: str, default: float) -> float:
        """从容量 JSON 中读取数字，异常值按默认值处理。"""

        try:
            return float(capacity.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bool(capacity: dict[str, Any], key: str, default: bool) -> bool:
        """从容量 JSON 中读取布尔值，兼容字符串配置。"""

        raw_value = capacity.get(key, default)
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(raw_value)

    @staticmethod
    def _has_capability(node: MediaNodeView, capability: str | None) -> bool:
        """判断节点是否具备指定能力；未指定能力时直接通过。"""

        if not capability:
            return True
        return capability in set(node.capabilities)

    def _score_recorder(
        self,
        node: MediaNodeView,
        criteria: NodeSelectionCriteria,
    ) -> NodeSelectionCandidate | None:
        capacity = node.capacity
        current_recordings = self._number(capacity, "current_recordings", 0)
        max_recordings = max(self._number(capacity, "max_recordings", 1), 1)
        current_bindings = self._number(capacity, "current_bindings", 0)
        max_bindings = max(self._number(capacity, "max_bindings", 1), 1)
        disk_usage_percent = self._number(capacity, "disk_usage_percent", 0)
        postprocess_queue_size = self._number(capacity, "postprocess_queue_size", 0)
        postprocess_current_processing = self._number(
            capacity,
            "postprocess_current_processing",
            0,
        )
        postprocess_max_workers = max(
            self._number(capacity, "postprocess_max_workers", 1),
            1,
        )

        if current_recordings >= max_recordings:
            return None
        if current_bindings >= max_bindings:
            return None
        if disk_usage_percent >= criteria.max_disk_usage_percent:
            return None

        record_free_ratio = (max_recordings - current_recordings) / max_recordings
        binding_free_ratio = (max_bindings - current_bindings) / max_bindings
        postprocess_free_ratio = max(
            0.0,
            (postprocess_max_workers - postprocess_current_processing)
            / postprocess_max_workers,
        )
        disk_free_ratio = max(0.0, (100 - disk_usage_percent) / 100)
        queue_penalty = min(postprocess_queue_size, 100)
        score = (
            node.weight * 10
            + binding_free_ratio * 100
            + record_free_ratio * 100
            + postprocess_free_ratio * 30
            + disk_free_ratio * 20
            - queue_penalty
        )
        return NodeSelectionCandidate(
            node=node,
            score=round(score, 4),
            reason=(
                "RECORDER capacity score: "
                f"bindings={current_bindings}/{max_bindings}, "
                f"recordings={current_recordings}/{max_recordings}, "
                f"disk={disk_usage_percent}, "
                f"postprocess={postprocess_current_processing}/"
                f"{postprocess_max_workers}, queue={postprocess_queue_size}"
            ),
        )

    def _score_worker(self, node: MediaNodeView) -> NodeSelectionCandidate | None:
        capacity = node.capacity
        if not self._bool(capacity, "consumer_enabled", True):
            return None
        processing_tasks = self._number(capacity, "processing_tasks", 0)
        worker_prefetch = max(self._number(capacity, "worker_prefetch", 1), 1)
        if processing_tasks >= worker_prefetch:
            return None

        free_ratio = (worker_prefetch - processing_tasks) / worker_prefetch
        score = node.weight * 10 + free_ratio * 100
        return NodeSelectionCandidate(
            node=node,
            score=round(score, 4),
            reason=(
                "WORKER capacity score: "
                f"processing={processing_tasks}/{worker_prefetch}"
            ),
        )

    def _candidate_for(
        self,
        node: MediaNodeView,
        criteria: NodeSelectionCriteria,
    ) -> NodeSelectionCandidate | None:
        if not self._has_capability(node, criteria.capability):
            return None
        if node.node_type == MediaNodeType.RECORDER:
            return self._score_recorder(node, criteria)
        if node.node_type == MediaNodeType.WORKER:
            return self._score_worker(node)
        return None

    def list_candidates(
        self,
        criteria: NodeSelectionCriteria,
        *,
        now: datetime | None = None,
    ) -> tuple[NodeSelectionCandidate, ...]:
        """返回按分数排序的健康候选节点。"""

        now = now or datetime.now()
        heartbeat_after = now - criteria.heartbeat_timeout
        with self.session_factory() as session:
            repository = MediaNodeRepository(session)
            nodes = repository.list_by_type(
                node_type=criteria.node_type.value,
                statuses=(MediaNodeStatus.ONLINE.value,),
                heartbeat_after=heartbeat_after,
                require_ready=criteria.node_type == MediaNodeType.RECORDER,
                recording_server_statuses=(
                    ("ACTIVE",)
                    if criteria.node_type == MediaNodeType.RECORDER
                    else None
                ),
            )
            server_repository = RecordingServerRepository(session)
            views = [
                self._to_view(
                    node,
                    server_repository.get_by_recorder_node_id(node.id)
                    if node.node_type == MediaNodeType.RECORDER.value
                    else None,
                )
                for node in nodes
            ]

        candidates = [
            candidate
            for node in views
            if (candidate := self._candidate_for(node, criteria)) is not None
        ]
        candidates.sort(
            key=lambda item: (
                item.score,
                item.node.weight,
                item.node.last_heartbeat_at or datetime.min,
                item.node.node_code,
            ),
            reverse=True,
        )
        return tuple(candidates)

    def select_best(
        self,
        criteria: NodeSelectionCriteria,
        *,
        now: datetime | None = None,
    ) -> NodeSelectionResult:
        """选择一个最佳节点，并返回候选列表便于排查。"""

        candidates = self.list_candidates(criteria, now=now)
        return NodeSelectionResult(
            selected=candidates[0] if candidates else None,
            candidates=candidates,
        )


__all__ = ["MediaNodeSelectionService"]
