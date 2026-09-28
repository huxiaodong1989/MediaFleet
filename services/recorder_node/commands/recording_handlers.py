"""录制节点录像命令处理器。

RabbitMQ 命令消费者是同步回调，而 ``StreamRecorder`` 是异步实现，并且
``start_recording`` 会创建持续运行的后台录制任务。因此本模块维护一个
recorder-node 私有的长期 asyncio 事件循环线程，确保录制任务在命令 ACK 后仍能
继续运行。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import inspect
import logging
from threading import Event, Lock, Thread
from typing import Any
from concurrent.futures import TimeoutError as FutureTimeoutError

from media_platform.contracts.command import (
    RecorderCommandMessage,
    RecorderCommandType,
)
from media_platform.infrastructure.messaging import (
    TaskPermanentError,
    TaskRetryableError,
)

LOGGER = logging.getLogger(__name__)


class RecorderCommandLoop:
    """为录制命令维护长期运行的 asyncio 事件循环。

    录制命令消费者运行在 RabbitMQ 阻塞消费线程中。处理 ``record.start`` 时，
    ``StreamRecorder`` 会在当前 asyncio loop 上创建后台录制任务；如果每条命令
    临时创建并关闭 loop，后台任务会被立即取消。因此这里使用单独线程持有 loop，
    命令处理通过 ``run_coroutine_threadsafe`` 投递协程并等待“已接受”结果。
    """

    def __init__(self, *, thread_name: str = "recorder-command-async-loop") -> None:
        self._thread_name = thread_name
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = Event()
        self._lock = Lock()

    def start(self) -> None:
        """幂等启动事件循环线程。"""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = Thread(
                target=self._run_loop,
                name=self._thread_name,
                daemon=True,
            )
            self._thread.start()
        self._ready.wait(5)
        if self._loop is None:
            raise RuntimeError("录制命令异步事件循环启动失败")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()
            self._loop = None

    def submit(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        timeout_seconds: float,
    ) -> Any:
        """把协程投递到录制节点事件循环，并等待同步命令处理结果。"""

        self.start()
        loop = self._loop
        if loop is None:
            raise RuntimeError("录制命令异步事件循环不可用")
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TaskRetryableError("录制命令执行超时") from exc

    def close(self) -> None:
        """停止事件循环线程，并取消仍在运行的后台任务。"""

        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(10)
        self._thread = None


@dataclass(frozen=True)
class RecordingCommandHandlerConfig:
    """录像命令处理器配置。"""

    accept_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.accept_timeout_seconds <= 0:
            raise ValueError("accept_timeout_seconds 必须大于0")


class RecordingCommandHandler:
    """处理 ``record.start`` 和 ``record.stop`` 命令。"""

    def __init__(
        self,
        *,
        recorder_factory: Callable[[], Any],
        recording_recovery_loader: (
            Callable[[str], dict[str, Any] | None] | None
        ) = None,
        loop: RecorderCommandLoop | None = None,
        config: RecordingCommandHandlerConfig | None = None,
    ) -> None:
        self._recorder_factory = recorder_factory
        self._recording_recovery_loader = recording_recovery_loader
        self._loop = loop or RecorderCommandLoop()
        self._config = config or RecordingCommandHandlerConfig()
        self._recorder: Any | None = None
        self._recorder_lock = Lock()

    def set_recording_recovery_loader(
        self,
        loader: Callable[[str], dict[str, Any] | None] | None,
    ) -> None:
        """设置按 task_id 读取恢复上下文的回调。

        运行时需要先创建命令处理器，再用该处理器创建恢复服务，因此通过 setter
        完成装配，避免把 recorder-node 应用层服务反向塞进公共命令模块。
        """

        self._recording_recovery_loader = loader

    def _recorder_instance(self) -> Any:
        """懒加载录制器，避免服务导入阶段初始化 ZL/存储相关依赖。"""

        with self._recorder_lock:
            if self._recorder is None:
                self._recorder = self._recorder_factory()
            return self._recorder

    def handle(self, message: RecorderCommandMessage) -> None:
        """同步处理 RabbitMQ 命令回调。"""

        if message.command == RecorderCommandType.RECORD_START:
            self._loop.submit(
                self._handle_record_start(message),
                timeout_seconds=self._config.accept_timeout_seconds,
            )
            return

        if message.command == RecorderCommandType.RECORD_STOP:
            self._loop.submit(
                self._handle_record_stop(message),
                timeout_seconds=self._config.accept_timeout_seconds,
            )
            return

        raise TaskPermanentError(f"录像命令处理器不支持命令: {message.command}")

    def active_recording_count(self) -> int:
        """返回当前录制器内仍在执行的录像任务数量。

        该值只用于节点心跳容量快照。跨节点调度仍以调用中心写入 MySQL 后的
        节点状态为事实来源，不直接读取某个进程内对象。
        """

        with self._recorder_lock:
            recorder = self._recorder
        if recorder is None:
            return 0
        count = getattr(recorder, "active_recording_count", None)
        if callable(count):
            return int(count())
        tasks = getattr(recorder, "recording_tasks", {}) or {}
        active_statuses = {
            "pending",
            "waiting",
            "processing",
            "recording",
            "stopping",
        }
        return sum(
            1
            for task in tasks.values()
            if str(task.get("status") or "").lower() in active_statuses
        )

    def recover_recording(self, task_id: str, params: dict[str, Any]) -> bool:
        """恢复本节点重启前已经下发但尚未结束的录制任务。

        恢复任务必须复用命令处理器持有的同一个 ``StreamRecorder`` 和长期
        asyncio loop，保证恢复后的任务可以继续接收后续 ``record.stop`` 命令。
        """

        async def recover_on_recorder_loop() -> dict[str, Any]:
            return await self._recorder_instance().start_recording(task_id, params)

        result = self._loop.submit(
            recover_on_recorder_loop(),
            timeout_seconds=self._config.accept_timeout_seconds,
        )
        status = str(result.get("status") or "").lower()
        if status == "failed":
            LOGGER.error(
                "录制任务恢复未被录制器接受: task_id=%s, error=%s",
                task_id,
                result.get("error") or result,
            )
            return False
        LOGGER.info(
            "录制任务已恢复到本机录制器: task_id=%s, app=%s, stream_id=%s",
            task_id,
            params.get("app"),
            params.get("stream_id"),
        )
        return True

    def recover_post_processing(self, task_id: str, params: dict[str, Any]) -> bool:
        """把 MySQL 中遗留的后处理任务重新交给同一个录制器。

        后处理管理器和 ``StreamRecorder`` 都可能持有绑定事件循环的资源，恢复
        不能在调用中心或运行时主事件循环里直接调用录制器协程。这里沿用录制
        命令的长期 loop，保证恢复任务与新收到的停止命令使用同一实例、同一资源。
        """

        async def recover_on_recorder_loop() -> bool:
            recover = getattr(
                self._recorder_instance(),
                "recover_post_processing",
                None,
            )
            if not callable(recover):
                raise TaskRetryableError("当前录制器不支持后处理恢复")
            return bool(await recover(task_id, params))

        accepted = self._loop.submit(
            recover_on_recorder_loop(),
            timeout_seconds=self._config.accept_timeout_seconds,
        )
        if not accepted:
            LOGGER.error("录制后处理恢复未被录制器接受: task_id=%s", task_id)
            return False
        LOGGER.info("录制后处理恢复已交给本机录制器: task_id=%s", task_id)
        return True

    def is_post_processing_tracked(self, task_id: str) -> bool:
        """返回任务是否已在本机后处理队列或正在执行。"""

        with self._recorder_lock:
            recorder = self._recorder
        if recorder is None:
            return False
        checker = getattr(recorder, "is_post_processing_tracked", None)
        return bool(checker(task_id)) if callable(checker) else False

    async def _handle_record_start(self, message: RecorderCommandMessage) -> None:
        params = dict(message.params or {})
        app = str(params.get("app") or "").strip()
        stream_id = str(params.get("stream_id") or "").strip()
        if not app:
            raise TaskPermanentError("record.start 缺少 app")
        if not stream_id:
            raise TaskPermanentError("record.start 缺少 stream_id")

        result = await self._recorder_instance().start_recording(
            message.task_id,
            params,
        )
        status = str(result.get("status") or "").lower()
        if status == "failed":
            raise TaskPermanentError(
                f"record.start 未被录制器接受: {result.get('error') or result}"
            )
        LOGGER.info(
            "录像开始命令已被录制器接受: task_id=%s, app=%s, stream_id=%s",
            message.task_id,
            app,
            stream_id,
        )

    async def _handle_record_stop(self, message: RecorderCommandMessage) -> None:
        params = dict(message.params or {})
        recorder = self._recorder_instance()
        if hasattr(recorder, "stop_recording"):
            stopped = await recorder.stop_recording(message.task_id, params)
            if not stopped:
                recovered = await self._recover_missing_recording_for_stop(
                    recorder=recorder,
                    task_id=message.task_id,
                )
                if recovered:
                    stopped = await recorder.stop_recording(message.task_id, params)
        else:
            delete_from_memory = bool(params.get("delete_from_memory", False))
            stopped = await recorder.cancel_task(
                message.task_id,
                delete_from_memory=delete_from_memory,
            )
        if not stopped:
            LOGGER.warning(
                "录像停止命令未找到本地任务，按幂等成功处理: task_id=%s",
                message.task_id,
            )
        else:
            LOGGER.info("录像停止命令已处理: task_id=%s", message.task_id)

    async def _recover_missing_recording_for_stop(
        self,
        *,
        recorder: Any,
        task_id: str,
    ) -> bool:
        """停止命令本机内存未命中时，尝试从 MySQL 上下文兜底恢复。"""

        if self._recording_recovery_loader is None:
            return False
        recovery_params = self._recording_recovery_loader(task_id)
        if not recovery_params:
            return False
        LOGGER.warning(
            "停止命令未命中本机内存，尝试按 MySQL 上下文恢复录制任务: task_id=%s",
            task_id,
        )
        result = await recorder.start_recording(task_id, recovery_params)
        status = str(result.get("status") or "").lower()
        if status == "failed":
            LOGGER.error(
                "停止命令兜底恢复失败，录制器拒绝恢复: task_id=%s, error=%s",
                task_id,
                result.get("error") or result,
            )
            return False
        return True

    def close(self) -> None:
        """先释放录制器的异步资源，再停止命令事件循环。"""

        with self._recorder_lock:
            recorder = self._recorder
        try:
            close = getattr(recorder, "close", None)
            if callable(close):
                close_result = close()
                if inspect.isawaitable(close_result):
                    self._loop.submit(
                        close_result,
                        timeout_seconds=self._config.accept_timeout_seconds,
                    )
        finally:
            self._loop.close()


__all__ = [
    "RecorderCommandLoop",
    "RecordingCommandHandler",
    "RecordingCommandHandlerConfig",
]
