from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator, field_serializer
from datetime import datetime
from media_platform.domain.task.legacy_models import TaskStatus, TaskType


class ObjectDetectionProcessRequest(BaseModel):
    """目标检测请求"""

    media_url: str = Field(..., description="媒体链接")
    callback_url: Optional[str] = Field(None, description="任务完成回调地址")
    params: Optional[Dict[str, Any]] = Field(..., description="其他参数:{target_classes: 要跟踪的目标类别列表，如果为None则跟踪所有类别}")

class ObjectDetectionProcessResponse(BaseModel):
    """目标检测响应"""

    task_id: str = Field(..., description="任务ID")
    status: str = Field(
        ..., description="任务状态: pending, processing, completed, failed"
    )
    message: Optional[str] = Field(None, description="状态说明")
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="创建时间，格式：YYYY-MM-DD HH:MM:SS.fff",
    )
class ObjectDetectionDetectionRequest(BaseModel):
    """目标检测请求"""

    img_url: str = Field(..., description="图片地址")
    callback_url: Optional[str] = Field(None, description="任务完成回调地址")
    params: Optional[Dict[str, Any]] = Field(..., description="其他参数:{target_classes: 要检测的目标类别列表，不传则检测所有类别}")

class ObjectDetectionDetectionResponse(BaseModel):
    """目标检测响应"""

    task_id: str = Field(..., description="任务ID")
    status: str = Field(
        ..., description="任务状态: pending, processing, completed, failed"
    )
    message: Optional[str] = Field(None, description="状态说明")
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="创建时间，格式：YYYY-MM-DD HH:MM:SS.fff",
    )

class ObjectDetectionProcessResultResponse(BaseModel):
    """目标检测结果响应"""

    task_id: str = Field(..., description="任务ID")
    status: Optional[TaskStatus] = None
    progress: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    message: Optional[str] = None


class ObjectDetectionProcessResultRequest(BaseModel):
    """目标检测结果请求"""

    task_id: str = Field(..., description="任务ID")
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    message: Optional[str] = None
