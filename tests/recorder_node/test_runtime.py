"""录制节点 FastAPI 生命周期与命令消费线程测试。"""

import asyncio
from threading import Event
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from media_platform.contracts.command import (
    RecorderCommandMessage,
    RecorderCommandType,
)
from media_platform.infrastructure.messaging import TaskPermanentError
from services.recorder_node.application.runtime import RecorderNodeRuntime
from services.recorder_node.application.runtime import (
    _build_recorder_node_heartbeat_reporter,
)
from services.recorder_node.commands import (
    RecorderCommandRegistry,
)
from services.recorder_node.main import create_recorder_node_app


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


class FakePostProcessingManager:
    """记录录制节点是否随服务生命周期启动和停止后处理 Worker 池。"""

    def __init__(self):
        self.start_calls: list[int] = []
        self.stop_calls = 0

    async def start(self, max_workers: int = 3):
        self.start_calls.append(max_workers)

    async def stop(self):
        self.stop_calls += 1


class FakeRecordingRecoveryService:
    """记录录制节点启动时是否执行恢复扫描。"""

    def __init__(self, recovered_count: int = 0):
        self.recovered_count = recovered_count
        self.calls = 0

    def recover_active_recordings(self):
        self.calls += 1
        return self.recovered_count


class FakePostProcessingRecoveryService:
    """记录启动时是否从 MySQL 恢复后处理任务。"""

    def __init__(self, recovered_count: int = 0):
        self.recovered_count = recovered_count
        self.calls = 0

    async def recover_pending_post_processing(self):
        self.calls += 1
        return self.recovered_count


class FakeRecordFileCleaner:
    def __init__(self):
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self):
        self.start_calls += 1

    async def stop(self):
        self.stop_calls += 1


def test_fastapi_lifespan_starts_and_stops_command_consumer_thread():
    consumer = FakeConsumer()
    heartbeat = FakeHeartbeatReporter()
    post_manager = FakePostProcessingManager()
    recovery_service = FakeRecordingRecoveryService(recovered_count=2)
    post_recovery_service = FakePostProcessingRecoveryService(recovered_count=3)
    cleaner = FakeRecordFileCleaner()
    runtime = RecorderNodeRuntime(
        consumer=consumer,
        registry=RecorderCommandRegistry(),
        node_id="recorder-test",
        reconnect_delay_seconds=0.01,
        heartbeat_reporter=heartbeat,
        post_processing_manager=post_manager,
        post_processing_max_workers=2,
        recording_recovery_service=recovery_service,
        post_processing_recovery_service=post_recovery_service,
        record_file_cleaner=cleaner,
    )
    app = create_recorder_node_app(lambda: runtime)

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
    assert post_manager.start_calls == [2]
    assert post_manager.stop_calls == 1
    assert recovery_service.calls == 1
    assert post_recovery_service.calls == 1
    assert cleaner.start_calls == 1
    assert cleaner.stop_calls == 1


def test_disabled_runtime_does_not_consume_command_queue():
    consumer = FakeConsumer()
    heartbeat = FakeHeartbeatReporter()
    post_manager = FakePostProcessingManager()
    runtime = RecorderNodeRuntime(
        consumer=consumer,
        registry=RecorderCommandRegistry(),
        node_id="recorder-disabled",
        command_consumer_enabled=False,
        heartbeat_reporter=heartbeat,
        post_processing_manager=post_manager,
        post_processing_max_workers=4,
    )

    asyncio.run(runtime.start())

    assert consumer.started.is_set() is False
    assert runtime._consumer_thread is None
    assert heartbeat.start_calls == 1
    assert post_manager.start_calls == [4]

    asyncio.run(runtime.close())

    assert post_manager.stop_calls == 1


def test_runtime_passes_idle_strategy_to_post_processing_manager():
    """录制节点启动时应把闲时策略和录制数探测器交给后处理管理器。"""

    class DelayAwarePostProcessingManager:
        def __init__(self):
            self.start_kwargs = None

        async def start(self, max_workers, **kwargs):
            self.start_kwargs = {"max_workers": max_workers, **kwargs}

        async def stop(self):
            pass

    manager = DelayAwarePostProcessingManager()
    runtime = RecorderNodeRuntime(
        consumer=FakeConsumer(),
        registry=RecorderCommandRegistry(),
        node_id="recorder-delay",
        command_consumer_enabled=False,
        post_processing_manager=manager,
        post_processing_max_workers=2,
        post_processing_media_concurrency=1,
        post_processing_idle_strategy_enabled=True,
        post_processing_busy_recording_threshold=20,
        post_processing_busy_media_concurrency=1,
        post_processing_busy_media_percent=50,
        post_processing_max_recordings=100,
        post_processing_idle_strategy_poll_interval_seconds=2,
        post_processing_db_save_max_attempts=5,
        post_processing_db_save_retry_delay_seconds=3,
        post_processing_active_recordings_provider=lambda: 0,
    )

    asyncio.run(runtime.start())

    assert manager.start_kwargs == {
        "max_workers": 2,
        "media_concurrency": 1,
        "idle_strategy_enabled": True,
        "busy_recording_threshold": 20,
        "busy_media_concurrency": 1,
        "busy_media_percent": 50,
        "max_recordings": 100,
        "idle_strategy_poll_interval_seconds": 2,
        "db_save_max_attempts": 5,
        "db_save_retry_delay_seconds": 3,
        "failed_auto_retry_enabled": True,
        "failed_auto_retry_max_attempts": 3,
        "failed_auto_retry_initial_delay_seconds": 60.0,
        "failed_auto_retry_max_delay_seconds": 900.0,
        "active_recordings_provider": runtime.post_processing_active_recordings_provider,
    }

    asyncio.run(runtime.close())


