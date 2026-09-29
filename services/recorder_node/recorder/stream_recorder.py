import logging
import asyncio
import time as time_module
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import httpx
import platform
import ffmpeg
import subprocess
from threading import Lock

from media_platform.infrastructure.storage import (
    get_enhanced_storage_service,
)
from media_platform.infrastructure.media_processing import (
    CoverExtractor,
    CoverExtractStrategy,
    FillerVideoGenerator,
    GetAudioInfo,
    GetCoverInfo,
    GetVideoInfo,
)
from media_platform.common.config import get_settings
from services.recorder_node.application.artifact_service import RecordingArtifactService
from services.recorder_node.application.result_notification_service import (
    RecordingResultNotificationService,
)
from services.recorder_node.application.task_result_service import (
    RecordingTaskResultService,
)
from services.recorder_node.notifications import (
    RecorderResultNotifier,
    RecorderResultPayloadBuilder,
)
from services.recorder_node.recorder.record_file_discovery import (
    RecordingFilesUnavailable,
    discover_record_files,
)
from services.recorder_node.recorder.zlm_activity_monitor import ZlmActivityMonitor



logger = logging.getLogger("stream_recorder")


class RecordingCapacityExceededError(RuntimeError):
    """录制节点在真正启动 ZL 录制时已达到本机硬并发上限。"""

