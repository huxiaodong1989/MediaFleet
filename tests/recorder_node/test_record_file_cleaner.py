"""录像残留兜底清理器的任务状态保护测试。"""

from datetime import datetime
from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from services.recorder_node.postprocess.record_file_cleaner import RecordFileCleaner


def _factory(tmp_path):
    database_file = (tmp_path / "record-cleaner.db").resolve().as_posix()
    engine = sa.create_engine(f"sqlite:///{database_file}")
    MediaTaskModel.__table__.create(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _insert_task(factory, *, status: str) -> None:
    now = datetime(2026, 9, 22, 10, 0, 0)
    with factory() as session, session.begin():
        session.add(
            MediaTaskModel(
                id="record-cleaner-task",
                task_type="record.stream",
                routing_key="record.start",
                status=status,
                publish_status=PublishStatus.PUBLISHED.value,
                priority=5,
                progress=90,
                params={"app": "live", "stream_id": "stream-protected"},
                callback_url=None,
                executor_node_id="recorder-1",
                recording_app="live",
                recording_stream_id="stream-protected",
                retry_count=0,
                max_retries=3,
                message_id="record-cleaner-task:record.start",
                created_by="test",
                updated_by="test",
                school_code="TEST",
                created_at=now,
                updated_at=now,
            )
        )


def _cleaner(monkeypatch, *, base_path, session_factory):
    monkeypatch.setattr(RecordFileCleaner, "_instance", None)
    cleaner = RecordFileCleaner()
    cleaner.settings = SimpleNamespace(
        record_cleanup=SimpleNamespace(
            enabled=True,
            scan_hour=2,
            keep_days=1,
            record_base_path=str(base_path),
            dry_run=False,
        )
    )
    cleaner.configure(session_factory=session_factory, node_id="recorder-1")
    return cleaner


def test_cleaner_preserves_failed_task_original_files(tmp_path, monkeypatch):
    engine, factory = _factory(tmp_path)
    _insert_task(factory, status=TaskStatus.FAILED.value)
    old_dir = tmp_path / "record" / "live" / "stream-protected" / "2020-01-01"
    old_dir.mkdir(parents=True)
    source = old_dir / "source.mp4"
    source.write_bytes(b"video")
    cleaner = _cleaner(
        monkeypatch,
        base_path=tmp_path / "record",
        session_factory=factory,
    )
    try:
        result = cleaner._scan_and_clean()
        assert source.exists()
        assert result["protected_streams"] == 1
        assert result["skipped_protected_streams"] == 1

        with factory() as session, session.begin():
            task = session.get(MediaTaskModel, "record-cleaner-task")
            task.status = TaskStatus.COMPLETED.value
        result = cleaner._scan_and_clean()
        assert not source.exists()
        assert result["deleted_date_dirs"] == 1
    finally:
        engine.dispose()


def test_cleaner_fails_safe_when_database_is_unavailable(tmp_path, monkeypatch):
    old_dir = tmp_path / "record" / "live" / "stream-1" / "2020-01-01"
    old_dir.mkdir(parents=True)
    source = old_dir / "source.mp4"
    source.write_bytes(b"video")

    def unavailable_factory():
        raise RuntimeError("database unavailable")

    cleaner = _cleaner(
        monkeypatch,
        base_path=tmp_path / "record",
        session_factory=unavailable_factory,
    )
    result = cleaner._scan_and_clean()

    assert source.exists()
    assert result["deleted_date_dirs"] == 0
    assert result["errors"]
