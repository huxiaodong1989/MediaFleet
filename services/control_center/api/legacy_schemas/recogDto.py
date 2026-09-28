from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator, field_serializer
from datetime import datetime
from media_platform.domain.task.legacy_models import TaskStatus, TaskType


class RecogProcessRequest(BaseModel):
    """语音识别请求"""

    media_url: str = Field(..., description="媒体链接")
    callback_url: Optional[str] = Field(None, description="任务完成回调地址")
    params: Optional[Dict[str, Any]] = Field(
        ...,
        description="其他参数:{lang: 语言, provider/asr_provider: 识别后端 'funasr' 或 'glm'}",
    )


class RecogProcessResponse(BaseModel):
    """语音识别响应"""

    task_id: str = Field(..., description="任务ID")
    status: str = Field(
        ..., description="任务状态: pending, processing, completed, failed"
    )
    message: Optional[str] = Field(None, description="状态说明")
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="创建时间，格式：YYYY-MM-DD HH:MM:SS.fff",
    )


class RecogProcessResultResponse(BaseModel):
    """语音识别结果响应"""

    task_id: str = Field(..., description="任务ID")
    status: Optional[TaskStatus] = None
    progress: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    message: Optional[str] = None


class RecogProcessResultRequest(BaseModel):
    """语音识别结果请求"""

    task_id: str = Field(..., description="任务ID")
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    message: Optional[str] = None
