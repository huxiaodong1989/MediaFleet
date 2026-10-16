"""独立管理凭据保护的服务运行配置 API。"""

import os
from secrets import compare_digest

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select

from media_platform.contracts.runtime_config import (
    CONFIG_SCHEMAS,
    SERVICE_NAMES,
    CATEGORY_NAMES,
    SERVICE_NODE_TYPES,
)
from media_platform.infrastructure.database.models import MediaNodeModel


def verify_config_admin(x_config_admin_key: str | None = Header(default=None)):
    expected = os.getenv("CONFIG_ADMIN_API_KEY", "")
    if not expected:
        raise HTTPException(503, "尚未配置 CONFIG_ADMIN_API_KEY")
    if not x_config_admin_key or not compare_digest(x_config_admin_key, expected):
        raise HTTPException(401, "需要独立的配置管理密钥")


router = APIRouter(
    prefix="/api/v1/admin/runtime-configs",
    tags=["runtime-configs"],
    dependencies=[Depends(verify_config_admin)],
)


def service(request: Request):
    management = getattr(
        request.app.state.control_center_runtime, "runtime_config_service", None
    )
    if management is None:
        raise HTTPException(503, "运行配置管理未初始化")
    return management


class PromptUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_code: str = Field(min_length=1, max_length=64)
    bundle_id: str = Field(min_length=1, max_length=36)
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    models: dict[str, str]


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: dict
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=512)
    api_key: SecretStr | None = None
    prompt_updates: list[PromptUpdate] = Field(default_factory=list, max_length=500)
    source_version_id: str | None = Field(default=None, max_length=36)


def result(operation):
    try:
        return operation()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("")
def catalog():
    return [
        {
            "service": key[0],
            "category": key[1],
            "schema": schema.model_json_schema(),
            "service_name": SERVICE_NAMES.get(key[0], key[0]),
            "category_name": CATEGORY_NAMES.get(key[1], key[1]),
            "scope": "GLOBAL",
            "effect": "new_tasks" if key[1] == "model_connection" else "live_limits",
        }
        for key, schema in CONFIG_SCHEMAS.items()
    ]


@router.get("/{service_name}/{category}")
def read_config(service_name: str, category: str, management=Depends(service)):
    def read():
        data = management.store.list_versions(service_name, category)
        data["prompts"] = (
            management.prompts(service_name) if category == "model_connection" else []
        )
        with management.store.session_factory() as session:
            nodes = session.scalars(
                select(MediaNodeModel).where(
                    MediaNodeModel.node_type == SERVICE_NODE_TYPES[service_name]
                )
            ).all()
            data["instances"] = [
                {
                    "node_code": node.node_code,
                    "status": node.status,
                    "last_heartbeat_at": node.last_heartbeat_at,
                    "runtime_config_supported": bool(
                        (node.capacity_config or {}).get("runtime_config_supported")
                    ),
                    "runtime_config": (node.capacity_config or {}).get(
                        "runtime_config", {}
                    ),
                    "stages": (node.capacity_config or {}).get("stages", {}),
                }
                for node in nodes
            ]
        return data

    return result(read)


@router.post("/{service_name}/{category}/drafts", status_code=201)
def save_draft(
    service_name: str, category: str, body: DraftRequest, management=Depends(service)
):
    return result(
        lambda: management.store.draft(
            service_name,
            category,
            content=body.content,
            expected_revision=body.expected_revision,
            request_id=body.request_id,
            reason=body.reason,
            api_key=body.api_key.get_secret_value()
            if body.api_key is not None
            else None,
            prompt_updates=[change.model_dump() for change in body.prompt_updates],
            source_version_id=body.source_version_id,
        )
    )


@router.post("/{service_name}/{category}/{version_id}/publish")
def publish(
    service_name: str, category: str, version_id: str, management=Depends(service)
):
    return result(lambda: management.publish(service_name, category, version_id))
