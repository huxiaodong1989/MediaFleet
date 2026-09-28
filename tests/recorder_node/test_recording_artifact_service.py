"""录制产物国标文件表落库服务测试。"""

from __future__ import annotations

import pytest

from media_platform.infrastructure.database.models import MediaFileModel
from services.recorder_node.application.artifact_service import RecordingArtifactService


@pytest.mark.asyncio
async def test_save_file_writes_standard_media_file_table(tmp_path) -> None:
    """录制产物必须写入 `media_artifact`，不能再写旧 `media_files`。"""

    local_file = tmp_path / "cover.jpg"
    local_file.write_bytes(b"fake-cover")
    added_models = []
    commit_calls = 0

    class FakeResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeSession:
        def __init__(self):
            self.execute_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def execute(self, statement):
            self.execute_calls += 1
            if self.execute_calls == 1:
                return FakeResult(None)
            return FakeResult("88888")

        def add(self, model):
            added_models.append(model)

        async def commit(self):
            nonlocal commit_calls
            commit_calls += 1

    service = RecordingArtifactService(async_session_factory=lambda: FakeSession())

    saved_file_id = await service.save_file(
        file_name="cover.jpg",
        task_id="record-task-1",
        file_id="file-1",
        file_url="https://cdn.files.example/upload/cover/cover.jpg",
        file_path=str(local_file),
        mime_type="image/jpeg",
        storage_key="upload/cover/cover.jpg",
        metadata={"bucket": "media-bucket", "md5": "abc"},
    )

    assert saved_file_id == "file-1"
    assert commit_calls == 1
    assert len(added_models) == 1
    file_model = added_models[0]
    assert isinstance(file_model, MediaFileModel)
    assert file_model.__tablename__ == "media_artifact"
    assert file_model.id == "file-1"
    assert file_model.task_id == "record-task-1"
    assert file_model.file_type == "COVER"
    assert file_model.bucket_name == "media-bucket"
    assert file_model.school_code == "88888"


@pytest.mark.asyncio
async def test_save_file_default_sync_path_is_idempotent(tmp_path) -> None:
    """生产同步 Session 路径应在线程中执行，并把同一文件主键视为幂等成功。"""

    local_file = tmp_path / "video.mp4"
    local_file.write_bytes(b"video")
    stored_ids: set[str] = set()
    added_models = []
    commit_calls = 0

    class FakeSyncSession:
        def __init__(self):
            self.scalar_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def scalar(self, statement):
            self.scalar_calls += 1
            if self.scalar_calls == 1:
                return "file-sync" if "file-sync" in stored_ids else None
            return "88888"

        def add(self, model):
            added_models.append(model)
            stored_ids.add(model.id)

        def commit(self):
            nonlocal commit_calls
            commit_calls += 1

    service = RecordingArtifactService(
        session_factory=lambda: FakeSyncSession()
    )
    values = {
        "file_name": "video.mp4",
        "task_id": "record-task-sync",
        "file_id": "file-sync",
        "file_url": "https://cdn.files.example/video.mp4",
        "file_path": str(local_file),
        "mime_type": "video/mp4",
    }

    assert await service.save_file(**values) == "file-sync"
    assert await service.save_file(**values) == "file-sync"

    assert len(added_models) == 1
    assert commit_calls == 1


def test_infer_media_file_type_by_mime_type() -> None:
    assert RecordingArtifactService.infer_media_file_type("video/mp4") == "VIDEO"
    assert RecordingArtifactService.infer_media_file_type("audio/mpeg") == "AUDIO"
    assert RecordingArtifactService.infer_media_file_type("image/jpeg") == "COVER"
    assert RecordingArtifactService.infer_media_file_type("text/vtt") == "SUBTITLE"
    assert RecordingArtifactService.infer_media_file_type("application/octet-stream") == "OTHER"
