"""调用中心录制计划时段容量预约测试。"""

from concurrent.futures import ThreadPoolExecutor
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    MediaStreamBindingModel,
    MediaTaskModel,
    RecordingServerModel,
)
from services.control_center.application.recording_task_state_service import (
    RecordingReservationCapacityExceededError,
    RecordingReservationConflictError,
    RecordingTaskStateService,
)
from media_platform.domain.task import PublishStatus


def _factory(tmp_path, *, max_recordings: int = 1):
    database_file = (tmp_path / "recording-reservation.db").resolve().as_posix()
    engine = sa.create_engine(
        f"sqlite:///{database_file}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        with session.begin():
            session.add(
                MediaNodeModel(
                    id="node-1",
                    node_code="recorder-1",
                    node_name="录制节点1",
                    node_type="RECORDER",
                    status="ONLINE",
                    readiness_status="READY",
                    capabilities=["record.start", "record.stop"],
                    capacity_config={},
                    created_by="test",
                    updated_by="test",
                )
            )
            session.add(
                RecordingServerModel(
                    id="server-1",
                    server_code="server-1",
                    server_name="录制服务器1",
                    status="ACTIVE",
                    recorder_node_id="node-1",
                    zlm_server_id="zlm-1",
                    max_recordings=max_recordings,
                    max_bindings=10,
                    occupied_bindings=2,
                    created_by="test",
                    updated_by="test",
                )
            )
            for index in (1, 2):
                session.add(
                    MediaStreamBindingModel(
                        id=f"binding-{index}",
                        school_code="SCHOOL-001",
                        resource_type="CAMERA",
                        resource_id=f"camera-{index}",
                        node_id="node-1",
                        app="live",
                        stream_id=f"stream-{index}",
                        stream_mode="PULL",
                        status="ACTIVE",
                        version=0,
                        created_by="test",
                        updated_by="test",
                    )
                )
    return engine, factory


def _reserve(
    service: RecordingTaskStateService,
    *,
    task_id: str,
    binding_id: str,
    stream_id: str,
    start: str | None,
    end: str | None,
) -> None:
    params = {
        "start_time": start,
        "end_time": end,
    }
    service.save_start_command(
        task_id=task_id,
        app="live",
        stream_id=stream_id,
        target_node_id="recorder-1",
        binding_id=binding_id,
        params=params,
        callback_url=None,
        message_id=f"{task_id}:record.start",
    )


def test_adjacent_half_open_recording_plans_share_one_capacity_slot(tmp_path):
    engine, factory = _factory(tmp_path, max_recordings=1)
    service = RecordingTaskStateService(factory, reservation_margin_seconds=0)
    try:
        _reserve(
            service,
            task_id="task-1",
            binding_id="binding-1",
            stream_id="stream-1",
            start="2026-09-17 10:00:00",
            end="2026-09-17 11:00:00",
        )
        _reserve(
            service,
            task_id="task-2",
            binding_id="binding-2",
            stream_id="stream-2",
            start="2026-09-17 11:00:00",
            end="2026-09-17 12:00:00",
        )

        with factory() as session:
            assert session.scalar(sa.select(sa.func.count(MediaTaskModel.id))) == 2
    finally:
        engine.dispose()


def test_recording_command_intent_is_claimed_then_confirmed(tmp_path):
    """API 发布前先持久化 CLAIMED 意图，Confirm 后才标记 PUBLISHED。"""

    engine, factory = _factory(tmp_path, max_recordings=1)
    service = RecordingTaskStateService(factory, reservation_margin_seconds=0)
    try:
        _reserve(
            service,
            task_id="task-command",
            binding_id="binding-1",
            stream_id="stream-1",
            start="2026-09-18 10:00:00",
            end="2026-09-18 11:00:00",
        )
        with factory() as session:
            task = session.get(MediaTaskModel, "task-command")
            assert task.publish_status == PublishStatus.CLAIMED.value
            assert task.locked_by == "api:task-command:record.start"

        assert service.mark_command_published(
            task_id="task-command",
            message_id="task-command:record.start",
        )
        with factory() as session:
            task = session.get(MediaTaskModel, "task-command")
            assert task.publish_status == PublishStatus.PUBLISHED.value
            assert task.locked_by is None
            assert task.published_at is not None
    finally:
        engine.dispose()


def test_overlapping_plan_is_rejected_when_server_capacity_is_full(tmp_path):
    engine, factory = _factory(tmp_path, max_recordings=1)
    service = RecordingTaskStateService(factory, reservation_margin_seconds=0)
    try:
        _reserve(
            service,
            task_id="task-1",
            binding_id="binding-1",
            stream_id="stream-1",
            start="2026-09-17 10:00:00",
            end="2026-09-17 11:00:00",
        )

        try:
            _reserve(
                service,
                task_id="task-2",
                binding_id="binding-2",
                stream_id="stream-2",
                start="2026-09-17 10:30:00",
                end="2026-09-17 11:30:00",
            )
        except RecordingReservationCapacityExceededError:
            pass
        else:
            raise AssertionError("重叠预约超过服务器容量时必须拒绝")
    finally:
        engine.dispose()


def test_same_stream_overlapping_plan_is_rejected_even_with_spare_capacity(tmp_path):
    engine, factory = _factory(tmp_path, max_recordings=2)
    service = RecordingTaskStateService(factory, reservation_margin_seconds=0)
    try:
        _reserve(
            service,
            task_id="task-1",
            binding_id="binding-1",
            stream_id="stream-1",
            start="2026-09-17 10:00:00",
            end="2026-09-17 11:00:00",
        )

        try:
            _reserve(
                service,
                task_id="task-2",
                binding_id="binding-1",
                stream_id="stream-1",
                start="2026-09-17 10:30:00",
                end="2026-09-17 11:30:00",
            )
        except RecordingReservationConflictError:
            pass
        else:
            raise AssertionError("同一技术流的重叠录制计划必须拒绝")
    finally:
        engine.dispose()


def test_open_ended_recording_occupies_future_capacity_until_closed(tmp_path):
    engine, factory = _factory(tmp_path, max_recordings=1)
    service = RecordingTaskStateService(factory, reservation_margin_seconds=0)
    try:
        _reserve(
            service,
            task_id="task-open",
            binding_id="binding-1",
            stream_id="stream-1",
            start="2026-09-17 10:00:00",
            end=None,
        )

        try:
            _reserve(
                service,
                task_id="task-future",
                binding_id="binding-2",
                stream_id="stream-2",
                start="2026-09-18 10:00:00",
                end="2026-09-18 11:00:00",
            )
        except RecordingReservationCapacityExceededError:
            pass
        else:
            raise AssertionError("开放式录制必须持续占用未来容量")
    finally:
        engine.dispose()


def test_duplicate_task_id_does_not_reserve_capacity_twice(tmp_path):
    engine, factory = _factory(tmp_path, max_recordings=1)
    service = RecordingTaskStateService(factory, reservation_margin_seconds=0)
    try:
        for _ in range(2):
            _reserve(
                service,
                task_id="task-1",
                binding_id="binding-1",
                stream_id="stream-1",
                start="2026-09-17 10:00:00",
                end="2026-09-17 11:00:00",
            )
        with factory() as session:
            assert session.scalar(sa.select(sa.func.count(MediaTaskModel.id))) == 1
    finally:
        engine.dispose()


def test_two_control_centers_cannot_overbook_last_recording_slot(tmp_path):
    engine, factory = _factory(tmp_path, max_recordings=1)
    services = [
        RecordingTaskStateService(factory, reservation_margin_seconds=0),
        RecordingTaskStateService(factory, reservation_margin_seconds=0),
    ]

    def reserve(index: int):
        try:
            _reserve(
                services[index],
                task_id=f"task-{index + 1}",
                binding_id=f"binding-{index + 1}",
                stream_id=f"stream-{index + 1}",
                start="2026-09-17 10:00:00",
                end="2026-09-17 11:00:00",
            )
            return "accepted"
        except RecordingReservationCapacityExceededError:
            return "full"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, (0, 1)))
        assert sorted(results) == ["accepted", "full"]
        with factory() as session:
            assert session.scalar(sa.select(sa.func.count(MediaTaskModel.id))) == 1
    finally:
        engine.dispose()
