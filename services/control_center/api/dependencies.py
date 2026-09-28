"""调用中心 API 依赖解析与内部认证。"""

from secrets import compare_digest

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from media_platform.application import (
    MediaNodeHeartbeatService,
    MediaNodeSelectionService,
    MediaStreamBindingService,
    RecorderCommandDispatchService,
    TaskDispatchService,
    TaskQueryService,
)
from media_platform.application.content_prompt_service import PromptManagementService
from services.control_center.application.recording_task_state_service import (
    RecordingTaskStateService,
)
from services.control_center.application.recording_server_service import (
    RecordingServerService,
)
from services.control_center.application.admin_service import AdminService
from services.control_center.application.content_evaluation_query_service import (
    ContentEvaluationQueryService,
)


X_API_KEY = APIKeyHeader(name="X-API-Key", auto_error=False)


def _get_runtime(request: Request):
    runtime = getattr(request.app.state, "control_center_runtime", None)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="调用中心运行时尚未初始化",
        )
    return runtime


def verify_internal_api_key(
    request: Request,
    api_key: str | None = Security(X_API_KEY),
) -> None:
    """校验 RTC 等内部调用方通过 `X-API-Key` 传入的服务密钥。"""

    runtime = _get_runtime(request)
    expected = str(runtime.api_key or "")
    if not api_key or not expected or not compare_digest(api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或缺失的内部API密钥",
        )


def get_task_dispatch_service(request: Request) -> TaskDispatchService:
    """从应用生命周期装配的运行时中获取任务应用服务。

    正常启动时 `ControlCenterRuntime` 会在 lifespan 中写入 `app.state`。
    若运行时未初始化，返回 503，避免请求错误地回退到旧 master 服务。
    """

    return _get_runtime(request).task_service


def get_task_query_service(request: Request) -> TaskQueryService:
    """从调用中心运行时获取任务状态查询服务。"""

    service = getattr(_get_runtime(request), "task_query_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="任务查询服务尚未初始化",
        )
    return service


def get_node_heartbeat_service(request: Request) -> MediaNodeHeartbeatService:
    """从调用中心运行时获取节点心跳服务。"""

    service = getattr(_get_runtime(request), "node_heartbeat_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="节点心跳服务尚未初始化",
        )
    return service


def get_node_selection_service(request: Request) -> MediaNodeSelectionService:
    """从调用中心运行时获取节点选择服务。"""

    service = getattr(_get_runtime(request), "node_selection_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="节点选择服务尚未初始化",
        )
    return service


def get_stream_binding_service(request: Request) -> MediaStreamBindingService:
    """从调用中心运行时获取 RTC 流绑定服务。"""

    service = getattr(_get_runtime(request), "stream_binding_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="流绑定服务尚未初始化",
        )
    return service


def get_recorder_command_service(request: Request) -> RecorderCommandDispatchService:
    """从调用中心运行时获取录制节点命令下发服务。"""

    service = getattr(_get_runtime(request), "recorder_command_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="录制命令服务尚未初始化",
        )
    return service


def get_recording_task_state_service(request: Request) -> RecordingTaskStateService:
    """从调用中心运行时获取录制任务状态服务。"""

    service = getattr(_get_runtime(request), "recording_task_state_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="录制任务状态服务尚未初始化",
        )
    return service


def get_recording_server_service(request: Request) -> RecordingServerService:
    """从调用中心运行时获取录制服务器管理服务。"""

    service = getattr(_get_runtime(request), "recording_server_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="录制服务器管理服务尚未初始化",
        )
    return service


def get_admin_service(request: Request) -> AdminService:
    """从调用中心运行时获取管理后台服务。"""

    service = getattr(_get_runtime(request), "admin_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="管理后台服务尚未初始化",
        )
    return service


def get_content_prompt_service(request: Request) -> PromptManagementService:
    service = getattr(_get_runtime(request), "content_prompt_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="提示词数据库管理服务尚未初始化",
        )
    return service


def get_content_evaluation_query_service(
    request: Request,
) -> ContentEvaluationQueryService:
    service = getattr(_get_runtime(request), "content_evaluation_query_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI评课进度查询服务尚未初始化",
        )
    return service


__all__ = [
    "get_node_heartbeat_service",
    "get_node_selection_service",
    "get_recorder_command_service",
    "get_recording_task_state_service",
    "get_recording_server_service",
    "get_admin_service",
    "get_content_evaluation_query_service",
    "get_content_prompt_service",
    "get_stream_binding_service",
    "get_task_dispatch_service",
    "get_task_query_service",
    "verify_internal_api_key",
]
