"""独立图像分析实例的依赖装配与后台消费生命周期。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import os
import socket
from threading import Event, Thread

from media_platform.application import NodeHeartbeatReporter, NodeHeartbeatReporterConfig
from media_platform.application.image_prompt_service import (
    ImagePromptPolicy,
    ImagePromptReader,
    ensure_image_prompt_bootstrap,
)
from media_platform.contracts.topology import (
    IMAGE_ANALYSIS_TASK_DEAD_LETTER_EXCHANGE,
    IMAGE_ANALYSIS_TASK_EXCHANGE,
    IMAGE_ANALYSIS_TASK_QUEUE,
    IMAGE_ANALYSIS_TASK_RETRY_EXCHANGE,
)
from media_platform.contracts.image_analysis import IMAGE_TASK_TYPE
from media_platform.infrastructure.messaging import (
    RabbitMQConsumerConfig,
    TaskBusyError,
)
from services.image_analysis.application.classroom_pipeline import (
    ClassroomImagePipeline,
)
from services.image_analysis.application.notification_delivery import (
    ImageNotificationDelivery,
)
from services.image_analysis.application.task_handler import ImageTaskHandler
from services.image_analysis.concurrency import (
    ConcurrencyStage,
    ImageConcurrencySettings,
    InstanceConcurrency,
)
from services.image_analysis.infrastructure.result_publisher import (
    ImageResultPublisherConfig,
    PikaImageResultPublisher,
)
from services.image_analysis.infrastructure.task_consumer import (
    ImageAnalysisTaskConsumer,
)
from services.image_analysis.limited_resources import StageLimitedResource
from services.image_analysis.planning import SamplingLimits
from services.image_analysis.prompt_defaults import load_bootstrap_prompt_bundle
from services.image_analysis.source_selection import SourceSelectionPolicy
from services.image_analysis.video_frames import FfmpegFrameExtractor, FfprobeVideoProbe
from services.image_analysis.video_source import HttpVideoSource
from services.image_analysis.vision_model import VisionModelClient


LOGGER = logging.getLogger(__name__)
DEFAULT_CONTROL_CENTER_URL = "http://127.0.0.1:8008"


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required when image consumer is enabled")
    return value


def _object_store():
    from media_platform.infrastructure.storage.image_objects import (
        CosImageObjectStore,
        MinioImageObjectStore,
    )

    provider = os.getenv("IMAGE_ANALYSIS_STORAGE_PROVIDER", "COS").strip().upper()
    if provider == "COS":
        from qcloud_cos import CosConfig, CosS3Client

        client = CosS3Client(
            CosConfig(
                Region=_required("COS_REGION"),
                SecretId=_required("COS_SECRET_ID"),
                SecretKey=_required("COS_SECRET_KEY"),
            )
        )
        return CosImageObjectStore(
            client, profile="image-analysis-cos", bucket=_required("COS_BUCKET")
        )
    if provider == "MINIO":
        from minio import Minio

        client = Minio(
            _required("MINIO_ENDPOINT"),
            access_key=_required("MINIO_ACCESS_KEY"),
            secret_key=_required("MINIO_SECRET_KEY"),
            secure=os.getenv("MINIO_SECURE", "true").lower() in {"1", "true", "yes"},
        )
        return MinioImageObjectStore(
            client, profile="image-analysis-minio", bucket=_required("MINIO_BUCKET")
        )
    raise ValueError("IMAGE_ANALYSIS_STORAGE_PROVIDER must be COS or MINIO")


@dataclass
class ImageAnalysisRuntime:
    consumer: ImageAnalysisTaskConsumer
    delivery: ImageNotificationDelivery
    publisher: PikaImageResultPublisher
    concurrency: InstanceConcurrency
    worker_id: str
    heartbeat_reporter: NodeHeartbeatReporter | None = None
    reconnect_delay_seconds: int = 5
    notification_interval_seconds: int = 2
    _stop: Event = field(default_factory=Event, init=False, repr=False)
    _threads: list[Thread] = field(default_factory=list, init=False, repr=False)

    def _consume(self) -> None:
        while not self._stop.is_set():
            try:
                self.consumer.start_consuming()
            except Exception:
                if not self._stop.is_set():
                    LOGGER.exception("图像分析消费连接异常，等待重连")
            finally:
                self.consumer.close()
            self._stop.wait(self.reconnect_delay_seconds)

    def _deliver(self) -> None:
        while not self._stop.is_set():
            try:
                self.delivery.deliver_batch()
            except Exception:
                if not self._stop.is_set():
                    LOGGER.exception("图像结果通知扫描失败")
            self._stop.wait(self.notification_interval_seconds)

    async def start(self) -> None:
        self._stop.clear()
        self._threads = [
            Thread(target=self._consume, name="image-task-consumer", daemon=True),
            Thread(target=self._deliver, name="image-result-delivery", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        if self.heartbeat_reporter is not None:
            self.heartbeat_reporter.start()

    async def close(self) -> None:
        self._stop.set()
        self.consumer.stop()
        for thread in self._threads:
            if thread.is_alive():
                await asyncio.to_thread(thread.join, 10)
        self.consumer.close()
        self.publisher.close()
        if self.heartbeat_reporter is not None:
            self.heartbeat_reporter.close()


def _consumer_ready(consumer: ImageAnalysisTaskConsumer) -> bool:
    connection = getattr(consumer, "_connection", None)
    return connection is not None and not connection.is_closed


def _build_image_analysis_heartbeat_reporter(
    *,
    worker_id: str,
    consumer: ImageAnalysisTaskConsumer,
    concurrency: InstanceConcurrency,
    settings: ImageConcurrencySettings,
    api_key: str,
) -> NodeHeartbeatReporter:
    """创建图像分析节点心跳，上报真实消费连接和实例内阶段容量。"""

    def heartbeat_payload() -> dict:
        stages = concurrency.snapshot()
        ready = _consumer_ready(consumer)
        classroom = stages[ConcurrencyStage.CLASSROOM.value]
        return {
            "node_code": worker_id,
            "node_name": os.getenv("MEDIA_NODE_NAME", worker_id),
            "node_type": "IMAGE_ANALYSIS",
            "status": "ONLINE",
            "weight": int(os.getenv("MEDIA_NODE_WEIGHT", "100")),
            "capabilities": [IMAGE_TASK_TYPE],
            "capacity": {
                "worker_prefetch": settings.consumer_prefetch,
                "consumer_enabled": True,
                "processing_tasks": classroom["active"],
                "max_concurrent_classrooms": classroom["limit"],
                "stages": stages,
            },
            "readiness": "READY" if ready else "NOT_READY",
            "readiness_details": {"consumer_ready": ready},
            "updated_by": "image-analysis",
        }

    return NodeHeartbeatReporter(
        NodeHeartbeatReporterConfig(
            control_center_base_url=os.getenv(
                "MEDIA_NODE_HEARTBEAT_CONTROL_CENTER_URL",
                DEFAULT_CONTROL_CENTER_URL,
            ),
            api_key=api_key,
            interval_seconds=_positive_float(
                "MEDIA_NODE_HEARTBEAT_INTERVAL_SECONDS", 30
            ),
            timeout_seconds=_positive_float(
                "MEDIA_NODE_HEARTBEAT_TIMEOUT_SECONDS", 5
            ),
            enabled=True,
        ),
        heartbeat_payload,
    )


def build_image_analysis_runtime(
    settings: ImageConcurrencySettings | None = None,
) -> ImageAnalysisRuntime:
    from media_platform.common.config import get_settings
    from media_platform.infrastructure.database.init import init_database
    from media_platform.infrastructure.database.session import SessionLocal

    platform = get_settings()
    init_database(platform)
    settings = settings or ImageConcurrencySettings.from_env()
    concurrency = InstanceConcurrency(settings)
    worker_id = (
        os.getenv("IMAGE_ANALYSIS_INSTANCE_ID", "").strip()
        or f"{socket.gethostname()}-{os.getpid()}"
    )
    model = _required("IMAGE_ANALYSIS_DEFAULT_MODEL")
    allowed = frozenset(
        filter(
            None,
            (
                item.strip()
                for item in _required("IMAGE_ANALYSIS_ALLOWED_MODELS").split(",")
            ),
        )
    )
    policy = ImagePromptPolicy(
        allowed_models=allowed,
        output_schema_versions=frozenset({"image-metrics.v1"}),
        metric_definition_versions=frozenset({"classroom-behavior.v1"}),
    )
    ensure_image_prompt_bootstrap(
        SessionLocal, lambda: load_bootstrap_prompt_bundle(model), policy
    )
    reader = ImagePromptReader(SessionLocal, policy)
    reader.published("GLOBAL")
    source = HttpVideoSource(
        allowed_hosts=tuple(
            filter(
                None,
                (
                    s.strip()
                    for s in _required("IMAGE_ANALYSIS_VIDEO_ALLOWED_HOSTS").split(",")
                ),
            )
        ),
        allowed_networks=tuple(
            filter(
                None,
                (
                    s.strip()
                    for s in _required("IMAGE_ANALYSIS_VIDEO_ALLOWED_NETWORKS").split(
                        ","
                    )
                ),
            )
        ),
        allowed_ports=tuple(
            int(s.strip())
            for s in os.getenv("IMAGE_ANALYSIS_VIDEO_ALLOWED_PORTS", "80,443").split(
                ","
            )
        ),
        max_download_bytes=_positive_int(
            "IMAGE_ANALYSIS_MAX_DOWNLOAD_BYTES", 4_000_000_000
        ),
    )
    store = StageLimitedResource(_object_store(), concurrency)
    extractor = StageLimitedResource(
        FfmpegFrameExtractor(threads=settings.ffmpeg_threads), concurrency
    )
    probe = StageLimitedResource(FfprobeVideoProbe(), concurrency)
    source = StageLimitedResource(source, concurrency)
    pipeline = ClassroomImagePipeline(
        SessionLocal,
        extractor,
        store,
        StageLimitedResource(
            VisionModelClient(
                api_key=_required("IMAGE_ANALYSIS_OPENAI_API_KEY"),
                base_url=_required("IMAGE_ANALYSIS_OPENAI_BASE_URL"),
            ),
            concurrency,
        ),
        max_pts_error_ms=_positive_int("IMAGE_ANALYSIS_MAX_PTS_ERROR_MS", 5000),
        video_source=source,
    )
    handler = ImageTaskHandler(
        SessionLocal,
        pipeline,
        reader,
        source,
        probe,
        worker_id=worker_id,
        selection_policy=SourceSelectionPolicy(
            version="duration-heuristic.v1",
            minimum_classroom_coverage=0.5,
            minimum_duration_ratio_to_longest=0.8,
            require_continuity=False,
        ),
        sampling_limits=SamplingLimits(
            min_interval_seconds=30,
            max_videos=4,
            max_classroom_duration_ms=4 * 60 * 60 * 1000,
            max_samples=480,
            max_images=1920,
        ),
        lease_timeout=timedelta(
            seconds=_positive_int("IMAGE_ANALYSIS_LEASE_SECONDS", 600)
        ),
    )

    def limited_handle(message) -> None:
        with concurrency.try_slot(ConcurrencyStage.CLASSROOM) as admitted:
            if not admitted:
                raise TaskBusyError("图像课堂并发额度已满")
            handler.handle(message)

    rabbit = platform.rabbitmq
    consumer = ImageAnalysisTaskConsumer(
        RabbitMQConsumerConfig(
            host=rabbit.host,
            port=rabbit.port,
            username=rabbit.username,
            password=rabbit.password,
            virtual_host=rabbit.vhost,
            heartbeat=rabbit.heartbeat,
            blocked_connection_timeout=rabbit.blocked_connection_timeout,
            exchange=IMAGE_ANALYSIS_TASK_EXCHANGE,
            retry_exchange=IMAGE_ANALYSIS_TASK_RETRY_EXCHANGE,
            dead_letter_exchange=IMAGE_ANALYSIS_TASK_DEAD_LETTER_EXCHANGE,
            queue_name=IMAGE_ANALYSIS_TASK_QUEUE,
            routing_keys=("image.#",),
            prefetch_count=settings.consumer_prefetch,
        ),
        limited_handle,
    )
    publisher = PikaImageResultPublisher(
        ImageResultPublisherConfig(
            host=rabbit.host,
            port=rabbit.port,
            username=rabbit.username,
            password=rabbit.password,
            virtual_host=rabbit.vhost,
            exchange=os.getenv(
                "IMAGE_ANALYSIS_RESULT_EXCHANGE", "image.analysis.result"
            ),
            routing_key=os.getenv(
                "IMAGE_ANALYSIS_RESULT_ROUTING_KEY", "image.analysis.sample"
            ),
        )
    )
    delivery = ImageNotificationDelivery(SessionLocal, publisher, instance_id=worker_id)
    heartbeat = _build_image_analysis_heartbeat_reporter(
        worker_id=worker_id,
        consumer=consumer,
        concurrency=concurrency,
        settings=settings,
        api_key=getattr(platform, "API_KEY", ""),
    )
    return ImageAnalysisRuntime(
        consumer=consumer,
        delivery=delivery,
        publisher=publisher,
        concurrency=concurrency,
        worker_id=worker_id,
        heartbeat_reporter=heartbeat,
    )


__all__ = [
    "ImageAnalysisRuntime",
    "_build_image_analysis_heartbeat_reporter",
    "build_image_analysis_runtime",
]
