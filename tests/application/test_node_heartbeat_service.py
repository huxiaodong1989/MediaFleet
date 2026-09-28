"""媒体节点心跳应用服务测试。"""

from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.application import MediaNodeHeartbeatService
from media_platform.infrastructure.database.models import MediaNodeModel


def _session_factory(tmp_path):
    database_file = (tmp_path / "node-heartbeat-service.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaNodeModel.__table__.create(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_mark_stale_offline_uses_last_heartbeat_time(tmp_path):
    engine, factory = _session_factory(tmp_path)
    now = datetime(2026, 7, 28, 10, 0, 0)
    try:
        with factory() as session:
            with session.begin():
                session.add(
                    MediaNodeModel(
                        id="media-worker-local-2",
                        node_code="media-worker-local-2",
                        node_name="媒体处理节点2",
                        node_type="WORKER",
                        status="ONLINE",
                        weight=100,
                        last_heartbeat_at=now - timedelta(seconds=90),
                        created_by="test",
                        updated_by="test",
                    )
                )

        service = MediaNodeHeartbeatService(factory)
        updated_count = service.mark_stale_offline(
            heartbeat_timeout=timedelta(seconds=60),
            now=now,
        )

        with factory() as session:
            node = session.get(MediaNodeModel, "media-worker-local-2")

        assert updated_count == 1
        assert node.status == "OFFLINE"
    finally:
        engine.dispose()
