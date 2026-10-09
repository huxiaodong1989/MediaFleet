"""离线课堂图像分析契约；不执行网络访问、选源、模型或旧 hook 发布。

外部沿用 AiLlmDTO camelCase。URL 校验只检查语法，不能替代执行层的
SSRF/租户授权检查；请求的 scheduleTime 是调度时间，结果 hook 的
id 确认为原请求 messageId，scheduleTime 透传首次接单的请求调度时间。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel


IMAGE_TASK_TYPE = "image.three_rates.analyze"
IMAGE_PROMPT_SCHEME = "CLASSROOM_IMAGE_BEHAVIOR"
LEGACY_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
Identifier = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128),
]
SchoolId = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=64),
]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
PromptText = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=100_000
    ),
]


class ImageContract(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_conflicting_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for name, field in cls.model_fields.items():
            alias = field.alias
            if alias and alias != name and alias in data and name in data:
                if data[alias] != data[name]:
                    raise ValueError(f"conflicting aliases for {alias}")
                del data[name]
        return data

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class AiAnalysisType(str, Enum):
    S_ANALYSIS = "S_ANALYSIS"
    T_ANALYSIS = "T_ANALYSIS"
    GRAPHIC_ANALYSIS = "GRAPHIC_ANALYSIS"
    VOICE_ANALYSIS = "VOICE_ANALYSIS"


class StreamingMediaSource(str, Enum):
    ROOM_CAMERA = "ROOM_CAMERA"
    TEACHER_CAMERA = "TEACHER_CAMERA"
    COMPUTER_DESKTOPS = "COMPUTER_DESKTOPS"
    CONTROL_PANEL = "CONTROL_PANEL"
    ELECTRONIC_CLASS_CARDS = "ELECTRONIC_CLASS_CARDS"


class BehaviorMetric(str, Enum):
    HEAD_UP = "HEAD_UP"
    HEAD_DOWN = "HEAD_DOWN"
    REST = "REST"
    HAND_UP = "HAND_UP"
    STAND_UP = "STAND_UP"
    WRITE = "WRITE"
    READ = "READ"
    LAUGH = "LAUGH"
    CRY = "CRY"
    PEACE = "PEACE"
    FRONT_NUMBER_PEOPLE = "FRONT_NUMBER_PEOPLE"
    FRONT_SEATS = "FRONT_SEATS"
    STUDENT_NUM = "STUDENT_NUM"
    PATROL = "PATROL"
    LECTURE = "LECTURE"
    BLACKBOARD = "BLACKBOARD"
    BACK_TO_STUDENT = "BACK_TO_STUDENT"
    TEACHER_NUM = "TEACHER_NUM"


STUDENT_METRICS = tuple(BehaviorMetric)[:13]
TEACHER_METRICS = tuple(BehaviorMetric)[13:]


def _ordered_metrics(values: Any) -> tuple[BehaviorMetric, ...]:
    selected = set(values)
    return tuple(metric for metric in BehaviorMetric if metric in selected)


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value
    ):
        return datetime.strptime(value, LEGACY_DATETIME_FORMAT)
    raise ValueError(
        "expected datetime or yyyy-MM-dd HH:mm:ss; epoch values are not accepted"
    )


def _localize(value: datetime, zone: ZoneInfo) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        # 旧格式不带offset，不能接受无法无歧义往返的fold时间。
        value = value.astimezone(zone).replace(tzinfo=None)
    candidates = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        restored = candidate.astimezone(timezone.utc).astimezone(zone)
        if restored.replace(tzinfo=None) == value:
            candidates.append(candidate)
    if not candidates or len({item.utcoffset() for item in candidates}) > 1:
        raise ValueError(
            "nonexistent or ambiguous local time; use an unambiguous business timezone"
        )
    return candidates[0]


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ImageAnalysisStream(ImageContract):
    id: Identifier
    stream_id: Identifier
    video_url: str = Field(strict=True, min_length=1, max_length=16_384, repr=False)
    # 接收旧字段后立即丢弃，不进入快照、repr 或幂等摘要。
    push_address: str | None = Field(
        default=None, strict=True, exclude=True, repr=False
    )
    ai_analysis_type_list: tuple[AiAnalysisType, ...] = Field(min_length=1)
    streaming_media_source: StreamingMediaSource
    ai_config: tuple[BehaviorMetric, ...] = ()
    recording_task_id: Identifier | None = None
    video_started_at: datetime | None = None

    @field_validator("push_address")
    @classmethod
    def discard_unused_live_address(cls, value: str | None) -> None:
        return None

    @field_validator("video_url")
    @classmethod
    def validate_video_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme in ("http", "https")
                and parsed.hostname
                and parsed.username is None
                and parsed.password is None
                and not parsed.fragment
                and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
                and "\\" not in value
            )
            _ = parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "videoUrl must be an HTTP(S) URL without credentials or fragment"
            )
        return value  # 保留签名query的原始字节，不重写/排序URL。

    @field_validator("video_started_at", mode="before")
    @classmethod
    def parse_video_time(cls, value: Any) -> datetime | None:
        return None if value is None else _parse_time(value)

    @field_serializer("video_started_at")
    def serialize_video_time(self, value: datetime | None) -> str | None:
        return value.strftime(LEGACY_DATETIME_FORMAT) if value else None

    @model_validator(mode="after")
    def normalize_analysis(self) -> ImageAnalysisStream:
        types = set(self.ai_analysis_type_list)
        if types - {AiAnalysisType.S_ANALYSIS, AiAnalysisType.T_ANALYSIS}:
            raise ValueError(
                "offline behavior analysis supports only S_ANALYSIS/T_ANALYSIS"
            )
        allowed = set()
        if AiAnalysisType.S_ANALYSIS in types:
            allowed.update(STUDENT_METRICS)
        if AiAnalysisType.T_ANALYSIS in types:
            allowed.update(TEACHER_METRICS)
        if set(self.ai_config) - allowed:
            raise ValueError(
                "aiConfig contains metrics outside selected analysis types"
            )
        object.__setattr__(
            self, "ai_analysis_type_list", tuple(sorted(types, key=lambda t: t.value))
        )
        object.__setattr__(
            self, "ai_config", _ordered_metrics(self.ai_config or allowed)
        )
        return self


class ImageAnalysisRequest(ImageContract):
    message_id: Identifier
    classroom_id: Identifier
    space_id: Identifier
    school_id: SchoolId
    start_time: datetime
    end_time: datetime
    # 兼容原 AiLlmDTO：未传时取当前调度时间，按请求业务时区记录到秒。
    schedule_time: datetime | None = None
    time_zone: Identifier = "Asia/Shanghai"
    sample_interval_seconds: PositiveInt = 120
    stream_info_list: tuple[ImageAnalysisStream, ...] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def apply_business_timezone(cls, data: Any, info: ValidationInfo) -> Any:
        if (
            isinstance(data, dict)
            and "timeZone" not in data
            and "time_zone" not in data
        ):
            data = dict(data)
            data["timeZone"] = (info.context or {}).get(
                "business_time_zone", "Asia/Shanghai"
            )
        return data

    @field_validator("start_time", "end_time", "schedule_time", mode="before")
    @classmethod
    def parse_request_time(cls, value: Any, info: ValidationInfo) -> datetime | None:
        if value is None and info.field_name == "schedule_time":
            return None
        return _parse_time(value)

    @field_serializer("start_time", "end_time", "schedule_time")
    def serialize_request_time(self, value: datetime | None) -> str | None:
        return value.strftime(LEGACY_DATETIME_FORMAT) if value else None

    @model_validator(mode="after")
    def validate_classroom(self) -> ImageAnalysisRequest:
        try:
            zone = ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(
                "timeZone must identify an installed IANA timezone"
            ) from None
        for name in ("start_time", "end_time", "schedule_time"):
            value = getattr(self, name)
            if value is not None:
                if value.microsecond:
                    raise ValueError(
                        "legacy request times must have whole-second precision"
                    )
                object.__setattr__(self, name, _localize(value, zone))
        if self.schedule_time is None:
            object.__setattr__(
                self,
                "schedule_time",
                datetime.now(zone).replace(microsecond=0),
            )
        if self.end_time.astimezone(timezone.utc) <= self.start_time.astimezone(
            timezone.utc
        ):
            raise ValueError("endTime must be after startTime")
        identities = [(stream.id, stream.stream_id) for stream in self.stream_info_list]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate stream (id, streamId)")
        streams = []
        for stream in self.stream_info_list:
            if stream.video_started_at is not None:
                if stream.video_started_at.microsecond:
                    raise ValueError("videoStartedAt must have whole-second precision")
                stream = stream.model_copy(
                    update={
                        "video_started_at": _localize(stream.video_started_at, zone)
                    }
                )
            streams.append(stream)
        object.__setattr__(self, "stream_info_list", tuple(streams))
        return self

    @property
    def requested_metrics(self) -> tuple[BehaviorMetric, ...]:
        return _ordered_metrics(
            metric for stream in self.stream_info_list for metric in stream.ai_config
        )

    @property
    def duration_ms(self) -> int:
        delta = self.end_time.astimezone(timezone.utc) - self.start_time.astimezone(
            timezone.utc
        )
        return (delta.days * 86400 + delta.seconds) * 1000

    def idempotency_key(self) -> str:
        return _digest([self.school_id, IMAGE_TASK_TYPE, self.message_id])

    def semantic_fingerprint(self) -> str:
        """业务语义摘要：排除scheduleTime、弃用pushAddress、数组等价顺序。"""
        payload = self.to_payload()
        for name in ("messageId", "scheduleTime", "timeZone"):
            payload.pop(name, None)
        payload["startTime"] = _utc_text(self.start_time)
        payload["endTime"] = _utc_text(self.end_time)
        streams = []
        for stream in sorted(self.stream_info_list, key=lambda s: (s.id, s.stream_id)):
            item = stream.to_payload()
            if stream.video_started_at:
                item["videoStartedAt"] = _utc_text(stream.video_started_at)
            streams.append(item)
        payload["streamInfoList"] = streams
        return _digest(payload)


class ImagePromptBundleContent(ImageContract):
    """图像版提示词是一份完整原文，模型参数另列；不拆分学生或教师部分。"""

    prompt: PromptText
    model: Identifier
    temperature: Annotated[
        float, Field(strict=True, ge=0, le=2, allow_inf_nan=False)
    ] = 0.2
    max_tokens: PositiveInt = 4096
    output_schema_version: Identifier
    metric_definition_version: Identifier


def validate_metric_result(
    raw: str | dict[str, Any], requested_metrics: tuple[BehaviorMetric, ...]
) -> dict[str, int]:
    """仅合法完整指标可成功；不确定质量状态由后续模型协议处理。

    教师四项动作已按用户提供的提示词确认为0/1；其他数值口径另行确认。
    """
    if not requested_metrics or any(
        not isinstance(m, BehaviorMetric) for m in requested_metrics
    ):
        raise ValueError("requested_metrics must contain known metrics")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate metric key")
            result[key] = value
        return result

    if isinstance(raw, str):
        try:
            raw = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
        except (ValueError, RecursionError):
            raise ValueError("invalid metric JSON") from None
    if not isinstance(raw, dict) or set(raw) != {m.value for m in requested_metrics}:
        raise ValueError("metric keys must exactly match requested metrics")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in raw.values()
    ):
        raise ValueError(
            "metric values must be non-negative integers, not bool/float/string"
        )
    teacher_flags = {
        BehaviorMetric.PATROL.value,
        BehaviorMetric.LECTURE.value,
        BehaviorMetric.BLACKBOARD.value,
        BehaviorMetric.BACK_TO_STUDENT.value,
    }
    if any(raw[key] not in (0, 1) for key in teacher_flags & raw.keys()):
        raise ValueError("teacher action values must be 0 or 1")
    return {
        metric.value: raw[metric.value]
        for metric in _ordered_metrics(requested_metrics)
    }


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("internal timeline requires a timezone-aware datetime")
    if value.microsecond % 1000:
        raise ValueError("internal timeline precision is milliseconds")
    return value.astimezone(timezone.utc)


class SelectedVideo(ImageContract):
    """已通过选源的持久输入。此DTO不声称已验证录像连续性。"""

    video_id: Identifier
    stream_record_id: Identifier
    stream_id: Identifier
    duration_ms: PositiveInt
    started_at: datetime
    timeline_basis: Literal[
        "recording_metadata", "upstream_continuity_guarantee", "duration_heuristic"
    ]

    _utc = field_validator("started_at")(_aware_utc)


class ExcludedVideo(ImageContract):
    stream_record_id: Identifier
    stream_id: Identifier
    reason: Literal[
        "insufficient_coverage",
        "duration_outlier",
        "timeline_unknown",
        "invalid_video",
        "probe_failed",
    ]


class SourceSelectionSnapshot(ImageContract):
    policy_version: Identifier
    selected_videos: tuple[SelectedVideo, ...] = Field(min_length=1)
    excluded_videos: tuple[ExcludedVideo, ...] = ()

    @model_validator(mode="after")
    def validate_partition(self) -> SourceSelectionSnapshot:
        videos = (*self.selected_videos, *self.excluded_videos)
        identities = [(video.stream_record_id, video.stream_id) for video in videos]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "selected/excluded stream identities must be unique and disjoint"
            )
        ids = [video.video_id for video in self.selected_videos]
        if len(ids) != len(set(ids)):
            raise ValueError("selected video ids must be unique")
        return self


class PlannedSampleFrame(ImageContract):
    video_id: Identifier
    video_offset_ms: NonNegativeInt | None = None
    unavailable_reason: Literal["outside_video_coverage"] | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> PlannedSampleFrame:
        if (self.video_offset_ms is None) == (self.unavailable_reason is None):
            raise ValueError(
                "frame must have either an offset or an unavailable reason"
            )
        return self


class ImageSamplePlan(ImageContract):
    """统一时间点组计划，不是已完成的组图或成功hook。"""

    sample_id: Identifier
    internal_task_id: Identifier
    classroom_offset_ms: NonNegativeInt
    captured_at: datetime
    frames: tuple[PlannedSampleFrame, ...] = Field(min_length=1)

    _utc = field_validator("captured_at")(_aware_utc)

    @model_validator(mode="after")
    def validate_members(self) -> ImageSamplePlan:
        ids = [frame.video_id for frame in self.frames]
        if len(ids) != len(set(ids)):
            raise ValueError("a sample cannot contain the same video twice")
        return self

    @property
    def fully_covered(self) -> bool:
        # 覆盖齐备不等于上传齐备，也不代表实际PTS偏差已通过。
        return all(frame.video_offset_ms is not None for frame in self.frames)
