"""录制节点独立入口。

模块导入只创建 FastAPI 路由和生命周期工厂。RabbitMQ 命令消费、ZLMediaKit
客户端和录像处理器均在服务启动或真正执行命令时延迟创建。
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
from services.recorder_node.api import post_processing_router
from services.recorder_node.application import (
    RecorderNodeRuntime,
    build_recorder_node_runtime,
)

load_env_file(
    Path(__file__).resolve().parent / ".env",
    enabled=get_env_bool("SERVICE_DOTENV_ENABLED", True),
)

SERVICE_INFO = ServiceInfo(
    name="mediafleet-recorder-node",
    role=ServiceRole.RECORDER_NODE,
    version="0.1.0",
    description="ZLMediaKit 拉流、录制与本地录像后处理节点",
    compatibility_entrypoint="app.worker.master.recorder",
)

RuntimeFactory = Callable[[], RecorderNodeRuntime]


def create_recorder_node_app(
    runtime_factory: RuntimeFactory = build_recorder_node_runtime,
) -> FastAPI:
    """创建录制节点应用，并把命令消费线程纳入 FastAPI 生命周期。"""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime = runtime_factory()
        application.state.recorder_node_runtime = runtime
        await runtime.start()
        try:
            yield
        finally:
            await runtime.close()

    application = create_service_app(SERVICE_INFO, lifespan=lifespan)
    application.include_router(post_processing_router)
    return application


app = create_recorder_node_app()


def _configure_logging() -> None:
    """让本地脚本和生产入口都能看到录制节点命令消费日志。"""

    logging.basicConfig(
        level=os.getenv("SERVICE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    configure_third_party_loggers()


def run() -> None:
    _configure_logging()
    uvicorn.run(
        "services.recorder_node.main:app",
        host=os.getenv("SERVICE_HOST", "0.0.0.0"),
        port=get_env_int("SERVICE_PORT", 8010),
        log_level=os.getenv("SERVICE_LOG_LEVEL", "INFO").lower(),
    )


if __name__ == "__main__":
    run()


__all__ = ["SERVICE_INFO", "app", "create_recorder_node_app", "run"]
