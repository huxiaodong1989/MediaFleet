"""Opt-in local MySQL/RabbitMQ/MinIO classroom flow through the control-center API."""

from datetime import datetime, timedelta
import json
import os
from time import monotonic, sleep
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pika
import pytest


@pytest.mark.integration
def test_image_classroom_from_control_center_to_rabbit_hook(monkeypatch):
    if os.getenv("IMAGE_TEST_E2E") != "1":
        pytest.skip("requires isolated local MySQL, RabbitMQ, MinIO and video HTTP")

    required = (
        "IMAGE_TEST_MYSQL_PASSWORD",
        "IMAGE_TEST_RABBIT_USER",
        "IMAGE_TEST_RABBIT_PASSWORD",
        "IMAGE_TEST_S3_ACCESS",
        "IMAGE_TEST_S3_SECRET",
        "IMAGE_TEST_VIDEO_URL",
    )
    assert all(os.getenv(name) for name in required)
    values = {
        "IGNORE_ENV_FILE": "1",
        "SERVICE_DOTENV_ENABLED": "false",
        "DB_TYPE": "mysql",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "19306",
        "DB_USER": "root",
        "DB_PASSWORD": os.environ["IMAGE_TEST_MYSQL_PASSWORD"],
        "DB_NAME": "mediafleet",
        "DB_AUTO_INIT": "true",
        "DB_AUTO_MIGRATE": "true",
        "RABBITMQ_HOST": "127.0.0.1",
        "RABBITMQ_PORT": "19172",
        "RABBITMQ_USER": os.environ["IMAGE_TEST_RABBIT_USER"],
        "RABBITMQ_PASSWORD": os.environ["IMAGE_TEST_RABBIT_PASSWORD"],
        "RABBITMQ_VHOST": "/",
        "MINIO_ENDPOINT": "127.0.0.1:19100",
        "MINIO_ACCESS_KEY": os.environ["IMAGE_TEST_S3_ACCESS"],
        "MINIO_SECRET_KEY": os.environ["IMAGE_TEST_S3_SECRET"],
        "MINIO_BUCKET": "image-e2e-test",
        "MINIO_SECURE": "false",
        "IMAGE_ANALYSIS_STORAGE_PROVIDER": "MINIO",
        "IMAGE_ANALYSIS_VIDEO_ALLOWED_HOSTS": "127.0.0.1",
        "IMAGE_ANALYSIS_VIDEO_ALLOWED_NETWORKS": "127.0.0.0/8",
        "IMAGE_ANALYSIS_VIDEO_ALLOWED_PORTS": "19080",
        "IMAGE_ANALYSIS_OPENAI_API_KEY": "local-mock-only",
        "IMAGE_ANALYSIS_OPENAI_BASE_URL": "http://127.0.0.1:1/v1",
        "IMAGE_ANALYSIS_DEFAULT_MODEL": "local-mock-vision",
        "IMAGE_ANALYSIS_ALLOWED_MODELS": "local-mock-vision",
        "IMAGE_ANALYSIS_INTAKE_ENABLED": "true",
        "IMAGE_ANALYSIS_CONSUMER_ENABLED": "true",
        "API_KEY": "local-image-e2e",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    from media_platform.common.config import get_settings

    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()

    from minio import Minio
    from media_platform.contracts.image_analysis import BehaviorMetric
    from media_platform.contracts.topology import IMAGE_ANALYSIS_TASK_QUEUE
    from services.control_center.main import create_control_center_app
    from services.image_analysis.main import create_image_analysis_app
    import services.image_analysis.application.runtime as image_runtime

    minio = Minio(
        "127.0.0.1:19100",
        access_key=os.environ["IMAGE_TEST_S3_ACCESS"],
        secret_key=os.environ["IMAGE_TEST_S3_SECRET"],
        secure=False,
    )
    if not minio.bucket_exists("image-e2e-test"):
        minio.make_bucket("image-e2e-test")

    calls = []

    class FakeVisionModel:
        def __init__(self, **kwargs):
            pass

        def analyze(self, *, image_urls, bundle, requested_metrics):
            assert len(image_urls) == 2
            for url in image_urls:
                image = httpx.get(url, timeout=10)
                assert image.status_code == 200
                assert image.content.startswith(b"\xff\xd8")
            calls.append(image_urls)
            return {metric.value: 0 for metric in requested_metrics}

    monkeypatch.setattr(image_runtime, "VisionModelClient", FakeVisionModel)
    credentials = pika.PlainCredentials(
        os.environ["IMAGE_TEST_RABBIT_USER"],
        os.environ["IMAGE_TEST_RABBIT_PASSWORD"],
    )
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host="127.0.0.1", port=19172, credentials=credentials)
    )
    channel = connection.channel()
    exchange = "image.analysis.result"
    queue = "codex-image-e2e-" + uuid4().hex
    channel.exchange_declare(exchange=exchange, exchange_type="direct", durable=True)
    channel.queue_declare(queue=queue, durable=False)
    channel.queue_bind(
        exchange=exchange, queue=queue, routing_key="image.analysis.sample"
    )
    try:
        with TestClient(create_control_center_app()) as center:
            with TestClient(create_image_analysis_app()) as image:
                deadline = monotonic() + 15
                while image.get("/ready").status_code != 200 and monotonic() < deadline:
                    sleep(0.1)
                assert image.get("/ready").status_code == 200
                marker = uuid4().hex
                start = datetime(2026, 3, 13, 8, 28, 5)
                request = {
                    "messageId": "local-e2e-" + marker,
                    "classroomId": "class-" + marker,
                    "spaceId": "room-" + marker,
                    "schoolId": "local-school",
                    "timeZone": "Asia/Shanghai",
                    "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
                    "endTime": (start + timedelta(minutes=4)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "sampleIntervalSeconds": 120,
                    "streamInfoList": [
                        {
                            "id": "local-student-video",
                            "streamId": "local-student-camera",
                            "videoUrl": os.environ["IMAGE_TEST_VIDEO_URL"],
                            "aiAnalysisTypeList": ["S_ANALYSIS"],
                            "streamingMediaSource": "ROOM_CAMERA",
                        },
                        {
                            "id": "local-teacher-video",
                            "streamId": "local-teacher-camera",
                            "videoUrl": os.environ["IMAGE_TEST_VIDEO_URL"],
                            "aiAnalysisTypeList": ["T_ANALYSIS"],
                            "streamingMediaSource": "TEACHER_CAMERA",
                        },
                    ],
                }
                created = center.post(
                    "/api/v1/tasks/image-analysis",
                    headers={"X-API-Key": "local-image-e2e"},
                    json=request,
                )
                assert created.status_code == 200, created.text
                task_id = created.json()["taskId"]
                payloads = []
                deadline = monotonic() + 90
                while monotonic() < deadline:
                    method, _, body = channel.basic_get(queue=queue, auto_ack=True)
                    if method is not None:
                        payloads.append(json.loads(body))
                    if len(payloads) == 2:
                        break
                    sleep(0.2)
                assert len(payloads) == 2
                assert len(calls) == 2
                assert {p["id"] for p in payloads} == {request["messageId"]}
                assert len({p["eventId"] for p in payloads}) == 2
                assert all(len(p["fileList"]) == 2 for p in payloads)
                assert all(
                    set(p["aiAnalysisImageFileUrlMap"]) == {"S_ANALYSIS", "T_ANALYSIS"}
                    for p in payloads
                )
                assert all(
                    set(BehaviorMetric._value2member_map_) <= p["data"].keys()
                    for p in payloads
                )
                status = center.get(
                    f"/api/v1/tasks/{task_id}",
                    headers={"X-API-Key": "local-image-e2e"},
                )
                assert status.status_code == 200
                assert status.json()["status"] == "completed"
                assert (
                    channel.queue_declare(
                        queue=IMAGE_ANALYSIS_TASK_QUEUE, passive=True
                    ).method.message_count
                    == 0
                )
    finally:
        channel.queue_delete(queue=queue)
        connection.close()
