from typing import Generic, TypeVar, Optional, Any, Union, Dict, List
from pydantic import BaseModel, Field
import uuid

# 泛型数据类型
T = TypeVar('T')

class ResponseCode:
    """响应状态码定义"""
    SUCCESS = 200               # 成功
    PARAM_ERROR = 10000       # 参数错误
    UNAUTHORIZED = 10001      # 未授权
    FORBIDDEN = 10002         # 禁止访问
    NOT_FOUND = 10003         # 资源不存在
    METHOD_NOT_ALLOWED = 10004  # 方法不允许
    CONFLICT = 10005          # 资源冲突
    TOO_MANY_REQUESTS = 10006  # 请求过多
    SERVER_ERROR = 20000      # 服务器错误
    SERVICE_UNAVAILABLE = 20001  # 服务不可用
    GATEWAY_ERROR = 20002     # 网关错误
    TASK_FAILED = 30000       # 任务失败
    TASK_TIMEOUT = 30001      # 任务超时
    TASK_CANCELED = 30002     # 任务取消

    # 业务特定错误码 (40000开始)
    STREAM_NOT_FOUND = 40001  # 直播流不存在
    STREAM_DISCONNECTED = 40002  # 直播流连接中断
    FILE_TOO_LARGE = 40003    # 文件过大
    FORMAT_NOT_SUPPORTED = 40004  # 格式不支持

class ResponseModel(BaseModel, Generic[T]):
    """统一API响应模型"""
    code: int = Field(ResponseCode.SUCCESS, description="状态码，0表示成功")
    msg: str = Field("操作成功", description="提示信息")
    data: Optional[T] = Field(None, description="响应数据")
    traceId: str = Field(default_factory=lambda: str(uuid.uuid4()), description="请求跟踪ID")

    def __init__(self, code: int = ResponseCode.SUCCESS, msg: str = "操作成功",
                 data: Optional[T] = None, traceId: Optional[str] = None, **kwargs):
        super().__init__(code=code, msg=msg, data=data, traceId=traceId or str(uuid.uuid4()), **kwargs)

    def dict(self) -> Dict[str, Any]:
        """返回字典格式的响应数据"""
        return {
            "code": self.code,
            "msg": self.msg,
            "data": self.data,
            "traceId": self.traceId
        }

# 预定义响应
def success(data: Any = None, msg: str = "操作成功", traceId: Optional[str] = None) -> Dict:
    """成功响应"""
    return ResponseModel(ResponseCode.SUCCESS, msg, data, traceId).dict()

def error(code: int = ResponseCode.SERVER_ERROR, msg: str = "服务器错误",
          traceId: Optional[str] = None) -> Dict:
    """错误响应"""
    return ResponseModel(code, msg, None, traceId).dict()

def param_error(msg: str = "参数错误", traceId: Optional[str] = None) -> Dict:
    """参数错误响应"""
    return ResponseModel(ResponseCode.PARAM_ERROR, msg, None, traceId).dict()

def not_found(msg: str = "资源不存在", traceId: Optional[str] = None) -> Dict:
    """资源不存在响应"""
    return ResponseModel(ResponseCode.NOT_FOUND, msg, None, traceId).dict()

def unauthorized(msg: str = "未授权", traceId: Optional[str] = None) -> Dict:
    """未授权响应"""
    return ResponseModel(ResponseCode.UNAUTHORIZED, msg, None, traceId).dict()

def task_failed(msg: str = "任务执行失败", traceId: Optional[str] = None) -> Dict:
    """任务失败响应"""
    return ResponseModel(ResponseCode.TASK_FAILED, msg, None, traceId).dict()

# ==================== 回调相关数据结构 ====================

class CallbackTaskInfo(BaseModel):
    """回调任务信息"""
    task_id: str = Field(..., description="任务ID")
    status: str = Field(..., description="任务状态")
    callback_url: Optional[str] = Field(None, description="回调地址")
    callback_result: Optional[Dict[str, Any]] = Field(None, description="回调结果")
    retry_count: Optional[int] = Field(0, description="重试次数")
    last_callback_time: Optional[str] = Field(None, description="最后回调时间")
    error_message: Optional[str] = Field(None, description="错误信息")
    created_at: Optional[str] = Field(None, description="创建时间")
    updated_at: Optional[str] = Field(None, description="更新时间")

class GetFailedCallbackTasksResponse(BaseModel):
    """获取回调失败任务列表响应"""
    tasks: List[CallbackTaskInfo] = Field([], description="任务列表")
    total: int = Field(0, description="总记录数")
    skip: int = Field(0, description="跳过记录数")
    limit: int = Field(100, description="每页记录数")

# 媒体信息相关子模型
class MediaResolution(BaseModel):
    """媒体分辨率信息"""
    width: Optional[int] = Field(None, description="宽度")
    height: Optional[int] = Field(None, description="高度")

