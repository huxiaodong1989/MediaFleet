"""调用中心任务 API 的请求和响应模型。

API 模型与 RabbitMQ 消息契约分离：API 面向 RTC 和内部业务服务，消息契约
面向媒体 Worker。应用服务负责将两者转换，避免外部调用方直接依赖 MQ 细节。
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class TaskCreateRequest(BaseModel):
    """创建通用媒体任务请求。"""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "school_code": "LOCAL_TEST",
                    "task_type": "video.cover.extract",
                    "priority": 0,
                    "max_retries": 3,
                    "params": {
                        "video_url": "http://127.0.0.1:8099/sample.mp4",
                        "cover_strategy": "timestamp",
                        "cover_info": {"start_time": 1},
                    },
                    "callback_url": "https://rtc.example.com/media/callback",
                    "created_by": "SWAGGER",
                },
                {
                    "school_code": "LOCAL_TEST",
                    "task_type": "video.audio.extract",
                    "priority": 0,
                    "max_retries": 3,
                    "params": {
                        "video_url": "https://example.com/sample.mp4",
                        "audio_format": "mp3",
                    },
                    "callback_url": "https://rtc.example.com/media/callback",
                    "created_by": "SWAGGER",
                },
                {
                    "school_code": "LOCAL_TEST",
                    "task_type": "video.clip.extract",
                    "priority": 0,
                    "max_retries": 3,
                    "params": {
                        "video_url": "https://example.com/sample.mp4",
                        "clips": [
                            {
                                "start_time": "00:00:10.500",
                                "end_time": "00:00:30.500",
                                "buss_id": "clip-1",
                            },
                            {
                                "start_time": 45.0,
                                "end_time": 60.5,
                                "buss_id": "clip-2",
                            },
                        ],
                    },
                    "callback_url": "https://rtc.example.com/media/callback",
                    "created_by": "SWAGGER",
                },
                {
                    "school_code": "LOCAL_TEST",
                    "task_type": "video.watermark",
                    "priority": 0,
                    "max_retries": 3,
                    "params": {
                        "video_url": "https://example.com/sample.mp4",
                        "watermark": "课堂录制",
                    },
                    "callback_url": "https://rtc.example.com/media/callback",
                    "created_by": "SWAGGER",
                },
                {
                    "school_code": "LOCAL_TEST",
                    "task_type": "object.detect.image",
                    "priority": 0,
                    "max_retries": 3,
                    "params": {
                        "image_url": "https://example.com/sample.jpg",
                        "target_classes": ["person", "car"],
                    },
                    "callback_url": "https://rtc.example.com/media/callback",
                    "created_by": "SWAGGER",
                },
                {
                    "school_code": "LOCAL_TEST",
                    "task_type": "object.track.video",
                    "priority": 0,
                    "max_retries": 3,
                    "params": {
                        "video_url": "https://example.com/sample.mp4",
                        "target_classes": ["person"],
                    },
                    "callback_url": "https://rtc.example.com/media/callback",
                    "created_by": "SWAGGER",
                },
                {
                    "school_code": "LOCAL_TEST",
                    "task_type": "speech.offline.recognize",
                    "priority": 0,
                    "max_retries": 3,
                    "params": {
                        "media_url": "https://example.com/sample.mp4",
                        "lang": "zh",
                        "provider": "funasr",
                        "hotwords": "课堂",
                    },
                    "callback_url": "https://rtc.example.com/media/callback",
                    "created_by": "SWAGGER",
                }
            ]
        },
    )

    school_code: str = Field(min_length=1, max_length=64, description="学校码")
    task_type: str = Field(min_length=1, max_length=64, description="媒体任务类型")
    priority: int = Field(default=0, ge=0, le=255, description="任务优先级")
    max_retries: int = Field(default=3, ge=1, le=100, description="最大执行重试次数")
    params: dict[str, Any] = Field(default_factory=dict, description="任务参数")
    callback_url: str = Field(
        min_length=1,
        max_length=1000,
        description="业务回调地址；任务完成或最终失败后由调用中心回调该地址",
    )
    target_node_id: str | None = Field(
        default=None,
        max_length=36,
        description="可选定向执行节点；通用竞争任务通常不传",
    )
    created_by: str = Field(
        default="SYSTEM", min_length=1, max_length=64, description="创建人"
    )


class TaskCreateResponse(BaseModel):
    """任务创建结果。

    调用方后续查询状态、取消任务或排查处理结果时使用 `task_id`。
    """

    task_id: str = Field(description="国标任务表主键")
    created: bool = Field(description="true为新建任务")


class TaskStatusResponse(BaseModel):
    """媒体任务状态查询结果。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "task_id": "6d1c65f1-6f4b-47d4-9448-38c8855c0001",
                    "school_code": "LOCAL_TEST",
                    "task_type": "video.cover.extract",
                    "status": "completed",
                    "publish_status": "PUBLISHED",
                    "progress": 100,
                    "params": {
                        "video_url": "https://example.com/sample.mp4",
                        "cover_strategy": "timestamp",
                        "cover_info": {"start_time": 1},
                    },
                    "result": {
                        "cover_url": "https://minio.example.com/media/cover.jpg"
                    },
                    "error_message": None,
                    "callback_url": "https://rtc.example.com/media/callback",
                    "callback_result": {"success": True, "status_code": 200},
                    "executor_node_id": "media-worker-01",
                    "retry_count": 0,
                    "max_retries": 3,
                    "message_id": "f3f4f5f6e7e84d0a9d0b1c2d3e4f5a6b",
                    "created_at": "2026-07-27T10:00:00",
                    "updated_at": "2026-07-27T10:00:08",
                    "published_at": "2026-07-27T10:00:01",
                    "started_at": "2026-07-27T10:00:02",
                    "completed_at": "2026-07-27T10:00:08",
                }
            ]
        }
    )

    task_id: str = Field(description="国标任务表主键，来自创建任务响应")
    school_code: str = Field(description="学校码")
    task_type: str = Field(description="媒体任务类型")
    status: str = Field(description="任务状态")
    publish_status: str = Field(description="消息发布状态")
    progress: float = Field(description="任务进度百分比")
    params: dict[str, Any] = Field(description="创建任务时写入的业务参数")
    result: dict[str, Any] | None = Field(default=None, description="任务执行结果JSON")
    error_message: str | None = Field(default=None, description="任务错误信息")
    callback_url: str | None = Field(default=None, description="业务回调地址")
    callback_result: dict[str, Any] | None = Field(
        default=None, description="业务回调执行结果JSON"
    )
    executor_node_id: str | None = Field(default=None, description="执行节点主键")
    retry_count: int = Field(description="当前重试次数")
    max_retries: int = Field(description="最大重试次数")
    message_id: str | None = Field(default=None, description="RabbitMQ消息编号，用于排障")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="修改时间")
    published_at: datetime | None = Field(default=None, description="消息发布时间")
    started_at: datetime | None = Field(default=None, description="任务开始时间")
    completed_at: datetime | None = Field(default=None, description="任务完成时间")


