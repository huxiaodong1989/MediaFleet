"""图像规划与图片检查点仓储；写操作必须持有公共任务有效租约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from media_platform.application.image_prompt_service import ImagePromptSnapshot
from media_platform.contracts.image_analysis import (
    IMAGE_TASK_TYPE,
    BehaviorMetric,
    ImageAnalysisRequest,
    validate_metric_result,
)
from media_platform.infrastructure.database.models import (
    ImageFrameModel,
    ImageSampleModel,
    ImageTaskNotificationModel,
    ImageVideoModel,
    MediaTaskModel,
)
from services.image_analysis.planning import ClassroomSamplingPlan
from services.image_analysis.read_policy import ReadAction, SeekFailure, VideoReadPolicy


def source_identity_hash(stream_record_id: str, stream_id: str) -> str:
    payload = json.dumps(
        [stream_record_id, stream_id], ensure_ascii=False, separators=(",", ":")
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _plan_hash(
    request: ImageAnalysisRequest,
    plan: ClassroomSamplingPlan,
    prompt: ImagePromptSnapshot,
) -> str:
    """冻结完整规划但只保存摘要，避免在明细重复存带签名的视频地址。"""
    request_data = request.model_dump(mode="json")
    request_data["stream_info_list"].sort(
        key=lambda item: (item["id"], item["stream_id"])
    )
    plan_data = plan.model_dump(mode="json")
    selection = plan_data["source_selection"]
    for name in ("selected_videos", "excluded_videos"):
        selection[name].sort(
            key=lambda item: (item["stream_record_id"], item["stream_id"])
        )
    plan_data["samples"].sort(key=lambda item: item["classroom_offset_ms"])
    for sample in plan_data["samples"]:
        sample["frames"].sort(key=lambda item: item["video_id"])
    payload = {
        "request": request_data,
        "plan": plan_data,
        "prompt": {
            "id": prompt.id,
            "version": prompt.version,
            "hash": prompt.content_hash,
            "model": prompt.content.model_dump(mode="json", exclude={"prompt"}),
        },
    }
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class FrozenImage:
    video_id: str
    storage_profile: str
    storage_provider: str
    bucket: str
    object_key: str
    content_hash: str
    actual_pts_ms: int


@dataclass(frozen=True)
class FrozenSampleInput:
    sample_id: str
    input_hash: str
    prompt_bundle_id: str
    prompt_hash: str
    requested_metrics: tuple[str, ...]
    images: tuple[FrozenImage, ...]


@dataclass(frozen=True)
class PendingNotification:
    sample_id: str
    event_id: str
    payload: dict
    attempts: int


@dataclass(frozen=True)
class PendingTaskNotification:
    notification_id: str
    task_id: str
    event_id: str
    payload: dict
    attempts: int


@dataclass(frozen=True)
class VideoReadAttempt:
    video_id: str
    mode: str
    attempt: int


class ImageAnalysisRepository:
    """调用方开启事务；此仓储不领取任务或代替租约续期。"""

    def __init__(self, session: Session):
        self.session = session

    def _guard_task(
        self,
        *,
        task_id: str,
        school_code: str,
        generation: int,
        lease_owner: str,
        now: datetime,
    ) -> MediaTaskModel:
        if not lease_owner or generation < 1 or now.tzinfo is None:
            raise ValueError("必须提供有效执行代次、租约持有者和时区时间")
        task = self.session.scalar(
            select(MediaTaskModel)
            .where(
                MediaTaskModel.id == task_id,
                MediaTaskModel.school_code == school_code,
                MediaTaskModel.task_type == IMAGE_TASK_TYPE,
                MediaTaskModel.status == "processing",
                MediaTaskModel.execution_generation == generation,
                MediaTaskModel.lease_owner == lease_owner,
                MediaTaskModel.lease_expires_at > _naive_utc(now),
            )
            .with_for_update()
        )
        if task is None:
            raise LookupError("课堂任务租约失效或学校不匹配")
        return task

    def _selected_video(
        self, *, task_id: str, school_code: str, video_id: str
    ) -> ImageVideoModel:
        video = self.session.scalar(
            select(ImageVideoModel)
            .where(
                ImageVideoModel.id == video_id,
                ImageVideoModel.task_id == task_id,
                ImageVideoModel.school_code == school_code,
                ImageVideoModel.selection_status == "SELECTED",
            )
            .with_for_update()
        )
        if video is None:
            raise LookupError("入选视频不存在或学校不匹配")
        return video

    def claim_video_read(
        self,
        *,
        task_id: str,
        school_code: str,
        video_id: str,
        generation: int,
        lease_owner: str,
        now: datetime,
        policy: VideoReadPolicy,
        max_download_attempts: int,
    ) -> VideoReadAttempt:
        """外部IO前先持久化消耗一次预算；崩溃不会让下一实例无限重试。"""
        if max_download_attempts < 1:
            raise ValueError("完整下载预算必须大于0")
        self._guard_task(
            task_id=task_id,
            school_code=school_code,
            generation=generation,
            lease_owner=lease_owner,
            now=now,
        )
        video = self._selected_video(
            task_id=task_id, school_code=school_code, video_id=video_id
        )
        if video.read_status not in {"PENDING", "ACTIVE"}:
            raise ValueError("视频读取状态不允许领取")
        if (
            video.read_status == "ACTIVE"
            and (video.selection_evidence or {}).get("activeReadGeneration")
            == generation
        ):
            raise ValueError("当前执行代次已有进行中的视频读取")
        if video.next_retry_at is not None and video.next_retry_at > _naive_utc(now):
            raise ValueError("视频读取尚未到重试时间")
        if video.read_mode == "SEEK":
            if video.seek_attempts >= policy.seek_retry_limit + 1:
                raise ValueError("视频级seek预算已耗尽")
            video.seek_attempts += 1
            attempt = video.seek_attempts
        elif video.read_mode == "DOWNLOAD":
            if video.download_attempts >= max_download_attempts:
                raise ValueError("视频级下载预算已耗尽")
            video.download_attempts += 1
            attempt = video.download_attempts
        else:
            raise ValueError("未知视频读取模式")
        video.read_status = "ACTIVE"
        video.next_retry_at = None
        video.selection_evidence = {
            **(video.selection_evidence or {}),
            "activeReadGeneration": generation,
        }
        self.session.flush()
        return VideoReadAttempt(
            video_id=video_id, mode=video.read_mode, attempt=attempt
        )

    def record_video_read_failure(
        self,
        *,
        task_id: str,
        school_code: str,
        video_id: str,
        generation: int,
        lease_owner: str,
        now: datetime,
        attempt: VideoReadAttempt,
        failure: SeekFailure,
        policy: VideoReadPolicy,
        max_download_attempts: int,
        retry_delay: timedelta,
    ) -> ReadAction:
        """只允许当前持久化尝试写回；下载失败不能回跳seek。"""
        if retry_delay < timedelta(0) or max_download_attempts < 1:
            raise ValueError("读取重试配置无效")
        self._guard_task(
            task_id=task_id,
            school_code=school_code,
            generation=generation,
            lease_owner=lease_owner,
            now=now,
        )
        video = self._selected_video(
            task_id=task_id, school_code=school_code, video_id=video_id
        )
        if (
            video.read_status != "ACTIVE"
            or attempt.video_id != video_id
            or attempt.mode != video.read_mode
            or attempt.attempt
            != (
                video.seek_attempts
                if video.read_mode == "SEEK"
                else video.download_attempts
            )
        ):
            raise ValueError("视频读取尝试已过期")
        failure = SeekFailure(failure)
        if video.read_mode == "SEEK":
            action = policy.after_seek_failure(
                failure, retries_used=video.seek_attempts - 1
            )
        else:
            action = (
                ReadAction.DOWNLOAD
                if failure == SeekFailure.TRANSIENT
                and video.download_attempts < max_download_attempts
                else ReadAction.FAIL
            )
        if action == ReadAction.FAIL:
            video.read_status = "FAILED"
            video.next_retry_at = None
        else:
            video.read_status = "PENDING"
            video.next_retry_at = _naive_utc(now + retry_delay)
            if action == ReadAction.DOWNLOAD:
                video.read_mode = "DOWNLOAD"
        video.selection_evidence = {
            **(video.selection_evidence or {}),
            "lastReadFailure": failure.value,
            "activeReadGeneration": None,
        }
        self.session.flush()
        return action

    def complete_video_read(
        self,
        *,
        task_id: str,
        school_code: str,
        video_id: str,
        generation: int,
        lease_owner: str,
        now: datetime,
        attempt: VideoReadAttempt,
    ) -> None:
        """全部目标帧已取得后结束视频级读取检查点。"""
        self._guard_task(
            task_id=task_id,
            school_code=school_code,
            generation=generation,
            lease_owner=lease_owner,
            now=now,
        )
        video = self._selected_video(
            task_id=task_id, school_code=school_code, video_id=video_id
        )
        if (
            video.read_status != "ACTIVE"
            or video.read_mode != attempt.mode
            or attempt.attempt
            != (
                video.seek_attempts
                if attempt.mode == "SEEK"
                else video.download_attempts
            )
        ):
            raise ValueError("视频读取完成检查点与当前尝试不一致")
        video.read_status = "COMPLETED"
        video.selection_evidence = {
            **(video.selection_evidence or {}),
            "activeReadGeneration": None,
        }
        self.session.flush()

    def initialize_plan(
        self,
        *,
        request: ImageAnalysisRequest,
        plan: ClassroomSamplingPlan,
        prompt: ImagePromptSnapshot,
        generation: int,
        lease_owner: str,
        now: datetime,
    ) -> tuple[int, int, int]:
        """一事务写入完整选源/时间点/可采图片；重复消息只复用相同冻结规划。"""
        task_id = plan.internal_task_id
        self._guard_task(
            task_id=task_id,
            school_code=request.school_id,
            generation=generation,
            lease_owner=lease_owner,
            now=now,
        )
        if prompt.school_code not in {request.school_id, "GLOBAL"}:
            raise ValueError("提示词版本学校与课堂不匹配")
        if plan.requested_metrics != request.requested_metrics:
            raise ValueError("规划指标与请求不一致")
        selection = plan.source_selection
        selected = {video.video_id: video for video in selection.selected_videos}
        plan_hash = _plan_hash(request, plan, prompt)
        if any(len(video_id) > 36 for video_id in selected):
            raise ValueError("视频主键超过数据库长度")
        existing_videos = list(
            self.session.scalars(
                select(ImageVideoModel).where(
                    ImageVideoModel.task_id == task_id,
                    ImageVideoModel.school_code == request.school_id,
                )
            )
        )
        existing_samples = list(
            self.session.scalars(
                select(ImageSampleModel).where(
                    ImageSampleModel.task_id == task_id,
                    ImageSampleModel.school_code == request.school_id,
                )
            )
        )
        existing_frames = list(
            self.session.scalars(
                select(ImageFrameModel).where(
                    ImageFrameModel.task_id == task_id,
                    ImageFrameModel.school_code == request.school_id,
                )
            )
        )
        expected_frame_count = sum(
            frame.video_offset_ms is not None
            for sample in plan.samples
            for frame in sample.frames
        )
        if existing_videos or existing_samples or existing_frames:
            by_id = {row.id: row for row in existing_samples}
            expected_frames = {
                (sample.sample_id, frame.video_id): frame.video_offset_ms
                for sample in plan.samples
                for frame in sample.frames
                if frame.video_offset_ms is not None
            }
            if (
                len(existing_videos) != len(request.stream_info_list)
                or len(existing_samples) != len(plan.samples)
                or len(existing_frames) != expected_frame_count
                or any(
                    (row.selection_evidence or {}).get("planHash") != plan_hash
                    for row in existing_videos
                )
                or set(by_id) != {sample.sample_id for sample in plan.samples}
                or {
                    (row.sample_id, row.video_id): row.target_offset_ms
                    for row in existing_frames
                }
                != expected_frames
                or any(
                    by_id[sample.sample_id].offset_ms != sample.classroom_offset_ms
                    or by_id[sample.sample_id].prompt_bundle_id != prompt.id
                    or by_id[sample.sample_id].prompt_hash != prompt.content_hash
                    or by_id[sample.sample_id].expected_video_ids != sorted(selected)
                    for sample in plan.samples
                )
            ):
                raise ValueError("已持久化课堂规划与本次冻结规划冲突")
            return len(existing_videos), len(existing_samples), len(existing_frames)

        for stream in request.stream_info_list:
            identity = (stream.id, stream.stream_id)
            chosen = next(
                (
                    video
                    for video in selection.selected_videos
                    if (video.stream_record_id, video.stream_id) == identity
                ),
                None,
            )
            excluded = next(
                (
                    video
                    for video in selection.excluded_videos
                    if (video.stream_record_id, video.stream_id) == identity
                ),
                None,
            )
            if chosen is None and excluded is None:
                raise ValueError("选源快照未覆盖请求视频")
            stable_id = (
                chosen.video_id
                if chosen
                else str(
                    uuid5(
                        NAMESPACE_URL,
                        "mediafleet-image-video:"
                        + json.dumps([task_id, *identity], separators=(",", ":")),
                    )
                )
            )
            self.session.add(
                ImageVideoModel(
                    id=stable_id,
                    task_id=task_id,
                    school_code=request.school_id,
                    stream_record_id=stream.id,
                    stream_id=stream.stream_id,
                    source_identity_hash=source_identity_hash(*identity),
                    source_type=stream.streaming_media_source.value,
                    recording_task_id=stream.recording_task_id,
                    ai_config=[metric.value for metric in stream.ai_config],
                    probe_result={"durationMs": chosen.duration_ms} if chosen else None,
                    time_axis={
                        "startedAt": chosen.started_at.isoformat(),
                        "basis": chosen.timeline_basis,
                    }
                    if chosen
                    else None,
                    selection_status="SELECTED" if chosen else "EXCLUDED",
                    selection_reason="selected" if chosen else excluded.reason,
                    selection_evidence={
                        "policyVersion": selection.policy_version,
                        "planHash": plan_hash,
                    },
                )
            )
        self.session.flush()

        metrics = [metric.value for metric in plan.requested_metrics]
        unsupported = [metric.value for metric in plan.unsupported_metrics]
        for sample in plan.samples:
            if sample.internal_task_id != task_id or len(sample.sample_id) > 36:
                raise ValueError("采样点任务或主键无效")
            outside = any(frame.video_offset_ms is None for frame in sample.frames)
            self.session.add(
                ImageSampleModel(
                    id=sample.sample_id,
                    task_id=task_id,
                    school_code=request.school_id,
                    offset_ms=sample.classroom_offset_ms,
                    sample_time=_naive_utc(sample.captured_at),
                    expected_video_ids=sorted(selected),
                    requested_metrics=metrics,
                    unsupported_metrics=unsupported,
                    status="UNAVAILABLE" if outside or unsupported else "WAITING",
                    unavailable_reason=(
                        "outside_video_coverage"
                        if outside
                        else "unsupported_metrics"
                        if unsupported
                        else None
                    ),
                    prompt_bundle_id=prompt.id,
                    prompt_version=prompt.version,
                    prompt_hash=prompt.content_hash,
                    model_snapshot=prompt.content.model_dump(
                        mode="json", exclude={"prompt"}
                    ),
                    event_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            "mediafleet-image-event:" + sample.sample_id,
                        )
                    ),
                )
            )
        self.session.flush()
        for sample in plan.samples:
            for member in sample.frames:
                if member.video_offset_ms is None:
                    continue
                if member.video_id not in selected:
                    raise ValueError("采样图片引用了未入选视频")
                self.session.add(
                    ImageFrameModel(
                        id=str(
                            uuid5(
                                NAMESPACE_URL,
                                "mediafleet-image-frame:"
                                + sample.sample_id
                                + ":"
                                + member.video_id,
                            )
                        ),
                        task_id=task_id,
                        school_code=request.school_id,
                        sample_id=sample.sample_id,
                        video_id=member.video_id,
                        target_offset_ms=member.video_offset_ms,
                    )
                )
        self.session.flush()
        return len(request.stream_info_list), len(plan.samples), expected_frame_count

    def mark_frame_uploaded(
        self,
        *,
        task_id: str,
        school_code: str,
        sample_id: str,
        video_id: str,
        generation: int,
        lease_owner: str,
        now: datetime,
        storage_profile: str,
        storage_provider: str,
        bucket: str,
        object_key: str,
        content_hash: str,
        actual_pts_ms: int,
    ) -> bool:
        """同一采样点串行完成组图屏障；返回组是否已齐备可推理。"""
        self._guard_task(
            task_id=task_id,
            school_code=school_code,
            generation=generation,
            lease_owner=lease_owner,
            now=now,
        )
        if (
            storage_provider not in {"COS", "MINIO"}
            or not storage_profile
            or not bucket
            or not object_key
            or len(content_hash) != 64
            or actual_pts_ms < 0
        ):
            raise ValueError("图片存储检查点参数无效")
        sample = self.session.scalar(
            select(ImageSampleModel)
            .where(
                ImageSampleModel.id == sample_id,
                ImageSampleModel.task_id == task_id,
                ImageSampleModel.school_code == school_code,
            )
            .with_for_update()
        )
        if sample is None or sample.status in {"UNAVAILABLE", "FAILED"}:
            raise LookupError("采样点不存在或不可写")
        frame = self.session.scalar(
            select(ImageFrameModel)
            .where(
                ImageFrameModel.sample_id == sample_id,
                ImageFrameModel.video_id == video_id,
                ImageFrameModel.task_id == task_id,
                ImageFrameModel.school_code == school_code,
            )
            .with_for_update()
        )
        if frame is None:
            raise LookupError("图片检查点不存在")
        identity = (
            storage_profile,
            storage_provider,
            bucket,
            object_key,
            content_hash,
            actual_pts_ms,
        )
        current = (
            frame.storage_profile,
            frame.storage_provider,
            frame.bucket,
            frame.object_key,
            frame.content_hash,
            frame.actual_pts_ms,
        )
        if frame.status == "UPLOADED":
            if current != identity:
                raise ValueError("已上传图片的存储引用不可覆盖")
            return sample.status == "READY"
        if sample.status != "WAITING" or frame.status not in {"PENDING", "EXTRACTED"}:
            raise ValueError("采样点或图片状态不允许上传写回")
        (
            frame.storage_profile,
            frame.storage_provider,
            frame.bucket,
            frame.object_key,
            frame.content_hash,
            frame.actual_pts_ms,
        ) = identity
        frame.status = "UPLOADED"
        frame.upload_attempts = int(frame.upload_attempts or 0) + 1
        self.session.flush()
        frames = list(
            self.session.scalars(
                select(ImageFrameModel).where(
                    ImageFrameModel.sample_id == sample_id,
                    ImageFrameModel.task_id == task_id,
                    ImageFrameModel.school_code == school_code,
                )
            )
        )
        ready = (
            len(frames) == len(sample.expected_video_ids)
            and {item.video_id for item in frames} == set(sample.expected_video_ids)
            and all(item.status == "UPLOADED" for item in frames)
        )
        if ready:
            sample.status = "READY"
            self.session.flush()
        return ready

    def freeze_sample_input(
        self,
        *,
        task_id: str,
        school_code: str,
        sample_id: str,
        generation: int,
        lease_owner: str,
        now: datetime,
        max_model_attempts: int,
    ) -> FrozenSampleInput:
        """上传齐备后冻结模型输入并消耗组级尝试次数；重试必须复用相同图片。"""
        self._guard_task(
            task_id=task_id,
            school_code=school_code,
            generation=generation,
            lease_owner=lease_owner,
            now=now,
        )
        if max_model_attempts < 1:
            raise ValueError("模型尝试预算必须大于0")
        sample = self.session.scalar(
            select(ImageSampleModel)
            .where(
                ImageSampleModel.id == sample_id,
                ImageSampleModel.task_id == task_id,
                ImageSampleModel.school_code == school_code,
            )
            .with_for_update()
        )
        if sample is None or sample.status not in {"READY", "PROCESSING"}:
            raise LookupError("采样点未齐备或不可推理")
        frames = list(
            self.session.scalars(
                select(ImageFrameModel).where(
                    ImageFrameModel.sample_id == sample_id,
                    ImageFrameModel.task_id == task_id,
                    ImageFrameModel.school_code == school_code,
                )
            )
        )
        frames.sort(key=lambda row: row.video_id)
        if (
            len(frames) != len(sample.expected_video_ids)
            or [frame.video_id for frame in frames] != sorted(sample.expected_video_ids)
            or any(frame.status != "UPLOADED" for frame in frames)
        ):
            raise ValueError("采样点预期图片尚未全部上传")
        images = tuple(
            FrozenImage(
                video_id=frame.video_id,
                storage_profile=frame.storage_profile,
                storage_provider=frame.storage_provider,
                bucket=frame.bucket,
                object_key=frame.object_key,
                content_hash=frame.content_hash,
                actual_pts_ms=frame.actual_pts_ms,
            )
            for frame in frames
        )
        payload = {
            "promptId": sample.prompt_bundle_id,
            "promptHash": sample.prompt_hash,
            "metrics": sample.requested_metrics,
            "images": [image.__dict__ for image in images],
        }
        digest = sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if sample.input_hash is not None and sample.input_hash != digest:
            raise ValueError("模型重试输入与冻结摘要冲突")
        if int(sample.model_attempts or 0) >= max_model_attempts:
            raise ValueError("采样点模型尝试预算已耗尽")
        sample.input_hash = digest
        sample.status = "PROCESSING"
        sample.model_attempts = int(sample.model_attempts or 0) + 1
        self.session.flush()
        return FrozenSampleInput(
            sample_id=sample_id,
            input_hash=digest,
            prompt_bundle_id=sample.prompt_bundle_id,
            prompt_hash=sample.prompt_hash,
            requested_metrics=tuple(sample.requested_metrics),
            images=images,
        )

    def complete_sample(
        self,
        *,
        task_id: str,
        school_code: str,
        sample_id: str,
        generation: int,
        lease_owner: str,
        now: datetime,
        input_hash: str,
        metrics: dict[str, int],
        model_usage: dict,
        notification_payload: dict,
    ) -> bool:
        """结果和待发通知在调用方同一事务提交；重复完成不覆盖历史快照。"""
        task = self._guard_task(
            task_id=task_id,
            school_code=school_code,
            generation=generation,
            lease_owner=lease_owner,
            now=now,
        )
        sample = self.session.scalar(
            select(ImageSampleModel)
            .where(
                ImageSampleModel.id == sample_id,
                ImageSampleModel.task_id == task_id,
                ImageSampleModel.school_code == school_code,
            )
            .with_for_update()
        )
        if sample is None:
            raise LookupError("采样点不存在")
        if sample.input_hash != input_hash or not input_hash:
            raise ValueError("模型结果与冻结输入摘要不匹配")
        validated = validate_metric_result(
            metrics, tuple(BehaviorMetric(item) for item in sample.requested_metrics)
        )
        data = (
            notification_payload.get("data")
            if isinstance(notification_payload, dict)
            else None
        )
        request_snapshot = (task.params or {}).get("request")
        business_message_id = (
            request_snapshot.get("messageId")
            if isinstance(request_snapshot, dict)
            else None
        )
        request_schedule_time = (
            request_snapshot.get("scheduleTime")
            if isinstance(request_snapshot, dict)
            else None
        )
        if (
            not isinstance(business_message_id, str)
            or not business_message_id
            or not isinstance(request_schedule_time, str)
            or not request_schedule_time
            or not isinstance(notification_payload, dict)
            or notification_payload.get("id") != business_message_id
            or notification_payload.get("scheduleTime") != request_schedule_time
            or not isinstance(data, dict)
            or not isinstance(data.get("classroomId"), str)
            or not data["classroomId"]
            or not isinstance(data.get("spaceId"), str)
            or not data["spaceId"]
            or notification_payload.get("eventId") != sample.event_id
            or notification_payload.get("eventType") != "SAMPLE_RESULT"
            or notification_payload.get("internalTaskId") != task_id
            or notification_payload.get("sampleId") != sample_id
            or not isinstance(notification_payload.get("sampleIndex"), int)
            or notification_payload["sampleIndex"] < 1
            or not isinstance(notification_payload.get("sampleTotal"), int)
            or notification_payload["sampleTotal"] < notification_payload["sampleIndex"]
            or notification_payload.get("classroomOffsetMs") != sample.offset_ms
            or not isinstance(notification_payload.get("capturedAt"), str)
            or not notification_payload["capturedAt"]
            or notification_payload.get("schoolId") != school_code
            or data
            != {
                **validated,
                "classroomId": data["classroomId"],
                "spaceId": data["spaceId"],
            }
        ):
            raise ValueError("通知快照与采样点结果或租户不匹配")
        if sample.status == "COMPLETED":
            if (
                sample.result != validated
                or sample.notification_payload != notification_payload
            ):
                raise ValueError("已完成采样点不可被新结果覆盖")
            return False
        if sample.status != "PROCESSING":
            raise ValueError("采样点尚未进入模型推理")
        sample.result = validated
        sample.model_usage = model_usage
        sample.notification_payload = notification_payload
        sample.notification_status = "PENDING"
        sample.status = "COMPLETED"
        self.session.flush()
        return True

    def claim_notifications(
        self,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int,
        limit: int = 20,
        max_attempts: int = 10,
    ) -> tuple[PendingNotification, ...]:
        """允许父课堂终态后继续补发；多实例按采样行领取。"""
        if (
            not owner
            or now.tzinfo is None
            or lease_seconds < 1
            or not 1 <= limit <= 100
            or max_attempts < 1
        ):
            raise ValueError("通知领取参数无效")
        current = _naive_utc(now)
        rows = list(
            self.session.scalars(
                select(ImageSampleModel)
                .where(
                    ImageSampleModel.status == "COMPLETED",
                    ImageSampleModel.notification_payload.is_not(None),
                    or_(
                        ImageSampleModel.notification_status == "PENDING",
                        (ImageSampleModel.notification_status == "CLAIMED")
                        & (ImageSampleModel.notification_claim_until < current),
                    ),
                )
                .order_by(ImageSampleModel.sample_time, ImageSampleModel.id)
                .limit(limit)
                .with_for_update()
            )
        )
        claimed = []
        for row in rows:
            if int(row.notification_attempts or 0) >= max_attempts:
                row.notification_status = "FAILED"
                row.notification_claim_owner = None
                row.notification_claim_until = None
                continue
            row.notification_status = "CLAIMED"
            row.notification_claim_owner = owner
            row.notification_claim_until = current + timedelta(seconds=lease_seconds)
            row.notification_attempts = int(row.notification_attempts or 0) + 1
            claimed.append(row)
        self.session.flush()
        return tuple(
            PendingNotification(
                sample_id=row.id,
                event_id=row.event_id,
                payload=row.notification_payload,
                attempts=row.notification_attempts,
            )
            for row in claimed
        )

    def settle_notification(
        self,
        *,
        sample_id: str,
        owner: str,
        now: datetime,
        confirmed: bool,
        max_attempts: int = 10,
    ) -> bool:
        """仅当前有效领取者可确认；发布失败回到待发布而不重跑模型。"""
        if not owner or now.tzinfo is None or max_attempts < 1:
            raise ValueError("通知结算参数无效")
        row = self.session.scalar(
            select(ImageSampleModel)
            .where(
                ImageSampleModel.id == sample_id,
            )
            .with_for_update()
        )
        if (
            row is None
            or row.notification_status != "CLAIMED"
            or row.notification_claim_owner != owner
            or row.notification_claim_until is None
            or row.notification_claim_until <= _naive_utc(now)
        ):
            return False
        row.notification_status = (
            "PUBLISHED"
            if confirmed
            else "FAILED"
            if row.notification_attempts >= max_attempts
            else "PENDING"
        )
        row.notification_claim_owner = None
        row.notification_claim_until = None
        self.session.flush()
        return True

    def ensure_task_terminal_notification(
        self,
        *,
        task_id: str,
        school_code: str,
    ) -> bool:
        """任务进入最终状态时持久化唯一终态通知意图。"""
        task = self.session.scalar(
            select(MediaTaskModel)
            .where(
                MediaTaskModel.id == task_id,
                MediaTaskModel.school_code == school_code,
                MediaTaskModel.task_type == IMAGE_TASK_TYPE,
            )
            .with_for_update()
        )
        if task is None or task.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("图像任务尚未进入最终状态")
        existing = self.session.scalar(
            select(ImageTaskNotificationModel)
            .where(ImageTaskNotificationModel.task_id == task_id)
            .with_for_update()
        )
        if existing is not None:
            return False
        if task.status == "failed":
            samples = list(
                self.session.scalars(
                    select(ImageSampleModel)
                    .where(ImageSampleModel.task_id == task_id)
                    .with_for_update()
                )
            )
            for sample in samples:
                if sample.status not in {"COMPLETED", "UNAVAILABLE", "FAILED"}:
                    sample.status = "FAILED"
                    sample.unavailable_reason = sample.unavailable_reason or "task_failed"
        self.session.add(
            ImageTaskNotificationModel(
                id=str(uuid5(NAMESPACE_URL, "mediafleet-image-task-notice:" + task_id)),
                task_id=task_id,
                school_code=school_code,
                event_id=str(
                    uuid5(
                        NAMESPACE_URL,
                        "mediafleet-image-task-terminated:" + task_id,
                    )
                ),
                notification_status="WAITING",
            )
        )
        self.session.flush()
        return True

    @staticmethod
    def _expected_sample_total(
        task: MediaTaskModel, samples: list[ImageSampleModel]
    ) -> int:
        if samples:
            return len(samples)
        snapshot = (task.params or {}).get("request")
        request = ImageAnalysisRequest.model_validate(snapshot)
        interval_ms = request.sample_interval_seconds * 1000
        return (request.duration_ms + interval_ms - 1) // interval_ms

    @staticmethod
    def _terminal_payload(
        task: MediaTaskModel,
        notice: ImageTaskNotificationModel,
        samples: list[ImageSampleModel],
    ) -> dict:
        snapshot = (task.params or {}).get("request")
        request = ImageAnalysisRequest.model_validate(snapshot)
        sample_total = ImageAnalysisRepository._expected_sample_total(task, samples)
        success_count = sum(row.status == "COMPLETED" for row in samples)
        unavailable_count = sum(row.status == "UNAVAILABLE" for row in samples)
        failed_count = max(sample_total - success_count - unavailable_count, 0)
        if task.status == "completed" and failed_count == 0 and unavailable_count == 0:
            analysis_status = "COMPLETED"
        elif success_count:
            analysis_status = "PARTIAL"
        else:
            analysis_status = "FAILED"
        published_count = sum(row.notification_status == "PUBLISHED" for row in samples)
        publish_failed_count = sum(
            row.notification_status == "FAILED" and row.notification_payload is not None
            for row in samples
        )
        completed_at = task.completed_at or datetime.now(timezone.utc)
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        return {
            "id": request.message_id,
            "eventId": notice.event_id,
            "eventType": "TASK_TERMINATED",
            "internalTaskId": task.id,
            "scheduleTime": request.to_payload()["scheduleTime"],
            "schoolId": request.school_id,
            "classroomId": request.classroom_id,
            "spaceId": request.space_id,
            "status": analysis_status,
            "sampleTotal": sample_total,
            "successCount": success_count,
            "failedCount": failed_count,
            "unavailableCount": unavailable_count,
            "resultPublishedCount": published_count,
            "resultPublishFailedCount": publish_failed_count,
            "finishedAt": completed_at.astimezone(timezone.utc).isoformat(),
        }

    def claim_task_terminal_notifications(
        self,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int,
        limit: int = 20,
        max_attempts: int = 10,
    ) -> tuple[PendingTaskNotification, ...]:
        """所有采样通知结束发布尝试后，才领取任务终态通知。"""
        if (
            not owner
            or now.tzinfo is None
            or lease_seconds < 1
            or not 1 <= limit <= 100
            or max_attempts < 1
        ):
            raise ValueError("任务终态通知领取参数无效")
        current = _naive_utc(now)
        notices = list(
            self.session.scalars(
                select(ImageTaskNotificationModel)
                .where(
                    or_(
                        ImageTaskNotificationModel.notification_status == "WAITING",
                        (ImageTaskNotificationModel.notification_status == "CLAIMED")
                        & (
                            ImageTaskNotificationModel.notification_claim_until
                            < current
                        ),
                    )
                )
                .order_by(
                    ImageTaskNotificationModel.created_at,
                    ImageTaskNotificationModel.id,
                )
                .limit(limit)
                .with_for_update()
            )
        )
        claimed = []
        for notice in notices:
            if int(notice.notification_attempts or 0) >= max_attempts:
                notice.notification_status = "FAILED"
                notice.notification_claim_owner = None
                notice.notification_claim_until = None
                continue
            task = self.session.get(MediaTaskModel, notice.task_id)
            if task is None or task.status not in {"completed", "failed", "cancelled"}:
                continue
            samples = list(
                self.session.scalars(
                    select(ImageSampleModel)
                    .where(ImageSampleModel.task_id == notice.task_id)
                    .order_by(ImageSampleModel.offset_ms, ImageSampleModel.id)
                )
            )
            if any(
                row.notification_payload is not None
                and row.notification_status not in {"PUBLISHED", "FAILED"}
                for row in samples
            ):
                continue
            payload = notice.notification_payload or self._terminal_payload(
                task, notice, samples
            )
            if payload.get("eventId") != notice.event_id:
                raise ValueError("任务终态通知事件编号不一致")
            notice.notification_payload = payload
            notice.notification_status = "CLAIMED"
            notice.notification_claim_owner = owner
            notice.notification_claim_until = current + timedelta(seconds=lease_seconds)
            notice.notification_attempts = int(notice.notification_attempts or 0) + 1
            claimed.append(notice)
        self.session.flush()
        return tuple(
            PendingTaskNotification(
                notification_id=row.id,
                task_id=row.task_id,
                event_id=row.event_id,
                payload=row.notification_payload,
                attempts=row.notification_attempts,
            )
            for row in claimed
        )

    def settle_task_terminal_notification(
        self,
        *,
        notification_id: str,
        owner: str,
        now: datetime,
        confirmed: bool,
        max_attempts: int = 10,
    ) -> bool:
        if not owner or now.tzinfo is None or max_attempts < 1:
            raise ValueError("任务终态通知结算参数无效")
        row = self.session.scalar(
            select(ImageTaskNotificationModel)
            .where(ImageTaskNotificationModel.id == notification_id)
            .with_for_update()
        )
        if (
            row is None
            or row.notification_status != "CLAIMED"
            or row.notification_claim_owner != owner
            or row.notification_claim_until is None
            or row.notification_claim_until <= _naive_utc(now)
        ):
            return False
        row.notification_status = (
            "PUBLISHED"
            if confirmed
            else "FAILED"
            if row.notification_attempts >= max_attempts
            else "WAITING"
        )
        row.notification_claim_owner = None
        row.notification_claim_until = None
        self.session.flush()
        return True


__all__ = [
    "FrozenImage",
    "FrozenSampleInput",
    "ImageAnalysisRepository",
    "PendingNotification",
    "PendingTaskNotification",
    "VideoReadAttempt",
    "source_identity_hash",
]
