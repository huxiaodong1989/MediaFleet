from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
import logging
from services.control_center.api.legacy_auth import verify_api_key
import uuid
from media_platform.domain.task.legacy_models import TaskStatus, TaskType
from services.control_center.api.legacy_schemas.recogDto import (
    RecogProcessRequest,
    RecogProcessResponse,
    RecogProcessResultResponse,
    RecogProcessResultRequest,
)
from services.control_center.api.legacy_response import ResponseModel, ResponseCode
from media_platform.application import TaskDispatchService, TaskQueryService
from services.control_center.api.dependencies import (
    get_task_dispatch_service,
    get_task_query_service,
)
from services.control_center.api.legacy_routes.media_task_adapter import (
    create_legacy_media_task,
    get_legacy_task_or_404,
    legacy_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recog", tags=["语音识别"])


@router.post("/process")
async def process_recog(
    request: RecogProcessRequest,
    task_service: TaskDispatchService = Depends(get_task_dispatch_service),
    api_key: dict = Depends(verify_api_key),
):
    """语音识别接口
    """
    # 简单验证必要参数
    if request.media_url is None:
        return ResponseModel(ResponseCode.PARAM_ERROR, "缺少媒体源地址")
        # raise HTTPException(status_code=400, detail="缺少媒体源地址")
    # if "operations" not in request or not request["operations"]:
    #     raise HTTPException(status_code=400, detail="缺少处理操作")
    task_id = f"recog-process-{uuid.uuid4().hex[:8]}"
    try:
        task_id = create_legacy_media_task(
            service=task_service,
            task_type=TaskType.MEDIA_RECOG,
            params={
                "callback_url": request.callback_url,
                "media_url": request.media_url,
                "ex_params": request.params or {},
            },
            callback_url=request.callback_url,
            priority=0,
        )
        return RecogProcessResponse(
            task_id=task_id,
            status=TaskStatus.PENDING,
            message="语音识别任务已创建",
        )

    except Exception as e:
        logger.error(f"创建任务失败: {str(e)}", exc_info=True)
        return RecogProcessResponse(
            task_id=task_id,
            status=TaskStatus.FAILED,
            message=f"创建任务失败: {str(e)}",
        )
        # raise HTTPException(status_code=500, detail=f"创建任务失败: {str(e)}")

@router.get("/result")
async def get_recog_result(
    task_id: str,
    query_service: TaskQueryService = Depends(get_task_query_service),
    api_key: dict = Depends(verify_api_key),
):
    """获取语音识别结果"""
    try:
        task = get_legacy_task_or_404(service=query_service, task_id=task_id)
        if not task:
            return RecogProcessResultResponse(
                task_id=task_id,
                status=TaskStatus.FAILED,
                message="任务不存在",
            )
        status = legacy_status(task.status)
        if status != TaskStatus.COMPLETED:
            return RecogProcessResultResponse(
                task_id=task_id,
                status=status,
                progress=task.progress,
                error_message=task.error_message,
                message="任务未完成",
            )
        return RecogProcessResultResponse(
            task_id=task_id,
            status=status,
            progress=task.progress,
            result=task.result,
            error_message=task.error_message,
            message="语音识别任务已完成",
        )
    except Exception as e:
        logger.error(f"获取语音识别结果失败: {str(e)}", exc_info=True)
        return RecogProcessResultResponse(
            task_id=task_id,
            status=TaskStatus.FAILED,
            message=f"获取语音识别结果失败: {str(e)}",
        )
