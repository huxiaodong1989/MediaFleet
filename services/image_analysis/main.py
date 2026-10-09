"""图像分析服务入口；消费者仅在显式开启后装配。"""

import os
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Response
from pydantic import BaseModel
import uvicorn

from media_platform.application import create_service_app
from media_platform.application.image_prompt_service import (
    ImagePromptPolicy,
    ImagePromptReader,
    ensure_image_prompt_bootstrap,
)
from media_platform.contracts.service import ServiceInfo, ServiceRole
from media_platform.infrastructure.observability.logger import (
    configure_third_party_loggers,
)
from services.image_analysis.concurrency import (
    ImageConcurrencySettings,
    InstanceConcurrency,
)
from services.image_analysis.prompt_defaults import load_bootstrap_prompt_bundle
from services.image_analysis.application.runtime import (
    ImageAnalysisRuntime,
    build_image_analysis_runtime,
)
from sqlalchemy.orm import Session


SERVICE_INFO = ServiceInfo(
    name="mediafleet-image-analysis",
    role=ServiceRole.IMAGE_ANALYSIS,
    version="0.1.0",
    description="独立课堂图像分析服务；需显式启用消费者并完成部署依赖配置",
)


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"] = "not_ready"
    reason: str = "consumer_disabled"
    consumer_enabled: bool = False


def create_image_analysis_app(
    concurrency_settings: ImageConcurrencySettings | None = None,
    *,
    prompt_session_factory: Callable[[], Session] | None = None,
    prompt_policy: ImagePromptPolicy | None = None,
    prompt_default_model: str | None = None,
    runtime_factory: Callable[[], ImageAnalysisRuntime] | None = None,
) -> FastAPI:
    """只有显式配置默认模型和能力名单时，才执行首次提示词初始化。"""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        enabled = os.getenv(
            "IMAGE_ANALYSIS_CONSUMER_ENABLED", "false"
        ).strip().lower() in {"1", "true", "yes", "on"}
        settings = concurrency_settings or ImageConcurrencySettings.from_env()
        application.state.image_concurrency_settings = settings
        application.state.image_concurrency = InstanceConcurrency(settings)
        model = (
            prompt_default_model
            or os.getenv("IMAGE_ANALYSIS_DEFAULT_MODEL", "").strip()
        )
        allowed_models = frozenset(
            item.strip()
            for item in os.getenv("IMAGE_ANALYSIS_ALLOWED_MODELS", "").split(",")
            if item.strip()
        )
        if prompt_policy is None and (model or allowed_models):
            prompt_config = ImagePromptPolicy(
                allowed_models=allowed_models,
                output_schema_versions=frozenset({"image-metrics.v1"}),
                metric_definition_versions=frozenset({"classroom-behavior.v1"}),
            )
        else:
            prompt_config = prompt_policy
        if prompt_config is not None or model or prompt_session_factory is not None:
            if not model or prompt_config is None:
                raise ValueError("图像提示词初始化需要默认模型和受控模型名单")
            if prompt_session_factory is None:
                from media_platform.infrastructure.database.session import SessionLocal

                session_factory = SessionLocal
            else:
                session_factory = prompt_session_factory

            def initialize_prompt() -> None:
                ensure_image_prompt_bootstrap(
                    session_factory,
                    lambda: load_bootstrap_prompt_bundle(model),
                    prompt_config,
                )
                ImagePromptReader(session_factory, prompt_config).published("GLOBAL")

            await asyncio.to_thread(initialize_prompt)
            application.state.image_prompt_policy = prompt_config
        runtime = None
        if enabled:
            runtime = (runtime_factory or build_image_analysis_runtime)()
            application.state.image_runtime = runtime
            await runtime.start()
        try:
            yield
        finally:
            if runtime is not None:
                await runtime.close()

    application = create_service_app(SERVICE_INFO, lifespan=lifespan)

    @application.get("/ready", response_model=Readiness, tags=["system"])
    async def readiness(response: Response) -> Readiness:
        response.headers["Cache-Control"] = "no-store"
        runtime = getattr(application.state, "image_runtime", None)
        if runtime is None:
            response.status_code = 503
            return Readiness(reason="consumer_disabled")
        if not runtime.consumer._connection or runtime.consumer._connection.is_closed:
            response.status_code = 503
            return Readiness(reason="rabbitmq_disconnected", consumer_enabled=True)
        return Readiness(status="ready", reason="ok", consumer_enabled=True)

    return application


app = create_image_analysis_app()


def _add_persistent_log_handler() -> RotatingFileHandler | None:
    """保留 stdout 给 Portainer，同时把业务日志写入宿主机挂载目录。"""
    log_dir = os.getenv("SERVICE_LOG_DIR", "").strip()
    if not log_dir:
        return None
    path = Path(log_dir).resolve()
    path.mkdir(parents=True, exist_ok=True)
    target = path / "image-analysis.log"
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(
            handler.baseFilename
        ) == target:
            return handler
    handler = RotatingFileHandler(
        target,
        maxBytes=int(os.getenv("SERVICE_LOG_MAX_BYTES", str(50 * 1024 * 1024))),
        backupCount=int(os.getenv("SERVICE_LOG_BACKUP_COUNT", "10")),
        encoding="utf-8",
    )
    handler.setLevel(os.getenv("SERVICE_LOG_LEVEL", "INFO").upper())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    root.addHandler(handler)
    return handler


def run() -> None:
    logging.basicConfig(
        level=os.getenv("SERVICE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    _add_persistent_log_handler()
    configure_third_party_loggers()
    uvicorn.run(
        "services.image_analysis.main:app",
        host=os.getenv("SERVICE_HOST", "127.0.0.1"),
        port=int(os.getenv("SERVICE_PORT", "8013")),
        log_level=os.getenv("SERVICE_LOG_LEVEL", "INFO").lower(),
    )


if __name__ == "__main__":
    run()
