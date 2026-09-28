"""
后处理队列管理器
用于管理录制完成后的后续处理任务（音频提取、封面提取、文件上传等），
通过限制并发worker数量来控制IO资源的使用
"""
import asyncio
import logging
import math
from collections.abc import Callable
from threading import RLock
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import traceback

from sqlalchemy.exc import DBAPIError, InterfaceError, InternalError, OperationalError

from services.recorder_node.postprocess.cleanup_service import RecordingCleanupService
from services.recorder_node.postprocess.media_processing_service import (
    RecordingMediaProcessingService,
)
from services.recorder_node.postprocess.persistence_service import (
    RecordingPostProcessPersistenceService,
)
from services.recorder_node.postprocess.preparation_service import (
    RecordingPreparationService,
)
from services.recorder_node.postprocess.stage_executor import (
    RecordingPostProcessStageExecutor,
)
from services.recorder_node.postprocess.stages import (
    CRITICAL_FAILURE_STAGES,
    DEFAULT_POST_PROCESS_STAGES,
    PostProcessStage,
    format_stage_list,
    stage_label,
)
from services.recorder_node.postprocess.upload_service import RecordingUploadService

logger = logging.getLogger("post_processor")


class PostProcessTask:
    """后处理任务"""
    def __init__(
        self,
        task_id: str,
        task_status: Dict[str, Any],
        result_url: str,
        mp4_files: List[Dict[str, Any]],
        stages: List[PostProcessStage],
        priority: int = 0,
        stream_recorder=None,
        recording_window: Dict[str, datetime] | None = None,
        recording_error: str | None = None,
    ):
        self.task_id = task_id
        self.task_status = task_status
        self.result_url = result_url
        self.mp4_files = mp4_files
        self.stages = stages
        self.priority = priority
        self.stream_recorder = stream_recorder  # StreamRecorder实例引用
        self.recording_window = recording_window
        self.recording_error = recording_error
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.current_stage: Optional[PostProcessStage] = None
        self.current_stage_started_at: Optional[datetime] = None
        self.errors: List[str] = []
        self.stage_history: List[Dict[str, Any]] = []

    def __lt__(self, other):
        """用于优先级队列排序"""
        # 优先级越高（数字越大）越先处理
        if self.priority != other.priority:
            return self.priority > other.priority
        # 优先级相同时，先创建的先处理
        return self.created_at < other.created_at


