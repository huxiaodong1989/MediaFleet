"""课堂图像规划持久化、组图屏障和父任务租约写入保护。"""

from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.application.image_prompt_service import ImagePromptSnapshot
from media_platform.contracts.image_analysis import (
    IMAGE_TASK_TYPE,
    ImageAnalysisRequest,
    SelectedVideo,
    SourceSelectionSnapshot,
)
from media_platform.infrastructure.database.models import (
    ImageFrameModel,
    ImageSampleModel,
    ImageTaskNotificationModel,
    ImageVideoModel,
    MediaNodeModel,
    MediaTaskModel,
    RecordingServerModel,
)
from services.image_analysis.infrastructure.repositories import ImageAnalysisRepository
from services.image_analysis.application.notification_delivery import (
    ImageNotificationDelivery,
)
from services.image_analysis.application.classroom_pipeline import (
    ClassroomImagePipeline,
)
from services.image_analysis.video_frames import ExtractedFrame
from services.image_analysis.video_frames import FrameExtractionError
from services.image_analysis.video_read_session import VideoReadSession
from media_platform.infrastructure.storage.image_objects import ImageObjectRef
from services.image_analysis.planning import SamplingLimits, plan_classroom_samples
from services.image_analysis.prompt_defaults import load_bootstrap_prompt_bundle
from services.image_analysis.read_policy import ReadAction, SeekFailure, VideoReadPolicy


NOW = datetime(2026, 9, 29, 8, 20, tzinfo=timezone.utc)


@pytest.fixture
def factory(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'checkpoints.db').as_posix()}")
    sa.event.listen(
        engine, "connect", lambda dbapi, _: dbapi.execute("PRAGMA foreign_keys=ON")
    )
    tables = [
        MediaNodeModel.__table__,
        RecordingServerModel.__table__,
        MediaTaskModel.__table__,
        ImageVideoModel.__table__,
        ImageSampleModel.__table__,
        ImageTaskNotificationModel.__table__,
        ImageFrameModel.__table__,
    ]
    MediaTaskModel.metadata.create_all(engine, tables=tables)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()


def _case(shorter: bool = False):
    request = ImageAnalysisRequest.model_validate(
        {
            "messageId": "message-1",
            "classroomId": "class-1",
            "spaceId": "space-1",
            "schoolId": "school-a",
            "timeZone": "UTC",
            "startTime": "2026-09-29 08:30:00",
            "endTime": "2026-09-29 08:36:00",
            "sampleIntervalSeconds": 120,
            "streamInfoList": [
                {
                    "id": f"source-{i}",
                    "streamId": f"camera-{i}",
                    "videoUrl": f"https://example.test/{i}.mp4",
                    "streamingMediaSource": "ROOM_CAMERA",
                    "aiAnalysisTypeList": ["S_ANALYSIS"],
                }
                for i in range(2)
            ],
        }
    )
    selection = SourceSelectionSnapshot(
        policy_version="test-policy",
        selected_videos=tuple(
            SelectedVideo(
                video_id=f"video-{i}",
                stream_record_id=f"source-{i}",
                stream_id=f"camera-{i}",
                duration_ms=240_000 if shorter and i == 1 else 360_000,
                started_at=request.start_time,
                timeline_basis="recording_metadata",
            )
            for i in range(2)
        ),
    )
    plan = plan_classroom_samples(
        internal_task_id="task-1",
        request=request,
        selection=selection,
        limits=SamplingLimits(
            min_interval_seconds=120,
            max_videos=2,
            max_classroom_duration_ms=360_000,
            max_samples=3,
            max_images=6,
        ),
    )
    prompt = ImagePromptSnapshot(
        id="prompt-1",
        school_code="GLOBAL",
        version=1,
        content_hash="c" * 64,
        content=load_bootstrap_prompt_bundle("test-vision"),
    )
    return request, plan, prompt


def _task(factory):
    with factory.begin() as session:
        session.add(
            MediaTaskModel(
                id="task-1",
                task_type=IMAGE_TASK_TYPE,
                status="processing",
                routing_key="image.task",
                school_code="school-a",
                created_by="test",
                updated_by="test",
                params={
                    "request": {
                        "messageId": "message-1",
                        "scheduleTime": "2026-09-29 08:20:00",
                    }
                },
                execution_generation=3,
                lease_owner="image-node-1",
                lease_expires_at=(NOW + timedelta(minutes=5)).replace(tzinfo=None),
            )
        )