class TaskFileResponse(BaseModel):
    """媒体任务产物文件查询结果。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "file_id": "44b40b3e-4bea-40d4-bc7c-25cb5dc20001",
                    "task_id": "6d1c65f1-6f4b-47d4-9448-38c8855c0001",
                    "school_code": "LOCAL_TEST",
                    "file_type": "COVER",
                    "file_name": "cover.jpg",
                    "file_url": "https://minio.example.com/media/cover.jpg",
                    "relative_path": "covers/2026/07/27/cover.jpg",
                    "bucket_name": "media",
                    "file_size": 102400,
                    "mime_type": "image/jpeg",
                    "extra_info": {"width": 1280, "height": 720},
                    "created_at": "2026-07-27T10:00:08",
                }
            ]
        }
    )

    file_id: str = Field(description="文件主键")
    task_id: str = Field(description="关联媒体任务主键")
    school_code: str = Field(description="学校码")
    file_type: str = Field(description="文件类型：VIDEO/AUDIO/COVER/SUBTITLE/OTHER")
    file_name: str = Field(description="文件名称")
    file_url: str = Field(description="文件访问地址")
    relative_path: str | None = Field(default=None, description="对象存储相对路径")
    bucket_name: str | None = Field(default=None, description="对象存储桶名称")
    file_size: int = Field(description="文件大小，单位字节")
    mime_type: str = Field(description="文件MIME类型")
    extra_info: dict[str, Any] | None = Field(
        default=None, description="文件扩展信息JSON"
    )
    created_at: datetime = Field(description="创建时间")


class NodeHeartbeatRequest(BaseModel):
    """录制节点或通用媒体 Worker 的心跳上报请求。"""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "node_code": "recorder-01",
                    "node_name": "录制节点01",
                    "node_type": "RECORDER",
                    "server_code": "recording-server-01",
                    "server_name": "一号录制服务器",
                    "agent_url": "http://127.0.0.1:8010",
                    "zlm_api_url": "http://127.0.0.1:8080",
                    "zlm_server_id": "zlm-recorder-01",
                    "play_host": "zlm.example.com",
                    "play_port": "443",
                    "play_protocol": "https",
                    "rtmp_port": "1935",
                    "rtsp_port": "554",
                    "record_root": "D:/record",
                    "weight": 100,
                    "capabilities": ["record.start", "record.stop"],
                    "capacity": {
                        "current_recordings": 12,
                        "max_recordings": 80,
                        "max_bindings": 300,
                        "postprocess_queue_size": 3,
                        "postprocess_current_processing": 1,
                        "postprocess_max_workers": 2,
                        "disk_usage_percent": 55.2,
                    },
                    "readiness": "READY",
                    "readiness_details": {
                        "zlm_api_ready": True,
                        "record_root_ready": True,
                        "disk_free_gb": 500.0,
                        "disk_usage_percent": 55.2,
                        "errors": [],
                    },
                    "reported_at": "2026-07-27T10:30:00",
                    "updated_by": "recorder-node",
                },
                {
                    "node_code": "media-worker-01",
                    "node_name": "媒体处理节点01",
                    "node_type": "WORKER",
                    "weight": 100,
                    "capabilities": [
                        "video.cover.extract",
                        "video.audio.extract",
                        "speech.offline.recognize",
                    ],
                    "capacity": {
                        "worker_prefetch": 1,
                        "consumer_enabled": True,
                        "processing_tasks": 1,
                        "media_recog_concurrency": 2,
                        "funasr_gpu_concurrency": 1,
                    },
                    "updated_by": "media-worker",
                },
            ]
        },
    )

    node_code: str = Field(min_length=1, max_length=64, description="节点编号")
    node_name: str | None = Field(
        default=None,
        max_length=128,
        description="节点名称；不传时使用节点编号",
    )
    node_type: Literal["RECORDER", "WORKER", "CONTENT_ANALYSIS"] = Field(
        description="节点类型"
    )
    server_code: str | None = Field(
        default=None,
        max_length=64,
        description="录制服务器稳定编号；RECORDER 必传，WORKER 不传",
    )
    server_name: str | None = Field(
        default=None,
        max_length=128,
        description="录制服务器名称；不传时使用 server_code",
    )
    status: Literal["ONLINE", "DRAINING", "DISABLED"] = Field(
        default="ONLINE",
        description=(
            "节点状态；普通节点心跳不需要传，默认 ONLINE。停止心跳后由后续巡检"
            "标记 OFFLINE，暂停接收新任务应由调用中心管理接口处理。"
        ),
    )
    agent_url: str | None = Field(
        default=None,
        max_length=500,
        description="节点本机服务地址，用于本机 API 或后续定向管理",
    )
    zlm_api_url: str | None = Field(
        default=None,
        max_length=500,
        description="录制节点关联 ZLMediaKit 内部管理接口地址",
    )
    zlm_server_id: str | None = Field(
        default=None,
        max_length=128,
        description="录制节点关联 ZLMediaKit 服务标识",
    )
    play_host: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "FLV统一播放主机，不含协议、端口和节点路径；例如 zlm.example.com。"
            "节点路径直接使用 /{server_id}"
        ),
    )
    play_port: str | None = Field(
        default=None,
        max_length=16,
        description="FLV播放端口，例如80或443",
    )
    play_protocol: Literal["http", "https"] | None = Field(
        default=None,
        description="FLV播放协议",
    )
    rtmp_port: str | None = Field(
        default=None,
        max_length=16,
        description="ZLMediaKit RTMP端口，例如1935",
    )
    rtsp_port: str | None = Field(
        default=None,
        max_length=16,
        description="ZLMediaKit RTSP端口，例如554",
    )
    record_root: str | None = Field(
        default=None,
        max_length=1000,
        description="录制节点本地录像根目录；Worker 可不传",
    )
    weight: int = Field(default=100, ge=0, description="调度权重")
    capabilities: list[str] = Field(
        default_factory=list,
        description="节点能力列表，示例：record.start、video.cover.extract",
    )
    capacity: dict[str, Any] = Field(
        default_factory=dict,
        description="节点容量快照 JSON；按节点类型携带录像数、队列数或转写并发等",
    )
    readiness: Literal["READY", "NOT_READY"] | None = Field(
        default=None,
        description=(
            "真实依赖就绪状态；RECORDER 由本机检查 ZL、录像目录和磁盘后上报，"
            "WORKER 不传时按 READY 处理"
        ),
    )
    readiness_details: dict[str, Any] = Field(
        default_factory=dict,
        description="依赖就绪检查明细；不得包含 ZL Secret 或带密码源流地址",
    )
    reported_at: datetime | None = Field(
        default=None,
        description="节点上报时间；不传时由调用中心使用当前时间",
    )
    updated_by: str = Field(
        default="node-heartbeat",
        min_length=1,
        max_length=64,
        description="修改人，通常为 recorder-node 或 media-worker",
    )

    @model_validator(mode="after")
    def validate_recorder_identity_and_readiness(self):
        """录制节点必须提供稳定录制单元身份和真实依赖检查结果。"""

        if self.node_type != "RECORDER":
            return self
        if not (self.server_code or "").strip():
            raise ValueError("RECORDER 必须提供 server_code")
        if not (self.zlm_server_id or "").strip():
            raise ValueError("RECORDER 必须提供 zlm_server_id")
        if not (self.zlm_api_url or "").strip():
            raise ValueError("RECORDER 必须提供 zlm_api_url")
        if not (self.play_host or "").strip():
            raise ValueError("RECORDER 必须提供 play_host")
        if self.play_protocol is None:
            raise ValueError("RECORDER 必须提供 play_protocol")
        for field_name in ("play_port", "rtmp_port", "rtsp_port"):
            value = getattr(self, field_name)
            if not value or not value.isdigit() or not 1 <= int(value) <= 65535:
                raise ValueError(
                    f"RECORDER {field_name} 必须是1到65535之间的端口"
                )
        if self.readiness is None:
            raise ValueError("RECORDER 必须提供 readiness")
        max_recordings = self.capacity.get("max_recordings")
        if not isinstance(max_recordings, int) or max_recordings < 1:
            raise ValueError("RECORDER capacity.max_recordings 必须是正整数")
        max_bindings = self.capacity.get("max_bindings", max_recordings)
        if not isinstance(max_bindings, int) or max_bindings < 1:
            raise ValueError("RECORDER capacity.max_bindings 必须是正整数")
        return self


class NodeHeartbeatResponse(BaseModel):
    """节点心跳处理结果。"""

    node_id: str = Field(description="国标节点表主键")
    created: bool = Field(description="true为首次注册，false为更新已有节点")
    status: str = Field(description="写入后的节点状态")
    last_heartbeat_at: datetime = Field(description="写入后的最后心跳时间")
    recording_server_id: str | None = Field(
        default=None,
        description="RECORDER 固定归属的录制服务器主键；WORKER 为空",
    )


class NodeCandidateResponse(BaseModel):
    """可调度节点候选响应。"""

    node_id: str = Field(description="国标节点表主键")
    node_code: str = Field(description="节点编号")
    node_name: str = Field(description="节点名称")
    node_type: str = Field(description="节点类型")
    status: str = Field(description="节点状态")
    agent_url: str | None = Field(default=None, description="节点本机服务地址")
    zlm_api_url: str | None = Field(default=None, description="ZLMediaKit API 地址")
    zlm_server_id: str | None = Field(default=None, description="ZLMediaKit 服务标识")
    record_root: str | None = Field(default=None, description="录像根目录")
    weight: int = Field(description="调度权重")
    capabilities: list[str] = Field(description="节点能力列表")
    capacity: dict[str, Any] = Field(description="节点容量快照")
    readiness: str = Field(description="节点真实依赖就绪状态")
    readiness_details: dict[str, Any] = Field(description="依赖就绪检查明细")
    recording_server_id: str | None = Field(
        default=None, description="录制服务器主键"
    )
    recording_server_code: str | None = Field(
        default=None, description="录制服务器编号"
    )
    recording_server_status: str | None = Field(
        default=None, description="录制服务器运维状态"
    )
    last_heartbeat_at: datetime | None = Field(description="最后心跳时间")
    score: float = Field(description="调度评分")
    reason: str = Field(description="评分原因")


class RecordingServerStatusUpdateRequest(BaseModel):
    """更新录制服务器运维状态。"""

    status: Literal["ACTIVE", "DRAINING", "MAINTENANCE", "DISABLED"] = Field(
        description="ACTIVE恢复接单；其他状态均停止新绑定"
    )
    updated_by: str = Field(
        default="control-center-admin",
        min_length=1,
        max_length=64,
        description="操作人",
    )


class RecordingServerResponse(BaseModel):
    """录制服务器稳定身份与运维状态。"""

    server_id: str = Field(description="录制服务器主键")
    server_code: str = Field(description="录制服务器稳定编号")
    server_name: str = Field(description="录制服务器名称")
    status: str = Field(description="录制服务器运维状态")
    recorder_node_id: str = Field(description="固定关联 recorder-node 主键")
    zlm_server_id: str = Field(description="固定关联 ZLMediaKit 服务标识")
    zlm_api_url: str | None = Field(default=None, description="ZL内部管理地址")
    play_host: str | None = Field(default=None, description="FLV统一播放主机")
    play_port: str | None = Field(default=None, description="FLV播放端口")
    play_protocol: str | None = Field(default=None, description="FLV播放协议")
    rtmp_port: str | None = Field(default=None, description="RTMP端口")
    rtsp_port: str | None = Field(default=None, description="RTSP端口")
    record_root: str | None = Field(default=None, description="录像根目录")
    max_recordings: int = Field(description="最大同时录制路数")
    max_bindings: int = Field(description="最大流绑定数")
    occupied_bindings: int = Field(description="当前已占用流绑定数")


class NodeSelectRequest(BaseModel):
    """选择一个可调度节点的请求。"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "node_type": "RECORDER",
                    "capability": "record.start",
                    "heartbeat_timeout_seconds": 90,
                    "max_disk_usage_percent": 90,
                }
            ]
        },
    )

    node_type: Literal["RECORDER", "WORKER"] = Field(description="节点类型")
    capability: str | None = Field(
        default=None,
        max_length=128,
        description="可选能力过滤，例如 record.start 或 video.cover.extract",
    )
    heartbeat_timeout_seconds: int = Field(
        default=90,
        ge=1,
        le=3600,
        description="心跳超时时间，超过该时间未上报则不参与选择",
    )
    max_disk_usage_percent: float = Field(
        default=90,
        ge=1,
        le=100,
        description="录制节点磁盘使用率硬阈值",
    )


