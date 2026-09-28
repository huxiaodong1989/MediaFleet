"""调用中心简易管理后台 API。

第一阶段提供任务、节点、绑定和队列查询；第二阶段提供失败任务重试和业务回调补发。
所有操作均读取或写入既有事实表。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from media_platform.application.content_prompt_service import PromptManagementService
from media_platform.contracts.content_evaluation import PromptBundleContent

from services.control_center.api.admin_schemas import (
    AdminBindingPageResponse,
    AdminBindingResponse,
    AdminCallbackRetryResponse,
    AdminNodePageResponse,
    AdminNodeResponse,
    AdminOverviewResponse,
    AdminQueueResponse,
    AdminTaskPageResponse,
    AdminTaskRetryResponse,
    AdminTaskSummaryResponse,
)
from services.control_center.api.dependencies import (
    get_admin_service,
    get_content_prompt_service,
    verify_internal_api_key,
)
from services.control_center.application.admin_service import (
    AdminOperationError,
    AdminService,
)


router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-console"],
    dependencies=[Depends(verify_internal_api_key)],
)


def _task(item) -> AdminTaskSummaryResponse:
    return AdminTaskSummaryResponse(**item.__dict__)


@router.get("/overview", response_model=AdminOverviewResponse, summary="查询媒体服务总览")
def get_overview(service: AdminService = Depends(get_admin_service)) -> AdminOverviewResponse:
    result = service.overview()
    return AdminOverviewResponse(
        tasks_by_status=result["tasks_by_status"],
        tasks_by_type=result["tasks_by_type"],
        nodes_by_status=result["nodes_by_status"],
        queues=[AdminQueueResponse(**item.__dict__) for item in result["queues"]],
    )


@router.get("/tasks", response_model=AdminTaskPageResponse, summary="按条件查询媒体任务")
def list_admin_tasks(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    task_type: str | None = Query(default=None, max_length=64),
    task_status: str | None = Query(default=None, alias="status", max_length=32),
    node_id: str | None = Query(default=None, max_length=64),
    school_code: str | None = Query(default=None, max_length=64),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    service: AdminService = Depends(get_admin_service),
) -> AdminTaskPageResponse:
    result = service.list_tasks(
        page=page,
        page_size=page_size,
        task_type=task_type,
        status=task_status,
        node_id=node_id,
        school_code=school_code,
        created_from=created_from,
        created_to=created_to,
    )
    return AdminTaskPageResponse(
        items=[_task(item) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get("/nodes", response_model=list[AdminNodeResponse], summary="查询节点运行与均衡状态")
def list_admin_nodes(
    service: AdminService = Depends(get_admin_service),
) -> list[AdminNodeResponse]:
    return [AdminNodeResponse(**item.__dict__) for item in service.list_nodes()]


@router.get(
    "/nodes/page",
    response_model=AdminNodePageResponse,
    summary="分页查询节点运行与均衡状态",
)
def list_admin_nodes_page(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    service: AdminService = Depends(get_admin_service),
) -> AdminNodePageResponse:
    result = service.list_nodes_page(page=page, page_size=page_size)
    return AdminNodePageResponse(
        items=[AdminNodeResponse(**item.__dict__) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get("/bindings", response_model=list[AdminBindingResponse], summary="查询流绑定关系")
def list_admin_bindings(
    node_id: str | None = Query(default=None, max_length=64),
    binding_status: str | None = Query(default=None, alias="status", max_length=32),
    resource_type: str | None = Query(default=None, max_length=32),
    space_id: str | None = Query(default=None, max_length=64),
    service: AdminService = Depends(get_admin_service),
) -> list[AdminBindingResponse]:
    return [
        AdminBindingResponse(**item.__dict__)
        for item in service.list_bindings(
            node_id=node_id,
            status=binding_status,
            resource_type=resource_type,
            space_id=space_id,
        )
    ]


@router.get(
    "/bindings/page",
    response_model=AdminBindingPageResponse,
    summary="分页查询流绑定关系",
)
def list_admin_bindings_page(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    node_id: str | None = Query(default=None, max_length=64),
    binding_status: str | None = Query(default=None, alias="status", max_length=32),
    resource_type: str | None = Query(default=None, max_length=32),
    space_id: str | None = Query(default=None, max_length=64),
    service: AdminService = Depends(get_admin_service),
) -> AdminBindingPageResponse:
    result = service.list_bindings_page(
        page=page,
        page_size=page_size,
        node_id=node_id,
        status=binding_status,
        resource_type=resource_type,
        space_id=space_id,
    )
    return AdminBindingPageResponse(
        items=[AdminBindingResponse(**item.__dict__) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.post(
    "/tasks/{task_id}/retry",
    response_model=AdminTaskRetryResponse,
    summary="重新投递失败媒体任务",
    description="只允许 failed/cancelled 的通用媒体任务重置为 pending；录制任务必须通过原录制接口重新发起。",
)
def retry_admin_task(
    task_id: str,
    updated_by: str = Query(default="admin-console", min_length=1, max_length=64),
    service: AdminService = Depends(get_admin_service),
) -> AdminTaskRetryResponse:
    try:
        task = service.retry_task(task_id, updated_by=updated_by)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AdminOperationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AdminTaskRetryResponse(task=_task(task))


@router.post(
    "/tasks/{task_id}/callback/retry",
    response_model=AdminCallbackRetryResponse,
    summary="重新发送业务回调",
    description="只重发已完成或最终失败任务的业务回调，不重新执行媒体处理。",
)
def retry_admin_callback(
    task_id: str,
    service: AdminService = Depends(get_admin_service),
) -> AdminCallbackRetryResponse:
    try:
        result = service.retry_callback(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AdminOperationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AdminCallbackRetryResponse(callback_result=result)


class ContentPromptDraftRequest(BaseModel):
    school_code: str = Field(default="GLOBAL", min_length=1, max_length=64)
    operator: str = Field(default="admin-console", min_length=1, max_length=64)
    content: PromptBundleContent


class ContentPromptCloneRequest(BaseModel):
    school_code: str = Field(default="GLOBAL", min_length=1, max_length=64)
    operator: str = Field(default="admin-console", min_length=1, max_length=64)


class ContentPromptUpdateRequest(BaseModel):
    operator: str = Field(default="admin-console", min_length=1, max_length=64)
    content: PromptBundleContent


class ContentPromptPublishRequest(BaseModel):
    operator: str = Field(default="admin-console", min_length=1, max_length=64)


def _prompt_result(call):
    try:
        result = call()
        if isinstance(result, list):
            return [item.__dict__ for item in result]
        return result.__dict__
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/content-prompts", summary="查询AI评课提示词版本")
def list_content_prompts(
    school_code: str = Query(default="GLOBAL", min_length=1, max_length=64),
    service: PromptManagementService = Depends(get_content_prompt_service),
):
    return _prompt_result(lambda: service.list_versions(school_code))


@router.post(
    "/content-prompts/drafts",
    summary="新建AI评课提示词草稿",
    status_code=status.HTTP_201_CREATED,
)
def create_content_prompt_draft(
    body: ContentPromptDraftRequest,
    service: PromptManagementService = Depends(get_content_prompt_service),
):
    return _prompt_result(
        lambda: service.create_draft(
            body.content,
            school_code=body.school_code,
            operator=body.operator,
        )
    )


@router.post(
    "/content-prompts/{bundle_id}/clone",
    summary="复制提示词版本为草稿",
    status_code=status.HTTP_201_CREATED,
)
def clone_content_prompt(
    bundle_id: str,
    body: ContentPromptCloneRequest,
    service: PromptManagementService = Depends(get_content_prompt_service),
):
    return _prompt_result(
        lambda: service.clone_draft(
            bundle_id,
            school_code=body.school_code,
            operator=body.operator,
        )
    )


@router.put("/content-prompts/{bundle_id}", summary="修改AI评课提示词草稿")
def update_content_prompt(
    bundle_id: str,
    body: ContentPromptUpdateRequest,
    service: PromptManagementService = Depends(get_content_prompt_service),
):
    return _prompt_result(
        lambda: service.update_draft(
            bundle_id,
            body.content,
            operator=body.operator,
        )
    )


@router.post("/content-prompts/{bundle_id}/publish", summary="发布AI评课提示词版本")
def publish_content_prompt(
    bundle_id: str,
    body: ContentPromptPublishRequest,
    service: PromptManagementService = Depends(get_content_prompt_service),
):
    return _prompt_result(
        lambda: service.publish(bundle_id, operator=body.operator)
    )


__all__ = ["router"]
