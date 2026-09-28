from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
import logging
from services.control_center.api.legacy_auth import verify_api_key
import uuid
from media_platform.domain.task.legacy_models import TaskStatus, TaskType
from services.control_center.api.legacy_schemas.object_detection_dto import (
    ObjectDetectionProcessRequest,
    ObjectDetectionProcessResponse,
    ObjectDetectionProcessResultResponse,
    ObjectDetectionProcessResultRequest,
    ObjectDetectionDetectionRequest,
    ObjectDetectionDetectionResponse,
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

router = APIRouter(prefix="/object_detection", tags=["目标检测"])


@router.post("/process")
async def process_object_tracking_video_to_img(
    request: ObjectDetectionProcessRequest,
    task_service: TaskDispatchService = Depends(get_task_dispatch_service),
    api_key: dict = Depends(verify_api_key),
):
    """目标检测接口
    """
    # 简单验证必要参数
    if request.media_url is None:
        return ObjectDetectionProcessResponse(
            task_id=None,
            status=TaskStatus.FAILED,
            message="缺少媒体源地址",
        )
    task_id = f"object-detection-{uuid.uuid4().hex[:8]}"
    logger.info(f"生成任务ID: {task_id}")
    try:
        task_id = create_legacy_media_task(
            service=task_service,
            task_type=TaskType.OBJ_TRACK_V_TO_IMG,
            params={
                "media_url": request.media_url,
                "callback_url": request.callback_url,
                "ex_params": request.params or {},
            },
            callback_url=request.callback_url,
            priority=0,
        )
        return ObjectDetectionProcessResponse(
            task_id=task_id,
            status=TaskStatus.PENDING,
            message="目标检测任务已创建",
        )

    except Exception as e:
        logger.error(f"创建任务失败: {str(e)}", exc_info=True)
        return ObjectDetectionProcessResponse(
            task_id=task_id,
            status=TaskStatus.FAILED,
            message=f"创建任务失败: {str(e)}",
        )

@router.post("/detection")
async def process_object_detection(
    request: ObjectDetectionDetectionRequest,
    task_service: TaskDispatchService = Depends(get_task_dispatch_service),
    api_key: dict = Depends(verify_api_key),
):
    """目标检测接口"""
    # 生成任务ID
    task_id = f"object-detection-{uuid.uuid4().hex[:8]}"
    logger.info(f"生成任务ID: {task_id}")
    try:
        if request.img_url is None:
            raise HTTPException(status_code=400, detail="缺少图片地址")
        task_id = create_legacy_media_task(
            service=task_service,
            task_type=TaskType.OBJ_DETECT_IMG,
            params={
                "img_url": request.img_url,
                "callback_url": request.callback_url,
                "ex_params": request.params or {},
            },
            callback_url=request.callback_url,
            priority=0,
        )
        return ObjectDetectionDetectionResponse(
            task_id=task_id,
            status=TaskStatus.PENDING,
            message="目标检测任务已创建",
        )
    except Exception as e:
        logger.error(f"创建任务失败: {str(e)}", exc_info=True)
        return ObjectDetectionDetectionResponse(
            task_id=task_id,
            status=TaskStatus.FAILED,
            message=f"创建任务失败: {str(e)}",
        )

@router.get("/result")
async def get_object_detection_result(
    task_id: str,
    query_service: TaskQueryService = Depends(get_task_query_service),
    api_key: dict = Depends(verify_api_key),
):
    """获取目标检测结果"""
    try:
        task = get_legacy_task_or_404(service=query_service, task_id=task_id)
        if not task:
            return ObjectDetectionProcessResultResponse(
                task_id=task_id,
                status=TaskStatus.FAILED,
                message="任务不存在",
            )
        status = legacy_status(task.status)
        if status != TaskStatus.COMPLETED:
            return ObjectDetectionProcessResultResponse(
                task_id=task_id,
                status=status,
                progress=task.progress,
                error_message=task.error_message,
                message="任务未完成",
            )
        return ObjectDetectionProcessResultResponse(
            task_id=task_id,
            status=status,
            progress=task.progress,
            result=task.result,
            error_message=task.error_message,
            message="目标检测任务已完成",
        )
    except Exception as e:
        logger.error(f"获取目标检测结果失败: {str(e)}", exc_info=True)
        return ObjectDetectionProcessResultResponse(
            task_id=task_id,
            status=TaskStatus.FAILED,
            message=f"获取目标检测结果失败: {str(e)}",
        )
