import copy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from media_platform.contracts.image_analysis import (
    STUDENT_METRICS,
    TEACHER_METRICS,
    BehaviorMetric,
    ImageAnalysisRequest,
    ImagePromptBundleContent,
    validate_metric_result,
)


@pytest.fixture
def payload():
    return {
        "messageId": "request-1",
        "classroomId": "class-1",
        "spaceId": "room-1",
        "schoolId": "school-1",
        "startTime": "2026-09-29 08:30:00",
        "endTime": "2026-09-29 10:10:00",
        "streamInfoList": [
            {
                "id": "media-1",
                "streamId": "camera-1",
                "videoUrl": "https://example.test/a.mp4?signature=test-only",
                "aiAnalysisTypeList": ["S_ANALYSIS", "T_ANALYSIS"],
                "streamingMediaSource": "ROOM_CAMERA",
            }
        ],
    }


def test_legacy_payload_roundtrip_and_expansion(payload):
    payload["streamInfoList"][0]["pushAddress"] = "rtsp://example.test/live"
    request = ImageAnalysisRequest.model_validate(payload)
    assert len(STUDENT_METRICS) == 13 and len(TEACHER_METRICS) == 5
    assert len(request.requested_metrics) == 18
    assert request.sample_interval_seconds == 120
    assert request.duration_ms == 6_000_000
    assert request.schedule_time is not None
    assert request.schedule_time.tzinfo is not None
    assert request.to_payload()["scheduleTime"] == request.schedule_time.strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    assert request.stream_info_list[0].push_address is None
    assert "pushAddress" not in request.to_payload()["streamInfoList"][0]
    assert "unused" not in request.model_dump_json()
    assert "signature" not in repr(request)
    assert request.to_payload()["startTime"] == "2026-09-29 08:30:00"
    assert ImageAnalysisRequest.model_validate(request.to_payload()) == request


@pytest.mark.parametrize("supplied", [False, True])
def test_schedule_time_defaults_to_business_zone_current_time(payload, supplied):
    payload["timeZone"] = "UTC"
    if supplied:
        payload["scheduleTime"] = None
    before = datetime.now(timezone.utc).replace(microsecond=0)
    request = ImageAnalysisRequest.model_validate(payload)
    after = datetime.now(timezone.utc).replace(microsecond=0)
    assert before <= request.schedule_time <= after
    assert request.schedule_time.tzinfo == ZoneInfo("UTC")
    assert request.to_payload()["scheduleTime"] == request.schedule_time.strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def test_explicit_schedule_time_remains_distinct_from_classroom_sampling_time(payload):
    payload["scheduleTime"] = "2026-09-29 12:34:56"
    request = ImageAnalysisRequest.model_validate(payload)
    assert request.to_payload()["scheduleTime"] == "2026-09-29 12:34:56"
    assert request.schedule_time != request.start_time


@pytest.mark.parametrize("interval", [True, False, 0, -1, 2.5, "120", None])
def test_interval_is_strict_positive_integer(payload, interval):
    payload["sampleIntervalSeconds"] = interval
    with pytest.raises(ValidationError):
        ImageAnalysisRequest.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("messageId", 123),
        ("schoolId", " "),
        ("startTime", 1780000000),
        ("startTime", "2026-9-29 08:30:00"),
        ("endTime", "2026-09-29 08:30:00"),
        ("endTime", "2026-09-29 07:30:00"),
        ("timeZone", "Mars/Olympus"),
        ("streamInfoList", []),
    ],
)
def test_invalid_request(payload, field, value):
    payload[field] = value
    with pytest.raises(ValidationError):
        ImageAnalysisRequest.model_validate(payload)


@pytest.mark.parametrize(
    "url",
    [
        "rtsp://example.test/live",
        "file:///tmp/a.mp4",
        "https://user:fake@example.test/a",
        "https://example.test/a#fragment",
        "https://example.test:invalid/a",
        "https:///a",
        "https://example.test/a\n",
        "https://example.test/a b",
        "https://example.test\\a",
    ],
)
def test_url_syntax_rejects_invalid_or_credentials(payload, url):
    payload["streamInfoList"][0]["videoUrl"] = url
    with pytest.raises(ValidationError):
        ImageAnalysisRequest.model_validate(payload)


def test_source_is_required_even_with_recording_association(payload):
    stream = payload["streamInfoList"][0]
    stream.pop("videoUrl")
    stream["recordingTaskId"] = "record-1"
    stream["pushAddress"] = "rtsp://example.test/live"
    with pytest.raises(ValidationError):
        ImageAnalysisRequest.model_validate(payload)