def _initialize(factory, request, plan, prompt, **overrides):
    args = dict(
        request=request,
        plan=plan,
        prompt=prompt,
        generation=3,
        lease_owner="image-node-1",
        now=NOW,
    )
    args.update(overrides)
    with factory.begin() as session:
        return ImageAnalysisRepository(session).initialize_plan(**args)


def test_plan_is_atomic_idempotent_and_rejects_stale_lease(factory):
    _task(factory)
    request, plan, prompt = _case()
    assert _initialize(factory, request, plan, prompt) == (2, 3, 6)
    assert _initialize(factory, request, plan, prompt) == (2, 3, 6)
    with factory() as session:
        assert len(session.query(ImageVideoModel).all()) == 2
        assert len(session.query(ImageSampleModel).all()) == 3
        assert len(session.query(ImageFrameModel).all()) == 6
        assert session.query(ImageSampleModel).first().status == "WAITING"
    with pytest.raises(LookupError, match="租约"):
        _initialize(factory, request, plan, prompt, generation=2)
    with pytest.raises(ValueError, match="冻结规划冲突"):
        _initialize(factory, request, plan, replace(prompt, content_hash="d" * 64))
    changed_source = request.model_copy(
        update={
            "stream_info_list": (
                request.stream_info_list[0].model_copy(
                    update={
                        "video_url": "https://example.test/replaced.mp4",
                    }
                ),
                request.stream_info_list[1],
            ),
        }
    )
    with pytest.raises(ValueError, match="冻结规划冲突"):
        _initialize(factory, changed_source, plan, prompt)
    changed_sample = plan.samples[0].model_copy(
        update={
            "frames": (
                plan.samples[0].frames[0].model_copy(update={"video_offset_ms": 1000}),
                plan.samples[0].frames[1],
            ),
        }
    )
    with pytest.raises(ValueError, match="冻结规划冲突"):
        _initialize(
            factory,
            request,
            plan.model_copy(
                update={
                    "samples": (changed_sample, *plan.samples[1:]),
                }
            ),
            prompt,
        )


def test_upload_requires_parent_lease_and_last_frame_opens_one_group(factory):
    _task(factory)
    request, plan, prompt = _case()
    _initialize(factory, request, plan, prompt)
    sample = plan.samples[0]

    def upload(video_id, **overrides):
        args = dict(
            task_id="task-1",
            school_code="school-a",
            sample_id=sample.sample_id,
            video_id=video_id,
            generation=3,
            lease_owner="image-node-1",
            now=NOW,
            storage_profile="cos-v1",
            storage_provider="COS",
            bucket="images",
            object_key=f"frames/{video_id}.jpg",
            content_hash="a" * 64,
            actual_pts_ms=0,
        )
        args.update(overrides)
        with factory.begin() as session:
            return ImageAnalysisRepository(session).mark_frame_uploaded(**args)

    assert not upload("video-0")
    assert upload("video-1")
    assert upload("video-0")
    with factory() as session:
        assert session.get(ImageSampleModel, sample.sample_id).status == "READY"
    with pytest.raises(ValueError, match="不可覆盖"):
        upload("video-0", object_key="frames/changed.jpg")
    with pytest.raises(LookupError, match="租约"):
        upload("video-0", generation=2)
    with pytest.raises(LookupError, match="租约"):
        upload("video-0", school_code="school-b")
    with pytest.raises(LookupError, match="租约"):
        upload("video-0", now=NOW + timedelta(minutes=6))
    with pytest.raises(LookupError, match="图片检查点"):
        upload("not-selected")
    with factory.begin() as session:
        session.get(MediaTaskModel, "task-1").status = "completed"
    with pytest.raises(LookupError, match="租约"):
        upload("video-0")


def test_outside_coverage_group_is_explicitly_unavailable(factory):
    _task(factory)
    request, plan, prompt = _case(shorter=True)
    assert _initialize(factory, request, plan, prompt) == (2, 3, 5)
    last = plan.samples[-1]
    with factory() as session:
        row = session.get(ImageSampleModel, last.sample_id)
        assert row.status == "UNAVAILABLE"
        assert row.unavailable_reason == "outside_video_coverage"
        assert (
            len(
                session.query(ImageFrameModel).filter_by(sample_id=last.sample_id).all()
            )
            == 1
        )


