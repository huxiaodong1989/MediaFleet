"""可选本地联调：真实HTTP视频seek + S3兼容对象服务 + SQLite课堂链。"""

from datetime import timedelta, timezone
import os

from minio import Minio
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.application.image_prompt_service import ImagePromptSnapshot
from media_platform.contracts.image_analysis import (
    IMAGE_TASK_TYPE,
    ImageAnalysisRequest,
)
from media_platform.contracts.task import TaskDeliveryChannel, TaskDispatchMessage
from media_platform.infrastructure.database.models import (
    ImageFrameModel,
    ImageSampleModel,
    ImageTaskNotificationModel,
    ImageVideoModel,
    MediaNodeModel,
    MediaTaskModel,
    RecordingServerModel,
)
from media_platform.infrastructure.storage.image_objects import MinioImageObjectStore
from services.image_analysis.application.classroom_pipeline import (
    ClassroomImagePipeline,
)
from services.image_analysis.application.notification_delivery import (
    ImageNotificationDelivery,
)
from services.image_analysis.application.task_handler import ImageTaskHandler
from services.image_analysis.planning import SamplingLimits
from services.image_analysis.prompt_defaults import load_bootstrap_prompt_bundle
from services.image_analysis.source_selection import SourceSelectionPolicy
from services.image_analysis.video_frames import FfmpegFrameExtractor, FfprobeVideoProbe
from services.image_analysis.video_source import HttpVideoSource
from urllib.parse import urlsplit


@pytest.mark.integration
def test_real_http_video_to_local_s3_and_sample_events(tmp_path):
    video_url = os.getenv("IMAGE_TEST_VIDEO_URL")
    s3_endpoint = os.getenv("IMAGE_TEST_S3_ENDPOINT")
    if not video_url or not s3_endpoint:
        pytest.skip("仅在显式指定本地HTTP视频和S3兼容测试端点时运行")
    probe = FfprobeVideoProbe().probe(video_url)
    assert probe.creation_time is not None
    start = probe.creation_time.astimezone(timezone(timedelta(hours=8)))
    classroom_minutes = 60 if os.getenv("IMAGE_TEST_FULL_CLASSROOM") == "1" else 6
    sample_count = classroom_minutes // 2
    request = ImageAnalysisRequest.model_validate(
        {
            "messageId": "local-real-video-1",
            "classroomId": "local-classroom",
            "spaceId": "local-room",
            "schoolId": "local-school",
            "timeZone": "Asia/Shanghai",
            "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": (start + timedelta(minutes=classroom_minutes)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "sampleIntervalSeconds": 120,
            "streamInfoList": [
                {
                    "id": "local-video",
                    "streamId": "local-camera",
                    "videoUrl": video_url,
                    "aiAnalysisTypeList": ["S_ANALYSIS", "T_ANALYSIS"],
                    "streamingMediaSource": "ROOM_CAMERA",
                }
            ],
        }
    )
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'image-local.db').as_posix()}")
    sa.event.listen(
        engine, "connect", lambda dbapi, _: dbapi.execute("PRAGMA foreign_keys=ON")
    )
    MediaTaskModel.metadata.create_all(
        engine,
        tables=[
            MediaNodeModel.__table__,
            RecordingServerModel.__table__,
            MediaTaskModel.__table__,
            ImageVideoModel.__table__,
            ImageSampleModel.__table__,
            ImageTaskNotificationModel.__table__,
            ImageFrameModel.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(
            MediaTaskModel(
                id="local-task",
                task_type=IMAGE_TASK_TYPE,
                status="pending",
                routing_key="image.task",
                message_id="mq-local-image-1",
                publish_status="PUBLISHED",
                max_retries=3,
                school_code=request.school_id,
                created_by="local-test",
                updated_by="local-test",
                params={
                    "request": request.to_payload(),
                    "promptBundle": {
                        "id": "local-prompt",
                        "version": 1,
                        "contentHash": "a" * 64,
                    },
                },
            )
        )
    client = Minio(
        s3_endpoint,
        access_key=os.getenv("IMAGE_TEST_S3_ACCESS", "localtest"),
        secret_key=os.getenv("IMAGE_TEST_S3_SECRET", "localtest123"),
        secure=False,
    )
    bucket = "image-local-test"
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    store = MinioImageObjectStore(client, profile="local-s3", bucket=bucket)

    class Model:
        calls = 0

        def analyze(self, *, image_urls, bundle, requested_metrics):
            self.calls += 1
            assert len(image_urls) == 1
            return {metric.value: 0 for metric in requested_metrics}

    class Publisher:
        events = []

        def publish(self, *, event_id, payload):
            self.events.append((event_id, payload))

    model, publisher = Model(), Publisher()
    prompt = ImagePromptSnapshot(
        id="local-prompt",
        school_code="GLOBAL",
        version=1,
        content_hash="a" * 64,
        content=load_bootstrap_prompt_bundle("local-fake-model"),
    )

    class PromptReader:
        def locked(self, bundle_id, *, school_code, expected_version, expected_hash):
            assert (bundle_id, expected_version, expected_hash) == (
                "local-prompt",
                1,
                "a" * 64,
            )
            return prompt

    parsed = urlsplit(video_url)
    handler = ImageTaskHandler(
        factory,
        ClassroomImagePipeline(
            factory,
            FfmpegFrameExtractor(),
            store,
            model,
            max_pts_error_ms=1000,
        ),
        PromptReader(),
        HttpVideoSource(
            allowed_hosts=(parsed.hostname,),
            allowed_networks=("127.0.0.0/8",),
            allowed_ports=(parsed.port,),
            max_download_bytes=2_000_000_000,
        ),
        FfprobeVideoProbe(),
        worker_id="local-image-node",
        selection_policy=SourceSelectionPolicy(
            version="local-duration-test",
            minimum_classroom_coverage=0.5,
            minimum_duration_ratio_to_longest=0.9,
            require_continuity=False,
        ),
        sampling_limits=SamplingLimits(
            min_interval_seconds=120,
            max_videos=3,
            max_classroom_duration_ms=7_200_000,
            max_samples=60,
            max_images=180,
        ),
    )
    message = TaskDispatchMessage(
        task_id="local-task",
        message_id="mq-local-image-1",
        school_code=request.school_id,
        task_type=IMAGE_TASK_TYPE,
        delivery_channel=TaskDeliveryChannel.IMAGE_ANALYSIS,
        routing_key=IMAGE_TASK_TYPE,
    )
    handler.handle(message)
    assert model.calls == sample_count
    with factory() as session:
        assert session.get(MediaTaskModel, "local-task").status == "completed"
    delivery = ImageNotificationDelivery(
        factory, publisher, instance_id="local-publisher"
    )
    assert delivery.deliver_batch(limit=sample_count).published == sample_count + 1
    assert len(publisher.events) == sample_count + 1
    sample_events = [
        payload
        for _, payload in publisher.events
        if payload["eventType"] == "SAMPLE_RESULT"
    ]
    terminal_events = [
        payload
        for _, payload in publisher.events
        if payload["eventType"] == "TASK_TERMINATED"
    ]
    assert len(sample_events) == sample_count
    assert all(len(payload["data"]) == 20 for payload in sample_events)
    assert len(terminal_events) == 1
    assert terminal_events[0]["status"] == "COMPLETED"
    with factory() as session:
        assert all(
            client.stat_object(bucket, row.object_key).size > 0
            for row in session.query(ImageFrameModel).all()
        )
    engine.dispose()