class PostProcessingManager:
    """后处理管理器 - 单例模式"""
    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
            self.max_workers: int = 3  # 默认最多3个并发worker
            self.media_concurrency: int = 2
            self.idle_strategy_enabled: bool = False
            self.busy_recording_threshold: int = 50
            self.busy_media_concurrency: int = 1
            self.busy_media_percent: int = 50
            self.max_recordings: int = 100
            self.idle_strategy_poll_interval_seconds: float = 1.0
            self.db_save_max_attempts: int = 5
            self.db_save_retry_delay_seconds: float = 5.0
            self.failed_auto_retry_enabled: bool = True
            self.failed_auto_retry_max_attempts: int = 3
            self.failed_auto_retry_initial_delay_seconds: float = 60.0
            self.failed_auto_retry_max_delay_seconds: float = 900.0
            self.active_recordings_provider: Callable[[], int] | None = None
            self._media_condition: asyncio.Condition | None = None
            self._media_active: int = 0
            self._last_effective_media_concurrency: int | None = None
            self.workers: List[asyncio.Task] = []
            self.running: bool = False
            self.stats = {
                "total_tasks": 0,
                "completed_tasks": 0,
                "failed_tasks": 0,
                "retry_waiting_tasks": 0,
                "current_processing": 0,
                "queue_size": 0,
                "active_recordings": 0,
                "effective_media_concurrency": 2,
            }
            self._task_map: Dict[str, PostProcessTask] = {}
            self._task_map_lock = RLock()
            self.media_processing_service = RecordingMediaProcessingService()
            self.upload_service = RecordingUploadService()
            self.cleanup_service = RecordingCleanupService()
            self.persistence_service = RecordingPostProcessPersistenceService()
            self.preparation_service = RecordingPreparationService()
            self.result_notification_service = None
            logger.info("后处理管理器初始化完成")

    async def start(
        self,
        max_workers: int = 3,
        media_concurrency: int | None = None,
        idle_strategy_enabled: bool = False,
        busy_recording_threshold: int = 50,
        busy_media_concurrency: int = 1,
        busy_media_percent: int = 50,
        max_recordings: int = 100,
        idle_strategy_poll_interval_seconds: float = 1.0,
        db_save_max_attempts: int = 5,
        db_save_retry_delay_seconds: float = 5.0,
        failed_auto_retry_enabled: bool = True,
        failed_auto_retry_max_attempts: int = 3,
        failed_auto_retry_initial_delay_seconds: float = 60.0,
        failed_auto_retry_max_delay_seconds: float = 900.0,
        active_recordings_provider: Callable[[], int] | None = None,
    ):
        """启动后处理worker池"""
        if self.running:
            logger.warning("后处理管理器已经在运行")
            return

        self.max_workers = max_workers
        self.media_concurrency = max(1, int(media_concurrency or self.media_concurrency))
        self.idle_strategy_enabled = bool(idle_strategy_enabled)
        self.busy_recording_threshold = max(1, int(busy_recording_threshold))
        self.busy_media_concurrency = max(
            0,
            min(int(busy_media_concurrency), self.media_concurrency),
        )
        self.busy_media_percent = max(1, min(int(busy_media_percent), 100))
        self.max_recordings = max(1, int(max_recordings))
        self.idle_strategy_poll_interval_seconds = max(
            0.1,
            float(idle_strategy_poll_interval_seconds),
        )
        self.db_save_max_attempts = max(1, int(db_save_max_attempts))
        self.db_save_retry_delay_seconds = max(
            0.0,
            float(db_save_retry_delay_seconds),
        )
        self.failed_auto_retry_enabled = bool(failed_auto_retry_enabled)
        self.failed_auto_retry_max_attempts = max(
            0,
            int(failed_auto_retry_max_attempts),
        )
        self.failed_auto_retry_initial_delay_seconds = max(
            0.0,
            float(failed_auto_retry_initial_delay_seconds),
        )
        self.failed_auto_retry_max_delay_seconds = max(
            self.failed_auto_retry_initial_delay_seconds,
            float(failed_auto_retry_max_delay_seconds),
        )
        self.active_recordings_provider = active_recordings_provider
        self._media_condition = asyncio.Condition()
        self._media_active = 0
        self._last_effective_media_concurrency = None
        self.running = True

        # 启动worker
        for i in range(max_workers):
            worker = asyncio.create_task(self._worker(i))
            self.workers.append(worker)

        logger.info(
            "后处理管理器启动成功，worker数量: %s，媒体重任务并发: %s，"
            "闲时策略: %s，忙碌录制阈值: %s，满载保留并发: %s%%，"
            "忙碌并发下限: %s，节点录制上限: %s",
            max_workers,
            self.media_concurrency,
            self.idle_strategy_enabled,
            self.busy_recording_threshold,
            self.busy_media_percent,
            self.busy_media_concurrency,
            self.max_recordings,
        )

    async def _run_media_stage(self, operation):
        """独立限制扫描、ffprobe、合并、音频和封面等重操作。"""

        await self._acquire_media_slot()
        try:
            return await operation()
        finally:
            await self._release_media_slot()

    async def stop(self):
        """停止后处理管理器"""
        logger.info("正在停止后处理管理器...")
        self.running = False

        condition = self._media_condition
        if condition is not None:
            async with condition:
                condition.notify_all()

        # 等待所有worker完成当前任务
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)
            self.workers.clear()

        self._media_active = 0

        logger.info("后处理管理器已停止")

    async def submit_task(
        self,
        task_id: str,
        task_status: Dict[str, Any],
        result_url: str,
        mp4_files: List[Dict[str, Any]],
        stream_recorder=None,
        stages: Optional[List[PostProcessStage]] = None,
        priority: int = 0,
        recording_window: Dict[str, datetime] | None = None,
        recording_error: str | None = None,
        duplicate_is_success: bool = True,
    ) -> bool:
        """
        提交后处理任务

        Args:
            task_id: 任务ID
            task_status: 任务状态字典
            result_url: 合并后的视频文件路径
            mp4_files: MP4分片文件列表
            stream_recorder: StreamRecorder实例引用
            stages: 处理阶段列表，如果为None则使用默认流程
            priority: 任务优先级，数字越大优先级越高

        Returns:
            是否成功提交
        """
        try:
            # 如果没有指定stages，使用默认完整流程
            if stages is None:
                stages = list(DEFAULT_POST_PROCESS_STAGES)
            if (
                recording_window is not None or recording_error is not None
            ) and PostProcessStage.RECORDING_PREPARE not in stages:
                stages = [PostProcessStage.RECORDING_PREPARE, *stages]

            post_task = PostProcessTask(
                task_id=task_id,
                task_status=task_status,
                result_url=result_url,
                mp4_files=mp4_files,
                stages=stages,
                priority=priority,
                stream_recorder=stream_recorder,
                recording_window=recording_window,
                recording_error=recording_error,
            )

            # 检查和登记必须处于同一临界区。正常停录与后台恢复扫描可能并发
            # 提交同一 task_id；恢复调用需要知道本次并未真正新增任务。
            with self._task_map_lock:
                if task_id in self._task_map:
                    logger.warning(
                        "录制后处理任务已在队列中，忽略重复提交: %s",
                        task_id,
                    )
                    return duplicate_is_success
                self._task_map[task_id] = post_task

            await self.queue.put(post_task)

            self.stats["total_tasks"] += 1
            self.stats["queue_size"] = self.queue.qsize()

            logger.info(
                f"后处理任务已提交: {task_id}, "
                f"优先级: {priority}, "
                f"阶段数: {len(stages)}, "
                f"阶段列表: {format_stage_list(stages)}, "
                f"当前队列大小: {self.stats['queue_size']}"
            )
            return True

        except Exception as e:
            logger.error(f"提交后处理任务失败: {task_id}, 错误: {str(e)}")
            return False

    def _active_recordings(self) -> int:
        """读取本机当前录制数；读取失败时按忙碌状态保护录制链路。"""

        provider = self.active_recordings_provider
        if not self.idle_strategy_enabled or provider is None:
            return 0
        try:
            return max(0, int(provider()))
        except Exception:
            logger.warning("读取当前录制数失败，后处理按忙碌并发运行", exc_info=True)
            return self.busy_recording_threshold + 1

    def _effective_media_concurrency(self, active_recordings: int) -> int:
        """按录制压力线性降低重 IO/FFmpeg 阶段并发。

        低于繁忙阈值时使用全量并发；从阈值到节点录制上限之间，逐步降到
        ``busy_media_percent`` 指定比例，并受兼容配置
        ``busy_media_concurrency`` 下限保护，避免一达到阈值就断崖式降为1。
        """

        if (
            self.idle_strategy_enabled
            and active_recordings >= self.busy_recording_threshold
        ):
            minimum_by_percent = max(
                1,
                math.ceil(self.media_concurrency * self.busy_media_percent / 100),
            )
            minimum = max(self.busy_media_concurrency, minimum_by_percent)
            if self.max_recordings <= self.busy_recording_threshold:
                return min(self.media_concurrency, minimum)
            pressure = min(
                1.0,
                max(
                    0.0,
                    (active_recordings - self.busy_recording_threshold)
                    / (self.max_recordings - self.busy_recording_threshold),
                ),
            )
            dynamic_limit = math.ceil(
                self.media_concurrency
                - pressure * (self.media_concurrency - minimum)
            )
            return max(minimum, min(self.media_concurrency, dynamic_limit))
        return self.media_concurrency

    async def _acquire_media_slot(self) -> None:
        """等待当前录制压力允许后处理进入重资源阶段。"""

        condition = self._media_condition
        if condition is None:
            return
        while self.running:
            active_recordings = self._active_recordings()
            limit = self._effective_media_concurrency(active_recordings)
            self.stats["active_recordings"] = active_recordings
            self.stats["effective_media_concurrency"] = limit
            if limit != self._last_effective_media_concurrency:
                logger.info(
                    "后处理资源策略切换: active_recordings=%s, "
                    "effective_media_concurrency=%s, full_media_concurrency=%s",
                    active_recordings,
                    limit,
                    self.media_concurrency,
                )
                self._last_effective_media_concurrency = limit
            async with condition:
                if self._media_active < limit:
                    self._media_active += 1
                    return
                try:
                    await asyncio.wait_for(
                        condition.wait(),
                        timeout=self.idle_strategy_poll_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
        raise asyncio.CancelledError

    async def _release_media_slot(self) -> None:
        """释放重资源阶段名额，并唤醒等待中的后处理任务。"""

        condition = self._media_condition
        if condition is None:
            return
        async with condition:
            self._media_active = max(0, self._media_active - 1)
            condition.notify_all()

    async def _worker(self, worker_id: int):
        """Worker处理循环"""
        logger.info(f"后处理Worker-{worker_id} 启动")

        while self.running:
            post_task = None
            try:
                # 等待任务，设置超时避免阻塞
                try:
                    post_task = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                self.stats["current_processing"] += 1
                self.stats["queue_size"] = self.queue.qsize()

                logger.info(
                    f"Worker-{worker_id} 开始处理任务: {post_task.task_id}, "
                    f"优先级: {post_task.priority}, "
                    f"阶段数: {len(post_task.stages)}, "
                    f"剩余队列: {self.stats['queue_size']}, "
                    f"当前处理中: {self.stats['current_processing']}/{self.max_workers}"
                )

                # 处理任务
                post_task.started_at = datetime.now()
                success = await self._process_task(worker_id, post_task)
                post_task.completed_at = datetime.now()

                # 更新统计
                if success:
                    self.stats["completed_tasks"] += 1
                    duration = (post_task.completed_at - post_task.started_at).total_seconds()
                    logger.info(
                        f"Worker-{worker_id} 完成任务: {post_task.task_id}, "
                        f"耗时: {duration:.2f}秒"
                    )
                elif (
                    post_task.task_status.get("post_processing_state")
                    == "auto_retry_waiting"
                ):
                    self.stats["retry_waiting_tasks"] += 1
                    logger.warning(
                        "Worker-%s 当前处理尝试失败，已持久化等待自动重试: "
                        "task_id=%s, retry_attempt=%s, next_retry_at=%s",
                        worker_id,
                        post_task.task_id,
                        post_task.task_status.get(
                            "post_processing_retry_attempt"
                        ),
                        post_task.task_status.get("next_retry_at"),
                    )
                else:
                    self.stats["failed_tasks"] += 1
                    logger.error(
                        f"Worker-{worker_id} 任务失败: {post_task.task_id}, "
                        f"错误: {post_task.errors}"
                    )

            except Exception as e:
                logger.error(
                    f"Worker-{worker_id} 发生异常: {str(e)}\n{traceback.format_exc()}"
                )
                if post_task is not None:
                    self.stats["failed_tasks"] += 1
                    post_task.errors.append(str(e))
                    post_task.task_status.update(
                        post_processing_state="failed",
                        status="failed",
                        error=str(e),
                    )
            finally:
                if post_task is not None:
                    self.stats["current_processing"] = max(
                        0,
                        self.stats["current_processing"] - 1,
                    )
                    with self._task_map_lock:
                        self._task_map.pop(post_task.task_id, None)
                    self.queue.task_done()

        logger.info(f"后处理Worker-{worker_id} 停止")

    async def _process_task(self, worker_id: int, post_task: PostProcessTask) -> bool:
        """
        处理单个后处理任务

        Args:
            worker_id: Worker ID
            post_task: 后处理任务

        Returns:
            是否处理成功
        """
        task_id = post_task.task_id
        task_status = post_task.task_status
        stream_recorder = post_task.stream_recorder

        try:
            # 按阶段执行
            total_stages = len(post_task.stages)
            for stage_index, stage in enumerate(post_task.stages, start=1):
                result_url = post_task.result_url
                post_task.current_stage = stage
                post_task.current_stage_started_at = datetime.now()
                current_stage_label = stage_label(stage)
                logger.info(
                    "Worker-%s 开始后处理阶段: task_id=%s, stage=%s, "
                    "stage_name=%s, progress=%s/%s",
                    worker_id,
                    task_id,
                    stage.value,
                    current_stage_label,
                    stage_index,
                    total_stages,
                )

                try:
                    if stage == PostProcessStage.RECORDING_PREPARE:
                        await self._run_media_stage(
                            lambda: self.preparation_service.prepare(post_task)
                        )
                    elif stage in {
                        PostProcessStage.VIDEO_INFO,
                        PostProcessStage.AUDIO_EXTRACT,
                        PostProcessStage.COVER_EXTRACT,
                        PostProcessStage.VIDEO_UPLOAD,
                        PostProcessStage.AUDIO_UPLOAD,
                        PostProcessStage.COVER_UPLOAD,
                    }:
                        await self._run_media_stage(
                            lambda: self._execute_stage(
                                stage=stage,
                                task_id=task_id,
                                result_url=result_url,
                                task_status=task_status,
                                stream_recorder=stream_recorder,
                                mp4_files=post_task.mp4_files,
                            )
                        )
                    else:
                        await self._execute_stage_with_retry(
                            stage=stage,
                            task_id=task_id,
                            result_url=result_url,
                            task_status=task_status,
                            stream_recorder=stream_recorder,
                            mp4_files=post_task.mp4_files,
                        )

                    # 阶段成功后上报流程轨迹
                    await self._report_stage_to_flow_trace(task_id, task_status, stage)
                    stage_duration = self._stage_duration_seconds(post_task)
                    post_task.stage_history.append(
                        {
                            "stage": stage.value,
                            "stage_name": current_stage_label,
                            "status": "success",
                            "duration_seconds": stage_duration,
                        }
                    )
                    logger.info(
                        "Worker-%s 完成后处理阶段: task_id=%s, stage=%s, "
                        "stage_name=%s, duration=%.2fs",
                        worker_id,
                        task_id,
                        stage.value,
                        current_stage_label,
                        stage_duration,
                    )

                except Exception as stage_error:
                    stage_duration = self._stage_duration_seconds(post_task)
                    error_msg = f"阶段 {stage.value} 执行失败: {str(stage_error)}"
                    logger.error(
                        "Worker-%s 后处理阶段失败: task_id=%s, stage=%s, "
                        "stage_name=%s, critical=%s, duration=%.2fs, error=%s",
                        worker_id,
                        task_id,
                        stage.value,
                        current_stage_label,
                        stage in CRITICAL_FAILURE_STAGES,
                        stage_duration,
                        stage_error,
                        exc_info=True,
                    )
                    post_task.errors.append(error_msg)
                    post_task.stage_history.append(
                        {
                            "stage": stage.value,
                            "stage_name": current_stage_label,
                            "status": "failed",
                            "duration_seconds": stage_duration,
                            "error": str(stage_error),
                        }
                    )

                    # 阶段失败时也上报流程轨迹
                    await self._report_stage_to_flow_trace(task_id, task_status, stage, failed=True, error_msg=str(stage_error))

                    # 某些关键阶段失败则终止整个任务
                    if stage in CRITICAL_FAILURE_STAGES:
                        logger.error(f"关键阶段失败，终止任务: {task_id}")
                        raise

            return True

        except Exception as e:
            logger.error(f"Worker-{worker_id} 处理任务失败: {task_id}, 错误: {str(e)}", exc_info=True)
            post_task.errors.append(str(e))
            if await self._schedule_automatic_retry(post_task, e):
                return False
            self._mark_task_failed(post_task, e)
            await self._notify_failure(task_id, task_status, stream_recorder)

            # 失败时保留源文件和派生文件，便于恢复和排障。只有正常
            # 路线最后的 CLEANUP 阶段才清理任务工作目录。

            return False

    async def _schedule_automatic_retry(
        self,
        post_task: PostProcessTask,
        error: Exception,
    ) -> bool:
        """把可恢复后处理失败持久化为等待状态，由后台扫描自动重新入队。"""

        if (
            not self.failed_auto_retry_enabled
            or self.failed_auto_retry_max_attempts <= 0
            or post_task.current_stage != PostProcessStage.DB_SAVE
            or not self._is_retryable_db_error(error)
        ):
            return False

        current_attempt = self._safe_int(
            post_task.task_status.get("post_processing_retry_attempt"),
            default=0,
        )
        if current_attempt >= self.failed_auto_retry_max_attempts:
            logger.error(
                "录制后处理自动重试预算已耗尽: task_id=%s, attempt=%s/%s",
                post_task.task_id,
                current_attempt,
                self.failed_auto_retry_max_attempts,
            )
            return False

        next_attempt = current_attempt + 1
        delay = min(
            self.failed_auto_retry_max_delay_seconds,
            self.failed_auto_retry_initial_delay_seconds
            * (2 ** (next_attempt - 1)),
        )
        now = datetime.now()
        next_retry_at = now + timedelta(seconds=delay)
        recovery_context = {
            "recording_window": self._serialize_recording_window(
                post_task.recording_window
            ),
            "recording_error": post_task.recording_error,
            "priority": post_task.priority,
            "post_processing_state": "auto_retry_waiting",
            "failed_stage": post_task.current_stage.value,
            "last_error": str(error)[:2000],
            "post_processing_retry_attempt": next_attempt,
            "post_processing_retry_max_attempts": (
                self.failed_auto_retry_max_attempts
            ),
            "next_retry_at": next_retry_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "scheduled_at": now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        }
        task_result_service = self._resolve_task_result_service(
            post_task.stream_recorder
        )
        if task_result_service is None:
            logger.error(
                "录制任务结果服务未初始化，无法持久化自动重试: task_id=%s",
                post_task.task_id,
            )
            return False
        try:
            updated = await asyncio.to_thread(
                task_result_service.mark_post_processing,
                task_id=post_task.task_id,
                result_url=post_task.result_url,
                recovery_context=recovery_context,
            )
        except Exception:
            logger.exception(
                "录制后处理自动重试状态写入MySQL失败: task_id=%s",
                post_task.task_id,
            )
            return False
        if not updated:
            logger.error(
                "录制后处理自动重试状态写入失败，任务不存在: task_id=%s",
                post_task.task_id,
            )
            return False

        post_task.task_status.update(
            status="post_processing",
            post_processing_state="auto_retry_waiting",
            post_processing_retry_attempt=next_attempt,
            next_retry_at=recovery_context["next_retry_at"],
            error=str(error),
        )
        logger.warning(
            "录制后处理失败已进入持久化自动重试等待: task_id=%s, "
            "stage=%s, attempt=%s/%s, delay=%.1fs, next_retry_at=%s",
            post_task.task_id,
            post_task.current_stage.value,
            next_attempt,
            self.failed_auto_retry_max_attempts,
            delay,
            recovery_context["next_retry_at"],
        )
        return True

    def _resolve_task_result_service(self, stream_recorder: Any) -> Any | None:
        """解析录制任务状态服务，保证重试等待事实写入 MySQL。"""

        notification_service = self._resolve_notification_service(stream_recorder)
        return (
            getattr(notification_service, "task_result_service", None)
            or getattr(stream_recorder, "task_result_service", None)
        )

    @staticmethod
    def _safe_int(value: Any, *, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    async def _execute_stage_with_retry(
        self,
        *,
        stage: PostProcessStage,
        task_id: str,
        result_url: str,
        task_status: dict[str, Any],
        stream_recorder: Any,
        mp4_files: list[dict[str, Any]],
    ) -> None:
        """对数据库保存阶段的瞬时连接错误执行有限指数退避重试。

        上传、回调等阶段仍保持原有失败语义，避免把永久业务错误误判为可恢复错误。
        ``DB_SAVE`` 重试期间任务继续保持 ``post_processing``，只有尝试耗尽后才进入
        统一失败出口，因此源录像不会在瞬时数据库抖动时被过早清理。
        """

        max_attempts = (
            self.db_save_max_attempts
            if stage == PostProcessStage.DB_SAVE
            else 1
        )
        for attempt in range(1, max_attempts + 1):
            try:
                await self._execute_stage(
                    stage=stage,
                    task_id=task_id,
                    result_url=result_url,
                    task_status=task_status,
                    stream_recorder=stream_recorder,
                    mp4_files=mp4_files,
                )
                if stage == PostProcessStage.DB_SAVE and attempt > 1:
                    task_status["db_save_retry_attempts"] = attempt - 1
                    logger.info(
                        "数据库保存阶段重试成功: task_id=%s, attempt=%s/%s",
                        task_id,
                        attempt,
                        max_attempts,
                    )
                return
            except Exception as error:
                if (
                    stage != PostProcessStage.DB_SAVE
                    or attempt >= max_attempts
                    or not self._is_retryable_db_error(error)
                ):
                    raise

                delay = min(
                    60.0,
                    self.db_save_retry_delay_seconds * (2 ** (attempt - 1)),
                )
                task_status["post_processing_state"] = "db_save_retrying"
                task_status["db_save_retry_attempts"] = attempt
                task_status["db_save_last_error"] = str(error)[:1000]
                logger.warning(
                    "数据库保存阶段发生瞬时错误，等待重试: task_id=%s, "
                    "attempt=%s/%s, delay=%.1fs, error=%s",
                    task_id,
                    attempt,
                    max_attempts,
                    delay,
                    error,
                )
                if delay > 0:
                    await asyncio.sleep(delay)

        raise RuntimeError("数据库保存阶段重试循环异常退出")

    @classmethod
    def _is_retryable_db_error(cls, error: Exception) -> bool:
        """判断异常链中是否包含数据库连接或协议层瞬时错误。"""

        retryable_messages = (
            "packet sequence number wrong",
            "server has gone away",
            "lost connection",
            "connection reset",
            "connection was closed",
            "not connected",
            "connection refused",
            "broken pipe",
        )
        for current in cls._exception_chain(error):
            if isinstance(current, (OperationalError, InterfaceError)):
                return True
            if isinstance(current, DBAPIError) and bool(
                getattr(current, "connection_invalidated", False)
            ):
                return True
            message = str(current).lower()
            if isinstance(current, (InternalError, DBAPIError)) and any(
                marker in message for marker in retryable_messages
            ):
                return True
            if any(marker in message for marker in retryable_messages):
                return True
        return False

    @staticmethod
    def _exception_chain(error: Exception) -> list[BaseException]:
        """展开 SQLAlchemy ``orig`` 及 Python cause/context 异常链。"""

        chain: list[BaseException] = []
        pending: list[BaseException] = [error]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            chain.append(current)
            for nested in (
                getattr(current, "orig", None),
                current.__cause__,
                current.__context__,
            ):
                if isinstance(nested, BaseException):
                    pending.append(nested)
        return chain

    async def _execute_stage(
        self,
        *,
        stage: PostProcessStage,
        task_id: str,
        result_url: str,
        task_status: dict[str, Any],
        stream_recorder: Any,
        mp4_files: list[dict[str, Any]],
    ) -> None:
        """执行单个后处理阶段。

        管理器每次用当前依赖构造执行器，保证测试或运行时替换
        `upload_service/cleanup_service` 等依赖后立即生效。
        """

        executor = RecordingPostProcessStageExecutor(
            media_processing_service=self.media_processing_service,
            upload_service=self.upload_service,
            persistence_service=self.persistence_service,
            cleanup_service=self.cleanup_service,
            result_notification_service=self.result_notification_service,
        )
        await executor.execute(
            stage=stage,
            task_id=task_id,
            result_url=result_url,
            task_status=task_status,
            stream_recorder=stream_recorder,
            mp4_files=mp4_files,
        )

    def _mark_task_failed(
        self,
        post_task: PostProcessTask,
        error: Exception,
    ) -> None:
        """把后处理失败写回任务状态字典，供通知和查询落库使用。"""

        failed_stage = (
            post_task.current_stage.value if post_task.current_stage else None
        )
        error_message = str(error)
        post_task.task_status["status"] = "failed"
        post_task.task_status["error"] = error_message
        post_task.task_status["failed_stage"] = failed_stage
        post_task.task_status["failed_at"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )[:-3]
        post_task.task_status["updated_at"] = post_task.task_status["failed_at"]
        post_task.task_status["post_processing_errors"] = list(post_task.errors)
        post_task.task_status["post_processing_state"] = "failed"
        recovery_context = {
            "recording_window": self._serialize_recording_window(
                post_task.recording_window
            ),
            "recording_error": post_task.recording_error,
            "priority": post_task.priority,
            "post_processing_state": "failed",
            "failed_stage": failed_stage,
            "failed_at": post_task.task_status["failed_at"],
        }
        retry_attempt = self._safe_int(
            post_task.task_status.get("post_processing_retry_attempt"),
            default=0,
        )
        if retry_attempt:
            recovery_context["post_processing_retry_attempt"] = retry_attempt
            recovery_context["post_processing_retry_max_attempts"] = (
                self.failed_auto_retry_max_attempts
            )
            recovery_context["last_error"] = error_message[:2000]
        post_task.task_status["post_processing_recovery"] = recovery_context
        logger.error(
            "录制后处理任务已标记失败: task_id=%s, failed_stage=%s, error=%s",
            post_task.task_id,
            failed_stage,
            error_message,
        )

    @staticmethod
    def _serialize_recording_window(
        recording_window: dict[str, datetime] | None,
    ) -> dict[str, str] | None:
        """把后处理恢复时间窗转换为可持久化 JSON 的文本。"""

        if not recording_window:
            return None
        return {
            key: value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            for key, value in recording_window.items()
            if isinstance(value, datetime)
        }

    @staticmethod
    def _stage_duration_seconds(post_task: PostProcessTask) -> float:
        """计算当前阶段耗时；阶段未开始时返回 0。"""

        if post_task.current_stage_started_at is None:
            return 0.0
        return round(
            (datetime.now() - post_task.current_stage_started_at).total_seconds(),
            3,
        )

    async def _notify_failure(
        self,
        task_id: str,
        task_status: dict[str, Any],
        stream_recorder: Any,
    ) -> None:
        """后处理关键阶段失败时发送失败通知并写入国标任务表。

        正常完成路径在 CALLBACK 阶段调用同一个通知出口。失败路径也必须走这里，
        否则调用中心只能看到本地后处理队列失败，`media_task` 不会进入最终失败。
        """

        notification_service = self._resolve_notification_service(stream_recorder)
        if notification_service is None:
            logger.error("录制结果通知服务未初始化，无法发送后处理失败通知: %s", task_id)
            return

        try:
            notified = await notification_service.notify(
                task_id=task_id,
                task_status=task_status,
            )
            if notified:
                logger.info("录制后处理失败通知已完成: task_id=%s", task_id)
            else:
                logger.warning("录制后处理失败通知未成功: task_id=%s", task_id)
        except Exception as notify_error:
            logger.error(
                "录制后处理失败通知异常: task_id=%s, error=%s",
                task_id,
                notify_error,
                exc_info=True,
            )

    def _resolve_notification_service(self, stream_recorder: Any) -> Any | None:
        """解析录制结果通知服务，避免后处理继续调用 StreamRecorder 薄委托方法。"""

        if self.result_notification_service is not None:
            return self.result_notification_service
        return getattr(stream_recorder, "result_notification_service", None)

    async def _report_stage_to_flow_trace(
        self, task_id: str, task_status: dict, stage: PostProcessStage,
        failed: bool = False, error_msg: str = None
    ):
        """将后处理阶段映射为流程轨迹步骤并上报，无 classroom_id 时自动跳过"""
        try:
            from media_platform.infrastructure.observability.flow_trace_client import (
                get_flow_trace_client, FlowTraceStepCode, FlowTraceStatus
            )

            STAGE_TO_STEP = {
                PostProcessStage.VIDEO_UPLOAD: FlowTraceStepCode.POST_VIDEO_UPLOAD,
                PostProcessStage.AUDIO_EXTRACT: FlowTraceStepCode.POST_AUDIO_EXTRACT,
                PostProcessStage.DB_SAVE: FlowTraceStepCode.POST_DB_SAVE,
                PostProcessStage.CALLBACK: FlowTraceStepCode.TASK_COMPLETED,
            }

            step_code = STAGE_TO_STEP.get(stage)
            if not step_code:
                return

            extra_params = task_status.get("extra_params") or {}
            classroom_id = extra_params.get("classroom_id")
            if not classroom_id:
                return

            flow_trace = get_flow_trace_client()
            status = FlowTraceStatus.FAILED if failed else FlowTraceStatus.COMPLETED
            await flow_trace.report_step(
                classroom_id=classroom_id,
                process_type=extra_params.get("process_type", "stream_record"),
                step_code=step_code,
                status=status,
                task_id=task_id,
                error_message=error_msg,
                step_end_time=datetime.now(),
            )
        except Exception as e:
            logger.warning(f"流程轨迹上报异常（不影响主流程）: {task_id}, {str(e)}")

    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        self.stats["queue_size"] = self.queue.qsize()
        self.stats["worker_count"] = len(self.workers)
        self.stats["running"] = self.running
        active_recordings = self._active_recordings()
        self.stats["active_recordings"] = active_recordings
        self.stats["effective_media_concurrency"] = (
            self._effective_media_concurrency(active_recordings)
        )
        self.stats["media_active"] = self._media_active
        return self.stats.copy()

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        with self._task_map_lock:
            task = self._task_map.get(task_id)
        if task is not None:
            return {
                "task_id": task.task_id,
                "priority": task.priority,
                "created_at": task.created_at.isoformat(),
                "started_at": task.started_at.isoformat() if task.started_at else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "current_stage": task.current_stage.value if task.current_stage else None,
                "current_stage_name": (
                    stage_label(task.current_stage)
                    if task.current_stage else None
                ),
                "current_stage_started_at": (
                    task.current_stage_started_at.isoformat()
                    if task.current_stage_started_at else None
                ),
                "stage_history": task.stage_history,
                "errors": task.errors
            }
        return None

    def has_task(self, task_id: str) -> bool:
        """线程安全判断任务是否已在队列或正在执行。"""

        with self._task_map_lock:
            return task_id in self._task_map


# 全局单例
_post_processing_manager: Optional[PostProcessingManager] = None


def get_post_processing_manager() -> PostProcessingManager:
    """获取后处理管理器单例"""
    global _post_processing_manager
    if _post_processing_manager is None:
        _post_processing_manager = PostProcessingManager()
    return _post_processing_manager