class AudioCodecInfo(BaseModel):
    """音频编解码信息"""
    codec: Optional[str] = Field(None, description="音频编码格式")
    bitrate: Optional[int] = Field(None, description="音频比特率")
    channels: Optional[int] = Field(None, description="音频声道数")
    sample_rate: Optional[int] = Field(None, description="音频采样率")

class VideoCodecInfo(BaseModel):
    """视频编解码信息"""
    codec: Optional[str] = Field(None, description="视频编码格式")
    bitrate: Optional[int] = Field(None, description="视频比特率")
    frame_rate: Optional[int] = Field(None, description="视频帧率")

class AudioInfo(BaseModel):
    """音频文件信息"""
    audio: Optional[AudioCodecInfo] = Field(None, description="音频编解码信息")
    duration: Optional[float] = Field(None, description="音频时长（秒）")
    file_size: Optional[int] = Field(None, description="文件大小（字节）")

class CoverInfo(BaseModel):
    """封面图片信息"""
    mode: Optional[str] = Field(None, description="图片颜色模式")
    format: Optional[str] = Field(None, description="图片格式")
    file_size: Optional[int] = Field(None, description="文件大小（字节）")
    resolution: Optional[MediaResolution] = Field(None, description="图片分辨率")

class VideoInfo(BaseModel):
    """视频文件信息"""
    audio: Optional[AudioCodecInfo] = Field(None, description="音频编解码信息")
    video: Optional[VideoCodecInfo] = Field(None, description="视频编解码信息")
    duration: Optional[float] = Field(None, description="视频时长（秒）")
    file_size: Optional[int] = Field(None, description="文件大小（字节）")
    resolution: Optional[MediaResolution] = Field(None, description="视频分辨率")
    segments_count: Optional[int] = Field(None, description="视频片段数量")

class CallbackSummary(BaseModel):
    """回调数据摘要信息 - 支持成功和失败两种情况"""
    end_time: Optional[str] = Field(None, description="实际结束时间")
    stream_id: Optional[str] = Field(None, description="流ID")
    audio_info: Optional[AudioInfo] = Field(None, description="音频信息")
    cover_info: Optional[CoverInfo] = Field(None, description="封面信息")
    start_time: Optional[str] = Field(None, description="实际开始时间")
    video_info: Optional[VideoInfo] = Field(None, description="视频信息")
    original_stream_id: Optional[str] = Field(None, description="原始流ID")

class CallbackResultData(BaseModel):
    """回调结果数据结构 - 支持成功和失败两种情况的数据格式"""
    error: Optional[str] = Field("", description="错误信息")
    status: str = Field(..., description="任务状态")
    summary: Optional[CallbackSummary] = Field(None, description="摘要信息")
    task_id: str = Field(..., description="任务ID")
    audio_url: Optional[str] = Field(None, description="音频文件URL")
    cover_url: Optional[str] = Field(None, description="封面图片URL")
    video_url: Optional[str] = Field(None, description="视频文件URL")
    created_at: Optional[str] = Field(None, description="创建时间")
    audio_file_id: Optional[str] = Field("", description="音频文件ID")
    cover_file_id: Optional[str] = Field("", description="封面文件ID")
    video_file_id: Optional[str] = Field("", description="视频文件ID")

# 特定的响应模型
class CallbackResultResponse(ResponseModel[CallbackResultData]):
    """获取回调结果的响应模型"""
    pass

# 辅助函数：创建带类型的成功响应
def success_with_model(data: T, msg: str = "操作成功", traceId: Optional[str] = None) -> ResponseModel[T]:
    """创建带具体数据模型的成功响应"""
    return ResponseModel[T](ResponseCode.SUCCESS, msg, data, traceId)

#

class RetryCallbackRequest(BaseModel):
    """重试回调请求"""
    force_retry: Optional[bool] = Field(False, description="是否强制重试（忽略重试次数限制）")
    callback_url: Optional[str] = Field(None, description="新的回调地址（可选）")

class RetryCallbackResponse(BaseModel):
    """重试回调响应"""
    task_id: str = Field(..., description="任务ID")
    callback_url: str = Field(..., description="回调地址")
    status: str = Field(..., description="重试状态")
    retry_count: int = Field(0, description="当前重试次数")

class UpdateCallbackResultRequest(BaseModel):
    """更新回调结果请求"""
    callback_result: Dict[str, Any] = Field(..., description="回调结果数据")
    status: Optional[str] = Field(None, description="更新任务状态")
    retry_count: Optional[int] = Field(None, description="重试次数")

class UpdateCallbackResultResponse(BaseModel):
    """更新回调结果响应"""
    task_id: str = Field(..., description="任务ID")
    callback_result: Dict[str, Any] = Field(..., description="回调结果数据")
    status: str = Field(..., description="任务状态")
    updated_at: str = Field(..., description="更新时间")