def test_runtime_periodically_scans_and_stops_auto_retry_loop():
    """运行时应周期扫描到期任务，并在关闭时取消后台扫描。"""

    async def scenario():
        recovery_service = FakePostProcessingRecoveryService()
        runtime = RecorderNodeRuntime(
            consumer=FakeConsumer(),
            registry=RecorderCommandRegistry(),
            node_id="recorder-auto-retry",
            command_consumer_enabled=False,
            post_processing_recovery_service=recovery_service,
            post_processing_failed_auto_retry_enabled=True,
            post_processing_failed_auto_retry_scan_interval_seconds=0.01,
        )

        await runtime.start()
        for _ in range(50):
            if recovery_service.calls >= 2:
                break
            await asyncio.sleep(0.01)
        retry_task = runtime._post_processing_retry_task
        assert recovery_service.calls >= 2
        assert retry_task is not None
        assert not retry_task.done()

        await runtime.close()

        assert runtime._post_processing_retry_task is None
        assert retry_task.done()

    asyncio.run(scenario())


def test_runtime_converts_unsupported_command_to_permanent_error():
    runtime = RecorderNodeRuntime(
        consumer=FakeConsumer(),
        registry=RecorderCommandRegistry(),
        node_id="recorder-a",
        command_consumer_enabled=False,
    )
    message = RecorderCommandMessage(
        task_id="task-1",
        target_node_id="recorder-a",
        command=RecorderCommandType.RECORD_START,
        params={},
    )

    with pytest.raises(TaskPermanentError, match="未注册命令处理器"):
        runtime.handle_command(message)


def test_runtime_closes_managed_command_resources():
    class Closable:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    resource = Closable()
    runtime = RecorderNodeRuntime(
        consumer=FakeConsumer(),
        registry=RecorderCommandRegistry(),
        node_id="recorder-a",
        command_consumer_enabled=False,
        managed_resources=(resource,),
    )

    asyncio.run(runtime.close())

    assert resource.close_calls == 1


def test_recorder_node_heartbeat_is_enabled_by_default(monkeypatch):
    """录制节点部署后必须默认上报心跳，不再依赖启用开关。"""

    monkeypatch.delenv("MEDIA_NODE_HEARTBEAT_CONTROL_CENTER_URL", raising=False)
    monkeypatch.delenv("CONTROL_CENTER_URL", raising=False)
    monkeypatch.setenv("MEDIA_NODE_HEARTBEAT_ENABLED", "false")

    reporter = _build_recorder_node_heartbeat_reporter(
        node_id="recorder-a",
        recording_handler=SimpleNamespace(active_recording_count=lambda: 0),
        command_consumer_enabled=True,
        settings=SimpleNamespace(
            API_KEY="internal-secret",
            zlm=SimpleNamespace(api_url="http://127.0.0.1:18080"),
            post_processing=SimpleNamespace(max_workers=3),
        ),
    )

    assert reporter.config.enabled is True
    assert reporter.config.control_center_base_url == "http://127.0.0.1:8008"


def test_recorder_node_heartbeat_reports_rtc_stream_endpoint(monkeypatch):
    """每个录制单元必须上报自身API、播放入口和协议端口。"""

    monkeypatch.setenv("ZLM_SERVER_ID", "zlm-01")
    monkeypatch.setenv("ZLM_PLAY_HOST", "zlm.example.com")
    monkeypatch.setenv("ZLM_PLAY_PORT", "443")
    monkeypatch.setenv("ZLM_PLAY_PROTOCOL", "https")
    monkeypatch.setenv("ZLM_RTMP_PORT", "1935")
    monkeypatch.setenv("ZLM_RTSP_PORT", "554")
    monkeypatch.setattr(
        "services.recorder_node.application.runtime.RecorderDependencyReadinessProbe.check",
        lambda _self: SimpleNamespace(ready=True, details={}),
    )
    monkeypatch.setattr(
        "services.recorder_node.postprocess.get_post_processing_manager",
        lambda: SimpleNamespace(
            stats={},
            queue=SimpleNamespace(qsize=lambda: 0),
            workers=[],
            running=True,
        ),
    )

    reporter = _build_recorder_node_heartbeat_reporter(
        node_id="recorder-a",
        recording_handler=SimpleNamespace(active_recording_count=lambda: 0),
        command_consumer_enabled=True,
        settings=SimpleNamespace(
            API_KEY="internal-secret",
            zlm=SimpleNamespace(
                api_url="http://192.0.2.21:8080",
                secret="secret",
                record_local_root="D:/record",
            ),
            post_processing=SimpleNamespace(max_workers=3, media_concurrency=2),
        ),
    )

    payload = dict(reporter.payload_factory())

    assert payload["zlm_api_url"] == "http://192.0.2.21:8080"
    assert payload["zlm_server_id"] == "zlm-01"
    assert payload["play_host"] == "zlm.example.com"
    assert payload["play_port"] == "443"
    assert payload["play_protocol"] == "https"
    assert payload["rtmp_port"] == "1935"
    assert payload["rtsp_port"] == "554"
