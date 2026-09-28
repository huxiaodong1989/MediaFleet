"""原媒体处理 API 到新通用任务链路的适配工具。

本模块只服务 `/api/v1/video/*`、`/api/v1/recog/*`、`/api/v1/object_detection/*`
等原 HTTP 契约。外部路由和入参保持不变，内部统一写入 `media_task`，
由调用中心后台发布到 RabbitMQ，再由 `media-worker` 共享队列竞争消费。
"""

from __future__ import annotations

from typing import Any

from media_platform.application import (
    CreateTaskCommand,
    TaskDispatchService,
    TaskQueryService,
)
from media_platform.domain.task.legacy_models import TaskStatus


DEFAULT_ORIGINAL_API_SCHOOL_CODE = "LEGACY"


def normalize_legacy_task_type(task_type: Any) -> str:
    """把原 `TaskType` 枚举或字符串转成 Worker 可识别的任务类型。"""

    value = getattr(task_type, "value", task_type)
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("任务类型不能为空")
    return normalized


def extract_legacy_school_code(params: dict[str, Any] | None) -> str:
    """从原扩展参数读取学校码；原调用未传时使用迁移占位值。"""

    params = params or {}
    ex_params = params.get("ex_params")
    if not isinstance(ex_params, dict):
        ex_params = params.get("extra_params")
    if not isinstance(ex_params, dict):
        ex_params = params.get("params")
    if not isinstance(ex_params, dict):
        ex_params = {}
    return str(
        params.get("school_code")
        or params.get("xxm")
        or ex_params.get("school_code")
        or ex_params.get("xxm")
        or DEFAULT_ORIGINAL_API_SCHOOL_CODE
    ).strip()


def create_legacy_media_task(
    *,
    service: TaskDispatchService,
    task_type: Any,
    params: dict[str, Any],
    callback_url: str | None,
    priority: int = 0,
) -> str:
    """按原接口语义创建通用媒体任务。

    原接口不要求调用方传 `request_id/idempotency_key/routing_key`，这里也不新增
    这些公开入参。服务端用任务类型作为路由键，后续由 `media-worker` 处理。
    """

    task_type_text = normalize_legacy_task_type(task_type)
    result = service.create_task(
        CreateTaskCommand(
            school_code=extract_legacy_school_code(params),
            task_type=task_type_text,
            routing_key=task_type_text,
            priority=priority,
            params=params,
            callback_url=callback_url,
            created_by="original-api",
        )
    )
    return result.task_id


def legacy_status(value: str | None) -> TaskStatus:
    """把国标任务表的大写状态转回原接口的小写枚举。"""

    normalized = str(value or TaskStatus.PENDING.value).strip().lower()
    try:
        return TaskStatus(normalized)
    except ValueError:
        return TaskStatus.FAILED


def legacy_task_message(status: TaskStatus, *, subject: str) -> str:
    """生成原查询接口使用的状态说明。"""

    if status == TaskStatus.PROCESSING:
        return f"{subject}处理中"
    if status == TaskStatus.COMPLETED:
        return f"{subject}处理完成"
    if status == TaskStatus.FAILED:
        return f"{subject}处理失败"
    return "等待处理"


def get_legacy_task_or_404(
    *,
    service: TaskQueryService,
    task_id: str,
):
    """读取新任务事实；原路由负责转换 404 响应形状。"""

    return service.get_task(task_id)
