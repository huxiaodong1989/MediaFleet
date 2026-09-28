"""通用媒体 Worker 服务边界测试。"""

from pathlib import Path


def test_media_worker_source_does_not_import_app_paths() -> None:
    """media_worker 真实实现不能直接依赖旧 app 包路径。"""

    service_root = Path(__file__).resolve().parents[2] / "services" / "media_worker"
    offenders = []
    for path in service_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "from app." in text or "import app." in text:
            offenders.append(path.relative_to(service_root).as_posix())

    assert offenders == []


def test_real_time_extract_audio_lives_under_media_worker() -> None:
    """实时流音频提取属于通用媒体 Worker。"""

    import services.media_worker.processors.stream.real_time_extract_audio as service_audio

    assert service_audio.RealTimeExtractAudio.__module__.startswith(
        "services.media_worker.processors.stream"
    )
