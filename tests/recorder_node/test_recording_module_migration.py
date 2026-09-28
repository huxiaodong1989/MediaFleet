"""录制节点核心实现目录归属测试。"""

from pathlib import Path

from services.recorder_node.postprocess import (
    PostProcessingManager,
    RecordFileCleaner,
)
from services.recorder_node.recorder import StreamRecorder


def test_recorder_node_source_does_not_import_app_paths() -> None:
    """recorder-node 真实实现不能再直接依赖旧 app 包路径。"""

    service_root = Path(__file__).resolve().parents[2] / "services" / "recorder_node"
    offenders = []
    for path in service_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "from app." in text or "import app." in text:
            offenders.append(path.relative_to(service_root).as_posix())

    assert offenders == []


def test_storage_real_implementation_lives_in_media_platform() -> None:
    """存储真实实现归属 media_platform，主项目不再保留旧 app.storage 壳。"""

    import media_platform.infrastructure.storage as platform_storage

    assert platform_storage.get_storage_service.__module__.startswith(
        "media_platform.infrastructure.storage"
    )
    assert not hasattr(platform_storage, "LocalStorageService")
    assert not hasattr(platform_storage, "EnhancedLocalStorageService")


def test_storage_factory_rejects_local_type() -> None:
    """业务结果存储只允许 COS/MinIO，不允许本地存储兜底。"""

    from media_platform.infrastructure.storage import enhanced_storage_service
    from media_platform.infrastructure.storage import storage_service

    original_storage_type = storage_service.settings.storage.type
    original_enhanced_storage_type = enhanced_storage_service.settings.storage.type
    try:
        storage_service.settings.storage.type = "local"
        enhanced_storage_service.settings.storage.type = "local"

        import pytest

        with pytest.raises(ValueError, match="仅支持 cos 或 minio"):
            storage_service.get_storage_service()

        with pytest.raises(ValueError, match="仅支持 cos 或 minio"):
            enhanced_storage_service.get_enhanced_storage_service()
    finally:
        storage_service.settings.storage.type = original_storage_type
        enhanced_storage_service.settings.storage.type = original_enhanced_storage_type


def test_recorder_node_exports_real_recording_modules() -> None:
    """recorder-node 对外导出录制、后处理和残留清理核心类型。"""

    assert StreamRecorder.__module__.startswith("services.recorder_node.recorder")
    assert PostProcessingManager.__module__.startswith(
        "services.recorder_node.postprocess"
    )
    assert RecordFileCleaner.__module__.startswith("services.recorder_node.postprocess")
