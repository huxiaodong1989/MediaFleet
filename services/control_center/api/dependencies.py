"""调用中心 API 依赖解析、身份认证、范围授权与写请求审计。"""

from __future__ import annotations

import uuid

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
from media_platform.application.image_prompt_service import ImagePromptManagementService
from services.control_center.application.image_analysis_intake_service import (
    ImageAnalysisIntakeService,
)
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
from services.control_center.application.api_security_service import (
    ADMIN_WRITE,
    AuthenticationFailed,
    AuthenticatedPrincipal,
    NODE_WRITE,
    PermissionDenied,
    READ,
    RECORDING_WRITE,
    TASK_WRITE,
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


def _required_scopes(request: Request) -> frozenset[str]:
    """按稳定 API 资源和 HTTP 语义映射最小权限范围。"""

    path = request.url.path
    if path.startswith("/api/v1/admin/security"):
        return frozenset({ADMIN_WRITE})
    if path.startswith("/api/v1/admin/tasks/"):
        return frozenset({TASK_WRITE})
    if path == "/api/v1/nodes/select":
        return frozenset({READ})
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        # 历史 GET cancel 路由实际会下发停止命令，不能按只读处理。
        if "/record_cancel/" not in request.url.path:
            return frozenset({READ})
    if path.startswith("/api/v1/nodes"):
        return frozenset({NODE_WRITE})
    if path.startswith("/api/v1/recording-servers"):
        return frozenset({ADMIN_WRITE})
    if path.startswith("/api/v1/recordings"):
        return frozenset({RECORDING_WRITE})
    if path.startswith("/api/v1/rtc/stream-bindings") or path.startswith("/api/v1/stream"):
        return frozenset({RECORDING_WRITE})
    if path.startswith("/api/v1/admin"):
        return frozenset({ADMIN_WRITE})
    if path.startswith(("/api/v1/tasks", "/api/v1/video", "/api/v1/recog", "/api/v1/object_detection")):
        return frozenset({TASK_WRITE})
    # 新增受保护写路由若未显式归类，默认要求最高管理范围，避免权限旁路。
    return frozenset({ADMIN_WRITE})


def get_authenticated_principal(request: Request) -> AuthenticatedPrincipal:
    principal = getattr(request.state, "authenticated_principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="请求尚未通过身份认证")
    return principal


def verify_internal_api_key(
    request: Request,
    api_key: str | None = Security(X_API_KEY),
) -> AuthenticatedPrincipal:
    """认证 API Key、校验权限范围，并为写请求预写审计事件。"""

    runtime = _get_runtime(request)
    service = getattr(runtime, "credential_service", None)
    try:
        if service is not None:
            principal = service.authenticate(api_key)
        else:
            # 测试夹具和迁移期运行时仍可使用部署注入的共享引导凭据。
            from secrets import compare_digest

            expected = str(runtime.api_key or "")
            if not api_key or not expected or not compare_digest(api_key, expected):
                raise AuthenticationFailed("API Key 无效")
            principal = AuthenticatedPrincipal(
                caller_id="bootstrap",
                display_name="部署引导凭据",
                scopes=frozenset({"*"}),
                bootstrap=True,
            )
        required_scopes = _required_scopes(request)
        principal.require(required_scopes)
    except AuthenticationFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except PermissionDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    request.state.authenticated_principal = principal
    request.state.required_scopes = required_scopes
    if request.method not in {"GET", "HEAD", "OPTIONS"} or "/record_cancel/" in request.url.path:
        audit_service = getattr(runtime, "audit_service", None)
        if service is not None and audit_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="审计服务不可用，写请求已拒绝",
            )
        if audit_service is not None:
            request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
            route = request.scope.get("route")
            route_name = getattr(route, "name", None) or request.url.path
            try:
                request.state.audit_event_id = audit_service.begin(
                    request_id=request_id[:64],
                    principal=principal,
                    method=request.method,
                    path=request.url.path,
                    action=f"{request.method.lower()}:{route_name}",
                    required_scopes=required_scopes,
                    idempotency_key=request.headers.get("Idempotency-Key"),
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="审计记录不可用，写请求已拒绝",
                ) from exc
    return principal


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


def get_image_prompt_service(request: Request) -> ImagePromptManagementService:
    service = getattr(_get_runtime(request), "image_prompt_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="图像提示词管理未配置模型名单",
        )
    return service


def get_image_intake_service(request: Request) -> ImageAnalysisIntakeService:
    runtime = _get_runtime(request)
    if not getattr(runtime, "image_intake_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="图像分析接单尚未启用",
        )
    service = getattr(runtime, "image_intake_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="图像分析提示词或模型名单尚未配置",
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
    "get_image_intake_service",
    "get_stream_binding_service",
    "get_task_dispatch_service",
    "get_task_query_service",
    "get_authenticated_principal",
    "verify_internal_api_key",
]