def test_invalid_plan_rolls_back_all_checkpoints(factory):
    _task(factory)
    request, plan, prompt = _case()
    bad_sample = plan.samples[0].model_copy(
        update={
            "frames": (
                plan.samples[0]
                .frames[0]
                .model_copy(update={"video_id": "not-selected"}),
                plan.samples[0].frames[1],
            )
        }
    )
    bad_plan = plan.model_copy(update={"samples": (bad_sample, *plan.samples[1:])})
    with pytest.raises(ValueError, match="未入选视频"):
        _initialize(factory, request, bad_plan, prompt)
    with factory() as session:
        assert not session.query(ImageVideoModel).count()
        assert not session.query(ImageSampleModel).count()
        assert not session.query(ImageFrameModel).count()


def test_frozen_group_result_and_notification_survive_parent_completion(factory):
    _task(factory)
    request, plan, prompt = _case()
    _initialize(factory, request, plan, prompt)
    sample = plan.samples[0]
    with factory.begin() as session:
        repo = ImageAnalysisRepository(session)
        for video_id in ("video-0", "video-1"):
            repo.mark_frame_uploaded(
                task_id="task-1",
                school_code="school-a",
                sample_id=sample.sample_id,
                video_id=video_id,
                generation=3,
                lease_owner="image-node-1",
                now=NOW,
                storage_profile="cos-v1",
                storage_provider="COS",
                bucket="images",
                object_key=f"frames/{video_id}.jpg",
                content_hash="a" * 64,
                actual_pts_ms=0,
            )
    with factory.begin() as session:
        frozen = ImageAnalysisRepository(session).freeze_sample_input(
            task_id="task-1",
            school_code="school-a",
            sample_id=sample.sample_id,
            generation=3,
            lease_owner="image-node-1",
            now=NOW,
            max_model_attempts=2,
        )
    assert len(frozen.images) == 2
    assert frozen.input_hash
    with factory.begin() as session:
        retry = ImageAnalysisRepository(session).freeze_sample_input(
            task_id="task-1",
            school_code="school-a",
            sample_id=sample.sample_id,
            generation=3,
            lease_owner="image-node-1",
            now=NOW,
            max_model_attempts=2,
        )
        assert retry.input_hash == frozen.input_hash
    with factory.begin() as session:
        with pytest.raises(ValueError, match="预算"):
            ImageAnalysisRepository(session).freeze_sample_input(
                task_id="task-1",
                school_code="school-a",
                sample_id=sample.sample_id,
                generation=3,
                lease_owner="image-node-1",
                now=NOW,
                max_model_attempts=2,
            )
    metrics = {metric: 0 for metric in frozen.requested_metrics}
    event_id = str(sessionmaker_event_id(factory, sample.sample_id))
    payload = {
        "id": "message-1",
        "scheduleTime": "2026-09-29 08:20:00",
        "eventId": event_id,
        "eventType": "SAMPLE_RESULT",
        "internalTaskId": "task-1",
        "sampleId": sample.sample_id,
        "sampleIndex": 1,
        "sampleTotal": 3,
        "classroomOffsetMs": sample.classroom_offset_ms,
        "capturedAt": sample.captured_at.isoformat(),
        "schoolId": "school-a",
        "data": {**metrics, "classroomId": "class-1", "spaceId": "space-1"},
    }
    with factory.begin() as session:
        repo = ImageAnalysisRepository(session)
        with pytest.raises(ValueError, match="通知快照"):
            repo.complete_sample(
                task_id="task-1",
                school_code="school-a",
                sample_id=sample.sample_id,
                generation=3,
                lease_owner="image-node-1",
                now=NOW,
                input_hash=frozen.input_hash,
                metrics=metrics,
                model_usage={},
                notification_payload={**payload, "id": "task-1"},
            )
        with pytest.raises(ValueError, match="通知快照"):
            repo.complete_sample(
                task_id="task-1",
                school_code="school-a",
                sample_id=sample.sample_id,
                generation=3,
                lease_owner="image-node-1",
                now=NOW,
                input_hash=frozen.input_hash,
                metrics=metrics,
                model_usage={},
                notification_payload={**payload, "scheduleTime": "2026-09-29 08:22:00"},
            )
        assert repo.complete_sample(
            task_id="task-1",
            school_code="school-a",
            sample_id=sample.sample_id,
            generation=3,
            lease_owner="image-node-1",
            now=NOW,
            input_hash=frozen.input_hash,
            metrics=metrics,
            model_usage={},
            notification_payload=payload,
        )
        assert not repo.complete_sample(
            task_id="task-1",
            school_code="school-a",
            sample_id=sample.sample_id,
            generation=3,
            lease_owner="image-node-1",
            now=NOW,
            input_hash=frozen.input_hash,
            metrics=metrics,
            model_usage={},
            notification_payload=payload,
        )
        session.get(MediaTaskModel, "task-1").status = "completed"
    with factory.begin() as session:
        pending = ImageAnalysisRepository(session).claim_notifications(
            owner="publisher-a",
            now=NOW,
            lease_seconds=30,
        )
        assert len(pending) == 1
        assert pending[0].payload == payload
        assert pending[0].attempts == 1
    with factory.begin() as session:
        assert not ImageAnalysisRepository(session).settle_notification(
            sample_id=sample.sample_id,
            owner="publisher-b",
            now=NOW,
            confirmed=True,
        )
        assert ImageAnalysisRepository(session).settle_notification(
            sample_id=sample.sample_id,
            owner="publisher-a",
            now=NOW,
            confirmed=False,
        )
    with factory.begin() as session:
        pending = ImageAnalysisRepository(session).claim_notifications(
            owner="publisher-b",
            now=NOW,
            lease_seconds=30,
        )
        assert pending[0].attempts == 2
        assert ImageAnalysisRepository(session).settle_notification(
            sample_id=sample.sample_id,
            owner="publisher-b",
            now=NOW,
            confirmed=True,
        )
    with factory.begin() as session:
        assert not ImageAnalysisRepository(session).claim_notifications(
            owner="publisher-c",
            now=NOW,
            lease_seconds=30,
        )


