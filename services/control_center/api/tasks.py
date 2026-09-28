"""调用中心媒体任务与兼容 AI 评课 API。"""

from hashlib import sha256
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from media_platform.common.redaction import sanitize_url
from media_platform.application import (
    CreateTaskCommand,
    TaskDispatchService,
    TaskQueryService,
)
from media_platform.contracts.content_evaluation import (
    ClassEvaluationCreateResponse,
    ClassEvaluationRecordResponse,
    ClassEvaluationRequest,
)
from media_platform.contracts.task import TaskDeliveryChannel
from services.control_center.api.dependencies import (
    get_admin_service,
    get_task_dispatch_service,
    get_task_query_service,
    verify_internal_api_key,
)
from services.control_center.application.admin_service import (
    AdminOperationError,
    AdminService,
)
from services.control_center.api.schemas import (
    TaskCreateRequest,
    TaskCreateResponse,
    TaskFileResponse,
    TaskStatusResponse,
)


LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/tasks",
    tags=["media-tasks"],
    dependencies=[Depends(verify_internal_api_key)],
)


def _evaluation_idempotency_key(school_code: str, business_task_id: str) -> str:
    digest = sha256(
        f"{school_code}:content.class_evaluation:{business_task_id}".encode()
    ).hexdigest()
    return f"class-evaluation:{digest}"


@router.post(
    "/class-evaluation",
    response_model=ClassEvaluationCreateResponse,
    summary="创建AI评课任务",
)
def create_class_evaluation(
    request: ClassEvaluationRequest,
    service: TaskDispatchService = Depends(get_task_dispatch_service),
    admin_service: AdminService = Depends(get_admin_service),
) -> ClassEvaluationCreateResponse:
    result = service.create_task(
        CreateTaskCommand(
            school_code=request.school_code,
            task_type="content.class_evaluation",
            delivery_channel=TaskDeliveryChannel.CONTENT_ANALYSIS,
            business_task_id=request.task_id,
            routing_key="content.class_evaluation",
            params=request.model_dump(mode="json", by_alias=True, exclude_none=True),
            idempotency_key=_evaluation_idempotency_key(
                request.school_code,
                request.task_id,
            ),
            max_retries=3,
            callback_url=(str(request.callback_url) if request.callback_url else None),
            created_by="class-evaluation-api",
        )
    )
    if not result.created and request.quest_type == 1:
        try:
            admin_service.retry_task(
                result.task_id,
                updated_by="class-evaluation-api",
            )
        except (AdminOperationError, LookupError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
    return ClassEvaluationCreateResponse(
        taskId=request.task_id,
        internalTaskId=result.task_id,
        status="PENDING" if result.created or request.quest_type == 1 else "EXISTS",
        message="评课任务已创建" if result.created else "评课任务已存在",
        created=result.created,
    )


@router.get(
    "/class-evaluation/result/{business_task_id}",
    response_model=ClassEvaluationRecordResponse,
    summary="按业务任务编号查询AI评课结果",
)
def get_class_evaluation_result(
    business_task_id: str,
    school_code: str | None = None,
    service: TaskQueryService = Depends(get_task_query_service),
) -> ClassEvaluationRecordResponse:
    task = service.get_by_business_task_id(
        business_task_id,
        task_type="content.class_evaluation",
        school_code=school_code,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="未找到AI评课任务")
    params = task.params or {}
    result = task.result or None
    return ClassEvaluationRecordResponse(
        taskId=business_task_id,
        internalTaskId=task.task_id,
        classroomId=str(params.get("classroomId") or ""),
        status=str(task.status).upper(),
        progress=float(task.progress or 0) / 100,
        currentStep=None,
        evaluationResult=result,
        errorMessage=task.error_message,
        promptVersion=(result or {}).get("promptVersion"),
        createdAt=task.created_at,
        updatedAt=task.updated_at,
    )


@router.post(
    "",
    response_model=TaskCreateResponse,
    summary="创建媒体任务",
    description=(
        "任务先写入 MySQL 国标任务表，由后台发布器可靠投递到 RabbitMQ。"
        "接口成功会返回 task_id，后续状态查询以 task_id 为准。"
        "callback_url 是业务交互必填字段，任务完成或最终失败后由调用中心统一回调。"
        "接口不要求调用方传请求号、幂等键或 RabbitMQ 路由键。"
        "视频封面提取使用 task_type=video.cover.extract，"
        "视频音频提取使用 task_type=video.audio.extract，"
        "视频片段提取使用 task_type=video.clip.extract，"
        "视频水印使用 task_type=video.watermark，"
        "图片目标检测使用 task_type=object.detect.image，"
        "视频目标轨迹图使用 task_type=object.track.video，"
        "离线语音识别使用 task_type=speech.offline.recognize，"
        "通用 Worker 消费后才会真正执行 FFmpeg 和上传对象存储。"
    ),
)
def create_media_task(
    request: TaskCreateRequest,
    service: TaskDispatchService = Depends(get_task_dispatch_service),
) -> TaskCreateResponse:
    """创建通用媒体任务。

    通用媒体任务统一进入共享 Worker 队列竞争消费，API 调用方不需要指定
    RabbitMQ 路由键；服务端使用 task_type 作为路由键。
    """

    routing_key = request.task_type
    LOGGER.info(
        "收到媒体任务创建请求: task_type=%s, routing_key=%s, "
        "school_code=%s, callback_url=%s",
        request.task_type,
        routing_key,
        request.school_code,
        sanitize_url(str(request.callback_url)) if request.callback_url else None,
    )
    result = service.create_task(
        CreateTaskCommand(
            school_code=request.school_code,
            task_type=request.task_type,
            routing_key=routing_key,
            priority=request.priority,
            max_retries=request.max_retries,
            params=request.params,
            callback_url=request.callback_url,
            target_node_id=request.target_node_id,
            created_by=request.created_by,
        )
    )
    LOGGER.info(
        "媒体任务创建请求处理完成: task_id=%s, created=%s, task_type=%s",
        result.task_id,
        result.created,
        request.task_type,
    )
    return TaskCreateResponse(task_id=result.task_id, created=result.created)


@router.get(
    "/{task_id}",
    response_model=TaskStatusResponse,
    summary="查询媒体任务状态",
    description=(
        "根据创建任务返回的 task_id 直接查询 MySQL 国标任务表 media_task。"
        "该接口不依赖调用中心内存状态，也不要求调用方传 request_id、idempotency_key "
        "或 RabbitMQ routing_key。"
    ),
)
def get_media_task(
    task_id: str,
    service: TaskQueryService = Depends(get_task_query_service),
) -> TaskStatusResponse:
    """查询媒体任务状态和结果。

    MySQL 是任务状态事实来源；调用中心只返回任务表中的持久化状态，不轮询
    RabbitMQ 或 Worker 内存。
    """

    task = service.get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到媒体任务: {task_id}",
        )
    return TaskStatusResponse(**task.__dict__)


@router.get(
    "/{task_id}/files",
    response_model=list[TaskFileResponse],
    summary="查询媒体任务产物文件",
    description=(
        "根据 task_id 查询 MySQL 国标媒体文件表 media_artifact。"
        "任务不存在返回 404；任务存在但暂未产生文件时返回空数组。"
    ),
)
def list_media_task_files(
    task_id: str,
    service: TaskQueryService = Depends(get_task_query_service),
) -> list[TaskFileResponse]:
    """查询任务产物文件列表。"""

    files = service.list_task_files(task_id)
    if files is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到媒体任务: {task_id}",
        )
    return [TaskFileResponse(**file.__dict__) for file in files]


__all__ = ["router"]
