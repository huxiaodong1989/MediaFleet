import asyncio
from threading import Event
from types import SimpleNamespace

from services.image_analysis.application.runtime import (
    ImageAnalysisRuntime,
    _build_image_analysis_heartbeat_reporter,
)
from services.image_analysis.concurrency import (
    ConcurrencyStage,
    ImageConcurrencySettings,
    InstanceConcurrency,
)


class FakeConsumer:
    def __init__(self):
        self.started = Event()
        self.stopped = Event()
        self.close_calls = 0
        self._connection = SimpleNamespace(is_closed=False)

    def start_consuming(self):
        self.started.set()
        self.stopped.wait(2)

    def stop(self):
        self.stopped.set()

    def close(self):
        self.close_calls += 1


class FakeDelivery:
    def deliver_batch(self):
        return 0


class FakePublisher:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class FakeHeartbeatReporter:
    def __init__(self):
        self.start_calls = 0
        self.close_calls = 0

    def start(self):
        self.start_calls += 1

    def close(self):
        self.close_calls += 1


def test_runtime_starts_and_closes_heartbeat_with_consumers():
    consumer = FakeConsumer()
    publisher = FakePublisher()
    heartbeat = FakeHeartbeatReporter()
    runtime = ImageAnalysisRuntime(
        consumer=consumer,
        delivery=FakeDelivery(),
        publisher=publisher,
        concurrency=InstanceConcurrency(ImageConcurrencySettings()),
        worker_id="image-analysis-01",
        heartbeat_reporter=heartbeat,
        notification_interval_seconds=1,
    )

    asyncio.run(runtime.start())
    assert consumer.started.wait(timeout=1)
    assert heartbeat.start_calls == 1

    asyncio.run(runtime.close())
    assert consumer.stopped.is_set()
    assert consumer.close_calls >= 1
    assert publisher.close_calls == 1
    assert heartbeat.close_calls == 1


def test_image_heartbeat_reports_connection_and_all_stage_limits(monkeypatch):
    settings = ImageConcurrencySettings(
        max_concurrent_classrooms=64,
        ffmpeg_concurrency=16,
        download_concurrency=2,
        upload_concurrency=8,
        model_concurrency=6,
        ffmpeg_threads=1,
    )
    concurrency = InstanceConcurrency(settings)
    consumer = SimpleNamespace(_connection=SimpleNamespace(is_closed=False))
    monkeypatch.setenv("MEDIA_NODE_NAME", "图像分析节点01")
    monkeypatch.setenv("MEDIA_NODE_WEIGHT", "80")
    monkeypatch.setenv(
        "MEDIA_NODE_HEARTBEAT_CONTROL_CENTER_URL", "http://control-center:8008"
    )

    reporter = _build_image_analysis_heartbeat_reporter(
        worker_id="image-analysis-01",
        consumer=consumer,
        concurrency=concurrency,
        settings=settings,
        api_key="internal-secret",
    )
    with concurrency.try_slot(ConcurrencyStage.CLASSROOM) as admitted:
        assert admitted is True
        payload = reporter.payload_factory()

    assert reporter.config.enabled is True
    assert reporter.config.control_center_base_url == "http://control-center:8008"
    assert payload["node_type"] == "IMAGE_ANALYSIS"
    assert payload["readiness"] == "READY"
    assert payload["capabilities"] == ["image.three_rates.analyze"]
    assert payload["weight"] == 80
    assert payload["capacity"]["processing_tasks"] == 1
    assert payload["capacity"]["max_concurrent_classrooms"] == 64
    assert payload["capacity"]["stages"] == {
        "classroom": {"limit": 64, "active": 1},
        "ffmpeg": {"limit": 16, "active": 0},
        "download": {"limit": 2, "active": 0},
        "upload": {"limit": 8, "active": 0},
        "model": {"limit": 6, "active": 0},
    }

    consumer._connection.is_closed = True
    unavailable = reporter.payload_factory()
    assert unavailable["readiness"] == "NOT_READY"
    assert unavailable["readiness_details"] == {"consumer_ready": False}
