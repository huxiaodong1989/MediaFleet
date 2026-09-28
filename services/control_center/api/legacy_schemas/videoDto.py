from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator, field_serializer
from datetime import datetime
from media_platform.domain.task.legacy_models import TaskStatus, TaskType


class GetAudioInVideoRequest(BaseModel):
    """直播流录制请求"""
    app: str = Field(..., description="应用名称，例如：live、classCard等")
    stream_id: str = Field(..., description="流ID，不包含文件扩展名")
    start_time: Optional[datetime] = Field(None, description="录制开始时间，不指定则立即开始，格式：YYYY-MM-DD HH:MM:SS.fff")
    end_time: Optional[datetime] = Field(None, description="录制结束时间，不指定则持续录制，格式：YYYY-MM-DD HH:MM:SS.fff")
    output_format: str = Field("mp4", description="输出格式，默认mp4")
    callback_url: Optional[str] = Field(None, description="任务完成回调地址")
    extra_params: Optional[Dict[str, Any]] = Field(None, description="额外参数")

    @validator('end_time')
    def validate_end_time(cls, v, values):
        """验证结束时间必须晚于开始时间"""
        if v and 'start_time' in values and values['start_time'] and v <= values['start_time']:
            raise ValueError('结束时间必须晚于开始时间')
        return v

    @field_serializer('start_time', 'end_time')
    def serialize_datetime(self, dt: Optional[datetime]) -> Optional[str]:
        """将datetime序列化为无时区的格式"""
        if dt is None:
            return None
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class VideoProcessRequest(BaseModel):
    operations:TaskType=Field(TaskType.NONE,description="任务类型")
    video_url:str=Field(None,description="视频链接")
    callback_url: Optional[str] = Field(None, description="任务完成回调地址")
    params:Dict[str,Any]=Field(None,description="其他参数：设置水印需添加参数{\"watermark\":\"水印文字\"}，提取图片间隔添加参数(默认5s){\"interval\":5}，指定存储服务类型添加参数（默认为none，可选cos\\minio\\local）{\"storage_service_type\":\"cos\"}")

class VideoProcessResponse(BaseModel):
    """直播流录制响应"""
    task_id: str = Field(..., description="任务ID")
    status: str = Field(..., description="任务状态: pending, processing, completed, failed")
    message: Optional[str] = Field(None, description="状态说明")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间，格式：YYYY-MM-DD HH:MM:SS.fff")

class VideoProcessResultResponse(BaseModel):
    """视频处理结果响应"""
    task_id:str
    status: Optional[TaskStatus] = None
    progress: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    message: Optional[str] = None