@pytest.mark.parametrize(
    "types,config",
    [
        (["VOICE_ANALYSIS"], []),
        (["S_ANALYSIS", "GRAPHIC_ANALYSIS"], []),
        (["S_ANALYSIS"], ["TEACHER_NUM"]),
        (["S_ANALYSIS"], ["UNKNOWN"]),
        ([], []),
    ],
)
def test_unsupported_types_and_metrics(payload, types, config):
    payload["streamInfoList"][0].update(aiAnalysisTypeList=types, aiConfig=config)
    with pytest.raises(ValidationError):
        ImageAnalysisRequest.model_validate(payload)


@pytest.mark.parametrize(
    "obsolete_metric", ["MAX_NUMBER_PEOPLE", "DISCUSS", "STU_PODIUM"]
)
def test_obsolete_metrics_are_not_in_current_18_item_contract(payload, obsolete_metric):
    payload["streamInfoList"][0]["aiConfig"] = [obsolete_metric]
    with pytest.raises(ValidationError):
        ImageAnalysisRequest.model_validate(payload)


def test_alias_conflict_and_equal_duplicate(payload):
    payload["classroom_id"] = "another-class"
    with pytest.raises(ValidationError, match="conflicting aliases"):
        ImageAnalysisRequest.model_validate(payload)
    payload["classroom_id"] = payload["classroomId"]
    assert ImageAnalysisRequest.model_validate(payload).classroom_id == "class-1"
    payload["streamInfoList"][0]["stream_id"] = "other"
    with pytest.raises(ValidationError, match="conflicting aliases"):
        ImageAnalysisRequest.model_validate(payload)


def test_duplicate_stream_rejected(payload):
    payload["streamInfoList"] *= 2
    with pytest.raises(ValidationError, match="duplicate stream"):
        ImageAnalysisRequest.model_validate(payload)


def test_semantic_fingerprint_ignores_nonsemantic_values(payload):
    stream = payload["streamInfoList"][0]
    stream["aiConfig"] = ["HEAD_UP", "TEACHER_NUM", "HEAD_UP"]
    second = dict(stream, id="media-2", streamId="camera-2")
    payload["streamInfoList"].append(second)
    original = ImageAnalysisRequest.model_validate(payload)
    changed = copy.deepcopy(payload)
    changed["scheduleTime"] = "2026-09-30 01:00:00"
    changed["streamInfoList"].reverse()
    for item in changed["streamInfoList"]:
        item["aiAnalysisTypeList"].reverse()
        item["aiConfig"] = ["TEACHER_NUM", "HEAD_UP"]
        item["pushAddress"] = "rtsp://ignored/live"
    repeat = ImageAnalysisRequest.model_validate(changed)
    assert repeat.semantic_fingerprint() == original.semantic_fingerprint()
    assert len(original.idempotency_key()) == 64
    changed["streamInfoList"][0]["videoUrl"] += "&version=2"
    different = ImageAnalysisRequest.model_validate(changed)
    assert different.idempotency_key() == original.idempotency_key()
    assert different.semantic_fingerprint() != original.semantic_fingerprint()
    changed["schoolId"] = "other-school"
    assert (
        ImageAnalysisRequest.model_validate(changed).idempotency_key()
        != original.idempotency_key()
    )


def test_timezone_normalization_and_equivalent_fingerprint(payload):
    local = ImageAnalysisRequest.model_validate(payload)
    payload.update(
        startTime="2026-09-29 00:30:00", endTime="2026-09-29 02:10:00", timeZone="UTC"
    )
    utc = ImageAnalysisRequest.model_validate(payload)
    assert utc.semantic_fingerprint() == local.semantic_fingerprint()
    assert local.start_time.astimezone(timezone.utc).hour == 0
    payload.pop("timeZone")
    assert (
        ImageAnalysisRequest.model_validate(
            payload, context={"business_time_zone": "UTC"}
        )
        == utc
    )


def test_cross_midnight_and_stream_time_roundtrip(payload):
    payload.update(startTime="2026-09-29 23:30:00", endTime="2026-09-30 01:10:00")
    payload["streamInfoList"][0]["videoStartedAt"] = datetime(
        2026, 9, 29, 15, 30, tzinfo=timezone.utc
    )
    request = ImageAnalysisRequest.model_validate(payload)
    assert request.duration_ms == 6_000_000
    assert (
        request.to_payload()["streamInfoList"][0]["videoStartedAt"]
        == "2026-09-29 23:30:00"
    )
    assert ImageAnalysisRequest.model_validate(request.to_payload()) == request


@pytest.mark.parametrize("start", ["2026-03-08 02:30:00", "2026-11-01 01:30:00"])
def test_dst_ambiguous_and_nonexistent_times_rejected(payload, start):
    payload.update(
        timeZone="America/New_York", startTime=start, endTime=start[:10] + " 04:00:00"
    )
    with pytest.raises(ValidationError, match="nonexistent or ambiguous"):
        ImageAnalysisRequest.model_validate(payload)