def sessionmaker_event_id(factory, sample_id):
    with factory() as session:
        return session.get(ImageSampleModel, sample_id).event_id


def test_notification_delivery_retries_snapshot_without_model_rerun(factory):
    _task(factory)
    payload = {
        "id": "message-1",
        "eventId": "event-1",
        "scheduleTime": "2026-09-29 08:20:00",
    }
    with factory.begin() as session:
        session.get(MediaTaskModel, "task-1").status = "completed"
        session.add(
            ImageSampleModel(
                id="sample-1",
                task_id="task-1",
                school_code="school-a",
                offset_ms=0,
                sample_time=NOW.replace(tzinfo=None),
                expected_video_ids=[],
                requested_metrics=[],
                unsupported_metrics=[],
                status="COMPLETED",
                prompt_bundle_id="prompt-1",
                prompt_version=1,
                prompt_hash="a" * 64,
                model_snapshot={},
                event_id="event-1",
                notification_payload=payload,
                notification_status="PENDING",
            )
        )

    class Publisher:
        calls = []

        def publish(self, *, event_id, payload):
            self.calls.append((event_id, payload))
            if len(self.calls) == 1:
                raise RuntimeError("broker unavailable")

    publisher = Publisher()
    delivery = ImageNotificationDelivery(factory, publisher, instance_id="publisher-1")
    assert delivery.deliver_batch().failed == 1
    assert delivery.deliver_batch().published == 1
    assert delivery.deliver_batch().claimed == 0
    assert publisher.calls == [("event-1", payload), ("event-1", payload)]
    with factory() as session:
        row = session.get(ImageSampleModel, "sample-1")
        assert row.notification_status == "PUBLISHED"
        assert row.notification_attempts == 2


def test_notification_attempt_budget_stops_poison_event(factory):
    _task(factory)
    with factory.begin() as session:
        session.add(
            ImageSampleModel(
                id="sample-budget",
                task_id="task-1",
                school_code="school-a",
                offset_ms=0,
                sample_time=NOW.replace(tzinfo=None),
                expected_video_ids=[],
                requested_metrics=[],
                unsupported_metrics=[],
                status="COMPLETED",
                prompt_bundle_id="prompt-1",
                prompt_version=1,
                prompt_hash="a" * 64,
                model_snapshot={},
                event_id="event-budget",
                notification_payload={"eventId": "event-budget"},
                notification_status="PENDING",
            )
        )
    with factory.begin() as session:
        pending = ImageAnalysisRepository(session).claim_notifications(
            owner="publisher-1",
            now=NOW,
            lease_seconds=30,
            max_attempts=1,
        )
        assert len(pending) == 1
        assert ImageAnalysisRepository(session).settle_notification(
            sample_id="sample-budget",
            owner="publisher-1",
            now=NOW,
            confirmed=False,
            max_attempts=1,
        )
    with factory.begin() as session:
        assert (
            ImageAnalysisRepository(session).claim_notifications(
                owner="publisher-2",
                now=NOW,
                lease_seconds=30,
                max_attempts=1,
            )
            == ()
        )
        assert (
            session.get(ImageSampleModel, "sample-budget").notification_status
            == "FAILED"
        )


