"""调用方凭据、轮换、吊销与审计查询 API。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from services.control_center.api.dependencies import (
    get_authenticated_principal,
    verify_internal_api_key,
)
from services.control_center.application.api_security_service import (
    ALL_SCOPES,
    AuthenticatedPrincipal,
    CredentialOperationError,
)


router = APIRouter(
    prefix="/api/v1/admin/security",
    tags=["api-security"],
    dependencies=[Depends(verify_internal_api_key)],
)


class CredentialCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    caller_id: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    credential_name: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(min_length=1, max_length=len(ALL_SCOPES))
    expires_at: datetime | None = None


class CredentialResponse(BaseModel):
    credential_id: str
    caller_id: str
    display_name: str
    credential_name: str
    scopes: list[str]
    status: str
    expires_at: datetime | None
    last_used_at: datetime | None = None
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime | None = None
    api_key: str | None = Field(
        default=None,
        description="只在创建或轮换响应中返回一次，服务端不保存明文",
    )


def _runtime(request: Request):
    runtime = getattr(request.app.state, "control_center_runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="调用中心运行时尚未初始化")
    return runtime


def _credential_service(request: Request):
    service = getattr(_runtime(request), "credential_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="范围化凭据服务尚未启用")
    return service


def _issued(value) -> CredentialResponse:
    return CredentialResponse(
        credential_id=value.credential_id,
        caller_id=value.caller_id,
        display_name=value.display_name,
        credential_name=value.name,
        scopes=list(value.scopes),
        status=value.status,
        expires_at=value.expires_at,
        api_key=value.api_key,
    )


def _view(value) -> CredentialResponse:
    return CredentialResponse(
        credential_id=value.credential_id,
        caller_id=value.caller_id,
        display_name=value.display_name,
        credential_name=value.name,
        scopes=list(value.scopes),
        status=value.status,
        expires_at=value.expires_at,
        last_used_at=value.last_used_at,
        rotated_at=value.rotated_at,
        revoked_at=value.revoked_at,
        created_at=value.created_at,
    )


@router.get("/scopes", summary="查询可分配权限范围")
def list_scopes() -> dict[str, list[str]]:
    return {"scopes": sorted(ALL_SCOPES)}


@router.get("/credentials", response_model=list[CredentialResponse], summary="查询调用方凭据")
def list_credentials(request: Request) -> list[CredentialResponse]:
    return [_view(item) for item in _credential_service(request).list()]


@router.post(
    "/credentials",
    response_model=CredentialResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建范围化调用方凭据",
)
def create_credential(
    body: CredentialCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
    ),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> CredentialResponse:
    try:
        return _issued(
            _credential_service(request).create(
                caller_id=body.caller_id,
                display_name=body.display_name,
                credential_name=body.credential_name,
                scopes=body.scopes,
                expires_at=body.expires_at,
                idempotency_key=idempotency_key,
                operator=principal,
            )
        )
    except CredentialOperationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/credentials/{credential_id}/rotate",
    response_model=CredentialResponse,
    summary="轮换调用方凭据",
)
def rotate_credential(
    credential_id: str,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> CredentialResponse:
    try:
        return _issued(
            _credential_service(request).rotate(credential_id, operator=principal)
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CredentialOperationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/credentials/{credential_id}/revoke",
    response_model=CredentialResponse,
    summary="吊销调用方凭据",
)
def revoke_credential(
    credential_id: str,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> CredentialResponse:
    try:
        return _view(
            _credential_service(request).revoke(credential_id, operator=principal)
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/audit-events", summary="查询最近写请求审计事件")
def list_audit_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
):
    service = getattr(_runtime(request), "audit_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="API审计服务尚未启用")
    return [item.__dict__ for item in service.list(limit=limit)]


__all__ = ["router"]