class NodeSelectResponse(BaseModel):
    """节点选择结果。"""

    selected: NodeCandidateResponse | None = Field(description="当前最佳节点")
    candidates: list[NodeCandidateResponse] = Field(description="候选节点列表")


class StreamBindingCreateRequest(BaseModel):
    """RTC 创建或获取媒体流绑定请求。"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "school_code": "88888",
                    "resource_type": "CAMERA",
                    "space_id": "10001",
                    "stream_id": "2079198571392",
                    "stream_name": "一号教室摄像头",
                    "app": "proxys",
                },
                {
                    "school_code": "88888",
                    "resource_type": "DESKTOP",
                    "stream_id": "2079198571393",
                    "app": "live",
                },
            ]
        },
    )

    school_code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^\d+$",
        description="纯数字字符串形式的业务学校码，例如 88888；仅用于业务数据归属和同空间亲和，不参与节点选择。请使用字符串，避免前导零丢失",
    )
    resource_type: Literal["CAMERA", "DESKTOP"] = Field(
        description="资源类型：CAMERA摄像头、DESKTOP桌面终端"
    )
    space_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^\d+$",
        description=(
            "可选的纯数字空间/教室编号，例如 10001。传入时用于同空间软亲和；"
            "旧桌面客户端未绑定空间时可不传，调用中心将按健康度、容量和权重选择节点"
        ),
    )
    stream_id: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "RTC 生成并持久化的技术流标识，重试时必须复用；"
            "不要使用前端展示流名称替代该字段"
        ),
    )
    stream_name: str | None = Field(
        default=None,
        max_length=255,
        description="业务展示流名称，不等同于技术 stream_id",
    )
    app: str = Field(
        min_length=1,
        max_length=64,
        description="ZLMediaKit app；摄像头现网使用 proxys，桌面按实际推流 app 传入",
    )

    @field_validator("space_id", "stream_name", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        """将旧客户端传入的空字符串视为未提供。"""

        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class StreamBindingResponse(BaseModel):
    """与 RTC `GetStreamBindResponse` 精确对齐的绑定结果。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "stream_id": "2079198571392",
                    "api_url": "http://192.0.2.21:8080",
                    "server_id": "zlm-01",
                    "ip": "192.0.2.21",
                }
            ]
        },
    )

    stream_id: str = Field(description="技术流标识")
    api_url: str = Field(description="ZLMediaKit API完整地址")
    server_id: str = Field(
        description=(
            "播放端口后的Nginx节点路由段，例如zlm-01；"
            "播放路径直接从/{server_id}/开始，不额外增加/media层"
        )
    )
    ip: str = Field(description="被绑定流媒体节点地址，取自api_url的主机部分")