def test_expired_notification_claim_is_reissued_with_same_event(factory):
    _task(factory)
    with factory.begin() as session:
        session.add(
            ImageSampleModel(
                id="sample-recover",
                task_id="task-1",
                school_code="school-a",
                offset_ms=0,
                sample_time=NOW.replace(tzinfo=None),
                expected_video_ids=[],
                requested_metrics=[],
                unsupported_metrics=[],
                status="COMPLETED",
                prompt_bundle_id="prompt-1",
                prompt_version=1,
                prompt_hash="a" * 64,
                model_snapshot={},
                event_id="event-recover",
                notification_payload={"eventId": "event-recover"},
                notification_status="PENDING",
            )
        )
    with factory.begin() as session:
        first = ImageAnalysisRepository(session).claim_notifications(
            owner="publisher-1",
            now=NOW,
            lease_seconds=10,
        )
        assert first[0].event_id == "event-recover"
    with factory.begin() as session:
        assert not ImageAnalysisRepository(session).settle_notification(
            sample_id="sample-recover",
            owner="publisher-1",
            now=NOW + timedelta(seconds=11),
            confirmed=True,
        )
        again = ImageAnalysisRepository(session).claim_notifications(
            owner="publisher-2",
            now=NOW + timedelta(seconds=11),
            lease_seconds=10,
        )
        assert again[0].event_id == first[0].event_id
        assert again[0].payload == first[0].payload
        assert again[0].attempts == 2


def test_partial_task_publishes_one_terminal_event_after_sample_results(factory):
    _task(factory)
    request, plan, prompt = _case()
    with factory.begin() as session:
        session.get(MediaTaskModel, "task-1").params = {
            "request": request.to_payload()
        }
    _initialize(factory, request, plan, prompt)
    with factory.begin() as session:
        task = session.get(MediaTaskModel, "task-1")
        task.status = "failed"
        task.completed_at = NOW.replace(tzinfo=None)
        samples = sorted(
            session.query(ImageSampleModel).all(), key=lambda row: row.offset_ms
        )
        samples[0].status = "COMPLETED"
        samples[0].result = {"HEAD_UP": 1}
        samples[0].notification_payload = {
            "id": request.message_id,
            "eventId": samples[0].event_id,
            "eventType": "SAMPLE_RESULT",
        }
        samples[0].notification_status = "PUBLISHED"
        samples[1].status = "FAILED"
        samples[2].status = "FAILED"
        repo = ImageAnalysisRepository(session)
        assert repo.ensure_task_terminal_notification(
            task_id="task-1", school_code="school-a"
        )
        assert not repo.ensure_task_terminal_notification(
            task_id="task-1", school_code="school-a"
        )

    class Publisher:
        sent = []

        def publish(self, *, event_id, payload):
            self.sent.append((event_id, payload))

    publisher = Publisher()
    batch = ImageNotificationDelivery(
        factory, publisher, instance_id="terminal-publisher"
    ).deliver_batch()
    assert (batch.claimed, batch.published, batch.failed) == (1, 1, 0)
    event_id, payload = publisher.sent[0]
    assert payload == {
        "id": "message-1",
        "eventId": event_id,
        "eventType": "TASK_TERMINATED",
        "internalTaskId": "task-1",
        "scheduleTime": request.to_payload()["scheduleTime"],
        "schoolId": "school-a",
        "classroomId": "class-1",
        "spaceId": "space-1",
        "status": "PARTIAL",
        "sampleTotal": 3,
        "successCount": 1,
        "failedCount": 2,
        "unavailableCount": 0,
        "resultPublishedCount": 1,
        "resultPublishFailedCount": 0,
        "finishedAt": "2026-09-29T08:20:00+00:00",
    }
    with factory() as session:
        notice = session.query(ImageTaskNotificationModel).one()
        assert notice.notification_status == "PUBLISHED"
        assert notice.notification_attempts == 1


