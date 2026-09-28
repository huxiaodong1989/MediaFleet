"""独立 AI 评课服务入口。"""

from collections.abc import Callable
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI
import uvicorn

from media_platform.application import create_service_app
from media_platform.common import get_env_bool, get_env_int, load_env_file
from media_platform.contracts.service import ServiceInfo, ServiceRole
from media_platform.infrastructure.observability.logger import configure_third_party_loggers
from services.content_analysis.application.runtime import (
    ContentAnalysisRuntime,
    build_content_analysis_runtime,
)


load_env_file(
    Path(__file__).resolve().parent / ".env",
    enabled=get_env_bool("SERVICE_DOTENV_ENABLED", True),
)

SERVICE_INFO = ServiceInfo(
    name="mediafleet-content-analysis",
    role=ServiceRole.CONTENT_ANALYSIS,
    version="0.1.0",
    description="独立AI评课任务执行服务",
)

RuntimeFactory = Callable[[], ContentAnalysisRuntime]


def create_content_analysis_app(
    runtime_factory: RuntimeFactory = build_content_analysis_runtime,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime = runtime_factory()
        application.state.content_analysis_runtime = runtime
        await runtime.start()
        try:
            yield
        finally:
            await runtime.close()

    application = create_service_app(SERVICE_INFO, lifespan=lifespan)
    return application


app = create_content_analysis_app()


def run() -> None:
    logging.basicConfig(
        level=os.getenv("SERVICE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    configure_third_party_loggers()
    uvicorn.run(
        "services.content_analysis.main:app",
        host=os.getenv("SERVICE_HOST", "0.0.0.0"),
        port=get_env_int("SERVICE_PORT", 8012),
        log_level=os.getenv("SERVICE_LOG_LEVEL", "INFO").lower(),
    )


if __name__ == "__main__":
    run()


__all__ = ["SERVICE_INFO", "app", "create_content_analysis_app", "run"]
