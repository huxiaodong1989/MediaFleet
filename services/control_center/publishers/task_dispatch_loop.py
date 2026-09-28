"""调用中心后台任务发布轮询器。

轮询器本身不保存任务队列。每一轮都从 MySQL 原子领取任务，再通过应用服务
发布到 RabbitMQ；进程退出或实例故障后，其他实例可在锁超时后恢复任务。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Protocol


LOGGER = logging.getLogger(__name__)


class BatchDispatchResult(Protocol):
    """发布循环读取的批次结果。"""

    claimed: int
    published: int
    failed_task_ids: tuple[str, ...]


class BatchDispatchService(Protocol):
    """后台发布循环所需的最小服务端口。

    通用媒体任务和录制命令使用不同的持久化发布服务，但都遵循同一个批量
    发布端口。这样轮询器不会通过具体类名暗示两条链路可以混用。
    """

    def dispatch_batch(
        self,
        instance_id: str,
        *,
        limit: int,
        lock_timeout: timedelta,
    ) -> BatchDispatchResult:
        """领取并发布一批待处理消息。"""


@dataclass(frozen=True)
class TaskDispatchLoopConfig:
    """后台发布循环配置。"""

    instance_id: str
    batch_size: int = 20
    poll_interval_seconds: float = 1.0
    lock_timeout_seconds: int = 300
    error_backoff_seconds: float = 5.0
    log_name: str = "媒体任务"

    def __post_init__(self) -> None:
        if not self.instance_id.strip():
            raise ValueError("instance_id 不能为空")
        if self.batch_size < 1:
            raise ValueError("batch_size 必须大于0")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds 必须大于0")
        if self.lock_timeout_seconds < 1:
            raise ValueError("lock_timeout_seconds 必须大于0")
        if self.error_backoff_seconds <= 0:
            raise ValueError("error_backoff_seconds 必须大于0")
        if not self.log_name.strip():
            raise ValueError("log_name 不能为空")


class TaskDispatchLoop:
    """在 asyncio 生命周期中调度同步数据库和 pika 发布操作。"""

    def __init__(
        self,
        service: BatchDispatchService,
        config: TaskDispatchLoopConfig,
    ):
        self.service = service
        self.config = config
        self._stop_event = asyncio.Event()
        self._runner: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._runner is not None and not self._runner.done()

    async def start(self) -> None:
        """幂等启动后台发布协程。"""

        if self.running:
            return
        self._stop_event.clear()
        self._runner = asyncio.create_task(
            self._run(), name=f"dispatch-{self.config.instance_id}"
        )
        LOGGER.info(
            "%s发布轮询器已启动: instance_id=%s, batch_size=%s",
            self.config.log_name,
            self.config.instance_id,
            self.config.batch_size,
        )

    async def stop(self) -> None:
        """通知循环停止并等待当前批次安全结束。"""

        self._stop_event.set()
        if self._runner is None:
            return
        await self._runner
        self._runner = None
        LOGGER.info(
            "%s发布轮询器已停止: %s",
            self.config.log_name,
            self.config.instance_id,
        )

    async def _wait_or_stop(self, timeout: float) -> None:
        """等待下一轮或在关闭事件到达时立即返回。"""

        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    async def _run(self) -> None:
        lock_timeout = timedelta(seconds=self.config.lock_timeout_seconds)
        while not self._stop_event.is_set():
            try:
                result = await asyncio.to_thread(
                    self.service.dispatch_batch,
                    self.config.instance_id,
                    limit=self.config.batch_size,
                    lock_timeout=lock_timeout,
                )
                if result.claimed:
                    LOGGER.info(
                        "%s发布批次完成: claimed=%s, published=%s, failed=%s",
                        self.config.log_name,
                        result.claimed,
                        result.published,
                        len(result.failed_task_ids),
                    )

                # 领满一批时立即继续，快速排空积压；未领满时按间隔轮询。
                if result.claimed < self.config.batch_size:
                    await self._wait_or_stop(self.config.poll_interval_seconds)
            except Exception:
                LOGGER.exception(
                    "%s发布轮询异常，%s秒后重试",
                    self.config.log_name,
                    self.config.error_backoff_seconds,
                )
                await self._wait_or_stop(self.config.error_backoff_seconds)


__all__ = ["TaskDispatchLoop", "TaskDispatchLoopConfig"]
