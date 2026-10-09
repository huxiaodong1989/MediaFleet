from datetime import timedelta

import pytest

from media_platform.contracts.image_analysis import ImageAnalysisRequest
from services.image_analysis.source_selection import (
    SourceProbeEvidence,
    SourceSelectionPolicy,
    select_classroom_sources,
)


@pytest.fixture
def classroom_request():
    return ImageAnalysisRequest.model_validate(
        {
            "messageId": "message-1",
            "classroomId": "class-1",
            "spaceId": "room-1",
            "schoolId": "school-1",
            "startTime": "2026-09-30 08:30:00",
            "endTime": "2026-09-30 10:10:00",
            "timeZone": "UTC",
            "streamInfoList": [
                {
                    "id": f"record-{i}",
                    "streamId": f"camera-{i}",
                    "videoUrl": f"https://video.test/{i}.mp4",
                    "aiAnalysisTypeList": ["S_ANALYSIS"],
                    "streamingMediaSource": "ROOM_CAMERA",
                }
                for i in range(3)
            ],
        }
    )


@pytest.fixture
def policy():
    return SourceSelectionPolicy(
        version="coverage-v1",
        minimum_classroom_coverage=0.95,
        minimum_duration_ratio_to_longest=0.9,
    )


def evidence(classroom_request, durations, *, starts=None, continuous=None):
    return tuple(
        SourceProbeEvidence(
            stream_record_id=f"record-{i}",
            stream_id=f"camera-{i}",
            duration_ms=duration * 60_000,
            started_at=(starts or {}).get(i, classroom_request.start_time),
            timeline_basis="upstream_continuity_guarantee",
            continuous=(continuous or {}).get(i, True),
        )
        for i, duration in enumerate(durations)
    )


def test_100_99_23_minute_sources_select_two_without_waiting_for_fragment(
    classroom_request, policy
):
    result = select_classroom_sources(
        task_id="task-1",
        request=classroom_request,
        evidence=evidence(classroom_request, (100, 99, 23)),
        policy=policy,
    )
    assert len(result.selected_videos) == 2
    assert result.excluded_videos[0].reason == "insufficient_coverage"
    assert result.selected_videos[0].video_id != result.selected_videos[1].video_id
    assert result == select_classroom_sources(
        task_id="task-1",
        request=classroom_request,
        evidence=evidence(classroom_request, (100, 99, 23))[::-1],
        policy=policy,
    )


def test_all_short_or_untrusted_sources_fail_instead_of_choosing_longest(
    classroom_request, policy
):
    with pytest.raises(ValueError, match="没有满足"):
        select_classroom_sources(
            task_id="task-1",
            request=classroom_request,
            evidence=evidence(classroom_request, (23, 22, 21)),
            policy=policy,
        )
    with pytest.raises(ValueError, match="没有满足"):
        select_classroom_sources(
            task_id="task-1",
            request=classroom_request,
            evidence=evidence(
                classroom_request,
                (100, 99, 98),
                continuous={0: False, 1: False, 2: False},
            ),
            policy=policy,
        )


def test_wrong_start_excluded_and_unknown_timeline_not_inferred_from_duration(
    classroom_request, policy
):
    shifted = classroom_request.start_time + timedelta(minutes=20)
    result = select_classroom_sources(
        task_id="task-1",
        request=classroom_request,
        evidence=evidence(
            classroom_request,
            (100, 100, 100),
            starts={0: shifted},
            continuous={1: False},
        ),
        policy=policy,
    )
    assert len(result.selected_videos) == 1
    assert {item.stream_id: item.reason for item in result.excluded_videos} == {
        "camera-0": "insufficient_coverage",
        "camera-1": "timeline_unknown",
    }


def test_probe_evidence_must_cover_request_exactly(classroom_request, policy):
    with pytest.raises(ValueError, match="一一对应"):
        select_classroom_sources(
            task_id="task-1",
            request=classroom_request,
            evidence=evidence(classroom_request, (100, 99, 23))[:2],
            policy=policy,
        )


def test_supplied_start_cannot_be_overridden_by_probe_evidence(
    classroom_request, policy
):
    payload = classroom_request.to_payload()
    payload["streamInfoList"][0]["videoStartedAt"] = "2026-09-30 08:30:10"
    changed = ImageAnalysisRequest.model_validate(payload)
    with pytest.raises(ValueError, match="videoStartedAt"):
        select_classroom_sources(
            task_id="task-1",
            request=changed,
            evidence=evidence(classroom_request, (100, 100, 100)),
            policy=policy,
        )


def test_duration_first_mode_keeps_near_full_videos_and_drops_short_fragment(
    classroom_request,
):
    relaxed = SourceSelectionPolicy(
        version="duration-first-v1",
        minimum_classroom_coverage=0.5,
        minimum_duration_ratio_to_longest=0.9,
        require_continuity=False,
    )
    observed = tuple(
        SourceProbeEvidence(
            stream_record_id=f"record-{i}",
            stream_id=f"camera-{i}",
            duration_ms=minutes * 60_000,
            started_at=classroom_request.start_time,
            timeline_basis="duration_heuristic",
            continuous=False,
        )
        for i, minutes in enumerate((100, 99, 23))
    )
    result = select_classroom_sources(
        task_id="task-1",
        request=classroom_request,
        evidence=observed,
        policy=relaxed,
    )
    assert {item.stream_id for item in result.selected_videos} == {
        "camera-0",
        "camera-1",
    }
    assert result.excluded_videos[0].reason == "insufficient_coverage"


def test_duration_first_mode_does_not_accept_only_a_short_fragment(classroom_request):
    relaxed = SourceSelectionPolicy(
        version="duration-first-v1",
        minimum_classroom_coverage=0.5,
        minimum_duration_ratio_to_longest=0.8,
        require_continuity=False,
    )
    observed = tuple(
        SourceProbeEvidence(
            stream_record_id=f"record-{i}",
            stream_id=f"camera-{i}",
            duration_ms=minutes * 60_000,
            started_at=classroom_request.start_time,
            timeline_basis="duration_heuristic",
            continuous=False,
        )
        for i, minutes in enumerate((23, 22, 21))
    )
    with pytest.raises(ValueError, match="没有满足"):
        select_classroom_sources(
            task_id="task-1",
            request=classroom_request,
            evidence=observed,
            policy=relaxed,
        )
