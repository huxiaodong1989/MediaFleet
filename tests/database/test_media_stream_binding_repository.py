"""媒体流绑定仓储测试。"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    MediaStreamBindingModel,
)
from media_platform.infrastructure.database.repositories import (
    MediaStreamBindingRepository,
)


def _session_factory(tmp_path):
    database_file = (tmp_path / "media-stream-binding.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaNodeModel.__table__.create(engine)
    MediaStreamBindingModel.__table__.create(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _node():
    return MediaNodeModel(
        id="recorder-1",
        node_code="recorder-1",
        node_name="录制节点1",
        node_type="RECORDER",
        status="ONLINE",
        weight=100,
        created_by="test",
        updated_by="test",
    )


def _binding():
    return MediaStreamBindingModel(
        id="binding-1",
        school_code="SCHOOL-001",
        resource_type="CAMERA",
        resource_id="camera-001",
        space_id="classroom-001",
        node_id="recorder-1",
        app="live",
        stream_id="stream-001",
        stream_name="摄像头展示名",
        stream_mode="PULL",
        status="ACTIVE",
        version=0,
        last_active_at=datetime(2026, 7, 27, 12, 0, 0),
        created_by="test",
        updated_by="test",
    )


def test_stream_binding_repository_queries_active_binding_and_stream(tmp_path):
    engine, factory = _session_factory(tmp_path)
    try:
        with factory() as session:
            with session.begin():
                session.add(_node())
                session.add(_binding())

        with factory() as session:
            repository = MediaStreamBindingRepository(session)
            by_resource = repository.get_active_by_resource(
                school_code="SCHOOL-001",
                resource_type="CAMERA",
                resource_id="camera-001",
            )
            by_stream = repository.get_by_app_stream(
                app="live",
                stream_id="stream-001",
            )

        assert by_resource is not None
        assert by_resource.id == "binding-1"
        assert by_stream is not None
        assert by_stream.resource_id == "camera-001"
    finally:
        engine.dispose()


def test_stream_binding_repository_lists_active_bindings_by_space(tmp_path):
    engine, factory = _session_factory(tmp_path)
    try:
        with factory() as session:
            with session.begin():
                session.add(_node())
                session.add(_binding())
                session.add(
                    MediaStreamBindingModel(
                        id="binding-released",
                        school_code="SCHOOL-001",
                        resource_type="DESKTOP",
                        resource_id="desktop-001",
                        space_id="classroom-001",
                        node_id="recorder-1",
                        app="live",
                        stream_id="stream-002",
                        stream_mode="PUSH",
                        status="RELEASED",
                        version=0,
                        created_by="test",
                        updated_by="test",
                    )
                )

        with factory() as session:
            bindings = MediaStreamBindingRepository(session).list_active_by_space(
                school_code="SCHOOL-001",
                space_id="classroom-001",
            )

        assert [binding.id for binding in bindings] == ["binding-1"]
    finally:
        engine.dispose()
