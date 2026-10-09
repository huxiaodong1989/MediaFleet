"""受限的课堂统一采样规划；输入是已完成选源的快照，不做探测或质量推断。"""

from __future__ import annotations

import json
from datetime import timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field, TypeAdapter

from media_platform.contracts.image_analysis import (
    BehaviorMetric,
    Identifier,
    ImageAnalysisRequest,
    ImageContract,
    ImageSamplePlan,
    PlannedSampleFrame,
    PositiveInt,
    SourceSelectionSnapshot,
)


class SamplingLimits(ImageContract):
    """由部署传入预算，不在未确认容量前给生产写死经验阈值。"""

    min_interval_seconds: PositiveInt
    max_videos: PositiveInt
    max_classroom_duration_ms: PositiveInt
    max_samples: PositiveInt
    max_images: PositiveInt


class ClassroomSamplingPlan(ImageContract):
    internal_task_id: Identifier
    source_selection: SourceSelectionSnapshot
    requested_metrics: tuple[BehaviorMetric, ...] = Field(min_length=1)
    unsupported_metrics: tuple[BehaviorMetric, ...]
    samples: tuple[ImageSamplePlan, ...] = Field(min_length=1)


def plan_classroom_samples(
    *,
    internal_task_id: str,
    request: ImageAnalysisRequest,
    selection: SourceSelectionSnapshot,
    limits: SamplingLimits,
) -> ClassroomSamplingPlan:
    """先检查总预算再分配。任务/偏移决定稳定sampleId，与重试及流顺序无关。

    unsupported_metrics只是配置能力缺口，不证明画面能判断；后续执行还需质量、
    实际PTS校验及组图屏障。选源和原请求必须属于同一任务，绑定由持久层保证。
    """
    internal_task_id = TypeAdapter(Identifier).validate_python(internal_task_id)
    if request.sample_interval_seconds < limits.min_interval_seconds:
        raise ValueError("sample interval exceeds deployment budget")
    if len(request.stream_info_list) > limits.max_videos:
        raise ValueError("too many input videos")
    if request.duration_ms > limits.max_classroom_duration_ms:
        raise ValueError("classroom duration exceeds deployment budget")
    interval_ms = request.sample_interval_seconds * 1000
    count = (request.duration_ms + interval_ms - 1) // interval_ms
    if (
        count > limits.max_samples
        or count * len(selection.selected_videos) > limits.max_images
    ):
        raise ValueError("sample/image count exceeds deployment budget")

    streams = {(s.id, s.stream_id): s for s in request.stream_info_list}
    all_sources = (*selection.selected_videos, *selection.excluded_videos)
    if {(s.stream_record_id, s.stream_id) for s in all_sources} != set(streams):
        raise ValueError(
            "source selection must account for every request stream exactly once"
        )
    supported = set()
    for video in selection.selected_videos:
        stream = streams[(video.stream_record_id, video.stream_id)]
        if (
            stream.video_started_at is not None
            and stream.video_started_at != video.started_at
        ):
            raise ValueError("selected timeline conflicts with request videoStartedAt")
        supported.update(stream.ai_config)
    unsupported = tuple(
        metric for metric in request.requested_metrics if metric not in supported
    )
    origin = request.start_time.astimezone(timezone.utc)
    selected_videos = sorted(
        selection.selected_videos, key=lambda v: (v.stream_record_id, v.stream_id)
    )
    samples = []
    for offset_ms in range(0, request.duration_ms, interval_ms):
        captured_at = origin + timedelta(milliseconds=offset_ms)
        members = []
        for video in selected_videos:
            delta = captured_at - video.started_at
            video_offset = (
                delta.days * 86400 + delta.seconds
            ) * 1000 + delta.microseconds // 1000
            available = 0 <= video_offset < video.duration_ms
            members.append(
                PlannedSampleFrame(
                    video_id=video.video_id,
                    video_offset_ms=video_offset if available else None,
                    unavailable_reason=None if available else "outside_video_coverage",
                )
            )
        identity = json.dumps([internal_task_id, offset_ms], separators=(",", ":"))
        samples.append(
            ImageSamplePlan(
                sample_id=str(
                    uuid5(NAMESPACE_URL, "mediafleet-image-sample:" + identity)
                ),
                internal_task_id=internal_task_id,
                classroom_offset_ms=offset_ms,
                captured_at=captured_at,
                frames=tuple(members),
            )
        )
    return ClassroomSamplingPlan(
        internal_task_id=internal_task_id,
        source_selection=selection,
        requested_metrics=request.requested_metrics,
        unsupported_metrics=unsupported,
        samples=tuple(samples),
    )
