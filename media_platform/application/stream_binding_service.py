"""RTC 媒体流绑定应用服务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from media_platform.application.media_node_selection_service import (
    MediaNodeSelectionService,
)
from media_platform.domain.node import (
    MediaNodeType,
    MediaNodeView,
    NodeSelectionCandidate,
    NodeSelectionCriteria,
)
from media_platform.domain.stream import (
    MediaStreamBindingCommand,
    MediaStreamBindingResult,
    StreamBindingConflictError,
    StreamBindingNodeUnavailableError,
    StreamBindingStatus,
    StreamMode,
    StreamResourceType,
)
from media_platform.infrastructure.database.models import MediaStreamBindingModel
from media_platform.infrastructure.database.repositories import (
    MediaNodeRepository,
    MediaStreamBindingRepository,
    RecordingServerRepository,
)


class _BindingReservationRace(RuntimeError):
    """同一历史绑定被其他调用中心并发重新启用。"""


class MediaStreamBindingService:
    """为 RTC 创建或复用摄像头、桌面流与录制节点的绑定。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        heartbeat_timeout_seconds: float = 60.0,
        max_disk_usage_percent: float = 90.0,
    ):
        self.session_factory = session_factory
        self.node_selection_service = MediaNodeSelectionService(session_factory)
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.max_disk_usage_percent = max_disk_usage_percent

    @staticmethod
    def _internal_resource_key(*, app: str, stream_id: str) -> str:
        """生成数据库旧 ``zybh`` 字段使用的稳定内部兼容键。

        RTC 公开契约以 ``app + stream_id`` 唯一定位技术流，不再要求业务方额外
        提供没有调度语义的 ``resource_id``。现阶段不做表结构迁移，因此用固定
        64 位摘要填充历史非空字段；该值不返回给 RTC，也不参与业务判断。
        """

        return sha256(f"{app}\0{stream_id}".encode("utf-8")).hexdigest()

    @staticmethod
    def _default_mode(resource_type: StreamResourceType) -> StreamMode:
        """按资源类型推导默认流接入方式。"""

        if resource_type == StreamResourceType.CAMERA:
            return StreamMode.PULL
        return StreamMode.PUSH

    def _to_result(
        self,
        binding: MediaStreamBindingModel,
        *,
        created: bool,
        session: Session,
        affinity_matched: bool = False,
        allocation_reason: str = "EXISTING_BINDING",
    ) -> MediaStreamBindingResult:
        """把绑定 ORM 转换为应用层结果，并补齐节点地址信息。"""

        node_model = MediaNodeRepository(session).get(binding.node_id)
        recording_server = RecordingServerRepository(
            session
        ).get_by_recorder_node_id(binding.node_id)
        node = (
            MediaNodeSelectionService._to_view(node_model, recording_server)
            if node_model is not None
            else None
        )
        return MediaStreamBindingResult(
            binding_id=binding.id,
            created=created,
            school_code=binding.school_code,
            resource_type=StreamResourceType(binding.resource_type),
            space_id=binding.space_id,
            node_id=binding.node_id,
            node=node,
            app=binding.app,
            stream_id=binding.stream_id,
            stream_name=binding.stream_name,
            stream_mode=StreamMode(binding.stream_mode),
            status=StreamBindingStatus(binding.status),
            binding_version=int(binding.version or 0),
            affinity_matched=affinity_matched,
            allocation_reason=allocation_reason,
        )

    @staticmethod
    def _assert_existing_matches_request(
        binding: MediaStreamBindingModel,
        command: MediaStreamBindingCommand,
    ) -> None:
        """校验同一技术流重复请求的业务归属保持一致。"""

        if binding.school_code != command.school_code:
            raise StreamBindingConflictError(
                "app + stream_id 已属于其他学校码，不能覆盖原绑定"
            )
        if binding.resource_type != command.resource_type.value:
            raise StreamBindingConflictError(
                "app + stream_id 已属于其他资源类型，不能覆盖原绑定"
            )
        if (
            command.space_id
            and binding.space_id
            and binding.space_id != command.space_id
        ):
            raise StreamBindingConflictError(
                "app + stream_id 已属于其他空间，不能覆盖原绑定"
            )

    def _selection_criteria(self) -> NodeSelectionCriteria:
        """构造录制节点选择条件。"""

        return NodeSelectionCriteria(
            node_type=MediaNodeType.RECORDER,
            capability="record.start",
            heartbeat_timeout=timedelta(seconds=self.heartbeat_timeout_seconds),
            max_disk_usage_percent=self.max_disk_usage_percent,
        )

    @staticmethod
    def _candidate_by_node_id(
        candidates: tuple[NodeSelectionCandidate, ...],
        node_id: str,
    ) -> NodeSelectionCandidate | None:
        """从健康候选中按节点主键查找候选。"""

        for candidate in candidates:
            if candidate.node.node_id == node_id:
                return candidate
        return None

    def _select_space_affinity_candidate(
        self,
        *,
        command: MediaStreamBindingCommand,
        candidates: tuple[NodeSelectionCandidate, ...],
    ) -> NodeSelectionCandidate | None:
        """优先选择同空间已有健康绑定节点。"""

        if not command.space_id:
            return None
        with self.session_factory() as session:
            bindings = MediaStreamBindingRepository(session).list_active_by_space(
                school_code=command.school_code,
                space_id=command.space_id,
            )
        for binding in bindings:
            candidate = self._candidate_by_node_id(candidates, binding.node_id)
            if candidate is not None:
                return candidate
        return None

    def _ordered_recorder_candidates(
        self,
        command: MediaStreamBindingCommand,
    ) -> tuple[tuple[MediaNodeView, bool, str], ...]:
        """按亲和与容量评分返回健康录制节点尝试顺序。

        有空间编号时，优先让同一空间的摄像头和桌面落在同一个 ZL/录制节点；
        但被复用的节点必须仍在健康候选集合中。事务占位失败时继续尝试后续候选，
        处理多个调用中心同时争抢同一录制单元最后名额的并发情况。
        """

        candidates = self.node_selection_service.list_candidates(
            self._selection_criteria()
        )
        if not candidates:
            return ()
        affinity_candidate = self._select_space_affinity_candidate(
            command=command,
            candidates=candidates,
        )
        ordered: list[tuple[MediaNodeView, bool, str]] = []
        if affinity_candidate is not None:
            ordered.append((affinity_candidate.node, True, "SPACE_AFFINITY"))
        ordered.extend(
            (candidate.node, False, "BEST_CAPACITY")
            for candidate in candidates
            if affinity_candidate is None
            or candidate.node.node_id != affinity_candidate.node.node_id
        )
        return tuple(ordered)

    def _get_reused_binding(
        self,
        command: MediaStreamBindingCommand,
        *,
        now: datetime,
    ) -> MediaStreamBindingResult | None:
        """在独立短事务中复查并复用已有有效绑定。"""

        with self.session_factory() as session:
            repository = MediaStreamBindingRepository(session)
            binding = repository.get_by_app_stream(
                app=command.app,
                stream_id=command.stream_id,
            )
            if binding is None or binding.status != StreamBindingStatus.ACTIVE.value:
                return None
            self._assert_existing_matches_request(binding, command)
            if binding.space_id is None and command.space_id:
                binding.space_id = command.space_id
            if command.stream_name:
                binding.stream_name = command.stream_name
            binding.last_active_at = now
            binding.updated_by = command.created_by
            session.commit()
            return self._to_result(binding, created=False, session=session)

    @staticmethod
    def _new_binding(
        command: MediaStreamBindingCommand,
        *,
        node_id: str,
        stream_id: str,
        stream_mode: StreamMode,
        now: datetime,
    ) -> MediaStreamBindingModel:
        """构造一条尚未持久化的新绑定记录。"""

        return MediaStreamBindingModel(
            id=str(uuid4()),
            school_code=command.school_code,
            resource_type=command.resource_type.value,
            resource_id=MediaStreamBindingService._internal_resource_key(
                app=command.app,
                stream_id=stream_id,
            ),
            space_id=command.space_id,
            node_id=node_id,
            app=command.app,
            stream_id=stream_id,
            stream_name=command.stream_name,
            stream_mode=stream_mode.value,
            status=StreamBindingStatus.ACTIVE.value,
            version=0,
            source_url_ciphertext=None,
            last_active_at=now,
            created_by=command.created_by,
            updated_by=command.created_by,
        )

    def _bind_on_candidate(
        self,
        command: MediaStreamBindingCommand,
        *,
        selected_node: MediaNodeView,
        stream_id: str,
        stream_mode: StreamMode,
        now: datetime,
        affinity_matched: bool,
        allocation_reason: str,
    ) -> MediaStreamBindingResult | None:
        """在一个短事务中复查绑定、占用容量并完成持久化。

        返回 ``None`` 表示该候选在事务执行时已无可用绑定名额，调用方应继续尝试
        下一个候选。任何异常都会回滚容量占位和绑定写入，避免计数与事实表分离。
        """

        with self.session_factory() as session:
            with session.begin():
                repository = MediaStreamBindingRepository(session)
                existing_binding = repository.get_by_app_stream(
                    app=command.app,
                    stream_id=stream_id,
                )
                if (
                    existing_binding is not None
                    and existing_binding.status == StreamBindingStatus.ACTIVE.value
                ):
                    self._assert_existing_matches_request(
                        existing_binding,
                        command,
                    )
                    if existing_binding.space_id is None and command.space_id:
                        existing_binding.space_id = command.space_id
                    if command.stream_name:
                        existing_binding.stream_name = command.stream_name
                    existing_binding.last_active_at = now
                    existing_binding.updated_by = command.created_by
                    return self._to_result(
                        existing_binding,
                        created=False,
                        session=session,
                    )

                historical_binding = existing_binding
                if historical_binding is not None:
                    self._assert_existing_matches_request(historical_binding, command)
                    if historical_binding.status not in (
                        StreamBindingStatus.RELEASED.value,
                        StreamBindingStatus.FAILED.value,
                    ):
                        raise StreamBindingConflictError(
                            "app + stream_id 当前绑定状态不允许重新分配"
                        )
                reserved = RecordingServerRepository(
                    session
                ).try_reserve_binding_slot(
                    recorder_node_id=selected_node.node_id,
                    updated_by=command.created_by,
                )
                if not reserved:
                    return None

                if historical_binding is None:
                    binding = self._new_binding(
                        command,
                        node_id=selected_node.node_id,
                        stream_id=stream_id,
                        stream_mode=stream_mode,
                        now=now,
                    )
                    repository.add(binding)
                else:
                    reactivated = repository.reactivate(
                        historical_binding,
                        expected_version=int(historical_binding.version or 0),
                        node_id=selected_node.node_id,
                        app=command.app,
                        stream_id=stream_id,
                        stream_name=command.stream_name,
                        stream_mode=stream_mode.value,
                        space_id=command.space_id,
                        source_url_ciphertext=None,
                        last_active_at=now,
                        updated_by=command.created_by,
                    )
                    if not reactivated:
                        raise _BindingReservationRace(
                            "绑定已被其他调用中心并发更新"
                        )
                    session.refresh(historical_binding)
                    binding = historical_binding

                return self._to_result(
                    binding,
                    created=True,
                    session=session,
                    affinity_matched=affinity_matched,
                    allocation_reason=allocation_reason,
                )

    def bind_stream(
        self,
        command: MediaStreamBindingCommand,
        *,
        now: datetime | None = None,
    ) -> MediaStreamBindingResult:
        """创建或复用流绑定。

        ``app + stream_id`` 是公开契约中的稳定技术流身份。重复请求优先复用
        已有有效绑定；新建绑定由 MySQL 唯一约束保护并按同空间亲和选择节点。
        """

        stream_mode = self._default_mode(command.resource_type)
        stream_id = command.stream_id
        now = now or datetime.now()

        reused = self._get_reused_binding(command, now=now)
        if reused is not None:
            return reused

        with self.session_factory() as session:
            existing = MediaStreamBindingRepository(session).get_by_app_stream(
                app=command.app,
                stream_id=stream_id,
            )
            if existing is not None:
                self._assert_existing_matches_request(existing, command)

        for selected_node, affinity_matched, allocation_reason in (
            self._ordered_recorder_candidates(command)
        ):
            try:
                result = self._bind_on_candidate(
                    command,
                    selected_node=selected_node,
                    stream_id=stream_id,
                    stream_mode=stream_mode,
                    now=now,
                    affinity_matched=affinity_matched,
                    allocation_reason=allocation_reason,
                )
                if result is not None:
                    return result
            except _BindingReservationRace:
                reused = self._get_reused_binding(command, now=now)
                if reused is not None:
                    return reused
                continue
            except IntegrityError as exc:
                reused = self._get_reused_binding(command, now=now)
                if reused is not None:
                    return reused
                raise StreamBindingConflictError(
                    "流绑定唯一约束冲突，请重新查询绑定"
                ) from exc

        reused = self._get_reused_binding(command, now=now)
        if reused is not None:
            return reused
        raise StreamBindingNodeUnavailableError(
            "健康录制节点的流绑定容量均已占满，无法创建流绑定"
        )

    def release_binding(
        self,
        binding_id: str,
        *,
        updated_by: str = "rtc-service",
        now: datetime | None = None,
    ) -> MediaStreamBindingResult | None:
        """幂等释放流绑定，并在同一事务内归还一个服务器绑定名额。

        只有 ``ACTIVE -> RELEASED`` 状态转换成功的调用才递减占用数，因此 RTC 重试、
        多调用中心并发释放都不会重复归还容量。绑定不存在时返回 ``None``。
        """

        now = now or datetime.now()
        with self.session_factory() as session:
            with session.begin():
                repository = MediaStreamBindingRepository(session)
                binding = repository.get(binding_id)
                if binding is None:
                    return None
                released = repository.mark_released(
                    binding_id,
                    updated_by=updated_by,
                    last_active_at=now,
                )
                if released:
                    server_repository = RecordingServerRepository(session)
                    recording_server = server_repository.get_by_recorder_node_id(
                        binding.node_id
                    )
                    if recording_server is not None:
                        slot_released = server_repository.release_binding_slot(
                            recorder_node_id=binding.node_id,
                            updated_by=updated_by,
                        )
                        if not slot_released:
                            raise StreamBindingConflictError(
                                "流绑定容量计数异常，释放已回滚，请检查录制服务器占用数"
                            )
                    session.refresh(binding)
                return self._to_result(
                    binding,
                    created=False,
                    session=session,
                    allocation_reason="BINDING_RELEASED",
                )

    def get_active_by_app_stream(
        self,
        *,
        app: str,
        stream_id: str,
    ) -> MediaStreamBindingResult | None:
        """按 ZL app 和技术流标识查询当前有效绑定。

        录制命令必须投递到流当前绑定的 recorder-node。这里不做重新调度；
        如果绑定不存在或已经释放，调用方应拒绝录制，而不是选择另一个健康节点。
        """

        with self.session_factory() as session:
            binding = MediaStreamBindingRepository(session).get_by_app_stream(
                app=app,
                stream_id=stream_id,
            )
            if binding is None or binding.status != StreamBindingStatus.ACTIVE.value:
                return None
            return self._to_result(
                binding,
                created=False,
                session=session,
            )


__all__ = ["MediaStreamBindingService"]
