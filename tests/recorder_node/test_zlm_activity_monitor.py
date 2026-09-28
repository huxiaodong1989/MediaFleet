"""录制节点 ZLMediaKit 共享状态监控测试。"""

from __future__ import annotations

import asyncio

import pytest

from services.recorder_node.recorder.zlm_activity_monitor import ZlmActivityMonitor


@pytest.mark.asyncio
async def test_one_app_snapshot_is_shared_by_multiple_recordings() -> None:
    """同一 app 的多路录制只发起一次 getMediaList 等价调用。"""

    list_calls = 0
    recording_calls: list[tuple[str, str]] = []

    async def list_active_streams(app: str) -> set[str]:
        nonlocal list_calls
        list_calls += 1
        assert app == "live"
        return {"stream-1", "stream-2"}

    async def is_recording(stream_id: str, app: str) -> bool:
        recording_calls.append((app, stream_id))
        return stream_id == "stream-1"

    monitor = ZlmActivityMonitor(
        list_active_streams,
        is_recording,
        interval=60,
        stale_grace=60,
        recording_concurrency=2,
    )
    await asyncio.gather(
        monitor.watch("live", "stream-1"),
        monitor.watch("live", "stream-2"),
        monitor.watch("live", "missing"),
    )
    for _ in range(100):
        one = await monitor.get_health("live", "stream-1")
        two = await monitor.get_health("live", "stream-2")
        if list_calls and one.recording is not None and two.recording is not None:
            break
        await asyncio.sleep(0.01)

    assert list_calls == 1
    assert set(recording_calls) == {("live", "stream-1"), ("live", "stream-2")}
    assert (await monitor.get_health("live", "stream-1")).recording is True
    assert (await monitor.get_health("live", "stream-2")).recording is False
    assert (await monitor.get_health("live", "missing")).active is False
    await monitor.close()


@pytest.mark.asyncio
async def test_snapshot_failure_returns_unknown_instead_of_false() -> None:
    """ZL API 失败必须是 unknown，不得导致录制任务集体重启。"""

    calls = 0

    async def list_active_streams(app: str) -> set[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"stream-1"}
        raise RuntimeError("ZLM unavailable")

    async def is_recording(stream_id: str, app: str) -> bool:
        return False

    monitor = ZlmActivityMonitor(
        list_active_streams,
        is_recording,
        interval=60,
        stale_grace=60,
    )
    await monitor.watch("live", "stream-1")
    for _ in range(100):
        if (await monitor.get_health("live", "stream-1")).recording is False:
            break
        await asyncio.sleep(0.01)
    await monitor.refresh_once()

    health = await monitor.get_health("live", "stream-1")
    assert health.active is None
    assert health.recording is None
    await monitor.close()
