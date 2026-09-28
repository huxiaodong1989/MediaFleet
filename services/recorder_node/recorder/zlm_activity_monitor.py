"""录制节点共享的 ZLMediaKit 流状态监控。

同一录制节点可能同时执行大量录像任务。ZLMediaKit 的 ``getMediaList`` 返回的是
应用级快照，如果每个任务都独立查询，会在集中开课时制造大量重复 HTTP 请求。本模块
按 app 统一轮询一次，再把结果分发给该 app 下所有正在录制的任务。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from typing import Awaitable, Callable, Optional


LOGGER = logging.getLogger("zlm_activity_monitor")


@dataclass(frozen=True)
class StreamHealth:
    """一次共享快照中的流状态。

    ``None`` 表示当前没有足够新的可靠结果。接口超时或 ZL 暂时不可用不能被解释为
    ``False``，否则所有录制任务可能同时误判中断并重复调用 ``startRecord``。
    """

    active: Optional[bool]
    recording: Optional[bool]
    checked_at: Optional[float]


class ZlmActivityMonitor:
    """按 app 共享活跃流快照，并受限并发查询 MP4 录制状态。"""

    def __init__(
        self,
        list_active_streams: Callable[[str], Awaitable[set[str]]],
        is_recording: Callable[[str, str], Awaitable[bool]],
        *,
        interval: float = 10.0,
        stale_grace: float = 30.0,
        summary_interval: float = 60.0,
        recording_concurrency: int = 8,
    ) -> None:
        self._list_active_streams = list_active_streams
        self._is_recording = is_recording
        self._interval = max(1.0, interval)
        self._stale_grace = max(self._interval, stale_grace)
        self._summary_interval = max(self._interval, summary_interval)
        self._recording_semaphore = asyncio.Semaphore(max(1, recording_concurrency))
        self._watched: set[tuple[str, str]] = set()
        self._states: dict[tuple[str, str], StreamHealth] = {}
        self._app_success_at: dict[str, float] = {}
        self._app_failure_at: dict[str, float] = {}
        self._last_summary_at = 0.0
        self._lock = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._runner: asyncio.Task | None = None

    async def watch(self, app: str, stream_id: str) -> None:
        """登记需要监控的流，并按需启动唯一轮询协程。"""

        async with self._lock:
            self._watched.add((app, stream_id))
            if self._runner is None or self._runner.done():
                self._runner = asyncio.create_task(
                    self._run(),
                    name="recorder-zlm-activity-monitor",
                )
            self._wake_event.set()

    async def unwatch(self, app: str, stream_id: str) -> None:
        """移除已结束的录制流。"""

        async with self._lock:
            self._watched.discard((app, stream_id))
            self._states.pop((app, stream_id), None)
            self._wake_event.set()

    async def get_health(self, app: str, stream_id: str) -> StreamHealth:
        """返回最近一次可靠快照；失败或过期时返回未知状态。"""

        async with self._lock:
            health = self._states.get((app, stream_id))
            success_at = self._app_success_at.get(app)
            failure_at = self._app_failure_at.get(app)
        if (
            health is None
            or success_at is None
            or (failure_at is not None and failure_at >= success_at)
            or time.monotonic() - success_at > self._stale_grace
        ):
            return StreamHealth(active=None, recording=None, checked_at=None)
        return health

    async def close(self) -> None:
        """停止轮询并释放内存状态。"""

        async with self._lock:
            runner, self._runner = self._runner, None
            self._watched.clear()
            self._states.clear()
            self._wake_event.set()
        if runner is not None and not runner.done():
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)

    async def refresh_once(self) -> None:
        """执行一次应用级快照和流级录制状态刷新，供轮询和测试复用。"""

        async with self._lock:
            watched = set(self._watched)
        if not watched:
            return

        apps = sorted({app for app, _ in watched})
        snapshots = await asyncio.gather(*(self._refresh_app(app) for app in apps))
        active_by_app = dict(zip(apps, snapshots))
        now = time.monotonic()

        active_watches: list[tuple[str, str]] = []
        async with self._lock:
            for app, stream_id in watched:
                streams = active_by_app.get(app)
                if streams is None:
                    continue
                active = stream_id in streams
                previous = self._states.get((app, stream_id))
                if not active and (previous is None or previous.active is not False):
                    LOGGER.warning(
                        "ZLM活跃流快照未包含录制流: app=%s, stream_id=%s, snapshot_count=%s",
                        app,
                        stream_id,
                        len(streams),
                    )
                self._states[(app, stream_id)] = StreamHealth(
                    active=active,
                    recording=None if not active else (previous.recording if previous else None),
                    checked_at=now,
                )
                if active:
                    active_watches.append((app, stream_id))

        recording_results = await asyncio.gather(
            *(self._refresh_recording(app, stream_id) for app, stream_id in active_watches)
        )
        async with self._lock:
            for (app, stream_id), recording in zip(active_watches, recording_results):
                previous = self._states.get((app, stream_id))
                if previous is not None and previous.active is True:
                    self._states[(app, stream_id)] = StreamHealth(
                        active=True,
                        recording=recording,
                        checked_at=now,
                    )
        await self._log_summary_if_due(watched, now)

    async def _refresh_app(self, app: str) -> set[str] | None:
        try:
            streams = await self._list_active_streams(app)
        except Exception as exc:
            LOGGER.warning("获取ZLM活跃流快照失败: app=%s, error=%s", app, exc)
            async with self._lock:
                self._app_failure_at[app] = time.monotonic()
            return None
        async with self._lock:
            self._app_success_at[app] = time.monotonic()
            self._app_failure_at.pop(app, None)
        return streams

    async def _refresh_recording(self, app: str, stream_id: str) -> bool | None:
        try:
            async with self._recording_semaphore:
                return await self._is_recording(stream_id, app)
        except Exception as exc:
            LOGGER.warning(
                "获取ZLM录制状态失败: app=%s, stream_id=%s, error=%s",
                app,
                stream_id,
                exc,
            )
            return None

    async def _log_summary_if_due(
        self,
        watched: set[tuple[str, str]],
        now: float,
    ) -> None:
        if now - self._last_summary_at < self._summary_interval:
            return
        self._last_summary_at = now
        async with self._lock:
            states = dict(self._states)
        for app in sorted({app for app, _ in watched}):
            healths = [
                states.get((app, stream_id))
                for watched_app, stream_id in watched
                if watched_app == app
            ]
            active = sum(item is not None and item.active is True for item in healths)
            inactive = sum(item is not None and item.active is False for item in healths)
            unknown = len(healths) - active - inactive
            recording = sum(
                item is not None and item.active is True and item.recording is True
                for item in healths
            )
            not_recording = sum(
                item is not None and item.active is True and item.recording is False
                for item in healths
            )
            LOGGER.info(
                "ZLM共享状态快照: app=%s, watched=%s, active=%s, inactive=%s, "
                "unknown=%s, recording=%s, not_recording=%s, recording_unknown=%s",
                app,
                len(healths),
                active,
                inactive,
                unknown,
                recording,
                not_recording,
                active - recording - not_recording,
            )

    async def _run(self) -> None:
        try:
            while True:
                await self.refresh_once()
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(
                        self._wake_event.wait(),
                        timeout=self._interval,
                    )
                except asyncio.TimeoutError:
                    pass
                async with self._lock:
                    if not self._watched:
                        return
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("ZLM活跃流共享监控循环异常")


__all__ = ["StreamHealth", "ZlmActivityMonitor"]
