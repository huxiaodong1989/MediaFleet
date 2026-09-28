"""调用中心后台发布轮询器测试。"""

import asyncio

import pytest

from media_platform.application import DispatchBatchResult
from services.control_center.publishers import (
    TaskDispatchLoop,
    TaskDispatchLoopConfig,
)


class FakeDispatchService:
    def __init__(self, results=None, error=None):
        self.results = list(results or [DispatchBatchResult(0, 0)])
        self.error = error
        self.calls = 0

    def dispatch_batch(self, instance_id, **kwargs):
        self.calls += 1
        if self.error is not None and self.calls == 1:
            raise self.error
        if self.results:
            return self.results.pop(0)
        return DispatchBatchResult(0, 0)


@pytest.mark.asyncio
async def test_loop_starts_idempotently_and_stops_cleanly():
    service = FakeDispatchService()
    loop = TaskDispatchLoop(
        service,
        TaskDispatchLoopConfig(
            instance_id="center-a",
            poll_interval_seconds=0.01,
            error_backoff_seconds=0.01,
        ),
    )

    await loop.start()
    first_runner = loop._runner
    await loop.start()
    assert loop._runner is first_runner
    await asyncio.sleep(0.03)
    await loop.stop()

    assert service.calls >= 1
    assert loop.running is False


@pytest.mark.asyncio
async def test_loop_recovers_after_transient_dispatch_error():
    service = FakeDispatchService(error=RuntimeError("database unavailable"))
    loop = TaskDispatchLoop(
        service,
        TaskDispatchLoopConfig(
            instance_id="center-a",
            poll_interval_seconds=0.01,
            error_backoff_seconds=0.01,
        ),
    )

    await loop.start()
    for _ in range(50):
        if service.calls >= 2:
            break
        await asyncio.sleep(0.01)
    await loop.stop()

    assert service.calls >= 2
