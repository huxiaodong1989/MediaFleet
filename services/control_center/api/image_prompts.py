"""调用中心直接管理共享数据库中的图像提示词版本。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from media_platform.application.image_prompt_service import ImagePromptManagementService
from media_platform.contracts.image_analysis import ImagePromptBundleContent
from services.control_center.api.dependencies import (
    get_image_prompt_service,
    verify_internal_api_key,
)


router = APIRouter(
    prefix="/api/v1/admin/image-prompts",
    tags=["admin-image-prompts"],
    dependencies=[Depends(verify_internal_api_key)],
)


class SchoolScope(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    school_code: str = Field(min_length=1, max_length=64)


class CreateDraft(SchoolScope):
    content: ImagePromptBundleContent


class ChangeVersion(SchoolScope):
    expected_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class UpdateDraft(ChangeVersion):
    content: ImagePromptBundleContent


def _result(call):
    try:
        result = call()
        if isinstance(result, list):
            return [item.__dict__ for item in result]
        return result.__dict__
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("", summary="按学校查询图像提示词版本")
def list_versions(
    school_code: str = Query(default="GLOBAL", min_length=1, max_length=64),
    service: ImagePromptManagementService = Depends(get_image_prompt_service),
):
    return _result(lambda: service.list_versions(school_code=school_code))


@router.post(
    "/drafts", status_code=status.HTTP_201_CREATED, summary="创建图像提示词草稿"
)
def create_draft(
    body: CreateDraft,
    service: ImagePromptManagementService = Depends(get_image_prompt_service),
):
    return _result(
        lambda: service.create_draft(
            body.content, school_code=body.school_code, operator="ADMIN_API_KEY"
        )
    )


@router.post(
    "/{bundle_id}/clone",
    status_code=status.HTTP_201_CREATED,
    summary="复制本校图像提示词版本为草稿",
)
def clone_draft(
    bundle_id: str,
    body: SchoolScope,
    service: ImagePromptManagementService = Depends(get_image_prompt_service),
):
    return _result(
        lambda: service.clone_draft(
            bundle_id, school_code=body.school_code, operator="ADMIN_API_KEY"
        )
    )


@router.put("/{bundle_id}", summary="修改图像提示词草稿")
def update_draft(
    bundle_id: str,
    body: UpdateDraft,
    service: ImagePromptManagementService = Depends(get_image_prompt_service),
):
    return _result(
        lambda: service.update_draft(
            bundle_id,
            body.content,
            school_code=body.school_code,
            operator="ADMIN_API_KEY",
            expected_hash=body.expected_hash,
        )
    )


@router.post("/{bundle_id}/publish", summary="发布或回滚图像提示词版本")
def publish(
    bundle_id: str,
    body: ChangeVersion,
    service: ImagePromptManagementService = Depends(get_image_prompt_service),
):
    return _result(
        lambda: service.publish(
            bundle_id,
            school_code=body.school_code,
            operator="ADMIN_API_KEY",
            expected_hash=body.expected_hash,
        )
    )


__all__ = ["router"]
