from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from datetime import datetime
from media_platform.common.time import app_now


class TaskType(str, Enum):
    """任务类型枚举"""

    NONE = "none"
    STREAM_RECORD = "stream_record"
    AUDIO_PROCESS = "audio_process"
    VIDEO_PROCESS = "video_process"
    STREAM_PROCESS = "stream_process"
    VIDEO_EXTRACT_AUDIO = "video_extract_audio"
    VIDEO_EXTRACT_IMGS = "video_extract_imgs"
    VIDEO_SET_WATERMARK = "video_set_watermark"
    VIDEO_EXTRACT_COVER = "video_extract_cover"
    STREAM_EXTRACT_AUDIO = "stream_extract_audio"
    # 视频精彩片段提取
    VIDEO_EXTRACT_CLIPS = "video_extract_clips"

    # 直播flv提取封面
    LIVE_EXTRACT_COVER = "live_extract_cover"

    # 语音识别相关任务类型
    MEDIA_RECOG = "media_recog"

    # 目标检测相关任务类型
    OBJ_TRACK_V_TO_IMG = "obj_track_v_to_img"
    OBJ_DETECT_IMG = "obj_detect_img"


class TaskStatus(str, Enum):
    """任务状态枚举"""

    PENDING = "pending"  # 等待执行
    PROCESSING = "processing"  # 处理中
    POST_PROCESSING = "post_processing"  # 后处理中（音频提取、封面提取、文件上传等）
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消


class Task(BaseModel):
    """任务基本模型"""

    task_id: str = Field(..., description="任务ID")
    task_type: TaskType = Field(..., description="任务类型")
    status: TaskStatus = Field(TaskStatus.PENDING, description="任务状态")
    priority: int = Field(0, description="任务优先级，数字越小优先级越高")
    progress: float = Field(0.0, description="任务进度，0-100")
    params: Dict[str, Any] = Field({}, description="任务参数")
    result: Optional[Dict[str, Any]] = Field(None, description="任务结果")
    error_message: Optional[str] = Field(None, description="错误信息")
    callback_url: Optional[str] = Field(None, description="回调地址")
    callback_result: Optional[Dict[str, Any]] = Field(None, description="回调结果")
    worker_node: Optional[str] = Field(None, description="执行节点")
    created_at: datetime = Field(default_factory=app_now, description="创建时间")
    updated_at: datetime = Field(default_factory=app_now, description="更新时间")
    started_at: Optional[datetime] = Field(None, description="开始执行时间")
    completed_at: Optional[datetime] = Field(None, description="完成时间")
    retry_count: int = Field(0, description="重试次数")
    max_retries: int = Field(3, description="最大重试次数")
    node_id: str = Field(0, description="节点ID")

    class Config:
        from_attributes = True


class TaskCreate(BaseModel):
    """任务创建模型"""

    task_id: str
    task_type: TaskType
    params: Dict[str, Any]
    priority: int = 0
    status: TaskStatus = TaskStatus.PENDING
    callback_url: Optional[str] = None


class TaskUpdate(BaseModel):
    """任务更新模型"""

    status: Optional[TaskStatus] = None
    progress: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    callback_result: Optional[Dict[str, Any]] = None
    worker_node: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: Optional[int] = None