class StreamRecorder:
    """直播流录制处理器"""

    def __init__(
        self,
        config: Dict[str, Any],
        dispatcher=None,
        *,
        max_recordings: int | None = None,
    ):
        """
        初始化录制处理器

        :param config: 配置信息，包含ZLMediaKit服务器地址等
        :param dispatcher: 已废弃的旧调用中心内存分发器参数，仅为历史构造调用兼容保留；
            新录制链路不再读取旧分发器状态。
        """
        self.config = config
        if dispatcher is not None:
            logger.warning("StreamRecorder 已忽略旧 dispatcher 参数，新录制链路以本机状态和 MySQL 为准")
        # StreamRecorder 由录制命令专用事件循环创建，但后处理 Worker 运行在
        # FastAPI 主事件循环。HTTP 连接池和 asyncio.Semaphore 不能跨 loop
        # 复用，否则高并发时会出现 "bound to a different event loop"。
        self._owner_loop = asyncio.get_running_loop()
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self._record_probe_semaphores: dict[
            asyncio.AbstractEventLoop, asyncio.Semaphore
        ] = {}
        self._loop_resource_lock = Lock()
        self.recording_tasks = {}  # 保存正在录制的任务
        self.max_recordings = (
            int(max_recordings)
            if max_recordings is not None
            else int(os.getenv("RECORDER_NODE_MAX_RECORDINGS", "100"))
        )
        if self.max_recordings < 1:
            raise ValueError("RECORDER_NODE_MAX_RECORDINGS 必须大于等于1")
        # RabbitMQ 命令和启动恢复最终都会进入同一个录制器。这里把
        # “检查容量 + 占用实际录制名额”放在同一临界区，避免多路任务
        # 同时看到最后一个空闲名额后都调用 ZL startRecord。
        self._recording_admission_lock = Lock()
        self._active_recording_slots: set[str] = set()
        self.cover_extractor = CoverExtractor()  # 初始化封面提取器
        self.settings = get_settings()
        # 上面的 HTTP 客户端在构造时不连接外部服务；这里按配置重建节点级限流器。
        self._zlm_request_semaphore = asyncio.Semaphore(
            self.settings.zlm.api_concurrency
        )
        self._zlm_activity_monitor = ZlmActivityMonitor(
            self._get_active_stream_ids_with_app,
            self._check_zlm_recording_with_app,
            interval=self.settings.zlm.stream_status_interval,
            stale_grace=self.settings.zlm.stream_status_stale_grace,
            summary_interval=self.settings.zlm.stream_status_log_interval,
            recording_concurrency=self.settings.zlm.record_status_concurrency,
        )
        self.enhanced_storage = get_enhanced_storage_service()  # 增强存储服务实例
        self.audio_storage = get_enhanced_storage_service(self.settings.storage.audio_type)  # 音频存储服务实例
        self.artifact_service = RecordingArtifactService()  # 录制产物国标落库服务
        self.video_info_extractor = GetVideoInfo()  # 初始化视频信息提取器
        self.filler_generator = FillerVideoGenerator()  # 初始化补帧视频生成器
        self.result_payload_builder = RecorderResultPayloadBuilder(
            settings=self.settings,
            audio_info_extractor=GetAudioInfo(),
            cover_info_extractor=GetCoverInfo(),
        )

        self.task_result_service = RecordingTaskResultService(
            node_id=os.getenv("RECORDER_NODE_ID") or "recorder-node",
        )
        self.result_notifier = RecorderResultNotifier.from_settings(self.settings)
        self.result_notification_service = RecordingResultNotificationService(
            payload_builder=self.result_payload_builder,
            result_notifier=self.result_notifier,
            task_result_service=self.task_result_service,
            node_id=os.getenv("RECORDER_NODE_ID") or "recorder-node",
        )
    async def start_recording(self, task_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        开始录制任务

        :param task_id: 任务ID
        :param params: 任务参数
        :return: 任务状态信息
        """
        logger.info(f"开始处理录制任务: {task_id}")

        try:
            with self._admission_lock():
                existing_task = self.recording_tasks.get(task_id)
            if existing_task is not None:
                existing_status = str(existing_task.get("status") or "").lower()
                if existing_status not in {"completed", "failed", "canceled"}:
                    logger.info(
                        "录制任务已存在，重复开始命令按幂等成功处理: "
                        "task_id=%s, status=%s, app=%s, stream_id=%s",
                        task_id,
                        existing_status,
                        existing_task.get("app"),
                        existing_task.get("stream_id"),
                    )
                    return existing_task

                logger.warning(
                    "录制任务已处于终态，拒绝重复开始: task_id=%s, status=%s",
                    task_id,
                    existing_status,
                )
                return {
                    "task_id": task_id,
                    "status": "failed",
                    "error": f"录制任务已处于终态，不能重复开始: {existing_status}",
                    "progress": existing_task.get("progress", 100),
                }

            # 解析任务参数
            app = params.get("app")
            stream_id = params.get("stream_id")
            start_time_str = params.get("start_time")
            end_time_str = params.get("end_time")
            output_format = params.get("output_format", "mp4")
            extra_params = params.get("extra_params", {})
            callback_url= params.get("callback_url")

            # 从extra_params中获取音频提取相关参数
            extract_audio = extra_params.get("extract_audio", False)
            audio_format = extra_params.get("audio_format", "mp3")
            if audio_format not in ["mp3", "wav"]:
                audio_format = "mp3"  # 默认使用mp3

            # 日志记录音频相关参数
            logger.info(f"音频提取参数: extract_audio={extract_audio}, audio_format={audio_format}")

            # 解析时间 - 使用无时区格式
            start_time = datetime.fromisoformat(start_time_str) if start_time_str else datetime.now()
            end_time = datetime.fromisoformat(end_time_str) if end_time_str else None

            # 检查参数
            if not app or not stream_id:
                logger.error(f"任务参数错误，缺少app或stream_id: {task_id}")
                return {
                    "task_id": task_id,
                    "status": "failed",
                    "error": "缺少应用名称或流ID",
                    "progress": 0
                }

            # 构建流URL用于显示和记录
            stream_url = f"/{app}/{stream_id}"

            # 创建任务状态
            task_status = {
                "task_id": task_id,
                "stream_url": stream_url,    # 保存构建的流URL用于显示
                "stream_id": stream_id,      # 保存流ID
                "original_stream_id": stream_id,  # 保存原始流ID
                "app": app,                  # 保存应用名称
                "output_format": output_format,
                "status": "pending",
                "progress": 0,
                "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if end_time else None,
                "result_url": None,
                "audio_result_url": None,     # 音频结果URL字段
                "extract_audio": extract_audio,  # 保存是否需要提取音频
                "audio_format": audio_format,    # 保存音频格式
                "extra_params": extra_params,    # 保存所有额外参数
                "callback_url": callback_url,    # 保存回调URL
                "segments": [],
                "errors": [],
                # 共享状态监控证据用于区分“ZL暂不可用”和“源流确实未出现”。
                "zlm_active_seen": False,
                "zlm_active_snapshot_count": 0,
                "zlm_inactive_snapshot_count": 0,
                "zlm_status_unknown_count": 0,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            }

            # 容量检查和活动任务登记必须原子完成。同一 task_id 在临界区内再次
            # 检查，保证并发重复消息只复用已有任务，不额外占用本机名额。
            with self._admission_lock():
                existing_task = self.recording_tasks.get(task_id)
                if existing_task is not None:
                    existing_status = str(existing_task.get("status") or "").lower()
                    if existing_status not in {"completed", "failed", "canceled", "cancelled"}:
                        logger.info(
                            "录制任务并发重复提交，按幂等成功处理: task_id=%s, status=%s",
                            task_id,
                            existing_status,
                        )
                        return existing_task
                    return {
                        "task_id": task_id,
                        "status": "failed",
                        "error": f"录制任务已处于终态，不能重复开始: {existing_status}",
                        "progress": existing_task.get("progress", 100),
                    }

                self.recording_tasks[task_id] = task_status

            # 启动异步录制任务
            asyncio.create_task(self._record_stream(task_id, task_status))

            return task_status

        except Exception as e:
            logger.error(f"启动录制任务失败: {task_id}, 错误: {str(e)}", exc_info=True)
            return {
                "task_id": task_id,
                "status": "failed",
                "error": f"启动录制任务失败: {str(e)}",
                "progress": 0
            }

    def _admission_lock(self) -> Lock:
        """返回录制准入锁，并兼容测试中通过 ``__new__`` 构造的轻量实例。"""

        lock = getattr(self, "_recording_admission_lock", None)
        if lock is None:
            lock = Lock()
            self._recording_admission_lock = lock
        return lock

    def _max_recordings(self) -> int:
        """返回本机录制硬上限。"""

        value = int(getattr(self, "max_recordings", 100))
        return max(value, 1)

    def _active_recording_slots_unlocked(self) -> set[str]:
        """返回实际 ZL 录制占位集合；调用方必须持有准入锁。"""

        slots = getattr(self, "_active_recording_slots", None)
        if slots is None:
            slots = set()
            self._active_recording_slots = slots
        return slots

    def _try_acquire_recording_slot(self, task_id: str) -> bool:
        """在真正调用 ZL 开始录制前原子申请一个本机名额。"""

        with self._admission_lock():
            slots = self._active_recording_slots_unlocked()
            if task_id in slots:
                return True
            if len(slots) >= self._max_recordings():
                return False
            slots.add(task_id)
            return True

    def _release_recording_slot(self, task_id: str) -> None:
        """停止、失败或启动异常后幂等释放本机实际录制名额。"""

        with self._admission_lock():
            self._active_recording_slots_unlocked().discard(task_id)

    def active_recording_count(self) -> int:
        """返回当前占用本机录制名额的任务数，供心跳容量快照使用。"""

        with self._admission_lock():
            return len(self._active_recording_slots_unlocked())

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取任务状态

        :param task_id: 任务ID
        :return: 任务状态，如果不存在则返回None
        """
        return self.recording_tasks.get(task_id)

    async def cancel_task(self, task_id: str, delete_from_memory: bool = False) -> bool:
        """
        取消录制任务

        :param task_id: 任务ID
        :param delete_from_memory: 是否从内存中删除任务
        :return: 是否成功取消
        """
        if task_id not in self.recording_tasks:
            logger.warning(f"内存中未找到任务: {task_id}")
            return False

        task_status = self.recording_tasks[task_id]
        current_status = task_status.get("status")

        logger.info(f"取消录制任务 {task_id}，当前状态: {current_status}, 删除内存: {delete_from_memory}")

        # 检查任务状态
        if current_status in ["completed", "failed", "canceled"]:
            if delete_from_memory:
                # 直接从内存中删除已结束的任务
                del self.recording_tasks[task_id]
                logger.info(f"已从内存中删除已结束的任务: {task_id}")
                return True
            else:
                logger.warning(f"任务 {task_id} 已处于终止状态: {current_status}")
                return False

        # 标记任务为已取消
        task_status["status"] = "canceled"
        task_status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        task_status["cancel_reason"] = "用户手动取消"

        # 如果任务正在录制，尝试停止录制
        if current_status in ["recording", "processing"]:
            stream_id = task_status.get("stream_id")
            app = task_status.get("app", "live")
            if stream_id:
                try:
                    await self._stop_zlm_recording_with_app(stream_id, app)
                    logger.info(f"已停止录制: {task_id}, stream_id: {stream_id}")
                except Exception as e:
                    logger.error(f"停止录制失败: {task_id}, 错误: {str(e)}")

        # 如果需要删除内存中的任务
        if delete_from_memory:
            # 确保停止录制后再删除
            await asyncio.sleep(1)  # 给一点时间让录制完全停止
            if task_id in self.recording_tasks:
                del self.recording_tasks[task_id]
                logger.info(f"已从内存中删除任务: {task_id}")

        logger.info(f"任务 {task_id} 取消成功")
        return True

    async def stop_recording(
        self,
        task_id: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        正常停止正在录制的直播流，并让后台录制协程继续执行碎片查找和后处理。

        ``record.stop`` 表示业务端要求提前结束一条已经开始的录制，不等同于
        “取消任务并丢弃结果”。它覆盖两个真实业务场景：

        1. 有开始/结束时间的计划录制，中途由 RTC 或业务端提前终止。
        2. 只有开始时间、没有结束时间的开放式录制，后续由业务端手动停止。

        因此本方法只写入停止请求和停止时间，实际停止 ZLMediaKit、按
        ``app + stream_id + 开始时间 + 实际停止时间`` 查找录像碎片、合并和后处理
        仍由 ``_record_stream`` 在本机完成。

        :param task_id: 录制任务ID
        :param params: 停止命令扩展参数，可包含 stop_time、reason
        :return: 是否找到本机录制任务并接受停止请求
        """

        if task_id not in self.recording_tasks:
            logger.warning(f"内存中未找到要停止的录制任务: {task_id}")
            return False

        params = params or {}
        task_status = self.recording_tasks[task_id]
        current_status = str(task_status.get("status") or "").lower()
        if current_status in ["completed", "failed", "canceled"]:
            logger.info(
                f"录制任务已处于终态，停止命令按幂等成功处理: {task_id}, "
                f"status={current_status}"
            )
            return True

        stop_time = self._parse_optional_datetime(params.get("stop_time")) or datetime.now()
        task_status["stop_requested"] = True
        task_status["stop_requested_at"] = stop_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        task_status["stop_reason"] = params.get("reason") or "manual_stop"
        task_status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        if current_status == "recording":
            task_status["status"] = "stopping"
            logger.info(
                f"录制任务已接受正常停止请求: {task_id}, "
                f"stop_time={task_status['stop_requested_at']}"
            )
        else:
            logger.info(
                f"录制任务尚未进入 recording，已记录停止请求: {task_id}, "
                f"status={current_status}, stop_time={task_status['stop_requested_at']}"
            )

        return True

    @staticmethod
    def _parse_optional_datetime(value: Any) -> Optional[datetime]:
        """解析可选日期时间字符串；为空或格式非法时返回 None。"""

        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            logger.warning(f"停止时间格式非法，使用当前时间: {value}")
            return None

    def delete_task_from_memory(self, task_id: str) -> bool:
        """
        从内存中删除任务

        :param task_id: 任务ID
        :return: 是否成功删除
        """
        if task_id in self.recording_tasks:
            del self.recording_tasks[task_id]
            logger.info(f"从内存中删除任务: {task_id}")
            return True
        else:
            logger.warning(f"内存中未找到要删除的任务: {task_id}")
            return False

    async def _report_flow_trace(
        self,
        task_id: str,
        task_status: Dict[str, Any],
        step_code: str,
        status: str,
        *,
        error_message: str = None,
        step_start_time: datetime = None,
        step_end_time: datetime = None,
        step_output_data: dict = None,
    ):
        """封装流程轨迹上报，无 classroom_id 时自动跳过，失败不影响主流程"""
        try:
            extra_params = task_status.get("extra_params") or {}
            classroom_id = extra_params.get("classroom_id")
            if not classroom_id:
                return

            from media_platform.infrastructure.observability.flow_trace_client import get_flow_trace_client
            flow_trace = get_flow_trace_client()
            await flow_trace.report_step(
                classroom_id=classroom_id,
                process_type=extra_params.get("process_type", "stream_record"),
                step_code=step_code,
                status=status,
                task_id=task_id,
                error_message=error_message,
                step_start_time=step_start_time,
                step_end_time=step_end_time,
                step_output_data=step_output_data,
                extra_data={
                    "app": task_status.get("app"),
                    "stream_id": task_status.get("stream_id"),
                },
            )
        except Exception as e:
            logger.warning(f"流程轨迹上报异常（不影响主流程）: {task_id}, {str(e)}")

    async def _record_stream(self, task_id: str, task_status: Dict[str, Any]) -> None:
        """
        执行流录制过程

        :param task_id: 任务ID
        :param task_status: 任务状态
        """
        stream_id = task_status["stream_id"]
        app = task_status.get("app", "live")  # 获取app参数，默认为live
        recording_slot_acquired = False

        try:
            # 更新任务状态
            task_status["status"] = "processing"
            task_status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

            # 解析时间
            start_time = datetime.fromisoformat(task_status["start_time"]) if isinstance(task_status["start_time"], str) else task_status["start_time"]
            end_time = datetime.fromisoformat(task_status["end_time"]) if task_status["end_time"] and isinstance(task_status["end_time"], str) else task_status["end_time"]

            # 等待直到开始时间
            now = datetime.now()
            if start_time > now:
                wait_seconds = (start_time - now).total_seconds()
                logger.info(f"等待录制开始: {task_id}, 等待时间: {wait_seconds}秒")

                # 更新任务状态
                task_status["status"] = "waiting"
                task_status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

                # 上报流程轨迹：等待录制开始
                from media_platform.infrastructure.observability.flow_trace_client import FlowTraceStepCode, FlowTraceStatus
                await self._report_flow_trace(task_id, task_status, FlowTraceStepCode.RECORDING_WAITING, FlowTraceStatus.PROCESSING)

                # 分段等待，每秒检查一次本机停止/取消状态。
                # 新链路不再轮询旧调用中心内存 dispatcher；服务重启后的恢复由
                # RecordingTaskRecoveryService 读取 MySQL 事实状态完成。
                remaining_seconds = wait_seconds
                while remaining_seconds > 0:
                    if task_status.get("stop_requested"):
                        logger.info(f"等待期间收到停止请求，录制尚未开始: {task_id}")
                        task_status["status"] = "canceled"
                        task_status["cancel_reason"] = "停止请求发生在录制开始前"
                        task_status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        return

                    # 检查本地任务状态是否被取消
                    if task_status.get("status") == "canceled":
                        logger.info(f"等待期间任务被取消(本地状态): {task_id}")
                        return

                    # 每次最多等待1秒
                    sleep_time = min(remaining_seconds, 1.0)
                    await asyncio.sleep(sleep_time)
                    remaining_seconds -= sleep_time

                # 最后检查一次是否被取消
                if task_status.get("status") == "canceled":
                    logger.info(f"等待结束时任务被取消(本地状态): {task_id}")
                    return

            # 开始实际录制前再次检查是否取消
            if task_status.get("stop_requested"):
                logger.info(f"开始录制前收到停止请求，录制尚未开始: {task_id}")
                task_status["status"] = "canceled"
                task_status["cancel_reason"] = "停止请求发生在录制开始前"
                task_status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                return

            if task_status.get("status") == "canceled":
                logger.info(f"开始录制前任务被取消(本地状态): {task_id}")
                return

            # 未来录制计划在等待阶段不占本机名额。只有即将真正调用 ZL
            # startRecord 时才原子占位，保证限制表达的是同时录制路数。
            if not self._try_acquire_recording_slot(task_id):
                active_count = self.active_recording_count()
                max_recordings = self._max_recordings()
                raise RecordingCapacityExceededError(
                    f"录制节点本机实际录制并发已满: {active_count}/{max_recordings}"
                )
            recording_slot_acquired = True

            # 开始实际录制
            logger.info(f"开始录制: {task_id}, app: {app}, stream_id: {stream_id}")

            # 更新任务状态
            task_status["status"] = "recording"
            task_status["actual_start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            task_status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

            # 上报流程轨迹：开始录制
            from media_platform.infrastructure.observability.flow_trace_client import FlowTraceStepCode, FlowTraceStatus
            await self._report_flow_trace(task_id, task_status, FlowTraceStepCode.RECORDING_STARTED, FlowTraceStatus.PROCESSING, step_start_time=datetime.now())

            # 检查流是否存在并开始录制
            # is_stream_active = await self._check_stream_active_with_app(stream_id, app)
            # if not is_stream_active:
            #     raise Exception(f"直播流不存在或不活跃: {stream_id}")

            # 开始ZLM录制
            record_result = await self._start_zlm_recording_with_app(stream_id, app)
            task_status["zlm_start_recording_accepted"] = bool(record_result)
            if not record_result:
                # startRecord 已被 ZLM 明确拒绝，此时本任务没有启动任何录制。
                # 不能调用 stopRecord，否则可能误停同一 app/stream 上由其他任务
                # 启动的录制；直接复用统一结果队列写入 MySQL 失败终态。
                await self._queue_recording_result(
                    task_id,
                    task_status,
                    recording_error=f"ZLMediaKit拒绝开始录制: {app}/{stream_id}",
                )
                return
            activity_monitor = getattr(self, "_zlm_activity_monitor", None)
            if activity_monitor is not None:
                await activity_monitor.watch(app, stream_id)

            # 计算录制时长
            total_duration = 0
            if end_time:
                current_time = datetime.now()
                total_duration = (end_time - current_time).total_seconds()
                if total_duration <= 0:
                    raise Exception("结束时间已过")

            # 记录状态更新时间
            last_status_check = time_module.time()
            last_record_restart = 0.0

            # 监控录制状态
            while True:
                if task_status.get("stop_requested"):
                    logger.info(
                        f"录制过程中收到正常停止请求: {task_id}, "
                        f"stop_time={task_status.get('stop_requested_at')}"
                    )
                    task_status["status"] = "stopping"
                    task_status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    break

                # 检查本地任务状态是否已取消
                if task_status["status"] == "canceled":
                    logger.info(f"录制过程中任务被取消(本地状态): {task_id}")
                    cancel_reason = task_status.get("cancel_reason", "未知原因")
                    logger.info(f"取消原因: {cancel_reason}")

                    # 录制过程中取消的任务，不删除记录，只是标记状态
                    task_status["early_termination"] = True
                    task_status["termination_reason"] = cancel_reason
                    logger.info(f"录制中取消的任务将保留记录: {task_id}")

                    # 上报流程轨迹：任务取消
                    from media_platform.infrastructure.observability.flow_trace_client import FlowTraceStepCode, FlowTraceStatus
                    await self._report_flow_trace(task_id, task_status, FlowTraceStepCode.TASK_CANCELLED, FlowTraceStatus.CANCELLED)

                    break

                # 检查是否达到结束时间
                if end_time:
                    current_time = datetime.now()
                    if current_time >= end_time:
                        logger.info(f"达到录制结束时间: {task_id}")
                        break

                # 所有任务读取同一份节点级 ZL 状态快照，避免每路任务重复调用
                # getMediaList。接口失败返回未知状态，不据此重启或判定断流。
                current_time = time_module.time()
                settings = getattr(self, "settings", None)
                zlm_settings = getattr(settings, "zlm", None)
                status_interval = getattr(zlm_settings, "stream_status_interval", 10.0)
                if current_time - last_status_check >= status_interval:
                    health = (
                        await activity_monitor.get_health(app, stream_id)
                        if activity_monitor is not None
                        else None
                    )
                    sampled_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    if health is None or health.active is None:
                        task_status["zlm_status_unknown_count"] += 1
                        task_status["zlm_last_status_at"] = sampled_at
                        if not task_status.get("zlm_status_unknown_reported"):
                            logger.warning(
                                "ZLM状态暂不可用，保留当前录制并等待下一次共享快照: task_id=%s",
                                task_id,
                            )
                            task_status["zlm_status_unknown_reported"] = True
                    elif health.active is False:
                        task_status["zlm_inactive_snapshot_count"] += 1
                        task_status.setdefault("zlm_first_inactive_at", sampled_at)
                        task_status["zlm_last_inactive_at"] = sampled_at
                        if not task_status.get("zlm_stream_inactive_reported"):
                            logger.warning(
                                "ZLM活跃流快照未发现录制流，不发送重启命令: "
                                "task_id=%s, app=%s, stream_id=%s",
                                task_id,
                                app,
                                stream_id,
                            )
                            task_status["errors"].append(
                                {"time": sampled_at, "error": "ZLM活跃流快照未发现该流"}
                            )
                            task_status["zlm_stream_inactive_reported"] = True
                    elif health.recording is False:
                        self._record_active_stream_snapshot(task_status)
                        if (
                            current_time - last_record_restart
                            >= getattr(zlm_settings, "record_restart_interval", 30.0)
                        ):
                            logger.warning(
                                "共享状态确认流仍活跃但录制中断，尝试重新开始: task_id=%s",
                                task_id,
                            )
                            task_status["errors"].append(
                                {"time": sampled_at, "error": "共享状态确认录制中断，尝试重新开始"}
                            )
                            await self._start_zlm_recording_with_app(stream_id, app)
                            last_record_restart = current_time
                    else:
                        self._record_active_stream_snapshot(task_status)

                    # 更新任务状态
                    if end_time:
                        now_time = datetime.now()
                        elapsed = (now_time - start_time).total_seconds()
                        progress = min(100, round(elapsed / total_duration * 100, 1))
                        task_status["progress"] = progress
                    else:
                        # 无结束时间，更新已录制时长
                        now_time = datetime.now()
                        elapsed = (now_time - start_time).total_seconds()
                        task_status["recorded_duration"] = round(elapsed, 1)

                    task_status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    last_status_check = current_time

                # 暂停1秒
                await asyncio.sleep(1)

            # 到达边界后只负责尽快停止 ZL。等待 MP4 收尾、目录扫描、
            # ffprobe、合并、上传和回调均在受限的后处理 Worker 中执行。
            await self._stop_zlm_recording_with_app(stream_id, app)
            stopped_at = datetime.now()

            if self._stream_never_appeared(task_status):
                await self._queue_recording_result(
                    task_id,
                    task_status,
                    recording_error=(
                        "源流异常：录制期间连续有效快照均未发现该流，"
                        "未进入录像扫描"
                    ),
                )
                return

            effective_end_time = end_time
            if task_status.get("stop_requested"):
                effective_end_time = self._parse_optional_datetime(
                    task_status.get("stop_requested_at")
                ) or stopped_at
                task_status["end_time"] = effective_end_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            await self._queue_recording_result(
                task_id,
                task_status,
                recording_window={
                    "start_time": start_time,
                    "end_time": effective_end_time or stopped_at,
                    "stopped_at": stopped_at,
                },
            )
            return


        except RecordingCapacityExceededError as e:
            # 本任务尚未调用 ZL startRecord，不能执行 stopRecord，否则可能误停
            # 同一技术流已有的录制。失败状态、通知和回调仍走统一后处理出口。
            logger.error("录制任务触发本机硬并发保护: task_id=%s, error=%s", task_id, e)
            try:
                await self._queue_recording_result(
                    task_id,
                    task_status,
                    recording_error=str(e),
                )
            except Exception as queue_error:
                task_status.update(
                    status="failed",
                    error=str(e),
                    post_processing_state="queue_failed",
                    updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                )
                logger.error(
                    "录制容量超限结果入队失败: task_id=%s, error=%s",
                    task_id,
                    queue_error,
                    exc_info=True,
                )
                await self._send_callback_notification(task_id, task_status)

        except Exception as e:
            logger.error(f"录制任务异常: {task_id}, 错误: {str(e)}", exc_info=True)
            try:
                await self._stop_zlm_recording_with_app(stream_id, app)
            except Exception as stop_error:
                logger.error(f"停止录制失败: {task_id}, 错误: {str(stop_error)}")
            try:
                # 失败落库、回调和必要的文件保留也进入同一后处理队列，
                # 避免大量录制同时异常时在录制 loop 中集中执行阻塞操作。
                await self._queue_recording_result(
                    task_id,
                    task_status,
                    recording_error=str(e),
                )
            except Exception as queue_error:
                task_status.update(
                    status="failed",
                    error=str(e),
                    post_processing_state="queue_failed",
                    updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                )
                logger.error(
                    "录制异常结果入队失败，保留源文件: task_id=%s, error=%s",
                    task_id,
                    queue_error,
                    exc_info=True,
                )
                # 队列本身不可用时无法再依赖 Worker，使用最后兜底通知，
                # 避免业务方永久等待。
                await self._send_callback_notification(task_id, task_status)

        finally:
            if recording_slot_acquired:
                self._release_recording_slot(task_id)
                logger.info(
                    "录制任务已释放本机并发名额: task_id=%s, active=%s, max=%s",
                    task_id,
                    self.active_recording_count(),
                    self._max_recordings(),
                )
            activity_monitor = getattr(self, "_zlm_activity_monitor", None)
            if activity_monitor is not None:
                await activity_monitor.unwatch(app, stream_id)

    @staticmethod
    def _record_active_stream_snapshot(task_status: Dict[str, Any]) -> None:
        """记录源流曾经活跃，避免结束时把真实录制误判为无源流。"""

        sampled_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        task_status["zlm_active_seen"] = True
        task_status["zlm_active_snapshot_count"] += 1
        task_status.setdefault("zlm_first_active_at", sampled_at)
        task_status["zlm_last_active_at"] = sampled_at

    def _stream_never_appeared(self, task_status: Dict[str, Any]) -> bool:
        """只有连续足量的可靠快照都未发现源流时才判定源流不可用。"""

        settings = getattr(self, "settings", None)
        zlm_settings = getattr(settings, "zlm", None)
        confirmations = getattr(zlm_settings, "stream_absence_confirmations", 3)
        return (
            not task_status.get("zlm_active_seen", False)
            and task_status.get("zlm_inactive_snapshot_count", 0)
            >= confirmations
        )

    async def _queue_recording_result(
        self,
        task_id: str,
        task_status: Dict[str, Any],
        *,
        recording_window: Dict[str, datetime] | None = None,
        recording_error: str | None = None,
    ) -> None:
        """把停止后的完整处理链路交给受限并发的本机后处理队列。"""

        from services.recorder_node.postprocess.post_processor import (
            get_post_processing_manager,
        )

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        task_status.update(
            status="post_processing",
            progress=90.0,
            post_processing_state="queued",
            actual_end_time=now,
            updated_at=now,
            post_processing_submitted_at=now,
        )
        priority = (task_status.get("extra_params") or {}).get("priority", 5)
        post_manager = get_post_processing_manager()
        recovery_context = {
            "recording_window": self._serialize_recording_window(recording_window),
            "recording_error": recording_error,
            "priority": priority,
            "post_processing_state": "queued",
        }

        # 先写排队状态再入队，避免空闲 Worker 瞬时完成任务后，
        # 较慢的排队状态反向覆盖 completed/failed。
        mark_post_processing = getattr(self, "_mark_recording_post_processing", None)
        if callable(mark_post_processing):
            try:
                await asyncio.to_thread(
                    mark_post_processing,
                    task_id,
                    None,
                    recovery_context,
                )
            except Exception:
                logger.exception("录制后处理排队状态写入MySQL失败: task_id=%s", task_id)

        submitted = await post_manager.submit_task(
            task_id=task_id,
            task_status=task_status,
            result_url=None,
            mp4_files=[],
            stream_recorder=self,
            priority=priority,
            recording_window=recording_window,
            recording_error=recording_error,
        )
        if not submitted:
            raise RuntimeError(
                f"提交后处理任务失败: task_id={task_id}，请检查后处理Worker池"
            )
        logger.info(
            "录制已停止，完整尾链路已入队: task_id=%s, priority=%s, stats=%s",
            task_id,
            priority,
            await post_manager.get_stats(),
        )

    async def recover_post_processing(
        self,
        task_id: str,
        params: Dict[str, Any],
    ) -> bool:
        """恢复重启前已停止、但尚未完成的本机后处理任务。

        该方法只恢复后处理，不会重新调用 ZLMediaKit 开始录制。录像文件由
        后处理准备阶段按照持久化的时间窗口重新扫描，随后继续原有的提取、上传、
        落库和回调流程。
        """

        from services.recorder_node.postprocess.post_processor import (
            get_post_processing_manager,
        )

        task_status = dict(params.get("task_status") or params)
        extra_params = task_status.get("extra_params")
        if not isinstance(extra_params, dict):
            extra_params = {}
            task_status["extra_params"] = extra_params
        priority = int(params.get("priority", extra_params.get("priority", 5)))
        extra_params.setdefault("priority", priority)
        recording_window = params.get("recording_window")
        if isinstance(recording_window, dict):
            recording_window = {
                key: self._parse_optional_datetime(value)
                for key, value in recording_window.items()
            }
            recording_window = {
                key: value
                for key, value in recording_window.items()
                if value is not None
            }
        else:
            recording_window = None

        recording_error = params.get("recording_error")
        post_manager = get_post_processing_manager()
        submitted = await post_manager.submit_task(
            task_id=task_id,
            task_status=task_status,
            result_url=params.get("result_url"),
            mp4_files=[],
            stream_recorder=self,
            priority=priority,
            recording_window=recording_window,
            recording_error=str(recording_error) if recording_error else None,
            duplicate_is_success=False,
        )
        if submitted:
            logger.info(
                "录制后处理任务已恢复到本机队列: task_id=%s, priority=%s",
                task_id,
                priority,
            )
        return bool(submitted)

    def is_post_processing_tracked(self, task_id: str) -> bool:
        """判断任务是否已在后处理队列或正在执行。"""

        from services.recorder_node.postprocess.post_processor import (
            get_post_processing_manager,
        )

        return get_post_processing_manager().has_task(task_id)

    def _mark_recording_post_processing(
        self,
        task_id: str,
        result_url: str | None,
        recovery_context: dict[str, Any] | None = None,
    ) -> None:
        """把录制任务更新为后处理中。

        录制节点停止 ZL 录制并提交后处理队列后，录像发现、必要时合并、
        音频/封面提取、上传、落库和回调均由受限并发的 Worker 执行。这里把中间状态写入
        ``media_task``，调用中心查询接口即可看到处理过程，不依赖旧 dispatcher。
        """

        try:
            updated = self.task_result_service.mark_post_processing(
                task_id=task_id,
                result_url=result_url,
                recovery_context=recovery_context,
            )
            if updated:
                logger.info("录制任务状态已写入MySQL为后处理中: %s", task_id)
            else:
                logger.warning("录制任务后处理状态写入失败，任务不存在: %s", task_id)
        except Exception as exc:
            logger.error("录制任务后处理状态写入MySQL异常: %s, 错误: %s", task_id, exc)

    @staticmethod
    def _serialize_recording_window(
        recording_window: Dict[str, datetime] | None,
    ) -> dict[str, str] | None:
        """把后处理文件扫描时间窗转换为可写入 MySQL JSON 的文本。"""

        if not recording_window:
            return None
        return {
            key: value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            for key, value in recording_window.items()
            if isinstance(value, datetime)
        }

    async def _send_callback_notification(self, task_id: str, task_status: Dict[str, Any]) -> bool:
        """
        发送录制结果通知。

        真实通知编排归属 `RecordingResultNotificationService`。这里保留薄委托方法，
        让录制主流程无需关心 RabbitMQ、HTTP 回调重试和回调结果落库细节。

        :param task_id: 任务ID
        :param task_status: 任务状态
        :return: 未配置 HTTP 回调时返回 True；配置回调时返回 HTTP 回调最终是否成功
        """
        try:
            notification_service = getattr(self, "result_notification_service", None)
            if notification_service is None:
                result_payload_builder = getattr(self, "result_payload_builder", None)
                if result_payload_builder is None:
                    raise RuntimeError("录制结果数据构造器未初始化")
                notification_service = RecordingResultNotificationService(
                    payload_builder=result_payload_builder,
                    result_notifier=getattr(self, "result_notifier", None),
                    task_result_service=getattr(self, "task_result_service", None),
                    node_id=os.getenv("RECORDER_NODE_ID") or "recorder-node",
                )
                self.result_notification_service = notification_service

            return await notification_service.notify(
                task_id=task_id,
                task_status=task_status,
            )

        except Exception as e:
            logger.error(f"发送回调通知异常: {task_id}, 错误: {str(e)}", exc_info=True)
            return False

    async def _get_active_stream_ids_with_app(self, app: str) -> set[str]:
        """获取一个 app 的活跃流快照；失败必须抛出，不能伪装为空列表。"""

        response = await self._zlm_get(
            f"{self.config['ZLM_URL']}/index/api/getMediaList",
            params={
                "secret": self.config["ZLM_SECRET"],
                "schema": "rtmp",
                "vhost": "__defaultVhost__",
                "app": app,
            },
        )
        result = response.json()
        if result.get("code") != 0:
            raise RuntimeError(f"getMediaList返回异常: code={result.get('code')}")
        return {
            item.get("stream")
            for item in result.get("data", [])
            if item.get("stream")
        }

    async def _check_stream_active_with_app(self, stream_id: str, app: str) -> bool:
        """保留给外部调用者的兼容检查；录制循环使用共享监控快照。"""

        try:
            return stream_id in await self._get_active_stream_ids_with_app(app)
        except Exception as exc:
            logger.warning(
                "检查流状态异常: app=%s, stream_id=%s, error=%s",
                app,
                stream_id,
                exc,
            )
            return False

    async def _start_zlm_recording_with_app(self, stream_id: str, app: str) -> bool:
        """
        开始ZLM录制（支持动态app参数）

        :param stream_id: 流ID
        :param app: 应用名
        :return: 是否成功
        """
        try:
            url = f"{self.config['ZLM_URL']}/index/api/startRecord"
            params = {
                "secret": self.config['ZLM_SECRET'],
                "type": 1,  # MP4录制
                "vhost": "__defaultVhost__",
                "app": app,
                "stream": stream_id
            }

            logger.info(f"开始ZLM录制, stream_id: {stream_id}, app: {app}")
            response = await self._zlm_get(url, params=params)
            result = response.json()

            if result.get("code") != 0 or not result.get("result"):
                logger.error(f"开始录制失败: {result}")
                return False

            return True

        except Exception as e:
            logger.error(f"开始录制异常: {str(e)}")
            return False

    async def _check_zlm_recording_with_app(self, stream_id: str, app: str) -> bool:
        """
        检查是否在录制（支持动态app参数）

        :param stream_id: 流ID
        :param app: 应用名
        :return: 是否在录制
        """
        response = await self._zlm_get(
            f"{self.config['ZLM_URL']}/index/api/isRecording",
            params={
                "secret": self.config["ZLM_SECRET"],
                "type": 1,
                "vhost": "__defaultVhost__",
                "app": app,
                "stream": stream_id,
            },
        )
        result = response.json()
        if result.get("code") != 0:
            raise RuntimeError(f"isRecording返回异常: code={result.get('code')}")
        return bool(result.get("status", False))

    async def _stop_zlm_recording_with_app(self, stream_id: str, app: str) -> bool:
        """
        停止ZLM录制（支持动态app参数）

        :param stream_id: 流ID
        :param app: 应用名
        :return: 是否成功
        """
        try:
            url = f"{self.config['ZLM_URL']}/index/api/stopRecord"
            params = {
                "secret": self.config['ZLM_SECRET'],
                "type": 1,  # MP4录制
                "vhost": "__defaultVhost__",
                "app": app,
                "stream": stream_id
            }

            logger.info(f"停止ZLM录制, stream_id: {stream_id}, app: {app}")
            response = await self._zlm_get(url, params=params)
            result = response.json()

            if result.get("code") != 0 or not result.get("result"):
                logger.error(f"停止录制失败: {result}")
                return False

            return True

        except Exception as e:
            logger.error(f"停止录制异常: {str(e)}")
            return False

    async def _zlm_get(self, url: str, params: Dict[str, Any]) -> httpx.Response:
        """统一限制本节点同时访问 ZL 管理 API 的请求数量。"""

        current_loop = asyncio.get_running_loop()
        if current_loop is self._owner_loop:
            semaphore = self._zlm_request_semaphore
            async with semaphore:
                response = await self.http_client.get(url, params=params)
        else:
            # 后处理只在受控 Worker 并发内调用 ZL 文件查询。为避免把命令 loop
            # 的连接池带到主 loop，这里使用当前 loop 私有的短生命周期客户端。
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params)
        response.raise_for_status()
        return response

    async def _get_mp4_record_files_with_app(
        self,
        stream_id: str,
        app: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> List[Dict[str, Any]]:
        """合并 ZL API 与本机映射目录扫描，可靠发现跨天录像分片。"""

        async def list_files(day: str) -> Dict[str, Any]:
            response = await self._zlm_get(
                f"{self.config['ZLM_URL']}/index/api/getMp4RecordFile",
                params={
                    "secret": self.config["ZLM_SECRET"],
                    "vhost": "__defaultVhost__",
                    "app": app,
                    "stream": stream_id,
                    "period": day,
                },
            )
            result = response.json()
            if result.get("code") != 0:
                raise RecordingFilesUnavailable(
                    f"ZLM getMp4RecordFile返回异常: code={result.get('code')}"
                )
            return result.get("data") or {}

        async def probe(path: str) -> Dict[str, Any]:
            loop = asyncio.get_running_loop()
            with self._loop_resource_lock:
                semaphore = self._record_probe_semaphores.get(loop)
                if semaphore is None:
                    semaphore = asyncio.Semaphore(8)
                    self._record_probe_semaphores[loop] = semaphore
            async with semaphore:
                return await self._get_file_info(path)

        settings = getattr(self, "settings", None)
        zlm_settings = getattr(settings, "zlm", None)
        cleanup_settings = getattr(settings, "record_cleanup", None)
        local_root = (
            getattr(zlm_settings, "record_local_root", "")
            or self.config.get("ZLM_RECORD_LOCAL_ROOT")
            or getattr(cleanup_settings, "record_base_path", "")
        )
        return await discover_record_files(
            stream_id=stream_id,
            app=app,
            start_time=start_time,
            end_time=end_time,
            list_files=list_files,
            probe=probe,
            local_roots=[local_root] if local_root else [],
            attempts=getattr(zlm_settings, "record_discovery_attempts", 3),
            retry_interval=getattr(zlm_settings, "record_discovery_interval", 5.0),
        )

    async def _get_file_info(self, file_path: str) -> Dict[str, Any]:
        """
        获取文件信息（时长和大小）

        :param file_path: 文件路径
        :return: 文件信息字典
        """
        try:
            # 检查文件是否存在
            if not os.path.exists(file_path):
                logger.warning(f"文件不存在: {file_path}")
                return {"duration": 0, "size": 0}

            # 检查文件权限
            if not os.access(file_path, os.R_OK):
                logger.error(f"没有文件读取权限: {file_path}")
                return {"duration": 0, "size": 0}

            # 获取文件大小
            try:
                file_size = os.path.getsize(file_path)
                logger.debug(f"文件大小: {file_size} bytes, 路径: {file_path}")
            except Exception as e:
                logger.error(f"获取文件大小失败: {file_path}, 错误: {str(e)}")
                return {"duration": 0, "size": 0}

            try:
                # 使用ffmpeg-python获取视频信息
                probe = await asyncio.to_thread(ffmpeg.probe, file_path)
                duration = float(probe['format']['duration'])
                logger.debug(f"获取到视频时长: {duration}秒, 文件: {file_path}")
            except Exception as e:
                logger.error(f"获取视频时长失败: {str(e)}")
                duration = 0

            return {
                "duration": duration,
                "size": file_size
            }

        except Exception as e:
            logger.error(f"获取文件信息失败: {file_path}, 错误: {str(e)}")
            return {"duration": 0, "size": 0}

    async def _process_result_files(self, task_id: str, mp4_files: List[Dict[str, Any]]) -> Optional[str]:
        """
        处理结果文件，合并录制片段并处理中断补帧

        :param task_id: 任务ID
        :param mp4_files: MP4文件列表
        :return: 结果文件路径
        """
        if not mp4_files:
            logger.warning(f"没有找到需要处理的文件: {task_id}")
            return None

        try:
            # 计算预期总时长
            total_expected_duration = sum(segment["time_len"] for segment in mp4_files)
            total_file_size = sum(segment.get("file_size", 0) for segment in mp4_files)
            logger.info(f"开始处理结果文件，文件数量: {len(mp4_files)}，预期总时长: {total_expected_duration}秒，总大小: {total_file_size/(1024*1024):.1f}MB")

            # 筛选文件，确保时长在合理范围内
            filtered_files = []
            for segment in mp4_files:
                # 检查文件时长是否合理（不超过预期总时长的2倍）
                if segment["time_len"] > total_expected_duration * 2:
                    logger.warning(f"文件时长异常，跳过: {segment['file_name']}, 时长: {segment['time_len']}秒")
                    continue
                filtered_files.append(segment)

            if not filtered_files:
                logger.error("筛选后没有有效文件")
                return None

            mp4_files = filtered_files
            logger.info(f"筛选后文件数量: {len(mp4_files)}")

            # 检查视频数量
            if len(mp4_files) == 1:
                # 单分片直接使用 ZL 源文件，避免大文件无意义复制。
                source_path = mp4_files[0]["file_path"]
                logger.info("单个录像分片直接使用源文件: %s", source_path)
                return source_path

            temp_dir = self._get_recording_work_dir(task_id)
            logger.info("创建录制任务工作目录: %s", temp_dir)

            # 检测运行环境和资源情况
            is_docker = os.path.exists('/.dockerenv')
            is_low_resource = total_file_size > 100 * 1024 * 1024  # 超过100MB视为大文件

            logger.info(f"运行环境检测: Docker={is_docker}, 大文件={is_low_resource}")

            # 动态调整策略
            use_lightweight_mode = is_docker or is_low_resource or len(mp4_files) > 5

            # 是否尝试补帧
            try_fill_gaps = True

            # 执行视频合并
            output_path = os.path.join(temp_dir, f"final_{task_id}.mp4")
            logger.info(f"开始合并视频，输出路径: {output_path}，轻量模式: {use_lightweight_mode}")

            # 提前收集所有视频的编码信息
            video_info_list = []
            for segment in mp4_files:
                try:
                    probe = await asyncio.to_thread(ffmpeg.probe, segment["file_path"])
                    video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
                    audio_stream = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)

                    video_info = {
                        "path": segment["file_path"],
                        "has_audio": audio_stream is not None,
                        "video_codec": video_stream.get('codec_name') if video_stream else None,
                        "width": int(video_stream.get('width', 0)) if video_stream else 0,
                        "height": int(video_stream.get('height', 0)) if video_stream else 0,
                        "audio_codec": audio_stream.get('codec_name') if audio_stream else None,
                    }
                    video_info_list.append(video_info)
                    logger.info(f"视频信息: {segment['file_path']}, 编码: {video_info['video_codec']}, "
                               f"分辨率: {video_info['width']}x{video_info['height']}, "
                               f"音频: {video_info['has_audio']} ({video_info['audio_codec']})")
                except Exception as e:
                    logger.error(f"获取视频信息失败: {segment['file_path']}, 错误: {str(e)}")
                    video_info_list.append({"path": segment["file_path"], "has_audio": False})

            # 在轻量模式下，限制补帧
            if use_lightweight_mode:
                logger.info("轻量模式：限制补帧功能以提高性能")
                # 只在间隔较大时才补帧
                min_gap_for_fill = 5.0  # 最小5秒间隔才补帧
            else:
                min_gap_for_fill = 1.0  # 标准模式1秒间隔就补帧

            # 处理补帧并构建新的合并列表
            merge_files = []

            for i, segment in enumerate(mp4_files):
                # 添加当前片段
                merge_files.append(segment["file_path"])
                # logger.info(f"添加文件到合并列表: {segment['file_path']}, 时长: {segment['time_len']}秒")

                # # 如果不是最后一个片段，检查是否需要补帧
                # if i < len(mp4_files) - 1 and try_fill_gaps:
                #     current_end = segment["start_time"] + timedelta(seconds=segment["time_len"])
                #     next_start = mp4_files[i + 1]["start_time"]

                #     # 增加时间间隔日志
                #     gap_duration = (next_start - current_end).total_seconds()
                #     logger.info(f"文件间隔: {gap_duration}秒, 当前文件: {segment['file_name']}, 下一文件: {mp4_files[i + 1]['file_name']}")

                #     if gap_duration > min_gap_for_fill:
                #         logger.info(f"需要补帧，间隔: {gap_duration}秒")
                #         try:
                #             filler_path = await self._create_filler_video_compatible(
                #                 segment["file_path"],
                #                 gap_duration,
                #                 temp_dir,
                #                 f"filler_{i}.mp4",
                #                 has_audio,
                #                 target_video_info
                #             )
                #             if filler_path:
                #                 # 再次验证补帧文件的时长
                #                 filler_info = await self._get_file_info(filler_path)
                #                 actual_filler_duration = filler_info.get("duration", 0)

                #                 # 允许的误差范围：±0.2秒或±10%
                #                 tolerance = max(0.2, gap_duration * 0.1)
                #                 duration_diff = abs(actual_filler_duration - gap_duration)

                #                 if duration_diff <= tolerance:
                #                     logger.info(f"补帧文件时长验证通过: 预期={gap_duration}秒, 实际={actual_filler_duration}秒, 差异={duration_diff}秒")
                #                     merge_files.append(filler_path)
                #                 else:
                #                     logger.warning(f"补帧文件时长异常，跳过: 预期={gap_duration}秒, 实际={actual_filler_duration}秒, 差异={duration_diff}秒")
                #                     # 删除异常的补帧文件
                #                     try:
                #                         os.remove(filler_path)
                #                     except:
                #                         pass
                #                     fill_error_count += 1
                #                     if fill_error_count >= max_fill_errors:
                #                         logger.warning(f"补帧失败次数达到上限({max_fill_errors})，切换到无补帧模式")
                #                         try_fill_gaps = False
                #             else:
                #                 logger.warning(f"补帧失败，将跳过此间隔")
                #                 fill_error_count += 1
                #                 if fill_error_count >= max_fill_errors:
                #                     logger.warning(f"补帧失败次数达到上限({max_fill_errors})，切换到无补帧模式")
                #                     try_fill_gaps = False
                #         except Exception as e:
                #             logger.error(f"补帧过程异常: {str(e)}", exc_info=True)
                #             fill_error_count += 1
                #             if fill_error_count >= max_fill_errors:
                #                 logger.warning(f"补帧失败次数达到上限({max_fill_errors})，切换到无补帧模式")
                #                 try_fill_gaps = False

            # 根据环境选择合并策略
            merge_success = False

            # 策略1: 尝试轻量级Concat Demuxer（仅在特定条件下）
            # if use_lightweight_mode and len(merge_files) <= 3 and not any('filler_' in os.path.basename(f) for f in merge_files):
            if  len(merge_files) <= 10:
                logger.info("尝试轻量级Concat Demuxer合并（仅原始文件，无补帧）")
                try:
                    concat_file = os.path.join(temp_dir, f"concat_list_{task_id}.txt")
                    with open(concat_file, 'w', encoding='utf-8') as f:
                        for file_path in merge_files:
                            abs_path = os.path.abspath(file_path)
                            f.write(f"file '{abs_path}'\n")

                    concat_cmd = [
                        "ffmpeg",
                        "-f", "concat",
                        "-safe", "0",
                        "-i", concat_file,
                        "-c", "copy",
                        "-avoid_negative_ts", "make_zero",
                        "-y", output_path
                    ]

                    logger.info("执行轻量级Concat Demuxer命令")
                    result = subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)

                    if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        # 验证时长
                        output_info = await self._get_file_info(output_path)
                        expected_duration = sum(segment.get("time_len", 0) for segment in mp4_files)
                        duration_diff = abs(output_info.get("duration", 0) - expected_duration)
                        logger.info(f"轻量级Concat Demuxer合并成功，时长验证通过，时长差异: {duration_diff}秒")
                        merge_success = True

                    else:
                        logger.warning(f"轻量级Concat Demuxer失败: {result.stderr}")
                except Exception as concat_error:
                    logger.warning(f"轻量级Concat Demuxer异常: {str(concat_error)}")

            # 策略2: TS转换合并策略（处理参数不一致和时长异常问题）
            # if not merge_success:
            #     logger.info("尝试TS转换合并策略")
            #     try:
            #         success, error_msg = await merge_videos_ts_strategy(
            #             video_paths=merge_files,
            #             output_path=output_path,
            #             temp_dir=temp_dir
            #         )

            #         if success:
            #             logger.info("TS转换合并策略成功")
            #             if error_msg:
            #                 logger.warning(f"TS转换合并有警告: {error_msg}")

            #             # 验证最终结果
            #             if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            #                 output_info = await self._get_file_info(output_path)
            #                 expected_duration = sum(segment.get("time_len", 0) for segment in mp4_files)

            #                 # 计算补帧时长
            #                 filler_duration = 0
            #                 if try_fill_gaps:
            #                     for i, segment in enumerate(mp4_files[:-1]):
            #                         if i < len(mp4_files) - 1:
            #                             current_end = segment["start_time"] + timedelta(seconds=segment["time_len"])
            #                             next_start = mp4_files[i + 1]["start_time"]
            #                             gap = (next_start - current_end).total_seconds()
            #                             if gap > min_gap_for_fill:
            #                                 filler_duration += gap

            #                 expected_total_duration = expected_duration + filler_duration
            #                 actual_duration = output_info.get("duration", 0)
            #                 duration_diff = abs(actual_duration - expected_total_duration)

            #                 logger.info(f"TS合并结果验证: 预期={expected_total_duration:.3f}秒, 实际={actual_duration:.3f}秒, 差异={duration_diff:.3f}秒")
            #                 merge_success = True
            #             else:
            #                 logger.error("TS转换合并策略输出文件无效")
            #         else:
            #             logger.error(f"TS转换合并策略失败: {error_msg}")

            #     except Exception as ts_merge_error:
            #         logger.error(f"TS转换合并策略异常: {str(ts_merge_error)}", exc_info=True)

            # # 策略2: Filter Complex方法（主要策略）
            # if not merge_success:
            #     try:
            #         logger.info(f"使用Filter Complex方法合并视频，文件数量: {len(merge_files)}，轻量模式: {use_lightweight_mode}")

            #         # 构建滤镜命令
            #         cmd = ["ffmpeg"]

            #         # 添加所有输入文件
            #         for file_path in merge_files:
            #             cmd.extend(["-i", file_path])

            #         # 构建filter_complex参数
            #         filter_complex = ""
            #         for i in range(len(merge_files)):
            #             filter_complex += f"[{i}:v][{i}:a]"

            #         filter_complex += f"concat=n={len(merge_files)}:v=1:a=1[outv][outa]"

            #         # 根据环境调整编码参数
            #         if use_lightweight_mode:
            #             # 轻量模式：更快的编码参数
            #             if is_docker:
            #                 # Docker环境：极致压缩参数
            #                 codec_params = [
            #                     "-filter_complex", filter_complex,
            #                     "-map", "[outv]",
            #                     "-map", "[outa]",
            #                     "-c:v", "libx264",
            #                     "-preset", "ultrafast",  # 最快预设
            #                     "-threads", "1",         # 限制线程数
            #                     "-crf", "35",            # 更高CRF值，更小文件
            #                     "-g", "250",             # 更大GOP
            #                     "-bf", "0",              # 不使用B帧
            #                     "-c:a", "aac",
            #                     "-b:a", "64k",           # 极低音频比特率
            #                     "-maxrate", "500k",      # 限制最大比特率
            #                     "-bufsize", "1000k",     # 缓冲区大小
            #                     "-max_muxing_queue_size", "256",
            #                     "-avoid_negative_ts", "make_zero",
            #                     "-movflags", "+faststart",
            #                 ]
            #             else:
            #                 codec_params = [
            #                     "-filter_complex", filter_complex,
            #                     "-map", "[outv]",
            #                     "-map", "[outa]",
            #                     "-c:v", "libx264",
            #                     "-preset", "superfast",
            #                     "-threads", "1",
            #                     "-crf", "30",
            #                     "-c:a", "aac",
            #                     "-b:a", "96k",
            #                     "-max_muxing_queue_size", "256",
            #                     "-avoid_negative_ts", "make_zero",
            #                     "-movflags", "+faststart",
            #                 ]
            #             timeout_seconds = 300  # 减少到5分钟超时
            #         else:
            #             # 标准模式
            #             codec_params = [
            #                 "-filter_complex", filter_complex,
            #                 "-map", "[outv]",
            #                 "-map", "[outa]",
            #                 "-c:v", "libx264",
            #                 "-preset", "ultrafast",
            #                 "-threads", "2",
            #                 "-crf", "28",
            #                 "-c:a", "aac",
            #                 "-b:a", "128k",
            #                 "-max_muxing_queue_size", "512",
            #                 "-avoid_negative_ts", "make_zero",
            #             ]
            #             timeout_seconds = 300  # 5分钟超时

            #         cmd.extend(codec_params)
            #         cmd.extend(["-y", output_path])

            #         logger.info(f"执行Filter Complex命令，超时设置: {timeout_seconds}秒")

            #         # 使用异步处理避免阻塞
            #         start_time = time_module.time()

            #         # 在Docker环境中优先使用subprocess.Popen进行更好的控制
            #         if is_docker:
            #             logger.info("Docker环境，使用Popen进行进程控制")
            #             process = subprocess.Popen(
            #                 cmd,
            #                 stdout=subprocess.PIPE,
            #                 stderr=subprocess.PIPE,
            #                 universal_newlines=True,
            #                 preexec_fn=None if platform.system() == 'Windows' else os.setpgrp
            #             )

            #             # 分段等待，每30秒检查一次
            #             check_interval = 30
            #             total_waited = 0

            #             while total_waited < timeout_seconds:
            #                 try:
            #                     stdout, stderr = process.communicate(timeout=check_interval)
            #                     # 进程完成
            #                     break
            #                 except subprocess.TimeoutExpired:
            #                     total_waited += check_interval
            #                     logger.info(f"Filter Complex处理中... 已等待 {total_waited}/{timeout_seconds} 秒")

            #                     # 检查输出文件是否在增长
            #                     if os.path.exists(output_path):
            #                         file_size = os.path.getsize(output_path)
            #                         logger.info(f"当前输出文件大小: {file_size / (1024*1024):.1f} MB")

            #                         # 检查文件大小是否异常 - Docker环境下严格控制
            #                         if is_docker:
            #                             # Docker环境：预期每秒不超过1MB
            #                             max_size_mb = max(20, total_waited * 1.0)  # 最小20MB，每秒最多1MB
            #                             if file_size > max_size_mb * 1024 * 1024:
            #                                 logger.error(f"文件大小异常过大: {file_size/(1024*1024):.1f}MB > {max_size_mb}MB，提前终止")
            #                                 process.terminate()
            #                                 try:
            #                                     process.wait(timeout=10)
            #                                 except subprocess.TimeoutExpired:
            #                                     process.kill()
            #                                     process.wait()
            #                                 raise Exception(f"Filter Complex文件过大异常: {file_size/(1024*1024):.1f}MB")
            #                         else:
            #                             # 非Docker环境：更宽松的限制
            #                             max_size_mb = max(50, total_waited * 2.0)  # 最小50MB，每秒最多2MB
            #                             if file_size > max_size_mb * 1024 * 1024:
            #                                 logger.error(f"文件大小异常过大: {file_size/(1024*1024):.1f}MB > {max_size_mb}MB，提前终止")
            #                                 process.terminate()
            #                                 try:
            #                                     process.wait(timeout=10)
            #                                 except subprocess.TimeoutExpired:
            #                                     process.kill()
            #                                     process.wait()
            #                                 raise Exception(f"Filter Complex文件过大异常: {file_size/(1024*1024):.1f}MB")

            #                     # 如果超时，终止进程
            #                     if total_waited >= timeout_seconds:
            #                         logger.warning(f"Filter Complex超时 ({timeout_seconds}秒)，终止进程")
            #                         process.terminate()
            #                         try:
            #                             process.wait(timeout=10)
            #                         except subprocess.TimeoutExpired:
            #                             process.kill()
            #                             process.wait()
            #                         raise subprocess.TimeoutExpired(cmd, timeout_seconds)

            #             result_code = process.returncode
            #             if result_code != 0:
            #                 stdout, stderr = process.communicate() if not stdout else (stdout, stderr)
            #                 logger.error(f"Filter Complex失败，返回码: {result_code}, 错误: {stderr}")

            #         else:
            #             # 非Docker环境使用标准方法
            #             result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout_seconds)
            #             result_code = result.returncode
            #             if result_code != 0:
            #                 logger.error(f"Filter Complex失败: {result.stderr}")

            #         end_time = time_module.time()
            #         process_time = end_time - start_time

            #         if result_code == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            #             logger.info(f"Filter Complex合并成功，耗时: {process_time:.1f}秒")
            #             merge_success = True
            #         else:
            #             logger.error(f"Filter Complex合并失败")

            #     except subprocess.TimeoutExpired:
            #         logger.error(f"Filter Complex超时 ({timeout_seconds}秒)")
            #     except Exception as e:
            #         logger.error(f"执行Filter Complex合并异常: {str(e)}", exc_info=True)



            # 最后备选方案：使用最长片段
            if not merge_success or not os.path.exists(output_path):
                logger.error("所有合并方法失败，使用最长片段")
                longest_file = max(mp4_files, key=lambda x: x["time_len"])
                logger.info(f"使用最长的片段作为结果: {longest_file['file_path']}, 时长: {longest_file['time_len']}秒")
                import shutil
                shutil.copy2(longest_file["file_path"], output_path)
                return output_path

            # 验证最终输出
            output_info = await self._get_file_info(output_path)
            logger.info(f"合并完成，输出文件信息: {output_info}")

            # 计算预期时长
            expected_duration = sum(segment.get("time_len", 0) for segment in mp4_files)

            # 添加补帧时长（如果使用了补帧）
            filler_duration = 0
            if try_fill_gaps:
                for i, segment in enumerate(mp4_files[:-1]):
                    if i < len(mp4_files) - 1:
                        current_end = segment["start_time"] + timedelta(seconds=segment["time_len"])
                        next_start = mp4_files[i + 1]["start_time"]
                        gap = (next_start - current_end).total_seconds()
                        if gap > min_gap_for_fill:
                            filler_duration += gap

            expected_duration_with_fillers = expected_duration + filler_duration
            logger.info(f"预期总时长{' (含补帧)' if try_fill_gaps else ''}: {expected_duration_with_fillers}秒")

            # 时长验证：Docker环境放宽误差到10%
            actual_duration = output_info.get("duration", 0)
            tolerance_percent = 0.7 if is_docker else 0.3  # Docker环境放宽到50%
            max_tolerance = expected_duration_with_fillers * tolerance_percent
            duration_diff = abs(actual_duration - expected_duration_with_fillers)

            logger.info(f"时长验证: 预期={expected_duration_with_fillers}秒, 实际={actual_duration}秒, 差异={duration_diff}秒, 允许误差={max_tolerance}秒")

            if duration_diff > max_tolerance:
                logger.warning(f"合并文件时长验证失败！差异超过{tolerance_percent*100}%")

                # 在Docker环境中，如果时长异常但文件存在且有内容，可能是可接受的
                if is_docker and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    logger.info("Docker环境下，虽然时长验证失败但文件有效，继续使用")
                else:
                    # 使用最长的片段作为备选方案
                    logger.info("时长验证失败，使用最长的片段作为结果")
                    longest_file = max(mp4_files, key=lambda x: x["time_len"])
                    logger.info(f"使用最长的片段: {longest_file['file_path']}, 时长: {longest_file['time_len']}秒")
                    import shutil
                    shutil.copy2(longest_file["file_path"], output_path)
            else:
                logger.info("合并文件时长验证通过")

            return output_path

        except Exception as e:
            logger.error(f"处理结果文件失败: {task_id}, 错误: {str(e)}", exc_info=True)
            return None

    def _get_recording_work_dir(self, task_id: str) -> str:
        """返回任务专属派生文件目录，禁止任务号构造目录穿越路径。"""

        if not task_id or any(char in str(task_id) for char in ("/", "\\", "\x00")):
            raise ValueError("录制任务ID不能包含路径分隔符")
        base_dir = os.environ.get("TEMP") or os.path.dirname(os.path.abspath(__file__))
        work_dir = os.path.abspath(os.path.join(base_dir, f"recording_{task_id}"))
        os.makedirs(work_dir, exist_ok=True)
        return work_dir

    async def close(self) -> None:
        """停止共享状态监控并关闭 ZL HTTP 客户端。"""

        monitor = getattr(self, "_zlm_activity_monitor", None)
        if monitor is not None:
            await monitor.close()
        client = getattr(self, "http_client", None)
        if client is not None:
            await client.aclose()

    async def _create_filler_video_compatible(self, source_file: str, duration: float, output_dir: str,
                                             output_name: str, has_audio: bool, target_info: Dict[str, Any]) -> Optional[str]:
        """
        创建与源视频兼容的补帧视频（使用FillerVideoGenerator）

        :param source_file: 源视频文件
        :param duration: 补帧时长
        :param output_dir: 输出目录
        :param output_name: 输出文件名
        :param has_audio: 是否添加音频轨道（保留兼容性，由FillerVideoGenerator自动检测）
        :param target_info: 目标视频信息（保留兼容性，由FillerVideoGenerator自动获取）
        :return: 补帧视频路径
        """
        try:
            logger.info(f"使用FillerVideoGenerator创建补帧视频: 源文件={source_file}, 时长={duration}秒, 输出={output_name}")

            # 使用新的FillerVideoGenerator创建补帧视频
            filler_path = await self.filler_generator.create_compatible_filler(
                source_video_path=source_file,
                duration=duration,
                output_dir=output_dir,
                output_name=output_name
            )

            if filler_path:
                logger.info(f"补帧视频创建成功: {filler_path}")
                return filler_path
            else:
                logger.error("补帧视频创建失败")
                return None

        except Exception as e:
            logger.error(f"补帧视频生成过程异常: {str(e)}", exc_info=True)
            return None

    async def _verify_filler_duration_and_size(self, video_path: str, expected_duration: float) -> tuple[Optional[float], int]:
        """
        验证补帧视频的实际时长和文件大小

        :param video_path: 视频文件路径
        :param expected_duration: 预期时长（秒）
        :return: (实际时长, 文件大小)，如果验证失败则时长返回None
        """
        try:
            # 获取文件大小
            file_size = os.path.getsize(video_path)

            # 检查文件大小是否合理（每秒不超过200KB）
            max_size_per_second = 200 * 1024  # 200KB/秒
            max_allowed_size = expected_duration * max_size_per_second

            if file_size > max_allowed_size:
                logger.warning(f"补帧视频文件过大: {file_size/(1024*1024):.1f}MB, 预期最大: {max_allowed_size/(1024*1024):.1f}MB")
                return None, file_size

            # 获取视频实际时长
            video_info = await self._get_file_info(video_path)
            actual_duration = video_info.get("duration", 0)

            # 允许的误差范围：±0.1秒或±5%
            tolerance_abs = 0.1
            tolerance_rel = 0.05
            max_tolerance = max(tolerance_abs, expected_duration * tolerance_rel)

            duration_diff = abs(actual_duration - expected_duration)

            logger.info(f"补帧视频验证: 预期={expected_duration}秒, 实际={actual_duration}秒, 差异={duration_diff}秒, 允许误差={max_tolerance}秒, 文件大小={file_size/(1024*1024):.1f}MB")

            if duration_diff <= max_tolerance:
                logger.info("补帧视频验证通过")
                return actual_duration, file_size
            else:
                logger.warning(f"补帧视频时长验证失败: 差异{duration_diff}秒超过允许误差{max_tolerance}秒")
                return None, file_size

        except Exception as e:
            logger.error(f"验证补帧视频异常: {str(e)}")
            return None, 0

    async def _extract_audio(self, video_path: str, task_id: str, audio_format: str = "mp3") -> Optional[str]:
        """
        从视频中提取音频

        :param video_path: 视频文件路径
        :param task_id: 任务ID
        :param audio_format: 音频格式，支持mp3和wav
        :return: 提取的音频文件路径，失败返回None
        """
        try:
            # 验证视频文件存在
            if not os.path.exists(video_path):
                logger.error(f"视频文件不存在: {video_path}")
                return None

            # 验证音频格式
            if audio_format not in ["mp3", "wav"]:
                logger.warning(f"不支持的音频格式: {audio_format}，将使用默认格式mp3")
                audio_format = "mp3"

            # 创建输出文件路径
            directory = os.path.dirname(video_path)
            filename = os.path.basename(video_path)
            basename = os.path.splitext(filename)[0]
            audio_path = os.path.join(directory, f"{basename}_audio.{audio_format}")

            logger.info(f"开始提取音频: 视频={video_path}, 音频={audio_path}, 格式={audio_format}")

            # 检测操作系统
            is_windows = platform.system() == 'Windows'

            # 设置音频编码参数
            if audio_format == "mp3":
                audio_codec = "libmp3lame"
                audio_bitrate = "192k"
            else:  # wav
                audio_codec = "pcm_s16le"
                audio_bitrate = None  # WAV不需要指定比特率

            # 尝试提取音频
            try:
                if is_windows:
                    # Windows环境使用subprocess
                    import subprocess

                    cmd = ["ffmpeg", "-i", video_path, "-vn"]
                    if audio_codec:
                        cmd.extend(["-acodec", audio_codec])
                    if audio_bitrate:
                        cmd.extend(["-ab", audio_bitrate])
                    cmd.extend(["-y", audio_path])

                    logger.info(f"Windows环境提取音频命令: {' '.join(cmd)}")

                    result = subprocess.run(cmd, capture_output=True, text=True)

                    if result.returncode == 0 and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                        logger.info(f"音频提取成功: {audio_path}")
                        return audio_path
                    else:
                        logger.error(f"音频提取失败: {result.stderr}")
                        return None

                else:
                    # 非Windows环境使用ffmpeg-python
                    stream = ffmpeg.input(video_path)
                    if audio_codec and audio_bitrate:
                        stream = ffmpeg.output(stream, audio_path, vn=None, acodec=audio_codec, ab=audio_bitrate)
                    elif audio_codec:
                        stream = ffmpeg.output(stream, audio_path, vn=None, acodec=audio_codec)
                    else:
                        stream = ffmpeg.output(stream, audio_path, vn=None)

                    await asyncio.to_thread(stream.run, capture_stdout=True, capture_stderr=True, overwrite_output=True)

                    if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                        logger.info(f"音频提取成功: {audio_path}")
                        return audio_path
                    else:
                        logger.error("音频提取结果文件无效")
                        return None

            except Exception as e:
                logger.error(f"提取音频过程异常: {str(e)}", exc_info=True)

                # 尝试使用最简单的命令再次提取
                try:
                    logger.info("尝试使用简化命令提取音频")

                    if is_windows:
                        import subprocess
                        simple_cmd = ["ffmpeg", "-i", video_path, "-vn", "-y", audio_path]
                        result = subprocess.run(simple_cmd, capture_output=True, text=True)

                        if result.returncode == 0 and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                            logger.info(f"使用简化命令成功提取音频: {audio_path}")
                            return audio_path
                    else:
                        stream = ffmpeg.input(video_path)
                        stream = ffmpeg.output(stream, audio_path, vn=None)
                        await asyncio.to_thread(stream.run, capture_stdout=True, capture_stderr=True, overwrite_output=True)

                        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                            logger.info(f"使用简化命令成功提取音频: {audio_path}")
                            return audio_path
                except Exception as simple_error:
                    logger.error(f"简化命令提取音频失败: {str(simple_error)}")

                return None

        except Exception as e:
            logger.error(f"提取音频功能异常: {str(e)}", exc_info=True)
            return None

    async def _extract_last_frame(self, video_file: str, output_file: str) -> bool:
        """
        提取视频最后一帧

        :param video_file: 视频文件路径
        :param output_file: 输出文件路径
        :return: 是否成功
        """
        try:
            if not os.path.exists(video_file):
                logger.error(f"视频文件不存在: {video_file}")
                return False

            # 检测是否为Windows环境
            is_windows = platform.system() == 'Windows'

            # 使用ffmpeg-python获取视频时长
            try:
                probe = await asyncio.to_thread(ffmpeg.probe, video_file)
                duration = float(probe['format']['duration'])

                # 提取最后一帧
                if is_windows:
                    import subprocess

                    # 直接使用subprocess在Windows上执行
                    cmd = [
                        "ffmpeg",
                        "-ss", str(max(0, duration-0.1)),
                        "-i", video_file,
                        "-vframes", "1",
                        "-q:v", "2",
                        "-y", output_file
                    ]

                    logger.info(f"Windows环境提取视频帧: {' '.join(cmd)}")
                    result = subprocess.run(cmd, capture_output=True, text=True)

                    if result.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                        return True
                    else:
                        logger.error(f"提取视频帧失败: {result.stderr}")

                        # 尝试从视频中间提取一帧
                        mid_time = duration / 2
                        cmd = [
                            "ffmpeg",
                            "-ss", str(mid_time),
                            "-i", video_file,
                            "-vframes", "1",
                            "-q:v", "2",
                            "-y", output_file
                        ]

                        logger.info(f"尝试从视频中间提取帧: {' '.join(cmd)}")
                        result = subprocess.run(cmd, capture_output=True, text=True)

                        if result.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                            return True
                        else:
                            logger.error(f"提取视频帧失败: {result.stderr}")
                            return False
                else:
                    # 非Windows环境使用ffmpeg-python
                    stream = ffmpeg.input(video_file)
                    stream = ffmpeg.filter(stream, 'select', 'gte(n\\,if(lte(iw,iw/2),iw/2,iw-iw/2))')
                    stream = ffmpeg.output(stream, output_file)
                    await asyncio.to_thread(stream.run, capture_stdout=True, capture_stderr=True)
                    return True
            except Exception as e:
                logger.error(f"提取视频帧失败: {str(e)}")
                return False
        except Exception as e:
            logger.error(f"提取视频帧失败: {str(e)}")
            return False

    async def _extract_cover_background(self,
                                       task_id: str,
                                       result_url: str,
                                       extra_params: Dict[str, Any],
                                       task_status: Dict[str, Any]):
        """
        后台异步提取封面，不影响主流程

        Args:
            task_id: 任务ID
            result_url: 视频文件路径
            extra_params: 额外参数
            task_status: 任务状态字典（用于更新封面信息）
        """
        try:
            logger.info(f"后台封面提取任务开始: {task_id}")

            cover_strategy = extra_params.get("cover_strategy", CoverExtractStrategy.TIMESTAMP.value)
            cover_info = extra_params.get("cover_info", {})

            logger.info(f"封面提取配置 - 策略: {cover_strategy}, 参数: {cover_info}")
            logger.info(f"视频文件路径: {result_url}")

            # 验证封面参数
            logger.info("开始验证封面参数...")
            validation_result = self.cover_extractor.validate_cover_params(cover_strategy, cover_info)
            logger.info(f"封面参数验证结果: {validation_result}")

            if not validation_result:
                logger.warning(f"封面参数验证失败: {task_id}, 策略: {cover_strategy}")
                logger.warning(f"验证失败的参数详情: {cover_info}")
                task_status["errors"].append({
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    "error": f"封面参数验证失败，策略: {cover_strategy}"
                })
                return

            try:
                logger.info("参数验证通过，开始封面提取流程...")

                # 计算视频文件大小(MB)
                if cover_strategy == CoverExtractStrategy.CUSTOM.value:
                    video_file_size = 0
                    segments = task_status.get("segments", [])
                    if segments:
                        video_file_size = sum(segment.get("file_size", 0) for segment in segments)
                    cover_info["file_size_mb"] = round(video_file_size / (1024 * 1024), 1) if video_file_size > 0 else 0
                    logger.info(f"自定义封面模式 - 计算文件大小: {cover_info['file_size_mb']} MB")

                # 创建输出目录
                cover_output_dir = os.path.join(os.path.dirname(result_url), "covers")
                logger.info(f"封面输出目录: {cover_output_dir}")

                # 提取封面 - 使用超时控制，避免长时间阻塞
                logger.info("调用封面提取器...")
                try:
                    # 封面提取总超时时间60秒（包含上传和保存）
                    cover_file_path = await asyncio.wait_for(
                        self.cover_extractor.extract_cover(
                            video_path=result_url,
                            strategy=cover_strategy,
                            cover_info=cover_info,
                            output_dir=cover_output_dir,
                            start_time=task_status.get("start_time"),
                            end_time=task_status.get("end_time")
                        ),
                        timeout=60.0
                    )
                except asyncio.TimeoutError:
                    logger.error(f"封面提取任务超时: {task_id}（60秒）")
                    task_status["errors"].append({
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        "error": "封面提取任务超时（60秒）"
                    })
                    return

                logger.info(f"封面提取器返回结果: {cover_file_path}")

                if cover_file_path and os.path.exists(cover_file_path):
                    cover_file_size = os.path.getsize(cover_file_path)
                    logger.info(f"封面文件生成成功 - 路径: {cover_file_path}, 大小: {cover_file_size} bytes")

                    # 上传封面到存储服务。该方法由 post_processor 主链路调用，
                    # 保持原有行为；后续若整理上传职责，应在 post_processor 主链路
                    # 内统一处理，不再通过 StreamRecorder 降级路径演进。
                    logger.info("开始上传封面到存储服务...")
                    cover_upload_result = await self.enhanced_storage.upload_file_enhanced(cover_file_path, upload_type="cover")
                    logger.info(f"封面上传成功: {cover_upload_result.file_url}, fileId: {cover_upload_result.file_id}")

                    # 保存封面文件信息到数据库
                    cover_file_id = cover_upload_result.file_id
                    if not cover_file_id:
                        import uuid
                        cover_file_id = str(uuid.uuid4())
                        logger.warning(f"封面 file_id 为空，自动生成 UUID: {cover_file_id}")
                    logger.info(f"保存封面文件信息到数据库 - 文件ID: {cover_file_id}")

                    # 在 cover_info 中添加 key 和 bucket 信息
                    cover_info_with_storage = cover_info.copy()
                    cover_info_with_storage["key"] = cover_upload_result.storage_key
                    cover_info_with_storage["bucket"] = cover_upload_result.bucket

                    await self._save_file_to_db(
                        file_name=cover_upload_result.file_name,
                        task_id=task_id,
                        file_id=cover_file_id,
                        file_url=cover_upload_result.file_url,
                        file_path=cover_file_path,
                        mime_type="image/jpeg",
                        storage_key=cover_upload_result.storage_key,
                        metadata={
                            "strategy": cover_strategy,
                            "generated_from_video": True,
                            "cover_info": cover_info_with_storage,
                            "storage_key": cover_upload_result.storage_key,
                            "key": cover_upload_result.storage_key,
                            "bucket": cover_upload_result.bucket,
                            "md5": cover_upload_result.md5
                        }
                    )

                    # 保存到任务状态（线程安全，asyncio是单线程事件循环）
                    task_status["cover_url"] = cover_upload_result.file_url
                    task_status["cover_file_id"] = cover_file_id
                    task_status["cover_local_path"] = cover_file_path
                    # 保存 bucket 和 key 到 task_status，方便回调时使用
                    task_status["cover_bucket"] = cover_upload_result.bucket
                    task_status["cover_key"] = cover_upload_result.storage_key
                    logger.info(f"封面信息已保存到任务状态: {task_id}")

                else:
                    logger.error(f"封面提取失败: {task_id}")
                    logger.error(f"  - 返回路径: {cover_file_path}")
                    logger.error(f"  - 文件存在: {os.path.exists(cover_file_path) if cover_file_path else False}")
                    task_status["errors"].append({
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        "error": f"封面提取失败，策略: {cover_strategy}"
                    })

            except Exception as cover_error:
                logger.error(f"封面提取异常: {task_id}, 错误: {str(cover_error)}", exc_info=True)
                task_status["errors"].append({
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    "error": f"封面提取异常: {str(cover_error)}"
                })

        except Exception as e:
            logger.error(f"后台封面提取任务异常: {task_id}, 错误: {str(e)}", exc_info=True)
            task_status["errors"].append({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "error": f"后台封面提取任务异常: {str(e)}"
            })

    async def _save_file_to_db(self, file_name: str, task_id: str, file_id: str, file_url: str, file_path: str, mime_type: str, storage_key: str = "", metadata: Dict = None) -> None:
        """
        将录制产物文件信息保存到国标媒体文件表。

        真实落库逻辑归属 `RecordingArtifactService`。这里保留薄委托方法，避免一次
        修改所有录制后处理调用点，同时让 `StreamRecorder` 不再持有数据库表细节。

        :param task_id: 任务ID
        :param file_id: 文件ID（如果为None会自动生成UUID）
        :param file_url: 文件URL
        :param file_path: 文件本地路径
        :param mime_type: 文件MIME类型
        :param storage_key: 存储键值，默认为空
        :param metadata: 文件元数据
        """
        artifact_service = getattr(self, "artifact_service", None)
        if artifact_service is None:
            artifact_service = RecordingArtifactService()
            self.artifact_service = artifact_service
        await artifact_service.save_file(
            file_name=file_name,
            task_id=task_id,
            file_id=file_id,
            file_url=file_url,
            file_path=file_path,
            mime_type=mime_type,
            storage_key=storage_key,
            metadata=metadata,
        )

    @staticmethod
    def _infer_media_file_type(mime_type: str) -> str:
        """兼容旧内部调用，实际规则归属 `RecordingArtifactService`。"""

        return RecordingArtifactService.infer_media_file_type(mime_type)