def test_video_read_budget_and_mode_survive_repository_reopen(factory):
    _task(factory)
    request, plan, prompt = _case()
    _initialize(factory, request, plan, prompt)
    policy = VideoReadPolicy(seek_retry_limit=1)
    common = dict(
        task_id="task-1",
        school_code="school-a",
        video_id="video-0",
        generation=3,
        lease_owner="image-node-1",
        policy=policy,
        max_download_attempts=2,
    )
    with factory.begin() as session:
        repo = ImageAnalysisRepository(session)
        first = repo.claim_video_read(**common, now=NOW)
        assert (first.mode, first.attempt) == ("SEEK", 1)
        with pytest.raises(ValueError, match="进行中"):
            repo.claim_video_read(**common, now=NOW)
        assert (
            repo.record_video_read_failure(
                **common,
                now=NOW,
                attempt=first,
                failure=SeekFailure.TRANSIENT,
                retry_delay=timedelta(seconds=2),
            )
            == ReadAction.RETRY_SEEK
        )
    with factory.begin() as session:
        repo = ImageAnalysisRepository(session)
        with pytest.raises(ValueError, match="重试时间"):
            repo.claim_video_read(**common, now=NOW + timedelta(seconds=1))
        second = repo.claim_video_read(**common, now=NOW + timedelta(seconds=2))
        assert (second.mode, second.attempt) == ("SEEK", 2)
        assert (
            repo.record_video_read_failure(
                **common,
                now=NOW + timedelta(seconds=2),
                attempt=second,
                failure=SeekFailure.TRANSIENT,
                retry_delay=timedelta(0),
            )
            == ReadAction.DOWNLOAD
        )
    with factory.begin() as session:
        repo = ImageAnalysisRepository(session)
        downloaded = repo.claim_video_read(**common, now=NOW + timedelta(seconds=3))
        assert (downloaded.mode, downloaded.attempt) == ("DOWNLOAD", 1)
        with pytest.raises(ValueError, match="已过期"):
            repo.record_video_read_failure(
                **common,
                now=NOW + timedelta(seconds=3),
                attempt=first,
                failure=SeekFailure.TRANSIENT,
                retry_delay=timedelta(0),
            )
        assert (
            repo.record_video_read_failure(
                **common,
                now=NOW + timedelta(seconds=3),
                attempt=downloaded,
                failure=SeekFailure.TRANSIENT,
                retry_delay=timedelta(0),
            )
            == ReadAction.DOWNLOAD
        )
    with factory.begin() as session:
        repo = ImageAnalysisRepository(session)
        last = repo.claim_video_read(**common, now=NOW + timedelta(seconds=4))
        assert last.attempt == 2
        assert (
            repo.record_video_read_failure(
                **common,
                now=NOW + timedelta(seconds=4),
                attempt=last,
                failure=SeekFailure.TRANSIENT,
                retry_delay=timedelta(0),
            )
            == ReadAction.FAIL
        )
        with pytest.raises(ValueError, match="状态"):
            repo.claim_video_read(**common, now=NOW + timedelta(seconds=4))


def test_video_read_is_guarded_by_parent_lease_and_selected_source(factory):
    _task(factory)
    request, plan, prompt = _case()
    _initialize(factory, request, plan, prompt)
    with factory.begin() as session:
        repo = ImageAnalysisRepository(session)
        with pytest.raises(LookupError, match="租约"):
            repo.claim_video_read(
                task_id="task-1",
                school_code="school-a",
                video_id="video-0",
                generation=2,
                lease_owner="image-node-1",
                now=NOW,
                policy=VideoReadPolicy(),
                max_download_attempts=2,
            )
        with pytest.raises(LookupError, match="学校"):
            repo.claim_video_read(
                task_id="task-1",
                school_code="school-a",
                video_id="absent",
                generation=3,
                lease_owner="image-node-1",
                now=NOW,
                policy=VideoReadPolicy(),
                max_download_attempts=2,
            )


def test_video_read_completed_only_by_current_attempt(factory):
    _task(factory)
    request, plan, prompt = _case()
    _initialize(factory, request, plan, prompt)
    common = dict(
        task_id="task-1",
        school_code="school-a",
        video_id="video-0",
        generation=3,
        lease_owner="image-node-1",
        now=NOW,
    )
    with factory.begin() as session:
        repo = ImageAnalysisRepository(session)
        attempt = repo.claim_video_read(
            **common, policy=VideoReadPolicy(), max_download_attempts=2
        )
        with pytest.raises(ValueError, match="不一致"):
            repo.complete_video_read(
                **common,
                attempt=replace(attempt, attempt=attempt.attempt + 1),
            )
        repo.complete_video_read(**common, attempt=attempt)
    with factory() as session:
        assert session.get(ImageVideoModel, "video-0").read_status == "COMPLETED"


