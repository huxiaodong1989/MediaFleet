from datetime import timedelta

import pytest
from pydantic import ValidationError

from media_platform.contracts.image_analysis import (
    BehaviorMetric,
    ExcludedVideo,
    ImageAnalysisRequest,
    PlannedSampleFrame,
    SelectedVideo,
    SourceSelectionSnapshot,
)
from services.image_analysis.planning import SamplingLimits, plan_classroom_samples


@pytest.fixture
def request_dto():
    return ImageAnalysisRequest.model_validate(
        {
            "messageId": "req",
            "classroomId": "class",
            "spaceId": "room",
            "schoolId": "school",
            "startTime": "2026-09-29 08:30:00",
            "endTime": "2026-09-29 10:10:00",
            "streamInfoList": [
                {
                    "id": f"media-{i}",
                    "streamId": f"camera-{i}",
                    "videoUrl": f"https://example.test/{i}.mp4",
                    "aiAnalysisTypeList": ["T_ANALYSIS" if i == 2 else "S_ANALYSIS"],
                    "streamingMediaSource": "TEACHER_CAMERA"
                    if i == 2
                    else "ROOM_CAMERA",
                }
                for i in range(3)
            ],
        }
    )


@pytest.fixture
def limits():
    return SamplingLimits(
        min_interval_seconds=10,
        max_videos=3,
        max_classroom_duration_ms=7_200_000,
        max_samples=100,
        max_images=300,
    )


def selection_for(request, indices=(0, 1, 2), durations=None, start_delta_ms=0):
    return SourceSelectionSnapshot(
        policy_version="test-policy",
        selected_videos=tuple(
            SelectedVideo(
                video_id=f"video-{i}",
                stream_record_id=f"media-{i}",
                stream_id=f"camera-{i}",
                duration_ms=(durations or {}).get(i, request.duration_ms),
                started_at=request.start_time + timedelta(milliseconds=start_delta_ms),
                timeline_basis="recording_metadata",
            )
            for i in indices
        ),
        excluded_videos=tuple(
            ExcludedVideo(
                stream_record_id=f"media-{i}",
                stream_id=f"camera-{i}",
                reason="insufficient_coverage",
            )
            for i in range(3)
            if i not in indices
        ),
    )


def build(request, selection, limits, task="task-1"):
    return plan_classroom_samples(
        internal_task_id=task, request=request, selection=selection, limits=limits
    )


def test_three_videos_form_50_groups_not_150_results(request_dto, limits):
    plan = build(request_dto, selection_for(request_dto), limits)
    assert len(plan.samples) == 50
    assert sum(len(sample.frames) for sample in plan.samples) == 150
    assert all(sample.fully_covered for sample in plan.samples)
    assert plan.samples[-1].classroom_offset_ms == 5_880_000
    assert not plan.unsupported_metrics


def test_excluded_video_not_waited_on_and_metrics_preserved(request_dto, limits):
    selection = selection_for(request_dto, (0, 1), {1: 99 * 60_000})
    plan = build(request_dto, selection, limits)
    assert all(
        len(sample.frames) == 2 and sample.fully_covered for sample in plan.samples
    )
    assert BehaviorMetric.TEACHER_NUM in plan.unsupported_metrics
    assert len(plan.requested_metrics) == 18
    assert len(plan.source_selection.excluded_videos) == 1


def test_one_selected_video_still_produces_groups(request_dto, limits):
    plan = build(request_dto, selection_for(request_dto, (0,)), limits)
    assert len(plan.samples) == 50
    assert all(len(sample.frames) == 1 for sample in plan.samples)


def test_selected_video_boundary_is_explicit_not_shortest_truncation(
    request_dto, limits
):
    plan = build(
        request_dto, selection_for(request_dto, durations={1: 96 * 60_000}), limits
    )
    assert len(plan.samples) == 50
    assert plan.samples[47].fully_covered
    assert not plan.samples[48].fully_covered
    assert plan.samples[48].frames[1].unavailable_reason == "outside_video_coverage"
    assert plan.samples[48].frames[1].video_offset_ms is None