class RecordingCommandStartRequest(BaseModel):
    """调用中心下发开始录制命令请求。

    字段保持原 `StreamRecordRequest` 的业务含义，不要求 RTC 为内部命令模型额外补字段。
    调用中心只使用 `app + stream_id` 查绑定节点，其他业务参数原样透传
    给 recorder-node，由 recorder-node 完成录制、后处理、回调和 RTC MQ 通知。
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "task_id": "rtc-record-plan-20260728-0001",
                    "app": "classCard",
                    "stream_id": "1912078168135675904",
                    "start_time": "2026-07-28 16:30:00.000",
                    "end_time": "2026-07-28 17:30:00.000",
                    "output_format": "mp4",
                    "callback_url": "https://rtc.example.com/api/media/record/callback",
                    "extra_params": {
                        "extract_audio": True,
                        "audio_format": "mp3",
                        "extract_cover": True,
                        "cover_strategy": "custom",
                        "cover_info": {
                            "replay_name": "数学基础课程",
                            "space_name": "三年级数学教室",
                            "class_time": "2026/07/28 16:30-17:30",
                        },
                    },
                },
                {
                    "task_id": "rtc-open-record-20260728-0002",
                    "app": "live",
                    "stream_id": "rtc-camera-stream-001",
                    "start_time": "2026-07-28 16:30:00.000",
                    "output_format": "mp4",
                    "callback_url": "https://rtc.example.com/api/media/record/callback",
                    "extra_params": {
                        "extract_audio": False,
                        "extract_cover": True,
                        "cover_strategy": "timestamp",
                        "cover_info": {"timestamp": 60},
                    },
                }
            ]
        },
    )

    task_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description=(
            "业务录制任务ID。RTC 已有录制计划任务号时传入；不传时调用中心按 "
            "record-{app}-{stream_id} 生成开放式活跃录制任务号。"
        ),
    )
    app: str = Field(default="live", min_length=1, max_length=64, description="ZL app")
    stream_id: str = Field(
        min_length=1,
        max_length=128,
        description="RTC 已绑定的技术流标识，不是前端展示流名称",
    )
    start_time: datetime | None = Field(
        default=None,
        description=(
            "录制开始时间，格式建议 YYYY-MM-DD HH:mm:ss.SSS；"
            "不传由录制节点按收到命令时间开始"
        ),
    )
    end_time: datetime | None = Field(
        default=None,
        description=(
            "录制结束时间，可选。不传表示开放式录制，后续调用停止接口提前结束并继续后处理"
        ),
    )
    output_format: str = Field(
        default="mp4",
        max_length=32,
        description="录像输出格式，默认 mp4",
    )
    callback_url: str | None = Field(
        default=None,
        max_length=1000,
        description="任务完成回调地址，随命令透传给 recorder-node",
    )
    extra_params: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("extra_params", "params"),
        description=(
            "录制扩展参数，沿用原接口。支持 extract_audio、audio_format、"
            "extract_cover、cover_strategy、cover_info、classroom_id、process_type 等。"
        ),
    )

    @model_validator(mode="after")
    def validate_record_time_window(self):
        """结束时间必须晚于开始时间，避免录制节点收到无效计划。"""

        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError("end_time 必须晚于 start_time")
        return self


class RecordingCommandStopRequest(BaseModel):
    """调用中心下发停止录制命令请求。

    `record.stop` 表示正常提前结束录制并继续后处理，不是取消并丢弃结果。
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "task_id": "rtc-open-record-20260728-0002",
                }
            ]
        },
    )

    task_id: str = Field(
        default=...,
        min_length=1,
        max_length=128,
        description="业务录制任务ID。停止录制只需要传开始录制时的同一个 task_id。",
    )


