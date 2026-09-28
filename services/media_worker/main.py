"""通用媒体 Worker FastAPI 与 RabbitMQ 消费入口。

模块导入只创建 FastAPI 路由和生命周期工厂。数据库、RabbitMQ、OpenCV、
FFmpeg 和存储客户端均在服务启动或真正执行任务时延迟创建。
"""

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
from media_platform.infrastructure.observability.logger import (
    configure_third_party_loggers,
)
from services.media_worker.application import (
    MediaWorkerRuntime,
    build_media_worker_runtime,
)

load_env_file(
    Path(__file__).resolve().parent / ".env",
    enabled=get_env_bool("SERVICE_DOTENV_ENABLED", True),
)

SERVICE_INFO = ServiceInfo(
    name="mediafleet-worker",
    role=ServiceRole.MEDIA_WORKER,
    version="0.1.0",
    description="封面、音频、视频和识别等通用媒体任务节点",
    compatibility_entrypoint=None,
)

RuntimeFactory = Callable[[], MediaWorkerRuntime]


def create_media_worker_app(
    runtime_factory: RuntimeFactory = build_media_worker_runtime,
) -> FastAPI:
    """创建 Worker 应用，并把 RabbitMQ 消费线程纳入 FastAPI 生命周期。"""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime = runtime_factory()
        application.state.media_worker_runtime = runtime
        await runtime.start()
        try:
            yield
        finally:
            await runtime.close()

    return create_service_app(SERVICE_INFO, lifespan=lifespan)


app = create_media_worker_app()


def _configure_logging() -> None:
    """让本地脚本和生产入口都能看到 Worker 消费和媒体处理日志。"""

    logging.basicConfig(
        level=os.getenv("SERVICE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    configure_third_party_loggers()


def run() -> None:
    _configure_logging()
    uvicorn.run(
        "services.media_worker.main:app",
        host=os.getenv("SERVICE_HOST", "0.0.0.0"),
        port=get_env_int("SERVICE_PORT", 8009),
        log_level=os.getenv("SERVICE_LOG_LEVEL", "INFO").lower(),
    )


if __name__ == "__main__":
    run()


__all__ = ["SERVICE_INFO", "app", "create_media_worker_app", "run"]