def test_offset_mapping_not_same_index_and_preserves_milliseconds(request_dto, limits):
    selection = selection_for(request_dto, start_delta_ms=5500)
    plan = build(request_dto, selection, limits)
    assert not plan.samples[0].fully_covered
    assert plan.samples[1].frames[0].video_offset_ms == 114_500


def test_ids_stable_across_recovery_and_stream_order(request_dto, limits):
    selection = selection_for(request_dto)
    first = build(request_dto, selection, limits)
    reordered = SourceSelectionSnapshot(
        policy_version="test-policy", selected_videos=selection.selected_videos[::-1]
    )
    repeat = build(request_dto, reordered, limits)
    assert first.samples == repeat.samples
    assert len(first.samples[0].sample_id) == 36
    assert (
        first.samples[0].sample_id
        != build(request_dto, selection, limits, "other-task").samples[0].sample_id
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("min_interval_seconds", 121),
        ("max_videos", 2),
        ("max_classroom_duration_ms", 1000),
        ("max_samples", 49),
        ("max_images", 149),
    ],
)
def test_budgets_reject_before_building_large_plan(request_dto, limits, field, value):
    changed = limits.model_dump()
    changed[field] = value
    with pytest.raises(ValueError):
        build(
            request_dto,
            selection_for(request_dto),
            SamplingLimits.model_validate(changed),
        )


def test_selection_must_account_for_all_request_sources(request_dto, limits):
    selection = selection_for(request_dto, (0,))
    incomplete = SourceSelectionSnapshot(
        policy_version="test", selected_videos=selection.selected_videos
    )
    with pytest.raises(ValueError, match="every request stream"):
        build(request_dto, incomplete, limits)


def test_zero_selected_or_duplicate_selection_is_invalid(request_dto):
    with pytest.raises(ValidationError):
        SourceSelectionSnapshot(policy_version="test", selected_videos=[])
    video = selection_for(request_dto).selected_videos[0]
    with pytest.raises(ValidationError):
        SourceSelectionSnapshot(policy_version="test", selected_videos=[video, video])
    with pytest.raises(ValidationError):
        SourceSelectionSnapshot(
            policy_version="test",
            selected_videos=[video],
            excluded_videos=[
                {
                    "streamRecordId": video.stream_record_id,
                    "streamId": video.stream_id,
                    "reason": "probe_failed",
                }
            ],
        )


def test_internal_timeline_must_be_aware(request_dto):
    video = selection_for(request_dto).selected_videos[0].model_dump()
    video["started_at"] = request_dto.start_time.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="timezone-aware"):
        SelectedVideo.model_validate(video)


def test_availability_cannot_silently_mean_missing():
    with pytest.raises(ValidationError):
        PlannedSampleFrame(video_id="v")
    with pytest.raises(ValidationError):
        PlannedSampleFrame(
            video_id="v", video_offset_ms=0, unavailable_reason="outside_video_coverage"
        )


def test_104_minutes_produces_52_groups(request_dto, limits):
    payload = request_dto.to_payload()
    payload["endTime"] = "2026-09-29 10:14:00"
    request = ImageAnalysisRequest.model_validate(payload)
    plan = build(request, selection_for(request), limits)
    assert len(plan.samples) == 52
    assert sum(len(sample.frames) for sample in plan.samples) == 156


def test_plan_is_serializable_for_future_checkpointing(request_dto, limits):
    plan = build(request_dto, selection_for(request_dto), limits)
    assert type(plan).model_validate_json(plan.model_dump_json(by_alias=True)) == plan


def test_supplied_video_start_cannot_be_reinterpreted(request_dto, limits):
    payload = request_dto.to_payload()
    payload["streamInfoList"][0]["videoStartedAt"] = "2026-09-29 08:30:10"
    request = ImageAnalysisRequest.model_validate(payload)
    with pytest.raises(ValueError, match="conflicts"):
        build(request, selection_for(request), limits)


def test_task_id_is_validated_before_hashing(request_dto, limits):
    selection = selection_for(request_dto)
    assert (
        build(request_dto, selection, limits, " task ").samples
        == build(request_dto, selection, limits, "task").samples
    )
    with pytest.raises(ValueError):
        build(request_dto, selection, limits, " ")