def test_video_session_retries_seek_then_downloads_once_for_same_video(factory):
    _task(factory)
    request, plan, prompt = _case()
    with factory.begin() as session:
        task = session.get(MediaTaskModel, "task-1")
        task.lease_expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).replace(tzinfo=None)
    _initialize(factory, request, plan, prompt, now=datetime.now(timezone.utc))

    class Extractor:
        def __init__(self):
            self.sources = []

        def extract(self, source, offset_ms):
            self.sources.append(source)
            if source.startswith("https:"):
                raise FrameExtractionError("ffmpeg_decode_failed")
            return ExtractedFrame(b"\xff\xd8ok\xff\xd9", offset_ms)

    class Source:
        downloads = 0

        def probe_range(self, url):
            return object()

        def download(self, url, *, target, probe):
            self.downloads += 1
            target.write_bytes(b"video")
            return target

    extractor, source = Extractor(), Source()
    reader = VideoReadSession(
        factory,
        extractor,
        source,
        task_id="task-1",
        school_code="school-a",
        generation=3,
        lease_owner="image-node-1",
        seek_retry_limit=2,
    )
    try:
        assert (
            reader.extract("video-0", "https://example.test/0.mp4", 0).actual_pts_ms
            == 0
        )
        assert (
            reader.extract(
                "video-0", "https://example.test/0.mp4", 120000
            ).actual_pts_ms
            == 120000
        )
        reader.finish()
    finally:
        reader.close()
    assert source.downloads == 1
    assert len([item for item in extractor.sources if item.startswith("https:")]) == 3
    with factory() as session:
        video = session.get(ImageVideoModel, "video-0")
        assert (
            video.read_mode,
            video.seek_attempts,
            video.download_attempts,
            video.read_status,
        ) == ("DOWNLOAD", 3, 1, "COMPLETED")


def test_new_execution_generation_recovers_abandoned_active_read(factory):
    _task(factory)
    request, plan, prompt = _case()
    _initialize(factory, request, plan, prompt)
    common = dict(
        task_id="task-1",
        school_code="school-a",
        video_id="video-0",
        now=NOW,
        policy=VideoReadPolicy(),
        max_download_attempts=2,
    )
    with factory.begin() as session:
        first = ImageAnalysisRepository(session).claim_video_read(
            **common, generation=3, lease_owner="image-node-1"
        )
    with factory.begin() as session:
        task = session.get(MediaTaskModel, "task-1")
        task.execution_generation = 4
        task.lease_owner = "image-node-2"
    with factory.begin() as session:
        repo = ImageAnalysisRepository(session)
        with pytest.raises(LookupError, match="租约"):
            repo.record_video_read_failure(
                task_id="task-1",
                school_code="school-a",
                video_id="video-0",
                generation=3,
                lease_owner="image-node-1",
                now=NOW,
                attempt=first,
                failure=SeekFailure.TRANSIENT,
                policy=VideoReadPolicy(),
                max_download_attempts=2,
                retry_delay=timedelta(0),
            )
        resumed = repo.claim_video_read(
            **common, generation=4, lease_owner="image-node-2"
        )
        assert (resumed.mode, resumed.attempt) == ("SEEK", 2)


