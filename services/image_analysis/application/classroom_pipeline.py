"""已冻结选源的课堂主链路：逐图抽帧上传、逐时间点一次多图推理。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import logging
from time import perf_counter
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_platform.application.image_prompt_service import ImagePromptSnapshot
from media_platform.contracts.image_analysis import ImageAnalysisRequest
from media_platform.infrastructure.database.models import (
    ImageFrameModel,
    ImageSampleModel,
)
from media_platform.infrastructure.storage.image_objects import (
    ImageObjectRef,
    ImageObjectStore,
)
from services.image_analysis.infrastructure.repositories import ImageAnalysisRepository
from services.image_analysis.planning import ClassroomSamplingPlan
from services.image_analysis.video_frames import ExtractedFrame
from services.image_analysis.video_read_session import VideoReadSession
from services.image_analysis.video_source import HttpVideoSource


LOGGER = logging.getLogger(__name__)


class FrameExtractor(Protocol):
    def extract(self, source: str, offset_ms: int) -> ExtractedFrame:
        ...


class MultiImageModel(Protocol):
    def analyze(
        self, *, image_urls: tuple[str, ...], bundle, requested_metrics
    ) -> dict[str, int]:
        ...


@dataclass(frozen=True)
class PipelineResult:
    uploaded_frames: int
    completed_samples: int
    skipped_samples: int


class ClassroomImagePipeline:
    """调用方已取得并续约公共任务租约；本类不领取MQ消息或自行猜选源。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        extractor: FrameExtractor,
        object_store: ImageObjectStore,
        model: MultiImageModel,
        *,
        max_pts_error_ms: int,
        model_url_lifetime_seconds: int = 900,
        business_url_lifetime_seconds: int = 86400,
        max_model_attempts: int = 3,
        video_source: HttpVideoSource | None = None,
    ) -> None:
        if max_pts_error_ms < 0 or (
            min(
                model_url_lifetime_seconds,
                business_url_lifetime_seconds,
                max_model_attempts,
            )
            < 1
        ):
            raise ValueError("图像执行预算必须大于0")
        self.session_factory = session_factory
        self.extractor = extractor
        self.object_store = object_store
        self.model = model
        self.max_pts_error_ms = max_pts_error_ms
        self.model_url_lifetime_seconds = model_url_lifetime_seconds
        self.business_url_lifetime_seconds = business_url_lifetime_seconds
        self.max_model_attempts = max_model_attempts
        self.video_source = video_source

    def execute(
        self,
        *,
        request: ImageAnalysisRequest,
        plan: ClassroomSamplingPlan,
        prompt: ImagePromptSnapshot,
        generation: int,
        lease_owner: str,
    ) -> PipelineResult:
        reader = (
            VideoReadSession(
                self.session_factory,
                self.extractor,
                self.video_source,
                task_id=plan.internal_task_id,
                school_code=request.school_id,
                generation=generation,
                lease_owner=lease_owner,
            )
            if self.video_source is not None
            else None
        )
        try:
            result = self._execute(
                request=request,
                plan=plan,
                prompt=prompt,
                generation=generation,
                lease_owner=lease_owner,
                reader=reader,
            )
            if reader is not None:
                reader.finish()
            return result
        finally:
            if reader is not None:
                reader.close()

    def _execute(
        self,
        *,
        request: ImageAnalysisRequest,
        plan: ClassroomSamplingPlan,
        prompt: ImagePromptSnapshot,
        generation: int,
        lease_owner: str,
        reader: VideoReadSession | None,
    ) -> PipelineResult:
        if prompt.id == "" or not plan.source_selection.selected_videos:
            raise ValueError("课堂选源或提示词快照无效")
        task_id = plan.internal_task_id

        def now() -> datetime:
            return datetime.now(timezone.utc)

        with self.session_factory.begin() as session:
            ImageAnalysisRepository(session).initialize_plan(
                request=request,
                plan=plan,
                prompt=prompt,
                generation=generation,
                lease_owner=lease_owner,
                now=now(),
            )
        sources = {
            (stream.id, stream.stream_id): stream for stream in request.stream_info_list
        }
        videos = {
            video.video_id: sources[(video.stream_record_id, video.stream_id)]
            for video in plan.source_selection.selected_videos
        }
        uploaded = completed = skipped = 0
        sample_total = len(plan.samples)
        for sample_index, sample in enumerate(plan.samples, start=1):
            sample_started = perf_counter()
            with self.session_factory() as session:
                row = session.get(ImageSampleModel, sample.sample_id)
                if (
                    row is None
                    or row.task_id != task_id
                    or row.school_code != request.school_id
                ):
                    raise LookupError("采样点与课堂任务不匹配")
                status = row.status
            if status in {"COMPLETED", "UNAVAILABLE"}:
                skipped += 1
                LOGGER.info(
                    "图像采样点跳过: task_id=%s, sample_id=%s, status=%s",
                    task_id,
                    sample.sample_id,
                    status,
                )
                continue
            LOGGER.info(
                "图像采样点开始: task_id=%s, sample_id=%s, offset_ms=%s, planned_images=%s",
                task_id,
                sample.sample_id,
                sample.classroom_offset_ms,
                len(sample.frames),
            )
            for member in sample.frames:
                if member.video_offset_ms is None:
                    continue
                with self.session_factory() as session:
                    frame_row = session.scalar(
                        select(ImageFrameModel).where(
                            ImageFrameModel.sample_id == sample.sample_id,
                            ImageFrameModel.video_id == member.video_id,
                            ImageFrameModel.task_id == task_id,
                            ImageFrameModel.school_code == request.school_id,
                        )
                    )
                    if frame_row is None:
                        raise LookupError("采样图片检查点不存在")
                    already_uploaded = frame_row.status == "UPLOADED"
                if already_uploaded:
                    LOGGER.info(
                        "图像抽帧检查点复用: task_id=%s, sample_id=%s, video_id=%s",
                        task_id,
                        sample.sample_id,
                        member.video_id,
                    )
                    continue
                frame_started = perf_counter()
                extracted = (
                    reader.extract(
                        member.video_id,
                        videos[member.video_id].video_url,
                        member.video_offset_ms,
                    )
                    if reader is not None
                    else self.extractor.extract(
                        videos[member.video_id].video_url, member.video_offset_ms
                    )
                )
                LOGGER.info(
                    "图像抽帧完成: task_id=%s, sample_id=%s, video_id=%s, offset_ms=%s, actual_pts_ms=%s, jpeg_bytes=%s, duration_ms=%s",
                    task_id,
                    sample.sample_id,
                    member.video_id,
                    member.video_offset_ms,
                    extracted.actual_pts_ms,
                    len(extracted.jpeg),
                    round((perf_counter() - frame_started) * 1000),
                )
                if (
                    abs(extracted.actual_pts_ms - member.video_offset_ms)
                    > self.max_pts_error_ms
                ):
                    raise ValueError("抽帧实际PTS超出采样时间对齐阈值")
                digest = sha256(extracted.jpeg).hexdigest()
                key = (
                    f"image-analysis/{task_id}/{sample.sample_id}/{member.video_id}.jpg"
                )
                ref = self.object_store.put_jpeg(key, extracted.jpeg)
                with self.session_factory.begin() as session:
                    ImageAnalysisRepository(session).mark_frame_uploaded(
                        task_id=task_id,
                        school_code=request.school_id,
                        sample_id=sample.sample_id,
                        video_id=member.video_id,
                        generation=generation,
                        lease_owner=lease_owner,
                        now=now(),
                        storage_profile=ref.profile,
                        storage_provider=ref.provider,
                        bucket=ref.bucket,
                        object_key=ref.key,
                        content_hash=digest,
                        actual_pts_ms=extracted.actual_pts_ms,
                    )
                uploaded += 1
                LOGGER.info(
                    "图像上传完成: task_id=%s, sample_id=%s, video_id=%s, provider=%s, jpeg_bytes=%s",
                    task_id,
                    sample.sample_id,
                    member.video_id,
                    ref.provider,
                    len(extracted.jpeg),
                )
            with self.session_factory.begin() as session:
                frozen = ImageAnalysisRepository(session).freeze_sample_input(
                    task_id=task_id,
                    school_code=request.school_id,
                    sample_id=sample.sample_id,
                    generation=generation,
                    lease_owner=lease_owner,
                    now=now(),
                    max_model_attempts=self.max_model_attempts,
                )
                event_id = session.get(ImageSampleModel, sample.sample_id).event_id
            image_refs = tuple(
                ImageObjectRef(
                    image.storage_profile,
                    image.storage_provider,
                    image.bucket,
                    image.object_key,
                )
                for image in frozen.images
            )
            model_urls = tuple(
                self.object_store.signed_read_url(
                    ref, lifetime_seconds=self.model_url_lifetime_seconds
                )
                for ref in image_refs
            )
            model_started = perf_counter()
            model_started_at = now()
            LOGGER.info(
                "图像模型调用开始: task_id=%s, sample_id=%s, model=%s, image_count=%s, metric_count=%s",
                task_id,
                sample.sample_id,
                prompt.content.model,
                len(model_urls),
                len(plan.requested_metrics),
            )
            metrics = self.model.analyze(
                image_urls=model_urls,
                bundle=prompt.content,
                requested_metrics=plan.requested_metrics,
            )
            model_completed_at = now()
            model_duration_ms = round((perf_counter() - model_started) * 1000)
            LOGGER.info(
                "图像模型调用完成: task_id=%s, sample_id=%s, model=%s, metric_count=%s, duration_ms=%s",
                task_id,
                sample.sample_id,
                prompt.content.model,
                len(metrics),
                model_duration_ms,
            )
            business_urls = tuple(
                self.object_store.signed_read_url(
                    ref, lifetime_seconds=self.business_url_lifetime_seconds
                )
                for ref in image_refs
            )
            representative = {}
            for image, url in zip(frozen.images, business_urls):
                source = videos[image.video_id]
                for analysis_type in source.ai_analysis_type_list:
                    representative.setdefault(analysis_type.value, url)
            payload = {
                "id": request.message_id,
                "eventId": event_id,
                "eventType": "SAMPLE_RESULT",
                "internalTaskId": task_id,
                "sampleId": sample.sample_id,
                "sampleIndex": sample_index,
                "sampleTotal": sample_total,
                "classroomOffsetMs": sample.classroom_offset_ms,
                "capturedAt": sample.captured_at.isoformat(),
                "aiType": "LLM",
                "scheduleTime": request.to_payload()["scheduleTime"],
                "schoolId": request.school_id,
                "data": {
                    **metrics,
                    "classroomId": request.classroom_id,
                    "spaceId": request.space_id,
                },
                "fileList": list(business_urls),
                "aiAnalysisImageFileUrlMap": representative,
            }
            with self.session_factory.begin() as session:
                if ImageAnalysisRepository(session).complete_sample(
                    task_id=task_id,
                    school_code=request.school_id,
                    sample_id=sample.sample_id,
                    generation=generation,
                    lease_owner=lease_owner,
                    now=now(),
                    input_hash=frozen.input_hash,
                    metrics=metrics,
                    model_usage={
                        "model": prompt.content.model,
                        "imageCount": len(model_urls),
                        "metricCount": len(metrics),
                        "startedAt": model_started_at.isoformat(),
                        "completedAt": model_completed_at.isoformat(),
                        "durationMs": model_duration_ms,
                    },
                    notification_payload=payload,
                ):
                    completed += 1
            LOGGER.info(
                "图像采样点完成: task_id=%s, sample_id=%s, offset_ms=%s, image_count=%s, duration_ms=%s",
                task_id,
                sample.sample_id,
                sample.classroom_offset_ms,
                len(image_refs),
                round((perf_counter() - sample_started) * 1000),
            )
        return PipelineResult(uploaded, completed, skipped)


__all__ = ["ClassroomImagePipeline", "PipelineResult"]
