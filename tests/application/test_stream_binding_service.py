"""RTC 媒体流绑定应用服务测试。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.application import MediaStreamBindingService
from media_platform.domain.stream import (
    MediaStreamBindingCommand,
    StreamBindingConflictError,
    StreamBindingNodeUnavailableError,
    StreamResourceType,
)
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    MediaStreamBindingModel,
    RecordingServerModel,
)


def _session_factory(tmp_path):
    database_file = (tmp_path / "stream-binding-service.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaNodeModel.__table__.create(engine)
    RecordingServerModel.__table__.create(engine)
    MediaStreamBindingModel.__table__.create(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _add_recorder(
    factory,
    *,
    node_id: str = "recorder-1",
    weight: int = 100,
    disk_usage: float = 40,
    max_bindings: int = 10,
):
    with factory() as session:
        with session.begin():
            session.add_all(
                [MediaNodeModel(
                    id=node_id,
                    node_code=node_id,
                    node_name="录制节点1",
                    node_type="RECORDER",
                    status="ONLINE",
                    agent_url="http://127.0.0.1:8010",
                    zlm_api_url="http://127.0.0.1:8080",
                    zlm_server_id=f"zlm-{node_id}",
                    weight=weight,
                    capabilities=["record.start", "record.stop"],
                    capacity_config={
                        "current_recordings": 0,
                        "max_recordings": 10,
                        "disk_usage_percent": disk_usage,
                        "postprocess_current_processing": 0,
                        "postprocess_max_workers": 2,
                    },
                    readiness_status="READY",
                    last_heartbeat_at=datetime.now(),
                    created_by="test",
                    updated_by="test",
                ), RecordingServerModel(
                    id=f"server-{node_id}",
                    server_code=f"server-{node_id}",
                    server_name=f"server-{node_id}",
                    status="ACTIVE",
                    recorder_node_id=node_id,
                    zlm_server_id=f"zlm-{node_id}",
                    zlm_api_url="http://127.0.0.1:8080",
                    play_host="zlm.example.com",
                    play_port="443",
                    play_protocol="https",
                    rtmp_port="1935",
                    rtsp_port="554",
                    max_recordings=10,
                    max_bindings=max_bindings,
                    occupied_bindings=0,
                    created_by="test",
                    updated_by="test",
                )]
            )


def _camera_command(
    stream_id: str = "rtc-stream-001",
    *,
    space_id: str | None = "classroom-001",
    stream_name: str | None = "一号教室摄像头",
):
    return MediaStreamBindingCommand(
        school_code="SCHOOL-001",
        resource_type=StreamResourceType.CAMERA,
        space_id=space_id,
        app="live",
        stream_id=stream_id,
        stream_name=stream_name,
        created_by="rtc-service",
    )


def test_bind_stream_creates_camera_binding_on_selected_recorder(tmp_path):
    engine, factory = _session_factory(tmp_path)
    _add_recorder(factory)
    try:
        result = MediaStreamBindingService(factory).bind_stream(_camera_command())

        assert result.created is True
        assert result.resource_type == StreamResourceType.CAMERA
        assert result.node_id == "recorder-1"
        assert result.node is not None
        assert result.node.zlm_api_url == "http://127.0.0.1:8080"
        assert result.stream_id == "rtc-stream-001"
        assert result.stream_mode.value == "PULL"
    finally:
        engine.dispose()


def test_bind_stream_keeps_business_school_code_but_does_not_filter_nodes_by_school(tmp_path):
    """流绑定保留业务学校码，但节点本身不带租户字段。"""

    engine, factory = _session_factory(tmp_path)
    _add_recorder(factory)
    command = MediaStreamBindingCommand(
        school_code="SCHOOL-001",
        resource_type=StreamResourceType.CAMERA,
        space_id="classroom-001",
        app="live",
        stream_id="school-stream-001",
        stream_name="学校业务摄像头",
        created_by="rtc-service",
    )
    try:
        result = MediaStreamBindingService(factory).bind_stream(command)

        assert result.created is True
        assert result.school_code == "SCHOOL-001"
        assert result.node_id == "recorder-1"
        assert result.node is not None
    finally:
        engine.dispose()


def test_bind_stream_reuses_existing_active_binding(tmp_path):
    engine, factory = _session_factory(tmp_path)
    _add_recorder(factory)
    service = MediaStreamBindingService(factory)
    try:
        first = service.bind_stream(_camera_command())
        second = service.bind_stream(_camera_command())

        with factory() as session:
            count = session.scalar(
                sa.select(sa.func.count()).select_from(MediaStreamBindingModel)
            )
            occupied = session.scalar(
                sa.select(RecordingServerModel.occupied_bindings)
            )

        assert first.created is True
        assert second.created is False
        assert second.binding_id == first.binding_id
        assert count == 1
        assert occupied == 1
    finally:
        engine.dispose()


def test_get_active_by_app_stream_returns_bound_recorder_node(tmp_path):
    engine, factory = _session_factory(tmp_path)
    _add_recorder(factory)
    service = MediaStreamBindingService(factory)
    try:
        created = service.bind_stream(_camera_command(stream_id="camera-stream"))

        found = service.get_active_by_app_stream(
            app="live",
            stream_id="camera-stream",
        )
        missing = service.get_active_by_app_stream(
            app="live",
            stream_id="missing-stream",
        )

        assert found is not None
        assert found.binding_id == created.binding_id
        assert found.node_id == "recorder-1"
        assert found.node is not None
        assert found.node.zlm_api_url == "http://127.0.0.1:8080"
        assert missing is None
    finally:
        engine.dispose()


def test_bind_stream_prefers_same_space_recorder_when_candidate_is_healthy(tmp_path):
    engine, factory = _session_factory(tmp_path)
    _add_recorder(factory, node_id="recorder-1", weight=10)
    _add_recorder(factory, node_id="recorder-2", weight=100)
    service = MediaStreamBindingService(factory)
    try:
        camera = service.bind_stream(_camera_command(stream_id="camera-stream"))
        desktop = service.bind_stream(
            MediaStreamBindingCommand(
                school_code="SCHOOL-001",
                resource_type=StreamResourceType.DESKTOP,
                space_id="classroom-001",
                app="live",
                stream_id="desktop-stream",
                stream_name="一号教室桌面",
                created_by="rtc-service",
            )
        )

        assert camera.node_id == "recorder-2"
        assert desktop.node_id == camera.node_id
        assert desktop.affinity_matched is True
        assert desktop.allocation_reason == "SPACE_AFFINITY"
    finally:
        engine.dispose()


def test_bind_stream_without_space_uses_best_weighted_healthy_recorder(tmp_path):
    engine, factory = _session_factory(tmp_path)
    _add_recorder(factory, node_id="recorder-1", weight=100)
    _add_recorder(factory, node_id="recorder-2", weight=50)
    service = MediaStreamBindingService(factory)
    try:
        desktop = service.bind_stream(
            MediaStreamBindingCommand(
                school_code="SCHOOL-001",
                resource_type=StreamResourceType.DESKTOP,
                space_id=None,
                app="live",
                stream_id="legacy-desktop-stream",
                stream_name=None,
                created_by="rtc-service",
            )
        )

        assert desktop.node_id == "recorder-1"
        assert desktop.space_id is None
        assert desktop.stream_name is None
        assert desktop.affinity_matched is False
        assert desktop.allocation_reason == "BEST_CAPACITY"
    finally:
        engine.dispose()


def test_bind_stream_reuses_existing_space_when_retry_omits_optional_metadata(tmp_path):
    engine, factory = _session_factory(tmp_path)
    _add_recorder(factory)
    service = MediaStreamBindingService(factory)
    try:
        created = service.bind_stream(_camera_command(stream_id="existing-space"))
        reused = service.bind_stream(
            _camera_command(
                stream_id="existing-space",
                space_id=None,
                stream_name=None,
            )
        )

        assert reused.created is False
        assert reused.binding_id == created.binding_id
        assert reused.space_id == "classroom-001"
        assert reused.stream_name == "一号教室摄像头"
    finally:
        engine.dispose()


def test_bind_stream_rejects_stream_bound_to_other_resource_type(tmp_path):
    engine, factory = _session_factory(tmp_path)
    _add_recorder(factory)
    service = MediaStreamBindingService(factory)
    try:
        service.bind_stream(_camera_command(stream_id="same-stream"))

        with pytest.raises(StreamBindingConflictError):
            service.bind_stream(
                MediaStreamBindingCommand(
                    school_code="SCHOOL-001",
                    resource_type=StreamResourceType.DESKTOP,
                    space_id="classroom-001",
                    app="live",
                    stream_id="same-stream",
                    created_by="rtc-service",
                )
            )
    finally:
        engine.dispose()


def test_bind_stream_fails_when_no_recorder_available(tmp_path):
    engine, factory = _session_factory(tmp_path)
    try:
        with pytest.raises(StreamBindingNodeUnavailableError):
            MediaStreamBindingService(factory).bind_stream(_camera_command())
    finally:
        engine.dispose()


def test_bind_stream_retries_next_recorder_when_best_candidate_is_full(tmp_path):
    engine, factory = _session_factory(tmp_path)
    _add_recorder(factory, node_id="recorder-1", weight=100, max_bindings=1)
    _add_recorder(factory, node_id="recorder-2", weight=10, max_bindings=1)
    service = MediaStreamBindingService(factory)
    try:
        first = service.bind_stream(_camera_command(stream_id="stream-1"))
        second = service.bind_stream(
            MediaStreamBindingCommand(
                school_code="SCHOOL-001",
                resource_type=StreamResourceType.DESKTOP,
                space_id="classroom-002",
                app="live",
                stream_id="stream-2",
                created_by="rtc-service",
            )
        )

        assert first.node_id == "recorder-1"
        assert second.node_id == "recorder-2"
        with factory() as session:
            occupied = dict(
                session.execute(
                    sa.select(
                        RecordingServerModel.recorder_node_id,
                        RecordingServerModel.occupied_bindings,
                    )
                ).all()
            )
        assert occupied == {"recorder-1": 1, "recorder-2": 1}
    finally:
        engine.dispose()


def test_bind_stream_reuses_legacy_binding_without_matching_internal_key(tmp_path):
    """历史行的旧资源编号不影响新接口按 app + stream_id 复用。"""

    engine, factory = _session_factory(tmp_path)
    _add_recorder(factory)
    with factory() as session:
        with session.begin():
            session.add(
                MediaStreamBindingModel(
                    id="legacy-binding",
                    school_code="SCHOOL-001",
                    resource_type="CAMERA",
                    resource_id="legacy-camera-business-id",
                    space_id="classroom-001",
                    node_id="recorder-1",
                    app="live",
                    stream_id="legacy-stream",
                    stream_name="历史摄像头",
                    stream_mode="PULL",
                    status="ACTIVE",
                    version=0,
                    created_by="legacy-service",
                    updated_by="legacy-service",
                )
            )
            server = session.get(RecordingServerModel, "server-recorder-1")
            server.occupied_bindings = 1
    try:
        result = MediaStreamBindingService(factory).bind_stream(
            _camera_command(stream_id="legacy-stream")
        )

        assert result.created is False
        assert result.binding_id == "legacy-binding"
        with factory() as session:
            assert session.scalar(
                sa.select(sa.func.count()).select_from(MediaStreamBindingModel)
            ) == 1
            assert session.get(
                RecordingServerModel,
                "server-recorder-1",
            ).occupied_bindings == 1
    finally:
        engine.dispose()


def test_release_binding_decrements_capacity_exactly_once_and_can_rebind(tmp_path):
    engine, factory = _session_factory(tmp_path)
    _add_recorder(factory, max_bindings=1)
    service = MediaStreamBindingService(factory)
    try:
        created = service.bind_stream(_camera_command(stream_id="stream-before"))
        released = service.release_binding(created.binding_id)
        released_again = service.release_binding(created.binding_id)

        assert released is not None
        assert released.status.value == "RELEASED"
        assert released_again is not None
        assert released_again.status.value == "RELEASED"
        with factory() as session:
            server = session.get(RecordingServerModel, "server-recorder-1")
            assert server.occupied_bindings == 0

        rebound = service.bind_stream(_camera_command(stream_id="stream-before"))
        assert rebound.created is True
        assert rebound.binding_id == created.binding_id
        assert rebound.stream_id == "stream-before"
        assert rebound.binding_version == 2
        with factory() as session:
            server = session.get(RecordingServerModel, "server-recorder-1")
            assert server.occupied_bindings == 1
    finally:
        engine.dispose()


def test_concurrent_control_centers_cannot_exceed_binding_capacity(tmp_path):
    """两个服务实例同时看到最后一个名额时，数据库条件更新只允许一个成功。"""

    engine, factory = _session_factory(tmp_path)
    _add_recorder(factory, max_bindings=1)
    services = [MediaStreamBindingService(factory), MediaStreamBindingService(factory)]
    barrier = Barrier(2)
    for service in services:
        original = service.node_selection_service.list_candidates

        def synchronized_candidates(criteria, *, now=None, original=original):
            candidates = original(criteria, now=now)
            barrier.wait(timeout=5)
            return candidates

        service.node_selection_service.list_candidates = synchronized_candidates

    commands = [
        MediaStreamBindingCommand(
            school_code="SCHOOL-001",
            resource_type=StreamResourceType.CAMERA,
            space_id=f"classroom-{index}",
            app="live",
            stream_id=f"stream-{index}",
            created_by="rtc-service",
        )
        for index in (1, 2)
    ]

    def bind(index: int):
        try:
            return services[index].bind_stream(commands[index])
        except StreamBindingNodeUnavailableError as exc:
            return exc

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(bind, (0, 1)))

        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert sum(
            isinstance(result, StreamBindingNodeUnavailableError)
            for result in results
        ) == 1
        with factory() as session:
            binding_count = session.scalar(
                sa.select(sa.func.count()).select_from(MediaStreamBindingModel)
            )
            occupied = session.scalar(
                sa.select(RecordingServerModel.occupied_bindings)
            )
        assert binding_count == 1
        assert occupied == 1
    finally:
        engine.dispose()