class RecordingCommandResponse(BaseModel):
    """录制命令下发结果。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "accepted": True,
                    "command": "record.start",
                    "task_id": "rtc-record-plan-20260728-0001",
                    "target_node_id": "recorder-node-id-001",
                    "binding_id": "3e97c2f7-2f4d-47a5-8b33-2b4b50c90001",
                    "app": "classCard",
                    "stream_id": "1912078168135675904",
                    "message_id": "rtc-record-plan-20260728-0001:record.start",
                    "confirmed": True,
                }
            ]
        }
    )

    accepted: bool = Field(description="true 表示命令已交给 RabbitMQ 并收到发布确认")
    command: Literal["record.start", "record.stop"] = Field(description="录制命令类型")
    task_id: str = Field(description="录制任务编号")
    target_node_id: str = Field(description="命令投递目标 recorder-node 主键")
    binding_id: str = Field(description="用于路由本次命令的有效流绑定主键")
    app: str = Field(description="ZL app")
    stream_id: str = Field(description="技术流标识")
    message_id: str = Field(description="RabbitMQ 命令消息编号")
    confirmed: bool = Field(description="Broker 发布确认结果")


__all__ = [
    "NodeCandidateResponse",
    "NodeHeartbeatRequest",
    "NodeHeartbeatResponse",
    "NodeSelectRequest",
    "NodeSelectResponse",
    "RecordingCommandResponse",
    "RecordingCommandStartRequest",
    "RecordingCommandStopRequest",
    "RecordingServerResponse",
    "RecordingServerStatusUpdateRequest",
    "StreamBindingCreateRequest",
    "StreamBindingResponse",
    "TaskCreateRequest",
    "TaskCreateResponse",
    "TaskFileResponse",
    "TaskStatusResponse",
]