@pytest.mark.parametrize(
    "raw",
    [
        '{"HEAD_UP":true}',
        '{"HEAD_UP":1.0}',
        '{"HEAD_UP":"1"}',
        '{"HEAD_UP":-1}',
        '{"HEAD_UP":null}',
        '{"HEAD_UP":NaN}',
        '{"HEAD_UP":Infinity}',
        "{}",
        "[]",
        '{"HEAD_UP":1,"READ":1}',
        '{"HEAD_UP":1,"HEAD_UP":2}',
        '```json\n{"HEAD_UP":1}\n```',
    ],
)
def test_metric_result_rejects_invalid_outputs(raw):
    with pytest.raises(ValueError):
        validate_metric_result(raw, (BehaviorMetric.HEAD_UP,))


def test_metric_subset_and_teacher_action_flags():
    assert validate_metric_result('{"HEAD_UP":2}', (BehaviorMetric.HEAD_UP,)) == {
        "HEAD_UP": 2
    }
    assert validate_metric_result('{"PATROL":1}', (BehaviorMetric.PATROL,)) == {
        "PATROL": 1
    }
    with pytest.raises(ValueError, match="0 or 1"):
        validate_metric_result('{"PATROL":2}', (BehaviorMetric.PATROL,))
    with pytest.raises(ValueError):
        validate_metric_result({}, ())


def prompt_payload():
    return dict(
        prompt="Observe visible evidence only and return JSON.",
        model="approved-vision-model",
        outputSchemaVersion="candidate-1",
        metricDefinitionVersion="candidate-1",
    )


def test_prompt_schema_is_independent_and_roundtrips():
    prompt = ImagePromptBundleContent.model_validate(prompt_payload())
    assert prompt.max_tokens == 4096
    assert ImagePromptBundleContent.model_validate(prompt.to_payload()) == prompt


def test_prompt_is_one_complete_text_without_split_fields():
    import re

    from services.image_analysis.prompt_defaults import load_bootstrap_prompt_bundle

    seed = load_bootstrap_prompt_bundle("approved-vision-model")
    assert "HEAD_UP" in seed.prompt
    assert "TEACHER_NUM" in seed.prompt
    assert "PATROL: 教师是否正在教室中巡视 (0或1)" in seed.prompt
    keys = re.findall(r"^- ([A-Z_]+):", seed.prompt, flags=re.MULTILINE)
    assert len(keys) == len(set(keys)) == 18
    assert "MAX_NUMBER_PEOPLE" not in keys
    assert set(seed.to_payload()) == {
        "prompt",
        "model",
        "temperature",
        "maxTokens",
        "outputSchemaVersion",
        "metricDefinitionVersion",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("temperature", True),
        ("temperature", float("nan")),
        ("temperature", 3),
        ("maxTokens", True),
        ("maxTokens", "4096"),
        ("maxTokens", 0),
        ("prompt", " "),
        ("studentPrompt", "wrongly split"),
        ("teacherPrompt", "wrongly split"),
        ("systemPrompt", "wrongly split"),
        ("userTemplate", "wrongly split"),
        ("apiKey", "test-only"),
        ("baseUrl", "https://example.test"),
    ],
)
def test_prompt_rejects_unsafe_or_invalid_parameters(field, value):
    payload = prompt_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        ImagePromptBundleContent.model_validate(payload)


def test_schema_can_be_generated_without_external_dependencies():
    schema = ImageAnalysisRequest.model_json_schema(by_alias=True)
    assert "streamInfoList" in schema["properties"]
    assert "videoUrl" in schema["$defs"]["ImageAnalysisStream"]["required"]


def test_aware_ambiguous_time_rejected_to_keep_legacy_roundtrip(payload):
    payload.update(
        timeZone="America/New_York",
        startTime=datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc),
        endTime="2026-11-01 04:00:00",
    )
    with pytest.raises(ValidationError, match="ambiguous"):
        ImageAnalysisRequest.model_validate(payload)


def test_snake_case_and_json_roundtrip(payload):
    request = ImageAnalysisRequest.model_validate(payload)
    assert ImageAnalysisRequest.model_validate(request.model_dump()) == request
    assert (
        ImageAnalysisRequest.model_validate_json(request.model_dump_json(by_alias=True))
        == request
    )


def test_request_rejects_typo_instead_of_ignoring_option(payload):
    payload["sampleIntervallSeconds"] = 10
    with pytest.raises(ValidationError):
        ImageAnalysisRequest.model_validate(payload)