def test_classroom_pipeline_two_cameras_one_model_call_per_sample(factory, caplog):
    caplog.set_level("INFO")
    _task(factory)
    request, plan, prompt = _case()
    with factory.begin() as session:
        task = session.get(MediaTaskModel, "task-1")
        task.params = {"request": request.to_payload()}
        task.lease_expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).replace(tzinfo=None)

    class Extractor:
        calls = []

        def extract(self, source, offset_ms):
            self.calls.append((source, offset_ms))
            return ExtractedFrame(b"\xff\xd8jpeg\xff\xd9", offset_ms)

    class Store:
        uploads = []

        def put_jpeg(self, key, body):
            self.uploads.append(key)
            return ImageObjectRef("test", "COS", "test-bucket", key)

        def signed_read_url(self, ref, *, lifetime_seconds):
            return f"https://images.test/{ref.key}?ttl={lifetime_seconds}"

    class Model:
        calls = []

        def analyze(self, *, image_urls, bundle, requested_metrics):
            self.calls.append(image_urls)
            return {metric.value: 0 for metric in requested_metrics}

    class Source:
        pass

    extractor, store, model = Extractor(), Store(), Model()
    pipeline = ClassroomImagePipeline(
        factory,
        extractor,
        store,
        model,
        max_pts_error_ms=1000,
        video_source=Source(),
    )
    result = pipeline.execute(
        request=request,
        plan=plan,
        prompt=prompt,
        generation=3,
        lease_owner="image-node-1",
    )
    assert (result.uploaded_frames, result.completed_samples) == (6, 3)
    assert len(extractor.calls) == len(store.uploads) == 6
    assert len(model.calls) == 3
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "图像采样点开始" in messages
    assert "图像抽帧完成" in messages
    assert "图像上传完成" in messages
    assert "图像模型调用开始" in messages
    assert "图像模型调用完成" in messages
    assert "图像采样点完成" in messages
    assert "https://images.test/" not in messages
    assert all(len(images) == 2 for images in model.calls)
    with factory() as session:
        rows = session.query(ImageSampleModel).all()
        assert len(rows) == 3
        assert all(row.status == "COMPLETED" for row in rows)
        assert all(row.notification_status == "PENDING" for row in rows)
        assert all(row.notification_payload["id"] == "message-1" for row in rows)
        assert [
            row.notification_payload["sampleIndex"]
            for row in sorted(rows, key=lambda item: item.offset_ms)
        ] == [1, 2, 3]
        assert all(row.notification_payload["sampleTotal"] == 3 for row in rows)
        assert all(len(row.notification_payload["fileList"]) == 2 for row in rows)
        assert all(len(row.notification_payload["data"]) == 15 for row in rows)
        assert all(row.model_usage["model"] == "test-vision" for row in rows)
        assert all(row.model_usage["imageCount"] == 2 for row in rows)
        assert all(row.model_usage["metricCount"] == 13 for row in rows)
        assert all(row.model_usage["durationMs"] >= 0 for row in rows)
        assert all(row.model_usage["startedAt"] for row in rows)
        assert all(row.model_usage["completedAt"] for row in rows)
        assert all(
            row.read_status == "COMPLETED"
            for row in session.query(ImageVideoModel).all()
        )
    again = pipeline.execute(
        request=request,
        plan=plan,
        prompt=prompt,
        generation=3,
        lease_owner="image-node-1",
    )
    assert again.skipped_samples == 3
    assert len(model.calls) == 3
    with factory.begin() as session:
        task = session.get(MediaTaskModel, "task-1")
        task.status = "completed"
        task.completed_at = NOW.replace(tzinfo=None)
        assert ImageAnalysisRepository(session).ensure_task_terminal_notification(
            task_id="task-1", school_code="school-a"
        )

    class Publisher:
        sent = []

        def publish(self, *, event_id, payload):
            self.sent.append((event_id, payload))

    publisher = Publisher()
    batch = ImageNotificationDelivery(
        factory, publisher, instance_id="image-publisher"
    ).deliver_batch()
    assert (batch.claimed, batch.published, batch.failed) == (4, 4, 0)
    assert len({event_id for event_id, _ in publisher.sent}) == 4
    assert [payload["eventType"] for _, payload in publisher.sent] == [
        "SAMPLE_RESULT",
        "SAMPLE_RESULT",
        "SAMPLE_RESULT",
        "TASK_TERMINATED",
    ]
    assert all(
        len(payload["fileList"]) == 2
        for _, payload in publisher.sent
        if payload["eventType"] == "SAMPLE_RESULT"
    )
    assert "图像结果通知发布成功" in "\n".join(
        record.getMessage() for record in caplog.records
    )


def test_classroom_pipeline_rejects_misaligned_pts_before_upload(factory):
    _task(factory)
    request, plan, prompt = _case()
    with factory.begin() as session:
        task = session.get(MediaTaskModel, "task-1")
        task.params = {"request": request.to_payload()}
        task.lease_expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).replace(tzinfo=None)

    class Extractor:
        def extract(self, source, offset_ms):
            return ExtractedFrame(b"\xff\xd8jpeg\xff\xd9", offset_ms + 5000)

    class Store:
        def put_jpeg(self, key, body):
            pytest.fail("错位帧不得上传")

    pipeline = ClassroomImagePipeline(
        factory, Extractor(), Store(), object(), max_pts_error_ms=1000
    )
    with pytest.raises(ValueError, match="PTS"):
        pipeline.execute(
            request=request,
            plan=plan,
            prompt=prompt,
            generation=3,
            lease_owner="image-node-1",
        )
