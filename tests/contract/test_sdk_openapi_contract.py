from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest


SDK_SRC = Path(__file__).resolve().parents[2] / "packages" / "mediafleet-sdk" / "src"
sys.path.insert(0, str(SDK_SRC))

from mediafleet_sdk import AsyncMediaFleetClient  # noqa: E402
from mediafleet_sdk.models import (  # noqa: E402
    AdminOverview,
    BindingCreate,
    BindingEndpoint,
    BindingSummary,
    NodeSummary,
    RecordingCommandResult,
    RecordingServer,
    RecordingStart,
    ServiceHealth,
    ServiceInfo,
    TaskCreate,
    TaskCreateResult,
    TaskDetail,
    TaskFile,
    TaskSummary,
)
from services.control_center.main import create_control_center_app  # noqa: E402


def test_sdk_read_endpoints_and_known_fields_exist_in_openapi() -> None:
    schema = create_control_center_app().openapi()
    paths = schema["paths"]
    for path in {
        "/health",
        "/api/v1/service-info",
        "/api/v1/admin/overview",
        "/api/v1/admin/tasks",
        "/api/v1/tasks/{task_id}",
        "/api/v1/tasks/{task_id}/files",
        "/api/v1/admin/nodes/page",
        "/api/v1/admin/bindings/page",
        "/api/v1/recording-servers",
    }:
        assert "get" in paths[path]

    components = schema["components"]["schemas"]
    model_pairs = {
        ServiceHealth: "ServiceHealth",
        ServiceInfo: "ServiceInfo",
        AdminOverview: "AdminOverviewResponse",
        TaskSummary: "AdminTaskSummaryResponse",
        TaskDetail: "TaskStatusResponse",
        TaskFile: "TaskFileResponse",
        NodeSummary: "AdminNodeResponse",
        BindingSummary: "AdminBindingResponse",
        RecordingServer: "RecordingServerResponse",
    }
    for sdk_model, server_schema_name in model_pairs.items():
        server_fields = set(components[server_schema_name]["properties"])
        assert set(sdk_model.model_fields) <= server_fields


def test_sdk_write_endpoints_and_models_exist_in_openapi() -> None:
    schema = create_control_center_app().openapi()
    paths = schema["paths"]
    expected_methods = {
        "/api/v1/tasks": "post",
        "/api/v1/admin/tasks/{task_id}/retry": "post",
        "/api/v1/admin/tasks/{task_id}/callback/retry": "post",
        "/api/v1/rtc/stream-bindings": "post",
        "/api/v1/rtc/stream-bindings/{binding_id}": "delete",
        "/api/v1/recordings/start": "post",
        "/api/v1/recordings/stop": "post",
        "/api/v1/recording-servers/{server_code}/status": "patch",
    }
    for path, method in expected_methods.items():
        assert method in paths[path]

    components = schema["components"]["schemas"]
    model_pairs = {
        TaskCreate: "TaskCreateRequest",
        TaskCreateResult: "TaskCreateResponse",
        BindingCreate: "StreamBindingCreateRequest",
        BindingEndpoint: "StreamBindingResponse",
        RecordingStart: "RecordingCommandStartRequest",
        RecordingCommandResult: "RecordingCommandResponse",
    }
    for sdk_model, server_schema_name in model_pairs.items():
        server_fields = set(components[server_schema_name]["properties"])
        assert set(sdk_model.model_fields) <= server_fields

    task_parameters = paths["/api/v1/tasks"]["post"]["parameters"]
    idempotency = next(
        parameter
        for parameter in task_parameters
        if parameter["name"] == "Idempotency-Key"
    )
    assert idempotency["in"] == "header"
    string_schema = next(
        item
        for item in idempotency["schema"]["anyOf"]
        if item.get("type") == "string"
    )
    assert string_schema["minLength"] == 8
    assert string_schema["maxLength"] == 128


@pytest.mark.asyncio
async def test_async_sdk_calls_real_fastapi_system_endpoints() -> None:
    app = create_control_center_app()
    transport = httpx.ASGITransport(app=app)
    async with AsyncMediaFleetClient(
        base_url="http://mediafleet.test",
        api_key="contract-test-placeholder-key",
        transport=transport,
    ) as client:
        health = await client.system.health()
        info = await client.system.service_info()

    assert health.service == "mediafleet-control-center"
    assert health.checked_at.tzinfo is not None
    assert info.name == health.service
    assert info.role == health.role
