"""录制节点后处理队列状态接口。

录制本身由 ZLMediaKit 和本机录制器持续执行；录制完成后的媒体信息读取、
音频提取、封面提取、上传、回调和清理需要受本机资源约束，因此由
``PostProcessingManager`` 的优先级队列和固定 worker 池控制并发。
这些接口只暴露本节点队列状态，供本地联调、运维排障或后续节点心跳聚合使用。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from media_platform.common.config import get_settings
from services.recorder_node.postprocess import get_post_processing_manager


router = APIRouter(prefix="/api/v1/post-processing", tags=["post-processing"])


@router.get("/stats")
async def get_post_processing_stats() -> dict[str, Any]:
    """返回录制节点本机后处理队列统计。

    该数据来自进程内队列，只用于本节点运行状态展示；调度中心做跨节点调度时
    应通过节点心跳上报后的 MySQL 节点状态作为事实来源。
    """

    manager = get_post_processing_manager()
    return {
        "code": 0,
        "message": "成功",
        "data": await manager.get_stats(),
    }


@router.get("/tasks/{task_id}")
async def get_post_processing_task_status(task_id: str) -> dict[str, Any]:
    """返回指定后处理任务在本节点队列中的状态。"""

    manager = get_post_processing_manager()
    task_status = await manager.get_task_status(task_id)
    if task_status is None:
        return {
            "code": 404,
            "message": "任务不存在或已完成",
            "data": None,
        }
    return {
        "code": 0,
        "message": "成功",
        "data": task_status,
    }


@router.post("/tasks/{task_id}/retry")
async def retry_failed_post_processing_task(
    task_id: str,
    request: Request,
) -> dict[str, Any]:
    """无需重启 recorder-node，重新入队一个本节点失败的录制后处理任务。"""

    runtime = getattr(request.app.state, "recorder_node_runtime", None)
    recovery_service = getattr(runtime, "post_processing_recovery_service", None)
    if recovery_service is None:
        raise HTTPException(status_code=503, detail="录制后处理恢复服务未初始化")
    try:
        accepted = await recovery_service.retry_failed_post_processing(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "code": 0,
        "message": "失败录制后处理任务已重新入队",
        "data": {"task_id": task_id, "accepted": accepted},
    }


@router.get("/config")
async def get_post_processing_config() -> dict[str, Any]:
    """返回录制节点后处理队列并发和阶段超时配置。"""

    settings = get_settings()
    post_processing = settings.post_processing
    manager = get_post_processing_manager()
    return {
        "code": 0,
        "message": "成功",
        "data": {
            "max_workers": post_processing.max_workers,
            "media_concurrency": post_processing.media_concurrency,
            "idle_strategy": {
                "enabled": post_processing.idle_strategy_enabled,
                "busy_recording_threshold": (
                    post_processing.busy_recording_threshold
                ),
                "busy_media_concurrency": (
                    post_processing.busy_media_concurrency
                ),
                "busy_media_percent": post_processing.busy_media_percent,
                "max_recordings": manager.max_recordings,
                "poll_interval_seconds": (
                    post_processing.idle_strategy_poll_interval_seconds
                ),
            },
            "db_save_retry": {
                "max_attempts": post_processing.db_save_max_attempts,
                "retry_delay_seconds": (
                    post_processing.db_save_retry_delay_seconds
                ),
            },
            "failed_auto_retry": {
                "enabled": post_processing.failed_auto_retry_enabled,
                "max_attempts": (
                    post_processing.failed_auto_retry_max_attempts
                ),
                "initial_delay_seconds": (
                    post_processing.failed_auto_retry_initial_delay_seconds
                ),
                "max_delay_seconds": (
                    post_processing.failed_auto_retry_max_delay_seconds
                ),
                "scan_interval_seconds": (
                    post_processing.failed_auto_retry_scan_interval_seconds
                ),
            },
            "priority": {
                "high": post_processing.priority_high,
                "normal": post_processing.priority_normal,
                "low": post_processing.priority_low,
            },
            "timeouts": {
                "video_info": post_processing.timeout_video_info,
                "audio_extract": post_processing.timeout_audio_extract,
                "cover_extract": post_processing.timeout_cover_extract,
                "video_upload": post_processing.timeout_video_upload,
                "audio_upload": post_processing.timeout_audio_upload,
                "cover_upload": post_processing.timeout_cover_upload,
            },
        },
    }


__all__ = [
    "get_post_processing_config",
    "get_post_processing_stats",
    "get_post_processing_task_status",
    "retry_failed_post_processing_task",
    "router",
]
