"""轻量服务入口工厂。

M1 阶段只提供无外部依赖副作用的服务元信息和健康检查。业务路由、
RabbitMQ 消费以及数据库生命周期将在后续迁移批次中逐步装配。
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI

from media_platform.contracts.service import (
    HealthStatus,
    ServiceHealth,
    ServiceInfo,
)


LifespanFactory = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_service_app(
    service_info: ServiceInfo,
    *,
    lifespan: LifespanFactory | None = None,
) -> FastAPI:
    """创建共享的 FastAPI 服务外壳。

    Args:
        service_info: 服务名称、角色、版本和兼容入口等静态信息。
        lifespan: 可选的异步生命周期工厂。具体服务可在其中装配数据库、
            RabbitMQ、后台任务等资源；不传时保持无外部副作用的轻量入口。

    Returns:
        已注册统一根路由、健康检查和服务信息接口的 FastAPI 实例。
    """

    app = FastAPI(
        title=service_info.name,
        version=service_info.version,
        description=service_info.description,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )
    app.state.service_info = service_info

    @app.get("/", response_model=ServiceInfo, tags=["system"])
    async def service_root() -> ServiceInfo:
        return service_info

    @app.get("/health", response_model=ServiceHealth, tags=["system"])
    async def health_check() -> ServiceHealth:
        return ServiceHealth(
            service=service_info.name,
            role=service_info.role,
            version=service_info.version,
            status=HealthStatus.UP,
        )

    @app.get(
        "/api/v1/service-info",
        response_model=ServiceInfo,
        tags=["system"],
    )
    async def get_service_info() -> ServiceInfo:
        return service_info

    return app
