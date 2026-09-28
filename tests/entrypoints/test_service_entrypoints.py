import asyncio
import importlib
import sys

import pytest

from media_platform.contracts.service import HealthStatus


SERVICE_CASES = (
    (
        "services.control_center.main",
        "control_center",
        None,
    ),
    (
        "services.recorder_node.main",
        "recorder_node",
        "app.worker.master.recorder",
    ),
    (
        "services.media_worker.main",
        "media_worker",
        None,
    ),
    (
        "services.content_analysis.main",
        "content_analysis",
        None,
    ),
)


@pytest.mark.parametrize(
    ("module_name", "expected_role", "compatibility_entrypoint"),
    SERVICE_CASES,
)
def test_service_entrypoint_imports_without_legacy_side_effects(
    module_name: str,
    expected_role: str,
    compatibility_entrypoint: str,
) -> None:
    module = importlib.import_module(module_name)
    route_paths = {
        route.path
        for route in module.app.routes
        if hasattr(route, "path")
    }
    service_info = module.app.state.service_info

    assert service_info.role == expected_role
    assert service_info.compatibility_entrypoint == compatibility_entrypoint
    assert {"/", "/health", "/api/v1/service-info"}.issubset(route_paths)
    assert "master.main" not in sys.modules
    assert "node.main" not in sys.modules


@pytest.mark.parametrize(
    ("module_name", "expected_role", "_compatibility_entrypoint"),
    SERVICE_CASES,
)
def test_service_health_contract(
    module_name: str,
    expected_role: str,
    _compatibility_entrypoint: str,
) -> None:
    module = importlib.import_module(module_name)
    health_route = next(
        route for route in module.app.routes if route.path == "/health"
    )

    health = asyncio.run(health_route.endpoint())

    assert health.role == expected_role
    assert health.status == HealthStatus.UP.value
    assert health.checked_at.tzinfo is not None
