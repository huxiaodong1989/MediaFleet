"""媒体节点选择服务测试。"""

from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.application import MediaNodeSelectionService
from media_platform.domain.node import MediaNodeType, NodeSelectionCriteria
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    RecordingServerModel,
)


def _session_factory(tmp_path):
    database_file = (tmp_path / "node-selection.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaNodeModel.__table__.create(engine)
    RecordingServerModel.__table__.create(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _node(
    *,
    node_id: str,
    code: str,
    node_type: str = "RECORDER",
    status: str = "ONLINE",
    heartbeat_at: datetime,
    weight: int = 100,
    capabilities=None,
    capacity=None,
):
    return MediaNodeModel(
        id=node_id,
        node_code=code,
        node_name=code,
        node_type=node_type,
        status=status,
        weight=weight,
        capabilities=capabilities or ["record.start"],
        capacity_config=capacity or {},
        readiness_status="READY",
        last_heartbeat_at=heartbeat_at,
        created_by="test",
        updated_by="test",
    )


def _server(
    node_id: str,
    *,
    status: str = "ACTIVE",
    max_bindings: int = 300,
    occupied_bindings: int = 0,
):
    return RecordingServerModel(
        id=f"server-{node_id}",
        server_code=f"server-{node_id}",
        server_name=f"server-{node_id}",
        status=status,
        recorder_node_id=node_id,
        zlm_server_id=f"zlm-{node_id}",
        max_recordings=10,
        max_bindings=max_bindings,
        occupied_bindings=occupied_bindings,
        created_by="test",
        updated_by="test",
    )


def test_select_best_recorder_filters_stale_full_and_disabled_nodes(tmp_path):
    engine, factory = _session_factory(tmp_path)
    now = datetime(2026, 7, 27, 12, 0, 0)
    try:
        with factory() as session:
            with session.begin():
                session.add_all(
                    [
                        _node(
                            node_id="healthy",
                            code="recorder-healthy",
                            heartbeat_at=now,
                            capacity={
                                "current_recordings": 1,
                                "max_recordings": 10,
                                "disk_usage_percent": 50,
                                "postprocess_queue_size": 0,
                                "postprocess_current_processing": 0,
                                "postprocess_max_workers": 2,
                            },
                        ),
                        _node(
                            node_id="full",
                            code="recorder-full",
                            heartbeat_at=now,
                            capacity={
                                "current_recordings": 10,
                                "max_recordings": 10,
                                "disk_usage_percent": 50,
                            },
                        ),
                        _node(
                            node_id="stale",
                            code="recorder-stale",
                            heartbeat_at=now - timedelta(minutes=10),
                        ),
                        _node(
                            node_id="disabled",
                            code="recorder-disabled",
                            status="DISABLED",
                            heartbeat_at=now,
                        ),
                        _server("healthy"),
                        _server("full"),
                        _server("stale"),
                        _server("disabled"),
                    ]
                )

        result = MediaNodeSelectionService(factory).select_best(
            NodeSelectionCriteria(
                node_type=MediaNodeType.RECORDER,
                capability="record.start",
                heartbeat_timeout=timedelta(seconds=90),
            ),
            now=now,
        )

        assert result.selected is not None
        assert result.selected.node.node_id == "healthy"
        assert [item.node.node_id for item in result.candidates] == ["healthy"]
    finally:
        engine.dispose()


def test_selection_does_not_require_school_code(tmp_path):
    """节点选择只关注节点类型、能力和容量，不接收学校码。"""

    engine, factory = _session_factory(tmp_path)
    now = datetime(2026, 7, 27, 12, 0, 0)
    try:
        with factory() as session:
            with session.begin():
                session.add_all(
                    [
                    _node(
                        node_id="public-recorder",
                        code="recorder-public",
                        heartbeat_at=now,
                        capacity={
                            "current_recordings": 0,
                            "max_recordings": 10,
                            "disk_usage_percent": 40,
                        },
                    ),
                    _server("public-recorder"),
                    ]
                )

        result = MediaNodeSelectionService(factory).select_best(
            NodeSelectionCriteria(
                node_type=MediaNodeType.RECORDER,
                capability="record.start",
                heartbeat_timeout=timedelta(seconds=90),
            ),
            now=now,
        )

        assert result.selected is not None
        assert result.selected.node.node_id == "public-recorder"
    finally:
        engine.dispose()


def test_worker_selection_requires_consumer_capacity(tmp_path):
    engine, factory = _session_factory(tmp_path)
    now = datetime(2026, 7, 27, 12, 0, 0)
    try:
        with factory() as session:
            with session.begin():
                session.add_all(
                    [
                        _node(
                            node_id="busy",
                            code="worker-busy",
                            node_type="WORKER",
                            heartbeat_at=now,
                            capabilities=["video.cover.extract"],
                            capacity={
                                "consumer_enabled": True,
                                "processing_tasks": 1,
                                "worker_prefetch": 1,
                            },
                        ),
                        _node(
                            node_id="idle",
                            code="worker-idle",
                            node_type="WORKER",
                            heartbeat_at=now,
                            capabilities=["video.cover.extract"],
                            capacity={
                                "consumer_enabled": True,
                                "processing_tasks": 0,
                                "worker_prefetch": 1,
                            },
                        ),
                    ]
                )

        result = MediaNodeSelectionService(factory).select_best(
            NodeSelectionCriteria(
                node_type=MediaNodeType.WORKER,
                capability="video.cover.extract",
                heartbeat_timeout=timedelta(seconds=90),
            ),
            now=now,
        )

        assert result.selected is not None
        assert result.selected.node.node_id == "idle"
        assert [item.node.node_id for item in result.candidates] == ["idle"]
    finally:
        engine.dispose()


def test_recorder_selection_requires_ready_active_recording_server(tmp_path):
    engine, factory = _session_factory(tmp_path)
    now = datetime(2026, 9, 17, 12, 0, 0)
    try:
        with factory() as session:
            with session.begin():
                not_ready = _node(
                    node_id="not-ready",
                    code="not-ready",
                    heartbeat_at=now,
                    capacity={
                        "current_recordings": 0,
                        "max_recordings": 10,
                        "disk_usage_percent": 10,
                    },
                )
                not_ready.readiness_status = "NOT_READY"
                session.add_all(
                    [
                        not_ready,
                        _server("not-ready"),
                        _node(
                            node_id="draining",
                            code="draining",
                            heartbeat_at=now,
                            capacity={
                                "current_recordings": 0,
                                "max_recordings": 10,
                                "disk_usage_percent": 10,
                            },
                        ),
                        _server("draining", status="DRAINING"),
                        _node(
                            node_id="ready",
                            code="ready",
                            heartbeat_at=now,
                            capacity={
                                "current_recordings": 0,
                                "max_recordings": 10,
                                "disk_usage_percent": 10,
                            },
                        ),
                        _server("ready"),
                    ]
                )

        result = MediaNodeSelectionService(factory).select_best(
            NodeSelectionCriteria(
                node_type=MediaNodeType.RECORDER,
                capability="record.start",
            ),
            now=now,
        )

        assert [item.node.node_id for item in result.candidates] == ["ready"]
        assert result.selected.node.recording_server_status == "ACTIVE"
    finally:
        engine.dispose()


def test_recorder_selection_filters_server_with_full_binding_capacity(tmp_path):
    engine, factory = _session_factory(tmp_path)
    now = datetime(2026, 9, 17, 12, 0, 0)
    try:
        with factory() as session:
            with session.begin():
                session.add_all(
                    [
                        _node(
                            node_id="binding-full",
                            code="binding-full",
                            heartbeat_at=now,
                            weight=100,
                            capacity={
                                "current_recordings": 0,
                                "max_recordings": 10,
                                "disk_usage_percent": 10,
                            },
                        ),
                        _server(
                            "binding-full",
                            max_bindings=1,
                            occupied_bindings=1,
                        ),
                        _node(
                            node_id="binding-free",
                            code="binding-free",
                            heartbeat_at=now,
                            weight=10,
                            capacity={
                                "current_recordings": 0,
                                "max_recordings": 10,
                                "disk_usage_percent": 10,
                            },
                        ),
                        _server(
                            "binding-free",
                            max_bindings=2,
                            occupied_bindings=1,
                        ),
                    ]
                )

        result = MediaNodeSelectionService(factory).select_best(
            NodeSelectionCriteria(
                node_type=MediaNodeType.RECORDER,
                capability="record.start",
            ),
            now=now,
        )

        assert [item.node.node_id for item in result.candidates] == [
            "binding-free"
        ]
        assert result.selected.node.capacity["current_bindings"] == 1
        assert result.selected.node.capacity["max_bindings"] == 2
    finally:
        engine.dispose()
