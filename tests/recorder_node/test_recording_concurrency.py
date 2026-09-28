"""录制尾链路排队、幂等和并发边界测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services.recorder_node.postprocess.post_processor import PostProcessingManager
from services.recorder_node.recorder import stream_recorder as stream_recorder_module
from services.recorder_node.postprocess.stages import PostProcessStage
from services.recorder_node.recorder.stream_recorder import StreamRecorder


@pytest.fixture
def manager(monkeypatch) -> PostProcessingManager:
    monkeypatch.setattr(PostProcessingManager, "_instance", None)
    return PostProcessingManager()


def _window() -> dict[str, datetime]:
    stopped_at = datetime.now() - timedelta(seconds=10)
    return {
        "start_time": stopped_at - timedelta(minutes=30),
        "end_time": stopped_at,
        "stopped_at": stopped_at,
    }


@pytest.mark.asyncio
async def test_zlm_request_uses_current_loop_client_off_command_loop(
    monkeypatch,
) -> None:
    """后处理 loop 不得复用录制命令 loop 的 HTTP 连接池。"""

    class Response:
        def raise_for_status(self) -> None:
            return None

    class OwnerClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("不应跨事件循环使用命令 loop 客户端")

    created_clients = []

    class CurrentLoopClient:
        def __init__(self, *, timeout):
            created_clients.append(timeout)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get(self, *args, **kwargs):
            return Response()

    recorder = StreamRecorder.__new__(StreamRecorder)
    recorder._owner_loop = object()
    recorder.http_client = OwnerClient()
    recorder._zlm_request_semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(
        stream_recorder_module.httpx,
        "AsyncClient",
        CurrentLoopClient,
    )

    response = await recorder._zlm_get("http://zl.invalid/api", params={})

    assert isinstance(response, Response)
    assert created_clients == [30.0]


@pytest.mark.asyncio
async def test_duplicate_task_id_is_only_queued_once(manager: PostProcessingManager) -> None:
    assert await manager.submit_task(
        "task-1",
        {},
        None,
        [],
        recording_window=_window(),
    )
    assert await manager.submit_task(
        "task-1",
        {},
        None,
        [],
        recording_window=_window(),
    )
    assert not await manager.submit_task(
        "task-1",
        {},
        None,
        [],
        recording_window=_window(),
        duplicate_is_success=False,
    )
    assert manager.queue.qsize() == 1
    assert manager.has_task("task-1")


@pytest.mark.asyncio
async def test_complete_preparation_is_bounded_by_worker_count(
    manager: PostProcessingManager,
    tmp_path: Path,
) -> None:
    """大量录制同时结束时，扫描和合并不会在入队前并发执行。"""

    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    recorder = StreamRecorder.__new__(StreamRecorder)
    recorder._get_recording_work_dir = lambda task_id: str(tmp_path / task_id)
    active = 0
    peak = 0
    entered = 0
    three_entered = asyncio.Event()
    release = asyncio.Event()

    async def discover(*args):
        nonlocal active, peak, entered
        active += 1
        peak = max(peak, active)
        entered += 1
        if entered == 3:
            three_entered.set()
        await release.wait()
        active -= 1
        return [{"file_path": str(source), "time_len": 10.0, "file_size": 5}]

    async def process(task_id, files):
        return str(source)

    recorder._get_mp4_record_files_with_app = discover
    recorder._process_result_files = process

    for index in range(20):
        await manager.submit_task(
            f"task-{index}",
            {"stream_id": f"stream-{index}", "app": "live"},
            None,
            [],
            stream_recorder=recorder,
            stages=[PostProcessStage.RECORDING_PREPARE],
            recording_window=_window(),
        )

    assert manager.queue.qsize() == 20
    await manager.start(max_workers=3, media_concurrency=3)
    try:
        await asyncio.wait_for(three_entered.wait(), timeout=2)
        assert manager.queue.qsize() == 17
        assert peak == 3
        release.set()
        await asyncio.wait_for(manager.queue.join(), timeout=5)
    finally:
        release.set()
        await manager.stop()

    assert entered == 20
    assert peak == 3
    assert manager.stats["current_processing"] == 0
    assert not manager._task_map


@pytest.mark.asyncio
async def test_unexpected_worker_error_releases_queue_accounting(
    manager: PostProcessingManager,
    monkeypatch,
) -> None:
    outcomes = iter([RuntimeError("unexpected"), True])

    async def process(worker_id, post_task):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(manager, "_process_task", process)
    await manager.submit_task("first", {}, "first.mp4", [])
    await manager.submit_task("second", {}, "second.mp4", [])
    await manager.start(max_workers=1)
    try:
        await asyncio.wait_for(manager.queue.join(), timeout=3)
    finally:
        await manager.stop()

    assert manager.stats["failed_tasks"] == 1
    assert manager.stats["completed_tasks"] == 1
    assert manager.stats["current_processing"] == 0
    assert not manager._task_map


@pytest.mark.asyncio
async def test_idle_strategy_uses_full_media_concurrency_without_recordings(
    manager: PostProcessingManager,
) -> None:
    """无录制任务时，后处理应使用配置的全量媒体并发。"""

    active_recordings = 0
    entered = 0
    all_entered = asyncio.Event()
    release = asyncio.Event()

    async def operation() -> None:
        nonlocal entered
        entered += 1
        if entered == 3:
            all_entered.set()
        await release.wait()

    await manager.start(
        max_workers=3,
        media_concurrency=3,
        idle_strategy_enabled=True,
        busy_recording_threshold=2,
        busy_media_concurrency=1,
        idle_strategy_poll_interval_seconds=0.01,
        active_recordings_provider=lambda: active_recordings,
    )
    operations = [asyncio.create_task(manager._run_media_stage(operation)) for _ in range(3)]
    try:
        await asyncio.wait_for(all_entered.wait(), timeout=1)
        assert manager.stats["effective_media_concurrency"] == 3
    finally:
        release.set()
        await asyncio.gather(*operations)
        await manager.stop()


@pytest.mark.asyncio
async def test_idle_strategy_reduces_concurrency_while_recording_is_busy(
    manager: PostProcessingManager,
) -> None:
    """录制数达到阈值时限制重处理，录制降低后自动恢复全量并发。"""

    state = {"active_recordings": 10, "entered": 0, "running": 0, "peak": 0}
    busy_limit_entered = asyncio.Event()
    all_entered = asyncio.Event()
    release = asyncio.Event()

    async def operation() -> None:
        state["entered"] += 1
        state["running"] += 1
        state["peak"] = max(state["peak"], state["running"])
        if state["entered"] == 2:
            busy_limit_entered.set()
        if state["entered"] == 4:
            all_entered.set()
        await release.wait()
        state["running"] -= 1

    await manager.start(
        max_workers=4,
        media_concurrency=4,
        idle_strategy_enabled=True,
        busy_recording_threshold=5,
        busy_media_concurrency=1,
        busy_media_percent=50,
        max_recordings=10,
        idle_strategy_poll_interval_seconds=0.01,
        active_recordings_provider=lambda: state["active_recordings"],
    )
    operations = [asyncio.create_task(manager._run_media_stage(operation)) for _ in range(4)]
    try:
        await asyncio.wait_for(busy_limit_entered.wait(), timeout=1)
        await asyncio.sleep(0.05)
        assert state["entered"] == 2
        assert manager.stats["effective_media_concurrency"] == 2

        state["active_recordings"] = 0
        await asyncio.wait_for(all_entered.wait(), timeout=1)
        assert state["peak"] == 4
        assert manager.stats["effective_media_concurrency"] == 4
    finally:
        release.set()
        await asyncio.gather(*operations)
        await manager.stop()


@pytest.mark.asyncio
async def test_disabled_idle_strategy_always_uses_configured_concurrency(
    manager: PostProcessingManager,
) -> None:
    """关闭闲时策略时，录制数不影响后处理配置并发。"""

    manager.idle_strategy_enabled = False
    manager.media_concurrency = 4
    manager.busy_recording_threshold = 1
    manager.busy_media_concurrency = 1
    manager.active_recordings_provider = lambda: 100

    assert manager._effective_media_concurrency(manager._active_recordings()) == 4


@pytest.mark.asyncio
async def test_busy_strategy_uses_percentage_and_fixed_floor(
    manager: PostProcessingManager,
) -> None:
    """达到阈值后平滑下降，满载保留比例且固定下限仍可抬高结果。"""

    manager.idle_strategy_enabled = True
    manager.media_concurrency = 10
    manager.busy_recording_threshold = 50
    manager.max_recordings = 750
    manager.busy_media_percent = 50
    manager.busy_media_concurrency = 1

    assert manager._effective_media_concurrency(49) == 10
    assert manager._effective_media_concurrency(50) == 10
    assert manager._effective_media_concurrency(700) == 6
    assert manager._effective_media_concurrency(750) == 5

    manager.busy_media_concurrency = 7
    assert manager._effective_media_concurrency(750) == 7
