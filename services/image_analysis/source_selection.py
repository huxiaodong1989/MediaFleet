"""组图前的保守选源；阈值由部署显式给出，不由最长视频推断连续性。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from media_platform.contracts.image_analysis import (
    ExcludedVideo,
    Identifier,
    ImageAnalysisRequest,
    ImageContract,
    SelectedVideo,
    SourceSelectionSnapshot,
)


class SourceSelectionPolicy(ImageContract):
    version: Identifier
    minimum_classroom_coverage: float = Field(strict=True, gt=0, le=1)
    minimum_duration_ratio_to_longest: float = Field(strict=True, gt=0, le=1)
    require_continuity: bool = True


@dataclass(frozen=True)
class SourceProbeEvidence:
    stream_record_id: str
    stream_id: str
    duration_ms: int | None
    started_at: datetime | None
    timeline_basis: str | None
    continuous: bool
    probe_failed: bool = False


def _overlap_ms(
    class_start: datetime,
    class_end: datetime,
    video_start: datetime,
    duration_ms: int,
) -> int:
    video_end = video_start + timedelta(milliseconds=duration_ms)
    overlap = min(class_end, video_end) - max(class_start, video_start)
    return max(0, round(overlap.total_seconds() * 1000))


def select_classroom_sources(
    *,
    task_id: str,
    request: ImageAnalysisRequest,
    evidence: tuple[SourceProbeEvidence, ...],
    policy: SourceSelectionPolicy,
) -> SourceSelectionSnapshot:
    """只选有可信连续时间轴且覆盖达标的视频；零合格源明确失败。"""
    expected = {(stream.id, stream.stream_id) for stream in request.stream_info_list}
    by_identity = {(item.stream_record_id, item.stream_id): item for item in evidence}
    if len(by_identity) != len(evidence) or set(by_identity) != expected:
        raise ValueError("探测证据必须与请求视频一一对应")
    if not task_id.strip():
        raise ValueError("内部任务编号不能为空")
    streams = {
        (stream.id, stream.stream_id): stream for stream in request.stream_info_list
    }
    start = request.start_time.astimezone(timezone.utc)
    end = request.end_time.astimezone(timezone.utc)
    preliminary: list[SourceProbeEvidence] = []
    exclusions: dict[tuple[str, str], str] = {}
    for identity in sorted(expected):
        item = by_identity[identity]
        supplied_start = streams[identity].video_started_at
        if supplied_start is not None and item.started_at != supplied_start:
            raise ValueError("探测时间轴与请求videoStartedAt冲突")
        if item.probe_failed:
            exclusions[identity] = "probe_failed"
        elif (
            isinstance(item.duration_ms, bool)
            or not isinstance(item.duration_ms, int)
            or item.duration_ms < 1
        ):
            exclusions[identity] = "invalid_video"
        elif (
            (policy.require_continuity and not item.continuous)
            or item.started_at is None
            or item.started_at.tzinfo is None
            or item.started_at.utcoffset() is None
            or item.timeline_basis
            not in (
                {"recording_metadata", "upstream_continuity_guarantee"}
                if policy.require_continuity
                else {
                    "recording_metadata",
                    "upstream_continuity_guarantee",
                    "duration_heuristic",
                }
            )
        ):
            exclusions[identity] = "timeline_unknown"
        elif (
            _overlap_ms(start, end, item.started_at, item.duration_ms)
            / request.duration_ms
            < policy.minimum_classroom_coverage
        ):
            exclusions[identity] = "insufficient_coverage"
        else:
            preliminary.append(item)
    if not preliminary:
        raise ValueError("没有满足连续时间轴与课堂覆盖要求的视频来源")
    # 仅比较课堂有效覆盖，不能把课前/课后多录的总时长作为完整基准。
    longest = max(
        _overlap_ms(start, end, item.started_at, item.duration_ms)
        for item in preliminary
    )
    if longest == 0:
        raise ValueError("视频与课堂时间窗口没有交集")
    selected = []
    for item in preliminary:
        identity = (item.stream_record_id, item.stream_id)
        if (
            _overlap_ms(start, end, item.started_at, item.duration_ms) / longest
            < policy.minimum_duration_ratio_to_longest
        ):
            exclusions[identity] = "duration_outlier"
            continue
        stable_id = str(
            uuid5(
                NAMESPACE_URL,
                "mediafleet-image-video:"
                + json.dumps(
                    [task_id, item.stream_record_id, item.stream_id],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        )
        selected.append(
            SelectedVideo(
                video_id=stable_id,
                stream_record_id=item.stream_record_id,
                stream_id=item.stream_id,
                duration_ms=item.duration_ms,
                started_at=item.started_at,
                timeline_basis=item.timeline_basis,
            )
        )
    if not selected:
        raise ValueError("视频来源时长差异超出策略允许范围")
    return SourceSelectionSnapshot(
        policy_version=policy.version,
        selected_videos=tuple(selected),
        excluded_videos=tuple(
            ExcludedVideo(
                stream_record_id=identity[0],
                stream_id=identity[1],
                reason=reason,
            )
            for identity, reason in sorted(exclusions.items())
        ),
    )


__all__ = ["SourceProbeEvidence", "SourceSelectionPolicy", "select_classroom_sources"]
