"""
流程轨迹上报客户端
对接 flow-trace-service 的 POST /v1/steps/status/report 接口，
用于在录制流程关键节点上报步骤执行状态。

当 extra_params 中包含 classroom_id 时自动上报，否则静默跳过。
"""
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger("flow_trace_client")


class FlowTraceStepCode:
    """录制流程步骤编码"""
    TASK_CREATED = "task_created"
    RECORDING_WAITING = "recording_waiting"
    RECORDING_STARTED = "recording_started"
    RECORDING_COMPLETED = "recording_completed"
    POST_VIDEO_UPLOAD = "post_video_upload"
    POST_AUDIO_EXTRACT = "post_audio_extract"
    POST_DB_SAVE = "post_db_save"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"


class FlowTraceStatus:
    """步骤状态"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class FlowTraceClient:
    """流程轨迹上报客户端（单例）"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        try:
            from media_platform.common.config import get_settings
            settings = get_settings()
            self.base_url: str = getattr(settings, "flow_trace_base_url", "")
        except Exception:
            self.base_url = ""

        self.report_url = f"{self.base_url}/v1/steps/status/report" if self.base_url else ""
        self.enabled = bool(self.base_url)
        self.timeout = 10.0

        if not self.enabled:
            logger.info("流程轨迹上报未配置 FLOW_TRACE_BASE_URL，功能已禁用")

    async def report_step(
        self,
        classroom_id: str,
        process_type: str,
        step_code: str,
        status: str,
        *,
        task_id: Optional[str] = None,
        error_message: Optional[str] = None,
        step_input_data: Optional[Dict[str, Any]] = None,
        step_output_data: Optional[Dict[str, Any]] = None,
        extra_data: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        step_start_time: Optional[datetime] = None,
        step_end_time: Optional[datetime] = None,
    ) -> bool:
        """
        上报步骤状态（异步、不抛异常）。
        上报失败仅记录日志，不影响主业务流程。
        """
        if not self.enabled:
            return False

        body: Dict[str, Any] = {
            "classroomId": classroom_id,
            "processType": process_type,
            "stepCode": step_code,
            "status": status,
        }

        if error_message is not None:
            body["errorMessage"] = error_message
        if step_input_data is not None:
            body["stepInputData"] = step_input_data
        if step_output_data is not None:
            body["stepOutputData"] = step_output_data
        if extra_data is not None:
            body["extraData"] = extra_data
        if trace_id is not None:
            body["traceId"] = trace_id
        if step_start_time is not None:
            body["stepStartTime"] = step_start_time.strftime("%Y-%m-%dT%H:%M:%S")
        if step_end_time is not None:
            body["stepEndTime"] = step_end_time.strftime("%Y-%m-%dT%H:%M:%S")

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.report_url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != 200:
                    logger.warning(
                        f"流程轨迹上报返回错误: task={task_id}, step={step_code}, "
                        f"code={data.get('code')}, msg={data.get('message')}"
                    )
                    return False

                logger.info(f"流程轨迹上报成功: task={task_id}, step={step_code}, status={status}")
                return True

        except Exception as e:
            logger.error(f"流程轨迹上报异常: task={task_id}, step={step_code}, error={str(e)}")
            return False


def get_flow_trace_client() -> FlowTraceClient:
    """获取全局单例"""
    return FlowTraceClient()
