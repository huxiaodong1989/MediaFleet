"""媒体节点国标表仓储测试。"""

from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.domain.node import (
    MediaNodeHeartbeat,
    MediaNodeStatus,
    MediaNodeType,
)
from media_platform.infrastructure.database.models import MediaNodeModel
from media_platform.infrastructure.database.repositories import MediaNodeRepository


def _session_factory(tmp_path):
    database_file = (tmp_path / "media-node.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaNodeModel.__table__.create(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_upsert_heartbeat_creates_then_updates_same_node(tmp_path):
    engine, factory = _session_factory(tmp_path)
    first_at = datetime(2026, 7, 27, 10, 0, 0)
    second_at = datetime(2026, 7, 27, 10, 1, 0)
    try:
        with factory() as session:
            with session.begin():
                node, created = MediaNodeRepository(session).upsert_heartbeat(
                    MediaNodeHeartbeat(
                        node_code="recorder-01",
                        node_name="录制节点01",
                        node_type=MediaNodeType.RECORDER,
                        status=MediaNodeStatus.ONLINE,
                        agent_url="http://127.0.0.1:8010",
                        zlm_api_url="http://127.0.0.1:8080",
                        zlm_server_id="zlm-01",
                        record_root="D:/record",
                        capabilities=("record.start", "record.stop"),
                        capacity={"current_recordings": 1, "max_recordings": 80},
                        updated_by="recorder-node",
                    ),
                    heartbeat_at=first_at,
                )
                node_id = node.id

        with factory() as session:
            with session.begin():
                node, updated_created = MediaNodeRepository(session).upsert_heartbeat(
                    MediaNodeHeartbeat(
                        node_code="recorder-01",
                        node_name="录制节点01",
                        node_type=MediaNodeType.RECORDER,
                        status=MediaNodeStatus.DRAINING,
                        capabilities=("record.start",),
                        capacity={"current_recordings": 2, "max_recordings": 80},
                        updated_by="recorder-node",
                    ),
                    heartbeat_at=second_at,
                )

        with factory() as session:
            rows = session.scalars(sa.select(MediaNodeModel)).all()

        assert created is True
        assert updated_created is False
        assert node.id == node_id
        assert len(rows) == 1
        assert rows[0].status == "DRAINING"
        assert rows[0].capabilities == ["record.start"]
        assert rows[0].capacity_config == {
            "current_recordings": 2,
            "max_recordings": 80,
        }
        assert rows[0].last_heartbeat_at == second_at
    finally:
        engine.dispose()


def test_list_by_type_filters_status_type_and_heartbeat(tmp_path):
    engine, factory = _session_factory(tmp_path)
    now = datetime(2026, 7, 27, 10, 0, 0)
    try:
        with factory() as session:
            with session.begin():
                session.add_all(
                    [
                        MediaNodeModel(
                            id="recorder-online",
                            node_code="recorder-online",
                            node_name="录制节点",
                            node_type="RECORDER",
                            status="ONLINE",
                            weight=100,
                            last_heartbeat_at=now,
                            created_by="test",
                            updated_by="test",
                        ),
                        MediaNodeModel(
                            id="recorder-stale",
                            node_code="recorder-stale",
                            node_name="过期节点",
                            node_type="RECORDER",
                            status="ONLINE",
                            weight=100,
                            last_heartbeat_at=now - timedelta(minutes=10),
                            created_by="test",
                            updated_by="test",
                        ),
                        MediaNodeModel(
                            id="worker-online",
                            node_code="worker-online",
                            node_name="Worker节点",
                            node_type="WORKER",
                            status="ONLINE",
                            weight=100,
                            last_heartbeat_at=now,
                            created_by="test",
                            updated_by="test",
                        ),
                        MediaNodeModel(
                            id="recorder-disabled",
                            node_code="recorder-disabled",
                            node_name="停用节点",
                            node_type="RECORDER",
                            status="DISABLED",
                            weight=100,
                            last_heartbeat_at=now,
                            created_by="test",
                            updated_by="test",
                        ),
                    ]
                )

        with factory() as session:
            nodes = MediaNodeRepository(session).list_by_type(
                node_type="RECORDER",
                heartbeat_after=now - timedelta(seconds=90),
            )

        assert [node.id for node in nodes] == ["recorder-online"]
    finally:
        engine.dispose()


def test_mark_stale_offline_updates_only_heartbeat_expired_nodes(tmp_path):
    engine, factory = _session_factory(tmp_path)
    now = datetime(2026, 7, 28, 10, 0, 0)
    try:
        with factory() as session:
            with session.begin():
                session.add_all(
                    [
                        MediaNodeModel(
                            id="worker-live",
                            node_code="worker-live",
                            node_name="在线节点",
                            node_type="WORKER",
                            status="ONLINE",
                            weight=100,
                            last_heartbeat_at=now,
                            created_by="test",
                            updated_by="test",
                        ),
                        MediaNodeModel(
                            id="worker-stale",
                            node_code="worker-stale",
                            node_name="超时节点",
                            node_type="WORKER",
                            status="ONLINE",
                            weight=100,
                            last_heartbeat_at=now - timedelta(minutes=5),
                            created_by="test",
                            updated_by="test",
                        ),
                        MediaNodeModel(
                            id="worker-disabled",
                            node_code="worker-disabled",
                            node_name="停用节点",
                            node_type="WORKER",
                            status="DISABLED",
                            weight=100,
                            last_heartbeat_at=now - timedelta(minutes=5),
                            created_by="test",
                            updated_by="test",
                        ),
                    ]
                )

        with factory() as session:
            with session.begin():
                updated_count = MediaNodeRepository(session).mark_stale_offline(
                    heartbeat_before=now - timedelta(seconds=60),
                )

        with factory() as session:
            rows = {
                node.id: node.status
                for node in session.scalars(sa.select(MediaNodeModel)).all()
            }

        assert updated_count == 1
        assert rows == {
            "worker-live": "ONLINE",
            "worker-stale": "OFFLINE",
            "worker-disabled": "DISABLED",
        }
    finally:
        engine.dispose()
