"""媒体 Worker FastAPI 生命周期与消费线程测试。"""

from threading import Event
import time
import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.media_worker.application.runtime import (
    MediaWorkerRuntime,
    _asr_preload_enabled,
    _build_media_worker_heartbeat_reporter,
    _worker_instance_id,
    register_default_processors,
)
from services.media_worker.main import create_media_worker_app
from services.media_worker.registry import TaskProcessorRegistry


class FakeConsumer:
    """模拟阻塞消费，直到生命周期调用 stop。"""

    def __init__(self):
        self.started = Event()
        self.stopped = Event()
        self.close_calls = 0

    def start_consuming(self):
        self.started.set()
        self.stopped.wait(2)

    def stop(self):
        self.stopped.set()

    def close(self):
        self.close_calls += 1


class FakeHeartbeatReporter:
    """记录运行时是否启动和关闭心跳上报。"""

    def __init__(self):
        self.start_calls = 0
        self.close_calls = 0

    def start(self):
        self.start_calls += 1

    def close(self):
        self.close_calls += 1


def test_fastapi_lifespan_starts_and_stops_consumer_thread():
    consumer = FakeConsumer()
    heartbeat = FakeHeartbeatReporter()
    runtime = MediaWorkerRuntime(
        consumer=consumer,
        execution_service=object(),
        registry=TaskProcessorRegistry(),
        worker_id="worker-test",
        reconnect_delay_seconds=0.01,
        heartbeat_reporter=heartbeat,
    )
    app = create_media_worker_app(lambda: runtime)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        for _ in range(50):
            if consumer.started.is_set():
                break
            time.sleep(0.01)
        assert consumer.started.is_set()

    assert consumer.stopped.is_set()
    assert runtime._consumer_thread is None
    assert consumer.close_calls >= 1
    assert heartbeat.start_calls == 1
    assert heartbeat.close_calls == 1


def test_disabled_runtime_does_not_consume_shared_queue():
    consumer = FakeConsumer()
    heartbeat = FakeHeartbeatReporter()
    runtime = MediaWorkerRuntime(
        consumer=consumer,
        execution_service=object(),
        registry=TaskProcessorRegistry(),
        worker_id="worker-disabled",
        consumer_enabled=False,
        heartbeat_reporter=heartbeat,
    )

    asyncio.run(runtime.start())

    assert consumer.started.is_set() is False
    assert runtime._consumer_thread is None
    assert heartbeat.start_calls == 1


def test_runtime_preloads_asr_before_reporting_heartbeat(monkeypatch):
    """开启预热时，模型加载必须先于心跳和队列消费。"""

    consumer = FakeConsumer()
    heartbeat = FakeHeartbeatReporter()
    runtime = MediaWorkerRuntime(
        consumer=consumer,
        execution_service=object(),
        registry=TaskProcessorRegistry(),
        worker_id="worker-preload",
        consumer_enabled=False,
        heartbeat_reporter=heartbeat,
        asr_preload_enabled=True,
        asr_provider="funasr",
    )
    preload_calls = []

    async def fake_preload():
        preload_calls.append("preloaded")
        assert heartbeat.start_calls == 0
        runtime._asr_preload_completed = True

    monkeypatch.setattr(runtime, "_preload_asr_model", fake_preload)

    asyncio.run(runtime.start())

    assert preload_calls == ["preloaded"]
    assert heartbeat.start_calls == 1
    assert runtime._asr_preload_completed is True

    asyncio.run(runtime.close())


def test_asr_preload_uses_current_provider_setting():
    """切换 ASR 提供方时，应读取对应模型的预热配置。"""

    funasr_settings = SimpleNamespace(
        asr=SimpleNamespace(provider="funasr"),
        funasr=SimpleNamespace(preload=True),
        glm_asr=SimpleNamespace(preload=False),
    )
    glm_settings = SimpleNamespace(
        asr=SimpleNamespace(provider="glm"),
        funasr=SimpleNamespace(preload=False),
        glm_asr=SimpleNamespace(preload=True),
    )

    assert _asr_preload_enabled(funasr_settings) is True
    assert _asr_preload_enabled(glm_settings) is True


def test_default_processors_include_cover_and_audio_tasks():
    registry = TaskProcessorRegistry()

    register_default_processors(registry)

    assert "video.cover.extract" in registry.task_types
    assert "video_extract_cover" in registry.task_types
    assert "video.frames.extract" in registry.task_types
    assert "video_extract_imgs" in registry.task_types
    assert "video.audio.extract" in registry.task_types
    assert "video_extract_audio" in registry.task_types
    assert "video.clip.extract" in registry.task_types
    assert "video_extract_clips" in registry.task_types
    assert "stream.cover.extract" in registry.task_types
    assert "live_extract_cover" in registry.task_types
    assert "stream.audio.chunk" in registry.task_types
    assert "stream_extract_audio" in registry.task_types
    assert "video.watermark" in registry.task_types
    assert "video_set_watermark" in registry.task_types


def test_worker_id_prefers_simplified_environment_name(monkeypatch):
    """MEDIA_WORKER_ID 是推荐节点编号，旧变量仅作为兼容兜底。"""

    monkeypatch.setenv("MEDIA_WORKER_ID", "media-worker-new")
    monkeypatch.setenv("MEDIA_WORKER_INSTANCE_ID", "media-worker-old")

    assert _worker_instance_id() == "media-worker-new"


def test_worker_id_falls_back_to_legacy_environment_name(monkeypatch):
    """已有部署只配置旧变量时仍能平滑启动。"""

    monkeypatch.delenv("MEDIA_WORKER_ID", raising=False)
    monkeypatch.setenv("MEDIA_WORKER_INSTANCE_ID", "media-worker-old")

    assert _worker_instance_id() == "media-worker-old"


def test_media_worker_heartbeat_is_enabled_by_default(monkeypatch):
    """媒体 Worker 部署后必须默认上报心跳，不再依赖启用开关。"""

    monkeypatch.delenv("MEDIA_NODE_HEARTBEAT_CONTROL_CENTER_URL", raising=False)
    monkeypatch.delenv("CONTROL_CENTER_URL", raising=False)
    monkeypatch.setenv("MEDIA_NODE_HEARTBEAT_ENABLED", "false")

    reporter = _build_media_worker_heartbeat_reporter(
        worker_id="worker-a",
        registry=TaskProcessorRegistry(),
        execution_service=SimpleNamespace(processing_count=0),
        consumer_enabled=True,
        prefetch_count=1,
        settings=SimpleNamespace(
            API_KEY="internal-secret",
            media=SimpleNamespace(recog_concurrency=2),
            funasr=SimpleNamespace(gpu_concurrency=1, cpu_concurrency=2),
        ),
    )

    assert reporter.config.enabled is True
    assert reporter.config.control_center_base_url == "http://127.0.0.1:8008"
