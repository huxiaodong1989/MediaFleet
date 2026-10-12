"""图像课堂任务消费业务编排：数据库领取、视频选源、执行与终态写回。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from threading import Event, Thread
from time import perf_counter

from sqlalchemy.orm import Session

from media_platform.application.image_prompt_service import ImagePromptReader
from media_platform.contracts.image_analysis import (
    IMAGE_TASK_TYPE,
    ImageAnalysisRequest,
)
from media_platform.contracts.task import TaskDeliveryChannel, TaskDispatchMessage
from media_platform.domain.task import TaskStatus
from media_platform.infrastructure.database.repositories import MediaTaskRepository
from media_platform.infrastructure.messaging import (
    TaskBusyError,
    TaskPermanentError,
    TaskRetryableError,
)
from services.image_analysis.application.classroom_pipeline import (
    ClassroomImagePipeline,
)
from services.image_analysis.infrastructure.repositories import ImageAnalysisRepository
from services.image_analysis.planning import SamplingLimits, plan_classroom_samples
from services.image_analysis.source_selection import (
    SourceProbeEvidence,
    SourceSelectionPolicy,
    select_classroom_sources,
)
from services.image_analysis.video_frames import FfprobeVideoProbe, FrameExtractionError
from services.image_analysis.video_source import HttpVideoSource, VideoSourceError


LOGGER = logging.getLogger(__name__)


class ImageTaskHandler:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        pipeline: ClassroomImagePipeline,
        prompt_reader: ImagePromptReader,
        video_source: HttpVideoSource,
        video_probe: FfprobeVideoProbe,
        *,
        worker_id: str,
        selection_policy: SourceSelectionPolicy,
        sampling_limits: SamplingLimits,
        lease_timeout: timedelta = timedelta(minutes=10),
    ) -> None:
        if not worker_id or lease_timeout < timedelta(seconds=30):
            raise ValueError("图像消费者身份或租约配置无效")
        self.session_factory = session_factory
        self.pipeline = pipeline
        self.prompt_reader = prompt_reader
        self.video_source = video_source
        self.video_probe = video_probe
        self.worker_id = worker_id
        self.selection_policy = selection_policy
        self.sampling_limits = sampling_limits
        self.lease_timeout = lease_timeout

    def _claim(self, message: TaskDispatchMessage) -> tuple[int, dict] | None:
        if (
            message.delivery_channel != TaskDeliveryChannel.IMAGE_ANALYSIS
            or message.task_type != IMAGE_TASK_TYPE
        ):
            raise TaskPermanentError("收到非课堂图像分析任务")
        with self.session_factory.begin() as session:
            repo = MediaTaskRepository(session)
            task = repo.get(message.task_id)
            if (
                task is None
                or task.message_id != message.message_id
                or task.school_code != message.school_code
            ):
                raise TaskPermanentError("图像任务与数据库消息不一致")
            task_status = (
                task.status.value
                if isinstance(task.status, TaskStatus)
                else str(task.status).lower()
            )
            if task_status in {"completed", "failed", "cancelled"}:
                return None
            if (
                task_status == TaskStatus.PENDING.value
                and task.retry_count > message.attempt
            ):
                # MySQL 已写入下一重试代次，但连接中断可能让 Broker 重投旧消息。
                # 以持久事实为准，让消费者逐代提升 attempt 后重新领取。
                raise TaskRetryableError("图像任务消息代次落后于MySQL事实")
            generation = repo.claim_execution_with_lease(
                message.task_id,
                message.message_id,
                self.worker_id,
                attempt=message.attempt,
                stale_before=datetime.now() - self.lease_timeout,
                started_at=datetime.now(),
                lease_timeout=self.lease_timeout,
            )
            if generation is None:
                raise TaskBusyError("图像任务正由其他实例执行")
            return generation, dict(task.params or {})

    def _evidence(
        self, request: ImageAnalysisRequest, *, task_id: str
    ) -> tuple[SourceProbeEvidence, ...]:
        evidence = []
        for stream in request.stream_info_list:
            started = perf_counter()
            LOGGER.info(
                "图像视频探测开始: task_id=%s, stream_id=%s",
                task_id,
                stream.stream_id,
            )
            # 单路录像故障不妨碍其他完整镜头；选源会保留排除原因。
            try:
                self.video_source.probe_range(stream.video_url)
                media = self.video_probe.probe(stream.video_url)
            except (VideoSourceError, FrameExtractionError) as error:
                LOGGER.warning(
                    "图像视频探测失败: task_id=%s, stream_id=%s, error_type=%s, duration_ms=%s",
                    task_id,
                    stream.stream_id,
                    type(error).__name__,
                    round((perf_counter() - started) * 1000),
                )
                evidence.append(
                    SourceProbeEvidence(
                        stream_record_id=stream.id,
                        stream_id=stream.stream_id,
                        duration_ms=None,
                        started_at=stream.video_started_at or request.start_time,
                        timeline_basis="duration_heuristic",
                        continuous=False,
                        probe_failed=True,
                    )
                )
                continue
            LOGGER.info(
                "图像视频探测完成: task_id=%s, stream_id=%s, video_duration_ms=%s, duration_ms=%s",
                task_id,
                stream.stream_id,
                media.duration_ms,
                round((perf_counter() - started) * 1000),
            )
            evidence.append(
                SourceProbeEvidence(
                    stream_record_id=stream.id,
                    stream_id=stream.stream_id,
                    duration_ms=media.duration_ms,
                    started_at=stream.video_started_at or request.start_time,
                    timeline_basis="duration_heuristic",
                    continuous=False,
                )
            )
        return tuple(evidence)

    def _renew(
        self, message: TaskDispatchMessage, generation: int, stop: Event, lost: Event
    ) -> None:
        while not stop.wait(self.lease_timeout.total_seconds() / 3):
            try:
                with self.session_factory.begin() as session:
                    renewed = MediaTaskRepository(session).renew_execution_lease(
                        message.task_id,
                        message.message_id,
                        self.worker_id,
                        generation,
                        lease_timeout=self.lease_timeout,
                    )
                if not renewed:
                    lost.set()
                    return
            except Exception:
                # 下次继续续租；所有检查点写入仍会校验数据库租约。
                pass

    def handle(self, message: TaskDispatchMessage) -> None:
        task_started = perf_counter()
        LOGGER.info(
            "图像课堂任务接收: task_id=%s, message_id=%s, attempt=%s",
            message.task_id,
            message.message_id,
            message.attempt,
        )
        claimed = self._claim(message)
        if claimed is None:
            LOGGER.info(
                "图像课堂任务已是终态，跳过重复消息: task_id=%s, message_id=%s",
                message.task_id,
                message.message_id,
            )
            return
        generation, params = claimed
        LOGGER.info(
            "图像课堂任务领取成功: task_id=%s, generation=%s, worker_id=%s",
            message.task_id,
            generation,
            self.worker_id,
        )
        stop, lost = Event(), Event()
        renewer = Thread(
            target=self._renew,
            args=(message, generation, stop, lost),
            name=f"image-lease-{message.task_id}",
            daemon=True,
        )
        renewer.start()
        try:
            request = ImageAnalysisRequest.model_validate(params["request"])
            LOGGER.info(
                "图像课堂任务开始: task_id=%s, message_id=%s, classroom_id=%s, streams=%s, interval_seconds=%s",
                message.task_id,
                request.message_id,
                request.classroom_id,
                len(request.stream_info_list),
                request.sample_interval_seconds,
            )
            locked = params["promptBundle"]
            prompt = self.prompt_reader.locked(
                locked["id"],
                school_code=request.school_id,
                expected_version=locked["version"],
                expected_hash=locked["contentHash"],
            )
            LOGGER.info(
                "图像提示词版本锁定: task_id=%s, prompt_id=%s, version=%s, model=%s",
                message.task_id,
                prompt.id,
                prompt.version,
                prompt.content.model,
            )
            selection = select_classroom_sources(
                task_id=message.task_id,
                request=request,
                evidence=self._evidence(request, task_id=message.task_id),
                policy=self.selection_policy,
            )
            LOGGER.info(
                "图像选源完成: task_id=%s, selected=%s, excluded=%s",
                message.task_id,
                len(selection.selected_videos),
                len(selection.excluded_videos),
            )
            plan = plan_classroom_samples(
                internal_task_id=message.task_id,
                request=request,
                selection=selection,
                limits=self.sampling_limits,
            )
            LOGGER.info(
                "图像采样计划生成: task_id=%s, samples=%s, images=%s, interval_seconds=%s",
                message.task_id,
                len(plan.samples),
                sum(len(sample.frames) for sample in plan.samples),
                request.sample_interval_seconds,
            )
            result = self.pipeline.execute(
                request=request,
                plan=plan,
                prompt=prompt,
                generation=generation,
                lease_owner=self.worker_id,
            )
            if lost.is_set():
                raise TaskBusyError("图像任务执行租约已失效")
            with self.session_factory.begin() as session:
                updated = MediaTaskRepository(session).mark_execution_completed(
                    message.task_id,
                    message.message_id,
                    self.worker_id,
                    {
                        "samples": len(plan.samples),
                        "completedSamples": result.completed_samples,
                        "skippedSamples": result.skipped_samples,
                    },
                    execution_generation=generation,
                )
                if updated:
                    ImageAnalysisRepository(session).ensure_task_terminal_notification(
                        task_id=message.task_id,
                        school_code=message.school_code,
                    )
            if not updated:
                raise TaskBusyError("图像任务最终写回失去执行权")
            LOGGER.info(
                "图像课堂任务完成: task_id=%s, uploaded_frames=%s, completed_samples=%s, skipped_samples=%s, duration_ms=%s",
                message.task_id,
                result.uploaded_frames,
                result.completed_samples,
                result.skipped_samples,
                round((perf_counter() - task_started) * 1000),
            )
        except TaskBusyError:
            raise
        except Exception as error:
            # 错误仅保存稳定类型，不把签名视频/图片URL或模型原文写入任务表/MQ日志。
            next_attempt = message.attempt + 1
            with self.session_factory.begin() as session:
                repo = MediaTaskRepository(session)
                if next_attempt < message.max_attempts:
                    repo.mark_execution_retry(
                        message.task_id,
                        message.message_id,
                        self.worker_id,
                        next_attempt=next_attempt,
                        error_message=type(error).__name__,
                        execution_generation=generation,
                    )
                else:
                    failed = repo.mark_execution_failed(
                        message.task_id,
                        message.message_id,
                        self.worker_id,
                        type(error).__name__,
                        execution_generation=generation,
                    )
                    if failed:
                        ImageAnalysisRepository(
                            session
                        ).ensure_task_terminal_notification(
                            task_id=message.task_id,
                            school_code=message.school_code,
                        )
            LOGGER.warning(
                "图像课堂任务失败: task_id=%s, attempt=%s, next_attempt=%s, error_type=%s, duration_ms=%s",
                message.task_id,
                message.attempt,
                next_attempt,
                type(error).__name__,
                round((perf_counter() - task_started) * 1000),
            )
            if next_attempt < message.max_attempts:
                raise TaskRetryableError(type(error).__name__) from None
            raise TaskPermanentError(type(error).__name__) from None
        finally:
            stop.set()
            renewer.join(timeout=5)


__all__ = ["ImageTaskHandler"]
