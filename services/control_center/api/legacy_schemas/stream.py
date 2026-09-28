from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator, field_serializer
from datetime import datetime
from media_platform.domain.task.legacy_models import TaskStatus, TaskType

class StreamRecordRequest(BaseModel):
    """直播流录制请求"""
    task_id: Optional[str] = Field(None, description="任务ID，如果指定则使用已有的任务ID")
    app: str = Field(..., description="应用名称，例如：live、classCard等")
    stream_id: str = Field(..., description="流ID，不包含文件扩展名")
    start_time: Optional[datetime] = Field(None, description="录制开始时间，不指定则立即开始，格式：YYYY-MM-DD HH:MM:SS.fff")
    end_time: Optional[datetime] = Field(None, description="录制结束时间，不指定则持续录制，格式：YYYY-MM-DD HH:MM:SS.fff")
    output_format: str = Field("mp4", description="输出格式，默认mp4")
    callback_url: Optional[str] = Field(None, description="任务完成回调地址")
    extra_params: Optional[Dict[str, Any]] = Field(None, description="""额外参数，支持以下选项：

音频处理：
- extract_audio: 是否提取音频（布尔值）
- audio_format: 音频格式，支持'mp3'或'wav'

封面提取：
- extract_cover: 是否提取视频封面（布尔值）
- cover_strategy: 封面提取策略，支持以下值：
  * 'timestamp': 从视频指定时间点提取关键帧作为封面
  * 'custom': 生成包含课程信息的自定义封面
- cover_info: 封面提取相关参数（字典），根据不同策略包含不同字段：

  当 cover_strategy='timestamp' 时：
  * timestamp: 提取时间点（整数，单位：秒，默认60秒）

  当 cover_strategy='custom' 时：
  * replay_name: 回放名称（字符串，必填）
  * space_name: 所属空间名称（字符串，必填）
  * class_time: 上课时间（字符串，可选，格式：'2024/11/18 10:00-11:40'，如不提供将自动从start_time和end_time生成）

封面提取示例：
{
  "extract_cover": true,
  "cover_strategy": "timestamp",
  "cover_info": {"timestamp": 60}
}
或
{
  "extract_cover": true,
  "cover_strategy": "custom",
  "cover_info": {
    "replay_name": "数学基础课程",
    "space_name": "三年级数学教室",
    "class_time": "2024/11/18 10:00-11:40"
  }
}""")

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

class StreamRecordResponse(BaseModel):
    """直播流录制响应"""
    task_id: str = Field(..., description="任务ID")
    status: str = Field(..., description="任务状态: pending, processing, completed, failed")
    message: Optional[str] = Field(None, description="状态说明")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间，格式：YYYY-MM-DD HH:MM:SS.fff")

    class Config:
        schema_extra = {
            "example": {
                "task_id": "task_12345678",
                "status": "pending",
                "message": "录制任务已创建",
                "created_at": "2023-05-10 14:30:00.000"
            }
        }

    @field_serializer('created_at')
    def serialize_datetime(self, dt: datetime) -> str:
        """将datetime序列化为无时区的格式"""
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

class StreamRecordStatusResponse(BaseModel):
    """直播流录制状态响应"""
    task_id: str = Field(..., description="任务ID")
    status: str = Field(..., description="任务状态: pending, processing, completed, failed")
    progress: float = Field(0.0, description="任务进度，0-100")
    message: Optional[str] = Field(None, description="状态说明")
    result_url: Optional[str] = Field(None, description="结果文件URL")
    start_time: Optional[datetime] = Field(None, description="实际开始时间，格式：YYYY-MM-DD HH:MM:SS.fff")
    end_time: Optional[datetime] = Field(None, description="实际结束时间，格式：YYYY-MM-DD HH:MM:SS.fff")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间，格式：YYYY-MM-DD HH:MM:SS.fff")
    extra_params: Optional[Dict[str, Any]] = Field(None, description="""扩展信息，可能包含以下内容：

音频处理结果：
- audio_url: 提取的音频文件URL（当extract_audio=true时）

封面提取结果：
- cover_url: 生成的封面图片URL（当extract_cover=true时）
- cover_file_id: 封面文件ID

其他信息：
- segments: 录制片段信息
- errors: 处理过程中的错误信息""")

    @field_serializer('start_time', 'end_time', 'updated_at')
    def serialize_datetime(self, dt: Optional[datetime]) -> Optional[str]:
        """将datetime序列化为无时区的格式"""
        if dt is None:
            return None
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    class Config:
        schema_extra = {
            "example": {
                "task_id": "task_12345678",
                "status": "completed",
                "progress": 100.0,
                "message": "录制任务已完成",
                "result_url": "https://example.com/result.mp4",
                "start_time": "2023-05-10 14:30:00.000",
                "end_time": "2023-05-10 14:35:00.000",
                "updated_at": "2023-05-10 14:35:00.000",
                "extra_params": {
                    "audio_url": "https://example.com/audio.mp3",
                    "cover_url": "https://example.com/cover.jpg",
                    "cover_file_id": "cover_12345678"
                }
            }
        }


class StreamProcessRequest(BaseModel):
    operations:TaskType=Field(TaskType.NONE,description="任务类型")
    stream_url:str=Field(None,description="直播流地址")
    api_endpoint:str=Field(None,description="接口请求地址")
    callback_url: Optional[str] = Field(None, description="任务完成回调地址")
    start_time: Optional[datetime] = Field(None, description="开始时间，格式：YYYY-MM-DD HH:MM:SS")
    end_time: Optional[datetime] = Field(None, description="结束时间，格式：YYYY-MM-DD HH:MM:SS")
    chunk_duration: Optional[int] = Field(None, description="切片时长，单位秒")
    params:Dict[str,Any]=Field(None,description="其他参数")

class StreamProcessResponse(BaseModel):
    """直播流录制响应"""
    task_id: str = Field(..., description="任务ID")
    status: str = Field(..., description="任务状态: pending, processing, completed, failed")
    message: Optional[str] = Field(None, description="状态说明")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间，格式：YYYY-MM-DD HH:MM:SS.fff")

class StreamProcessResultResponse(BaseModel):
    """视频处理结果响应"""
    task_id:str
    status: Optional[TaskStatus] = None
    progress: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    message: Optional[str] = None
