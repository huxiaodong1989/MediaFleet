"""媒体调用中心 FastAPI 入口。

模块导入只创建应用和路由，不连接数据库或 RabbitMQ。外部资源统一在 FastAPI
lifespan 中初始化和释放，确保测试导入、Alembic 和脚本工具不会产生启动副作用。
"""

from collections.abc import Callable
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn

from media_platform.application import create_service_app
from media_platform.common import get_env_bool, get_env_int, load_env_file
from media_platform.contracts.service import ServiceInfo, ServiceRole
from media_platform.infrastructure.observability.logger import (
    configure_third_party_loggers,
)
from services.control_center.api import (
    admin_router,
    node_heartbeat_router,
    node_selection_router,
    recording_command_router,
    recording_server_router,
    stream_binding_router,
    task_router,
)
from services.control_center.api.legacy_routes.object_detection import (
    router as legacy_object_detection_router,
)
from services.control_center.api.legacy_routes.recog import router as legacy_recog_router
from services.control_center.api.legacy_routes.stream import (
    router as legacy_stream_router,
)
from services.control_center.api.legacy_routes.video import router as legacy_video_router
from services.control_center.application import (
    ControlCenterRuntime,
    build_control_center_runtime,
)

load_env_file(
    Path(__file__).resolve().parent / ".env",
    enabled=get_env_bool("SERVICE_DOTENV_ENABLED", True),
)

SERVICE_INFO = ServiceInfo(
    name="mediafleet-control-center",
    role=ServiceRole.CONTROL_CENTER,
    version="0.1.0",
    description="媒体任务入口、节点调度与状态中心",
    compatibility_entrypoint=None,
)

ADMIN_UI_DIR = Path(__file__).resolve().parent / "admin_ui"

RuntimeFactory = Callable[[], ControlCenterRuntime]


def create_control_center_app(
    runtime_factory: RuntimeFactory = build_control_center_runtime,
) -> FastAPI:
    """创建调用中心应用并装配运行时生命周期。

    Args:
        runtime_factory: 生产环境使用默认工厂；测试可注入不连接外部系统的运行时。

    Returns:
        注册系统接口、任务 API 和后台发布生命周期的 FastAPI 应用。
    """

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime = runtime_factory()
        application.state.control_center_runtime = runtime
        await runtime.start()
        try:
            yield
        finally:
            await runtime.close()

    application = create_service_app(SERVICE_INFO, lifespan=lifespan)
    application.include_router(admin_router)
    application.include_router(node_heartbeat_router)
    application.include_router(node_selection_router)
    application.include_router(recording_command_router)
    application.include_router(recording_server_router)
    application.include_router(stream_binding_router)
    application.include_router(task_router)
    application.include_router(legacy_stream_router, prefix="/api/v1")
    application.include_router(legacy_video_router, prefix="/api/v1")
    application.include_router(legacy_recog_router, prefix="/api/v1")
    application.include_router(legacy_object_detection_router, prefix="/api/v1")

    @application.middleware("http")
    async def disable_admin_ui_cache(request, call_next):
        """避免管理页 HTML 与 JavaScript 跨版本缓存后发生 DOM 不匹配。"""

        response = await call_next(request)
        if request.url.path == "/admin" or request.url.path.startswith("/admin/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # 管理后台是调用中心随包提供的静态页面；页面本身只调用已认证的 admin API，
    # 不在前端保存 MySQL/RabbitMQ 等基础设施凭据。
    application.mount(
        "/admin",
        StaticFiles(directory=ADMIN_UI_DIR, html=True),
        name="admin-ui",
    )
    return application


app = create_control_center_app()


def _configure_logging() -> None:
    """让本地脚本和生产入口都能看到调用中心业务处理日志。"""

    logging.basicConfig(
        level=os.getenv("SERVICE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    configure_third_party_loggers()


def run() -> None:
    _configure_logging()
    uvicorn.run(
        "services.control_center.main:app",
        host=os.getenv("SERVICE_HOST", "0.0.0.0"),
        port=get_env_int("SERVICE_PORT", 8008),
        log_level=os.getenv("SERVICE_LOG_LEVEL", "INFO").lower(),
    )


if __name__ == "__main__":
    run()


__all__ = ["ADMIN_UI_DIR", "SERVICE_INFO", "app", "create_control_center_app", "run"]
