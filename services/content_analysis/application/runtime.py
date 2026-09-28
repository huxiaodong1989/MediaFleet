"""独立 AI 评课服务的依赖装配和消费线程生命周期。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import os
import socket
from threading import Event, Thread

from sqlalchemy.exc import IntegrityError

from media_platform.application import NodeHeartbeatReporter, NodeHeartbeatReporterConfig
from media_platform.contracts.topology import (
    CONTENT_ANALYSIS_TASK_DEAD_LETTER_EXCHANGE,
    CONTENT_ANALYSIS_TASK_EXCHANGE,
    CONTENT_ANALYSIS_TASK_QUEUE,
    CONTENT_ANALYSIS_TASK_RETRY_EXCHANGE,
)
from media_platform.infrastructure.messaging import PikaTaskConsumer, RabbitMQConsumerConfig
from services.content_analysis.application.log_sanitizer import mask_secret, sanitize_url
from services.content_analysis.application.prompt_defaults import load_bootstrap_prompt_bundle
from services.content_analysis.application.task_execution_service import ContentTaskExecutionService
from services.content_analysis.application.workflow import ClassEvaluationWorkflow
from services.content_analysis.infrastructure.clients import (
    BehaviorAnalysisClient,
    EvaluationLlmClient,
    ProgressCallbackClient,
    SubtitleClient,
)
from services.content_analysis.infrastructure.file_preprocessor import EvaluationFilePreprocessor
from services.content_analysis.infrastructure.repositories import PromptBundleRepository


LOGGER = logging.getLogger(__name__)
DEFAULT_CONTROL_CENTER_URL = "http://127.0.0.1:8008"


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} 必须大于0")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} 必须大于0")
    return value


def _bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是true或false")


def _instance_id() -> str:
    configured = os.getenv("CONTENT_ANALYSIS_INSTANCE_ID", "").strip()
    return configured or f"{socket.gethostname()}-{os.getpid()}"


def _ensure_bootstrap_prompt(session_factory, default_model: str) -> None:
    """并发启动多个内容分析实例时，只允许一个实例写入首个提示词版本。"""

    try:
        with session_factory() as session:
            with session.begin():
                PromptBundleRepository(session).ensure_bootstrap(
                    load_bootstrap_prompt_bundle(default_model)
                )
    except IntegrityError:
        with session_factory() as session:
            existing = PromptBundleRepository(session).get_published("GLOBAL")
            if existing is None:
                raise
            LOGGER.info(
                "其他内容分析实例已并发完成提示词初始化，复用数据库版本: version=%s, bundle_id=%s",
                existing.version,
                existing.id,
            )


@dataclass
class ContentAnalysisRuntime:
    consumer: PikaTaskConsumer
    execution_service: ContentTaskExecutionService
    worker_id: str
    consumer_enabled: bool = True
    reconnect_delay_seconds: float = 5.0
    heartbeat_reporter: NodeHeartbeatReporter | None = None
    _stop_event: Event = field(default_factory=Event, init=False, repr=False)
    _consumer_thread: Thread | None = field(default=None, init=False, repr=False)

    def _consume_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.consumer.start_consuming()
            except Exception:
                if not self._stop_event.is_set():
                    LOGGER.exception(
                        "AI评课消费连接异常，%s秒后重连",
                        self.reconnect_delay_seconds,
                    )
            finally:
                self.consumer.close()
            if not self._stop_event.is_set():
                self._stop_event.wait(self.reconnect_delay_seconds)

    async def start(self) -> None:
        if self.heartbeat_reporter is not None:
            self.heartbeat_reporter.start()
        if not self.consumer_enabled:
            LOGGER.warning("AI评课消费者已禁用: worker_id=%s", self.worker_id)
            return
        self._stop_event.clear()
        self._consumer_thread = Thread(
            target=self._consume_loop,
            name=f"content-analysis-consumer-{self.worker_id}",
            daemon=True,
        )
        self._consumer_thread.start()

    async def close(self) -> None:
        self._stop_event.set()
        self.consumer.stop()
        if self._consumer_thread is not None and self._consumer_thread.is_alive():
            await asyncio.to_thread(self._consumer_thread.join, 10)
        self._consumer_thread = None
        self.consumer.close()
        if self.heartbeat_reporter is not None:
            self.heartbeat_reporter.close()


def build_content_analysis_runtime() -> ContentAnalysisRuntime:
    from media_platform.common.config import get_settings
    from media_platform.infrastructure.database.init import init_database
    from services.content_analysis.infrastructure import models as _content_models  # noqa: F401

    settings = get_settings()
    init_database(settings)
    from media_platform.infrastructure.database.session import SessionLocal

    worker_id = _instance_id()
    default_model = os.getenv("CONTENT_ANALYSIS_DEFAULT_MODEL", "gpt-4.1").strip()
    llm_api_key = os.getenv("CONTENT_ANALYSIS_OPENAI_API_KEY", "").strip()
    llm_base_url = os.getenv(
        "CONTENT_ANALYSIS_OPENAI_BASE_URL",
        "https://api.openai.com/v1",
    ).strip()
    LOGGER.info(
        "AI评课大模型配置已加载: base_url=%s, model=%s, api_key=%s",
        sanitize_url(llm_base_url),
        default_model,
        mask_secret(llm_api_key),
    )
    _ensure_bootstrap_prompt(SessionLocal, default_model)

    progress_callback = ProgressCallbackClient(
        timeout_seconds=_positive_float("CONTENT_ANALYSIS_CALLBACK_TIMEOUT_SECONDS", 10)
    )
    workflow = ClassEvaluationWorkflow(
        SessionLocal,
        subtitle_client=SubtitleClient(
            timeout_seconds=_positive_float("CONTENT_ANALYSIS_DOWNLOAD_TIMEOUT_SECONDS", 60),
            max_size_mb=_positive_int("CONTENT_ANALYSIS_MAX_FILE_SIZE_MB", 100),
        ),
        behavior_client=BehaviorAnalysisClient(
            os.getenv("CONTENT_ANALYSIS_BEHAVIOR_API_URL", "").strip() or None,
            timeout_seconds=_positive_float("CONTENT_ANALYSIS_BEHAVIOR_TIMEOUT_SECONDS", 30),
        ),
        file_preprocessor=EvaluationFilePreprocessor(
            timeout_seconds=_positive_float("CONTENT_ANALYSIS_DOWNLOAD_TIMEOUT_SECONDS", 60),
            max_size_mb=_positive_int("CONTENT_ANALYSIS_MAX_FILE_SIZE_MB", 100),
        ),
        llm_client=EvaluationLlmClient(
            api_key=llm_api_key,
            base_url=llm_base_url,
            timeout_seconds=_positive_float("CONTENT_ANALYSIS_LLM_TIMEOUT_SECONDS", 180),
        ),
        progress_callback=progress_callback,
        llm_max_retries=_positive_int("CONTENT_ANALYSIS_LLM_MAX_RETRIES", 3),
    )
    execution_service = ContentTaskExecutionService(
        SessionLocal,
        workflow,
        worker_id,
        execution_timeout=timedelta(seconds=_positive_int("CONTENT_ANALYSIS_EXECUTION_TIMEOUT_SECONDS", 3600)),
        lease_timeout=timedelta(seconds=_positive_int("CONTENT_ANALYSIS_LEASE_SECONDS", 1800)),
        lease_renew_interval=timedelta(seconds=_positive_int("CONTENT_ANALYSIS_LEASE_RENEW_SECONDS", 600)),
    )
    rabbitmq = settings.rabbitmq
    prefetch = _positive_int("CONTENT_ANALYSIS_PREFETCH_COUNT", 1)
    consumer = PikaTaskConsumer(
        RabbitMQConsumerConfig(
            host=rabbitmq.host,
            exchange=CONTENT_ANALYSIS_TASK_EXCHANGE,
            retry_exchange=CONTENT_ANALYSIS_TASK_RETRY_EXCHANGE,
            dead_letter_exchange=CONTENT_ANALYSIS_TASK_DEAD_LETTER_EXCHANGE,
            queue_name=CONTENT_ANALYSIS_TASK_QUEUE,
            routing_keys=("content.#",),
            port=rabbitmq.port,
            username=rabbitmq.username,
            password=rabbitmq.password,
            virtual_host=rabbitmq.vhost,
            heartbeat=rabbitmq.heartbeat,
            blocked_connection_timeout=rabbitmq.blocked_connection_timeout,
            prefetch_count=prefetch,
            retry_delay_milliseconds=_positive_int("CONTENT_ANALYSIS_RETRY_DELAY_MILLISECONDS", 5000),
        ),
        execution_service.handle,
    )
    consumer_enabled = _bool("CONTENT_ANALYSIS_CONSUMER_ENABLED", True)

    def heartbeat_payload() -> dict:
        return {
            "node_code": worker_id,
            "node_name": os.getenv("MEDIA_NODE_NAME", worker_id),
            "node_type": "CONTENT_ANALYSIS",
            "status": "ONLINE",
            "weight": int(os.getenv("MEDIA_NODE_WEIGHT", "100")),
            "capabilities": ["content.class_evaluation"],
            "capacity": {
                "worker_prefetch": prefetch,
                "consumer_enabled": consumer_enabled,
                "processing_tasks": execution_service.processing_count,
            },
            "readiness": "READY",
            "readiness_details": {"consumer_ready": consumer_enabled},
            "updated_by": "content-analysis",
        }

    heartbeat = NodeHeartbeatReporter(
        NodeHeartbeatReporterConfig(
            control_center_base_url=os.getenv(
                "MEDIA_NODE_HEARTBEAT_CONTROL_CENTER_URL",
                DEFAULT_CONTROL_CENTER_URL,
            ),
            api_key=getattr(settings, "API_KEY", ""),
            interval_seconds=_positive_float("MEDIA_NODE_HEARTBEAT_INTERVAL_SECONDS", 30),
            timeout_seconds=_positive_float("MEDIA_NODE_HEARTBEAT_TIMEOUT_SECONDS", 5),
            enabled=True,
        ),
        heartbeat_payload,
    )
    return ContentAnalysisRuntime(
        consumer=consumer,
        execution_service=execution_service,
        worker_id=worker_id,
        consumer_enabled=consumer_enabled,
        reconnect_delay_seconds=_positive_float("CONTENT_ANALYSIS_RECONNECT_DELAY_SECONDS", 5),
        heartbeat_reporter=heartbeat,
    )


__all__ = ["ContentAnalysisRuntime", "build_content_analysis_runtime"]
