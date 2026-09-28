from fastapi import APIRouter, Depends, HTTPException
from services.control_center.api.legacy_auth import verify_api_key
from media_platform.domain.task.legacy_models import TaskType, TaskStatus
from services.control_center.api.legacy_schemas.videoDto import VideoProcessRequest
import uuid
import logging
from media_platform.infrastructure.storage import get_storage_service
from services.control_center.api.legacy_schemas.videoDto import VideoProcessResponse,VideoProcessResultResponse
from media_platform.application import TaskDispatchService, TaskQueryService
from services.control_center.api.dependencies import (
    get_task_dispatch_service,
    get_task_query_service,
)
from services.control_center.api.legacy_routes.media_task_adapter import (
    create_legacy_media_task,
    get_legacy_task_or_404,
    legacy_status,
    legacy_task_message,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/video", tags=["视频处理"])


@router.post("/process",response_model=VideoProcessResponse, summary="视频处理接口", description="视频处理接口，支持视频提取音频、提取图片、设置水印等操作")
async def process_video(
    request: VideoProcessRequest,
    task_service: TaskDispatchService = Depends(get_task_dispatch_service),
    api_key: dict = Depends(verify_api_key)
):
    """视频处理接口"""
    # 检查请求参数
    if request.video_url is None:
        raise HTTPException(status_code=400, detail="缺少视频源地址")
    if request.operations is None or request.operations is TaskType.NONE:
        raise HTTPException(status_code=400, detail="缺少处理操作")
    if request.operations is TaskType.VIDEO_SET_WATERMARK and request.params["watermark"] is None:
        raise HTTPException(status_code=400, detail="缺少水印信息")
    # if request.operations is TaskType.VIDEO_EXTRACT_COVER and request.params["cover_strategy"] is None:
    #     raise HTTPException(status_code=400, detail="缺少封面信息")

    try:
        task_id = create_legacy_media_task(
            service=task_service,
            task_type=request.operations,
            params={
                "callback_url": request.callback_url,
                "video_url": request.video_url,
                "ex_params": request.params or {},
            },
            callback_url=request.callback_url,
            priority=0,
        )
        return {
            "task_id": task_id,
            "status": TaskStatus.PENDING,
            "message": "视频处理任务已创建"
        }

    except Exception as e:
        logger.error(f"创建任务失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建任务失败: {str(e)}")

@router.get("/process/{task_id}",response_model=VideoProcessResultResponse, summary="获取视频处理任务状态", description="通过任务ID获取视频处理任务的详细状态信息")
async def get_video_process_status(
    task_id: str,
    query_service: TaskQueryService = Depends(get_task_query_service),
    api_key: dict = Depends(verify_api_key)
):
    """获取视频处理任务状态"""
    try:
        task = get_legacy_task_or_404(service=query_service, task_id=task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        status = legacy_status(task.status)

        return {
            "task_id": task_id,
            "status": status,
            "progress": task.progress,
            "result": task.result,
            "error_message": task.error_message,
            "message": legacy_task_message(status, subject="视频"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取任务状态失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取任务状态失败: {str(e)}")


@router.post("/get_local_video_imgs")
async def get_local_video_imgs(file_url: str,interval: int = 5):
    """获取视频帧"""
    from services.media_worker.processors.video.video_process import VideoProcess

    storageService= get_storage_service()

    file_path=await storageService.download_file(file_url)
    logger.info(f"文件下载成功{file_path}")
    video_process=VideoProcess()
    # video_process.set_callback(task_data.get('params').get('callback_url'))
    # 每{interval}秒提取一帧
    image_paths =await video_processor.get_video_imgs(file_path, interval)
    print(f"提取了{len(image_paths)}个视频帧")
    # 上传图片到COS
    image_urls = []
    for image_path in image_paths:
        image_url= await storageService.upload_file(image_path)
        image_urls.append(image_url)
    logger.info(f"图片上传成功")

    return {"imgs": image_urls}


@router.post("/video_set_watermark")
async def video_set_watermark(file_url:str="",watermark: str = ""):
    """视频添加水印"""
    from services.media_worker.processors.video.video_process import VideoProcess

    # storageService= get_storage_service()

    # file_path=await storageService.download_file(file_url)
    # logger.info(f"文件下载成功{file_path}")
    video_processor = VideoProcess()
    # 每{interval}秒提取一帧
    video_file =await video_processor.video_set_watermark(file_url, watermark)
    print(f"视频添加水印成功：{video_file}")
    # 上传图片到COS
    # image_urls = []
    # for image_path in image_paths:
    #     image_url= await storageService.upload_file(image_path)
    #     image_urls.append(image_url)
    # logger.info(f"图片上传成功")

    return {"video_file": video_file}
