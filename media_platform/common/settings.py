import os
import logging
from typing import Optional, Literal
from functools import lru_cache

# 尝试导入Pydantic v2兼容性
try:
    # 新版本的pydantic
    from pydantic_settings import BaseSettings
    from pydantic import Field, field_validator, ConfigDict
except ImportError:
    try:
        # 兼容旧版本
        from pydantic import BaseSettings, Field, validator as field_validator, ConfigDict
    except ImportError:
        # 最旧版本
        from pydantic import BaseSettings, Field
        # 创建一个兼容的ConfigDict
        ConfigDict = dict
        def field_validator(field_name, **kwargs):
            def decorator(func):
                return func
            return decorator

# 创建配置专用的日志记录器
logger = logging.getLogger("config")


_PydanticBaseSettings = BaseSettings


class BaseSettings(_PydanticBaseSettings):
    """项目配置基类。

    PowerShell 中常见的 `$env:XXX=''` 会生成一个“存在但为空”的环境变量。
    pydantic-settings 默认会优先读取该空字符串，导致整数和布尔字段解析失败。
    项目配置统一忽略空环境变量，让它回退到 `.env` 或字段默认值。
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("_env_ignore_empty", True)
        super().__init__(**kwargs)


def should_use_env_file() -> bool:
    """
    检测是否应该使用.env文件
    在Docker环境中或设置了IGNORE_ENV_FILE时不使用.env文件
    """
    return not (
        os.getenv("DOCKER_ENV") or
        os.getenv("IGNORE_ENV_FILE") or
        os.path.exists("/.dockerenv")  # Docker容器标识文件
    )

class DatabaseSettings(BaseSettings):
    """数据库配置"""
    type: str = Field("mysql", alias="DB_TYPE")
    host: str = Field("127.0.0.1", alias="DB_HOST")
    port: int = Field(3306, alias="DB_PORT")
    username: str = Field("root", alias="DB_USER")
    password: str = Field("", alias="DB_PASSWORD")
    database: str = Field("mediafleet", alias="DB_NAME")
    charset: str = Field("utf8mb4", alias="DB_CHARSET")
    pool_size: int = Field(10, alias="DB_POOL_SIZE")
    max_overflow: int = Field(20, alias="DB_MAX_OVERFLOW")
    auto_init: bool = Field(True, alias="DB_AUTO_INIT")
    auto_migrate: bool = Field(True, alias="DB_AUTO_MIGRATE")
    init_create_schema: bool = Field(True, alias="DB_INIT_CREATE_SCHEMA")
    dm_mysql_compat: bool = Field(True, alias="DB_DM_MYSQL_COMPAT")

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        normalized = (v or "mysql").lower()
        if normalized not in ("mysql", "dm"):
            raise ValueError("DB_TYPE 必须是 mysql 或 dm")
        return normalized

    @field_validator("port")
    @classmethod
    def validate_port(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError("数据库端口必须在1-65535范围内")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class RedisSettings(BaseSettings):
    """Redis配置"""
    host: str = Field("localhost", alias="REDIS_HOST")
    port: int = Field(6379, alias="REDIS_PORT")
    password: str = Field("", alias="REDIS_PASSWORD")
    db: int = Field(0, alias="REDIS_DB")
    pool_size: int = Field(10, alias="REDIS_POOL_SIZE")

    @field_validator("port")
    @classmethod
    def validate_port(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError("Redis端口必须在1-65535范围内")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class RabbitMQSettings(BaseSettings):
    """RabbitMQ配置"""
    host: str = Field("localhost", alias="RABBITMQ_HOST")
    port: int = Field(5672, alias="RABBITMQ_PORT")
    username: str = Field("guest", alias="RABBITMQ_USER")
    password: str = Field("guest", alias="RABBITMQ_PASSWORD")
    vhost: str = Field("/", alias="RABBITMQ_VHOST")
    heartbeat: int = Field(600, alias="RABBITMQ_HEARTBEAT")
    blocked_connection_timeout: int = Field(300, alias="RABBITMQ_BLOCKED_TIMEOUT")
    max_tasks: int = Field(1, alias="RABBITMQ_MAX_TASKS")
    """录制结果扇出交换机名称"""
    record_result_exchange: str = Field("mediafleet.callback.fanout.exchange", alias="RABBITMQ_RECORD_RESULT_EXCHANGE")
    """录制结果消息TTL(毫秒) - 默认10小时"""
    record_result_ttl: int = Field(36000000, alias="RABBITMQ_RECORD_RESULT_TTL")
    worker_result_exchange: str = Field("mediafleet.worker.exchange", alias="RABBITMQ_WORKER_RESULT_EXCHANGE")
    worker_result_ttl: int = Field(36000000, alias="RABBITMQ_WORKER_RESULT_TTL")

    @field_validator("port")
    @classmethod
    def validate_port(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError("RabbitMQ端口必须在1-65535范围内")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class MinioSettings(BaseSettings):
    """MinIO配置"""
    endpoint: str = Field("localhost:9000", alias="MINIO_ENDPOINT")
    access_key: str = Field("", alias="MINIO_ACCESS_KEY")
    secret_key: str = Field("", alias="MINIO_SECRET_KEY")
    bucket_name: str = Field("mediafleet", alias="MINIO_BUCKET")
    secure: bool = Field(False, alias="MINIO_SECURE")
    region: str = Field("", alias="MINIO_REGION")
    callback_url: str = Field("", alias="MINIO_CALLBACK_URL")
    app_id: str = Field("", alias="MINIO_APPID")
    # 回调降级配置
    callback_fallback_enabled: bool = Field(False, alias="MINIO_CALLBACK_FALLBACK_ENABLED")
    callback_max_retries: int = Field(3, alias="MINIO_CALLBACK_MAX_RETRIES")
    callback_timeout: int = Field(45, alias="MINIO_CALLBACK_TIMEOUT")
    fileurl_head: str = Field("", alias="MINIO_FILEURL_HEAD")

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class CosSettings(BaseSettings):
    """腾讯云COS配置"""
    secret_id: str = Field("", alias="COS_SECRET_ID")
    secret_key: str = Field("", alias="COS_SECRET_KEY")
    region: str = Field("ap-guangzhou", alias="COS_REGION")
    bucket: str = Field("", alias="COS_BUCKET")
    domain: str = Field("", alias="COS_DOMAIN")
    callback_url: str = Field("", alias="COS_CALLBACK_URL")
    app_id: str = Field("", alias="COS_APPID")
    # 回调降级配置
    callback_fallback_enabled: bool = Field(False, alias="COS_CALLBACK_FALLBACK_ENABLED")
    callback_max_retries: int = Field(3, alias="COS_CALLBACK_MAX_RETRIES")
    callback_timeout: int = Field(45, alias="COS_CALLBACK_TIMEOUT")
    fileurl_head: str = Field("", alias="COS_FILEURL_HEAD")

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class StorageSettings(BaseSettings):
    """存储配置"""
    type: Literal["minio", "cos"] = Field("cos", alias="STORAGE_TYPE")
    local_path: str = Field("./data/media", alias="STORAGE_LOCAL_PATH")
    download_path: str = Field("./data/download", alias="STORAGE_DOWNLOAD_PATH")
    "音频存储类型"
    audio_type: Literal["minio", "cos"] = Field("cos", alias="STORAGE_AUDIO_TYPE")
    @field_validator("audio_type")
    @classmethod
    def validate_audio_type(cls, v):
        if v not in ["minio", "cos"]:
            raise ValueError("音频存储类型必须是 minio 或 cos")
        return v

    @field_validator("type")
    @classmethod
    def validate_storage_type(cls, v):
        if v not in ["minio", "cos"]:
            raise ValueError("存储类型必须是 minio 或 cos")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class ZlmSettings(BaseSettings):
    """ZLMediaKit配置"""
    api_url: str = Field("http://localhost:8080", alias="ZLM_API_URL")
    secret: str = Field("", alias="ZLM_SECRET")
    record_local_root: str = Field("", alias="ZLM_RECORD_LOCAL_ROOT")
    api_concurrency: int = Field(32, ge=1, le=100, alias="ZLM_API_CONCURRENCY")
    record_discovery_attempts: int = Field(
        3,
        ge=1,
        le=10,
        alias="ZLM_RECORD_DISCOVERY_ATTEMPTS",
    )
    record_discovery_interval: float = Field(
        5.0,
        ge=0,
        le=60,
        alias="ZLM_RECORD_DISCOVERY_INTERVAL",
    )
    stream_status_interval: float = Field(
        10.0,
        ge=1,
        le=300,
        alias="ZLM_STREAM_STATUS_INTERVAL",
    )
    stream_status_stale_grace: float = Field(
        30.0,
        ge=1,
        le=600,
        alias="ZLM_STREAM_STATUS_STALE_GRACE",
    )
    stream_status_log_interval: float = Field(
        60.0,
        ge=10,
        le=3600,
        alias="ZLM_STREAM_STATUS_LOG_INTERVAL",
    )
    record_status_concurrency: int = Field(
        8,
        ge=1,
        le=64,
        alias="ZLM_RECORD_STATUS_CONCURRENCY",
    )
    record_restart_interval: float = Field(
        30.0,
        ge=1,
        le=600,
        alias="ZLM_RECORD_RESTART_INTERVAL",
    )
    stream_absence_confirmations: int = Field(
        3,
        ge=1,
        le=100,
        alias="ZLM_STREAM_ABSENCE_CONFIRMATIONS",
    )

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class MasterSettings(BaseSettings):
    """主节点配置"""
    url: str = Field("http://localhost:8008", alias="MASTER_URL")
    registration_endpoint: str = Field("/api/v1/node/register", alias="MASTER_REGISTRATION_ENDPOINT")
    heartbeat_endpoint: str = Field("/api/v1/node/heartbeat", alias="MASTER_HEARTBEAT_ENDPOINT")
    heartbeat_interval: int = Field(30, alias="MASTER_HEARTBEAT_INTERVAL")

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class NodeManagerSettings(BaseSettings):
    """节点管理配置"""
    heartbeat_timeout: int = Field(60, alias="NODE_HEARTBEAT_TIMEOUT")
    health_check_interval: int = Field(30, alias="NODE_HEALTH_CHECK_INTERVAL")
    registration_endpoint: str = Field("/api/v1/node/register", alias="NODE_REGISTRATION_ENDPOINT")
    heartbeat_endpoint: str = Field("/api/v1/node/heartbeat", alias="NODE_HEARTBEAT_ENDPOINT")

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class MediaSettings(BaseSettings):
    """媒体处理配置"""
    ffmpeg_path: str = Field("ffmpeg", alias="MEDIA_FFMPEG_PATH")
    ffprobe_path: str = Field("ffprobe", alias="MEDIA_FFPROBE_PATH")
    temp_dir: str = Field("./data/temp", alias="MEDIA_TEMP_DIR")
    max_upload_size: int = Field(1024 * 1024 * 100, alias="MEDIA_MAX_UPLOAD_SIZE")  # 100MB
    # 添加媒体识别并发限制配置
    recog_concurrency: int = Field(2, alias="MEDIA_RECOG_CONCURRENCY")  # 默认并发数为2

    @field_validator("recog_concurrency")
    @classmethod
    def validate_recog_concurrency(cls, v):
        if v < 1:
            raise ValueError("媒体识别并发数必须大于0")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class ASRSettings(BaseSettings):
    """ASR 后端选择配置"""
    provider: str = Field("funasr", alias="ASR_PROVIDER")

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v):
        normalized = (v or "funasr").strip().lower()
        if normalized in ("glm_asr", "glm-asr", "glm_asr_nano", "glm-asr-nano"):
            return "glm"
        if normalized not in ("funasr", "glm"):
            raise ValueError("ASR_PROVIDER 仅支持 funasr 或 glm")
        return normalized

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class FunASRSettings(BaseSettings):
    """FunASR 离线识别配置"""
    model_type: Literal["paraformer", "sensevoice"] = Field(
        "paraformer", alias="FUNASR_MODEL_TYPE"
    )
    device: str = Field("cpu", alias="FUNASR_DEVICE")
    # ASR 是 media-worker 的核心能力。默认在服务启动阶段加载模型，避免第一条
    # 业务任务承担模型下载和初始化耗时；资源受限的特殊部署可显式设为 false。
    preload: bool = Field(True, alias="FUNASR_PRELOAD")
    disable_update: bool = Field(True, alias="FUNASR_DISABLE_UPDATE")
    ncpu: int = Field(4, alias="FUNASR_NCPU")

    # GPU 并发默认设为 1，避免多路任务同时抢同一张卡导致总耗时变长。
    gpu_concurrency: int = Field(1, alias="FUNASR_GPU_CONCURRENCY")
    cpu_concurrency: int = Field(2, alias="FUNASR_CPU_CONCURRENCY")

    batch_size_s: int = Field(60, alias="FUNASR_BATCH_SIZE_S")
    merge_vad: bool = Field(True, alias="FUNASR_MERGE_VAD")
    merge_length_s: int = Field(15, alias="FUNASR_MERGE_LENGTH_S")
    vad_max_segment_ms: int = Field(30000, alias="FUNASR_VAD_MAX_SEGMENT_MS")

    paraformer_zh_model: str = Field(
        "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        alias="FUNASR_PARAFORMER_ZH_MODEL",
    )
    paraformer_en_model: str = Field(
        "iic/speech_paraformer_asr-en-16k-vocab4199-pytorch",
        alias="FUNASR_PARAFORMER_EN_MODEL",
    )
    vad_model: str = Field(
        "damo/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        alias="FUNASR_VAD_MODEL",
    )
    punc_model: str = Field(
        "damo/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        alias="FUNASR_PUNC_MODEL",
    )
    spk_model: str = Field(
        "damo/speech_campplus_sv_zh-cn_16k-common",
        alias="FUNASR_SPK_MODEL",
    )

    sensevoice_model: str = Field("iic/SenseVoiceSmall", alias="FUNASR_SENSEVOICE_MODEL")
    sensevoice_language: str = Field("auto", alias="FUNASR_SENSEVOICE_LANGUAGE")
    sensevoice_use_itn: bool = Field(True, alias="FUNASR_SENSEVOICE_USE_ITN")
    sensevoice_output_timestamp: bool = Field(
        True, alias="FUNASR_SENSEVOICE_OUTPUT_TIMESTAMP"
    )
    sensevoice_trust_remote_code: bool = Field(
        True, alias="FUNASR_SENSEVOICE_TRUST_REMOTE_CODE"
    )

    @field_validator("device")
    @classmethod
    def validate_device(cls, v):
        if not v:
            return "cpu"
        normalized = v.strip().lower()
        if normalized == "auto":
            return normalized
        if normalized == "cpu" or normalized.startswith(("cuda", "mps", "xpu")):
            return normalized
        raise ValueError("FUNASR_DEVICE 仅支持 cpu、auto、cuda、mps 或 xpu")

    @field_validator(
        "gpu_concurrency",
        "cpu_concurrency",
        "ncpu",
        "batch_size_s",
        "merge_length_s",
        "vad_max_segment_ms",
    )
    @classmethod
    def validate_positive_int(cls, v):
        if v < 1:
            raise ValueError("FunASR 数值配置必须大于0")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class GLMASRSettings(BaseSettings):
    """GLM-ASR-Nano 离线识别配置"""
    model: str = Field(
        "/root/.cache/modelscope/hub/models/ZhipuAI/GLM-ASR-Nano-2512",
        alias="GLM_ASR_MODEL",
    )
    device: str = Field("cuda:0", alias="GLM_ASR_DEVICE")
    preload: bool = Field(True, alias="GLM_ASR_PRELOAD")
    vad_model: str = Field("fsmn-vad", alias="GLM_ASR_VAD_MODEL")
    batch_size: int = Field(32, alias="GLM_ASR_BATCH_SIZE")
    max_gap_ms: int = Field(700, alias="GLM_ASR_MAX_GAP_MS")
    max_segment_ms: int = Field(30000, alias="GLM_ASR_MAX_SEGMENT_MS")
    min_segment_ms: int = Field(800, alias="GLM_ASR_MIN_SEGMENT_MS")
    recognition_context_ms: int = Field(0, alias="GLM_ASR_RECOGNITION_CONTEXT_MS")
    min_recognition_ms: int = Field(0, alias="GLM_ASR_MIN_RECOGNITION_MS")
    max_new_tokens: int = Field(128, alias="GLM_ASR_MAX_NEW_TOKENS")
    no_repeat_ngram_size: int = Field(0, alias="GLM_ASR_NO_REPEAT_NGRAM_SIZE")
    repetition_penalty: float = Field(1.0, alias="GLM_ASR_REPETITION_PENALTY")
    length_penalty: float = Field(1.0, alias="GLM_ASR_LENGTH_PENALTY")
    prompt: str = Field("请将音频转写成简体中文文本。", alias="GLM_ASR_PROMPT")
    keep_chunks: bool = Field(False, alias="GLM_ASR_KEEP_CHUNKS")
    gpu_concurrency: int = Field(1, alias="GLM_ASR_GPU_CONCURRENCY")
    cpu_concurrency: int = Field(1, alias="GLM_ASR_CPU_CONCURRENCY")

    @field_validator("device")
    @classmethod
    def validate_device(cls, v):
        if not v:
            return "cpu"
        normalized = v.strip().lower()
        if normalized == "cpu" or normalized.startswith(("cuda", "mps", "xpu")):
            return normalized
        raise ValueError("GLM_ASR_DEVICE 仅支持 cpu、cuda、mps 或 xpu")

    @field_validator(
        "batch_size",
        "max_gap_ms",
        "max_segment_ms",
        "min_segment_ms",
        "max_new_tokens",
        "gpu_concurrency",
        "cpu_concurrency",
    )
    @classmethod
    def validate_positive_int(cls, v):
        if v < 1:
            raise ValueError("GLM-ASR 数值配置必须大于0")
        return v

    @field_validator(
        "recognition_context_ms",
        "min_recognition_ms",
        "no_repeat_ngram_size",
    )
    @classmethod
    def validate_non_negative_int(cls, v):
        if v < 0:
            raise ValueError("GLM-ASR 非负数值配置不能小于0")
        return v

    @field_validator("repetition_penalty", "length_penalty")
    @classmethod
    def validate_positive_float(cls, v):
        if v <= 0:
            raise ValueError("GLM-ASR penalty 配置必须大于0")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class ServiceSettings(BaseSettings):
    """服务配置"""
    host: str = Field("127.0.0.1", alias="SERVICE_HOST")
    port: int = Field(8008, alias="SERVICE_PORT")
    debug: bool = Field(False, alias="SERVICE_DEBUG")
    log_level: str = Field("INFO", alias="SERVICE_LOG_LEVEL")
    log_dir: str = Field("logs", alias="SERVICE_LOG_DIR")

    @field_validator("port")
    @classmethod
    def validate_port(cls, v):
        if not 1 <= v <= 65535:
            raise ValueError("服务端口必须在1-65535范围内")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class QueueSettings(BaseSettings):
    """队列配置"""
    type: Literal["memory", "redis"] = Field("memory", alias="QUEUE_TYPE")

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class TaskCleanupSettings(BaseSettings):
    """任务清理配置"""
    enabled: bool = Field(True, alias="TASK_CLEANUP_ENABLED")
    interval_hours: int = Field(6, alias="TASK_CLEANUP_INTERVAL_HOURS")
    keep_hours: int = Field(48, alias="TASK_CLEANUP_KEEP_HOURS")
    memory_cleanup_interval_minutes: int = Field(30, alias="TASK_CLEANUP_MEMORY_INTERVAL_MINUTES")

    @field_validator("interval_hours")
    @classmethod
    def validate_interval_hours(cls, v):
        if v < 1:
            raise ValueError("清理间隔必须大于0小时")
        return v

    @field_validator("keep_hours")
    @classmethod
    def validate_keep_hours(cls, v):
        if v < 1:
            raise ValueError("保留时间必须大于0小时")
        return v

    @field_validator("memory_cleanup_interval_minutes")
    @classmethod
    def validate_memory_cleanup_interval(cls, v):
        if v < 1:
            raise ValueError("内存清理间隔必须大于0分钟")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class RecordCleanupSettings(BaseSettings):
    """录制残留文件清理配置"""
    enabled: bool = Field(True, alias="RECORD_CLEANUP_ENABLED")
    scan_hour: int = Field(2, alias="RECORD_CLEANUP_SCAN_HOUR")
    keep_days: int = Field(3, alias="RECORD_CLEANUP_KEEP_DAYS")
    record_base_path: str = Field(
        "/srv/mediafleet/www/record",
        alias="RECORD_CLEANUP_BASE_PATH"
    )
    dry_run: bool = Field(False, alias="RECORD_CLEANUP_DRY_RUN")

    @field_validator("scan_hour")
    @classmethod
    def validate_scan_hour(cls, v):
        if not 0 <= v <= 23:
            raise ValueError("扫描时间必须在0-23之间（整点小时）")
        return v

    @field_validator("keep_days")
    @classmethod
    def validate_keep_days(cls, v):
        if v < 1:
            raise ValueError("保留天数必须大于0")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class TaskSettings(BaseSettings):
    """任务配置"""
    queue_name: str = Field("stream_media_tasks", alias="TASK_QUEUE_NAME")
    concurrency: int = Field(4, alias="TASK_CONCURRENCY")
    timeout: int = Field(3600, alias="TASK_TIMEOUT")
    cache_size: int = Field(1024, alias="TASK_CACHE_SIZE")
    queue_size: int = Field(1000, alias="TASK_QUEUE_SIZE")

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class MonitoringSettings(BaseSettings):
    """监控配置"""
    enabled: bool = Field(True, alias="MONITORING_ENABLED")
    metrics_port: int = Field(9090, alias="MONITORING_METRICS_PORT")
    collect_interval: int = Field(10, alias="MONITORING_COLLECT_INTERVAL")  # 秒
    retention_days: int = Field(7, alias="MONITORING_RETENTION_DAYS")

    # 资源监控阈值配置
    cpu_warning_threshold: float = Field(80.0, alias="MONITORING_CPU_WARNING")
    cpu_critical_threshold: float = Field(90.0, alias="MONITORING_CPU_CRITICAL")
    memory_warning_threshold: float = Field(80.0, alias="MONITORING_MEMORY_WARNING")
    memory_critical_threshold: float = Field(90.0, alias="MONITORING_MEMORY_CRITICAL")
    disk_warning_threshold: float = Field(80.0, alias="MONITORING_DISK_WARNING")
    disk_critical_threshold: float = Field(90.0, alias="MONITORING_DISK_CRITICAL")

    @field_validator("metrics_port")
    @classmethod
    def validate_metrics_port(cls, v):
        if not 1024 <= v <= 65535:
            raise ValueError("监控指标端口必须在1024-65535范围内")
        return v

    @field_validator("collect_interval")
    @classmethod
    def validate_collect_interval(cls, v):
        if v < 1:
            raise ValueError("监控收集间隔必须大于0秒")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class AlertSettings(BaseSettings):
    """告警配置"""
    enabled: bool = Field(True, alias="ALERT_ENABLED")
    callback_url: Optional[str] = Field(None, alias="ALERT_CALLBACK_URL")
    resource_callback_url: Optional[str] = Field(None, alias="ALERT_RESOURCE_CALLBACK_URL")
    max_retries: int = Field(3, alias="ALERT_MAX_RETRIES")
    retry_interval: int = Field(5, alias="ALERT_RETRY_INTERVAL")  # 秒
    timeout: int = Field(10, alias="ALERT_TIMEOUT")  # 秒

    # 告警冷却时间，避免频繁告警（分钟）
    cooldown_minutes: int = Field(30, alias="ALERT_COOLDOWN_MINUTES")

    @field_validator("max_retries")
    @classmethod
    def validate_max_retries(cls, v):
        if v < 0:
            raise ValueError("最大重试次数不能小于0")
        return v

    @field_validator("retry_interval")
    @classmethod
    def validate_retry_interval(cls, v):
        if v < 1:
            raise ValueError("重试间隔必须大于0秒")
        return v

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, v):
        if v < 1:
            raise ValueError("告警超时时间必须大于0秒")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class PostProcessingSettings(BaseSettings):
    """后处理队列配置"""
    max_workers: int = Field(3, alias="POST_PROCESSING_MAX_WORKERS")
    media_concurrency: int = Field(
        2,
        ge=1,
        le=10,
        alias="POST_PROCESSING_MEDIA_CONCURRENCY",
    )
    idle_strategy_enabled: bool = Field(
        False,
        alias="POST_PROCESSING_IDLE_STRATEGY_ENABLED",
        description="是否按本机当前录制压力动态降低后处理并发",
    )
    busy_recording_threshold: int = Field(
        50,
        ge=1,
        alias="POST_PROCESSING_BUSY_RECORDING_THRESHOLD",
        description="达到该录制路数后启用忙碌时后处理并发",
    )
    busy_media_concurrency: int = Field(
        1,
        ge=0,
        le=10,
        alias="POST_PROCESSING_BUSY_MEDIA_CONCURRENCY",
        description="动态计算后的繁忙并发下限，0表示不额外设置固定下限",
    )
    busy_media_percent: int = Field(
        50,
        ge=1,
        le=100,
        alias="POST_PROCESSING_BUSY_MEDIA_PERCENT",
        description="录制达到节点上限时保留的重IO和FFmpeg后处理并发百分比",
    )
    idle_strategy_poll_interval_seconds: float = Field(
        1,
        gt=0,
        le=60,
        alias="POST_PROCESSING_IDLE_STRATEGY_POLL_INTERVAL_SECONDS",
        description="闲时策略重新检查当前录制数的间隔秒数",
    )
    db_save_max_attempts: int = Field(
        5,
        ge=1,
        le=20,
        alias="POST_PROCESSING_DB_SAVE_MAX_ATTEMPTS",
        description="数据库保存阶段发生瞬时连接错误时的最大尝试次数",
    )
    db_save_retry_delay_seconds: float = Field(
        5.0,
        ge=0,
        le=300,
        alias="POST_PROCESSING_DB_SAVE_RETRY_DELAY_SECONDS",
        description="数据库保存阶段首次重试等待秒数，后续指数退避且最多60秒",
    )
    failed_auto_retry_enabled: bool = Field(
        True,
        alias="POST_PROCESSING_FAILED_AUTO_RETRY_ENABLED",
        description="数据库保存阶段任务内重试耗尽后是否持久化等待并自动重新入队",
    )
    failed_auto_retry_max_attempts: int = Field(
        3,
        ge=0,
        le=20,
        alias="POST_PROCESSING_FAILED_AUTO_RETRY_MAX_ATTEMPTS",
        description="后处理完整尾链路自动重新入队最大次数，0表示不进行跨任务自动重试",
    )
    failed_auto_retry_initial_delay_seconds: float = Field(
        60.0,
        ge=0,
        le=3600,
        alias="POST_PROCESSING_FAILED_AUTO_RETRY_INITIAL_DELAY_SECONDS",
        description="后处理第一次持久化自动重试等待秒数",
    )
    failed_auto_retry_max_delay_seconds: float = Field(
        900.0,
        ge=0,
        le=86400,
        alias="POST_PROCESSING_FAILED_AUTO_RETRY_MAX_DELAY_SECONDS",
        description="后处理持久化自动重试单次最大等待秒数",
    )
    failed_auto_retry_scan_interval_seconds: float = Field(
        30.0,
        gt=0,
        le=3600,
        alias="POST_PROCESSING_FAILED_AUTO_RETRY_SCAN_INTERVAL_SECONDS",
        description="recorder-node 扫描到期自动重试任务的间隔秒数",
    )

    # 任务优先级配置
    priority_high: int = Field(10, alias="POST_PROCESSING_PRIORITY_HIGH")
    priority_normal: int = Field(5, alias="POST_PROCESSING_PRIORITY_NORMAL")
    priority_low: int = Field(1, alias="POST_PROCESSING_PRIORITY_LOW")

    # 各阶段超时配置（秒）
    timeout_video_info: int = Field(60, alias="POST_PROCESSING_TIMEOUT_VIDEO_INFO")
    timeout_audio_extract: int = Field(300, alias="POST_PROCESSING_TIMEOUT_AUDIO_EXTRACT")
    timeout_cover_extract: int = Field(120, alias="POST_PROCESSING_TIMEOUT_COVER_EXTRACT")
    timeout_video_upload: int = Field(600, alias="POST_PROCESSING_TIMEOUT_VIDEO_UPLOAD")
    timeout_audio_upload: int = Field(300, alias="POST_PROCESSING_TIMEOUT_AUDIO_UPLOAD")
    timeout_cover_upload: int = Field(60, alias="POST_PROCESSING_TIMEOUT_COVER_UPLOAD")

    @field_validator("max_workers")
    @classmethod
    def validate_max_workers(cls, v):
        if v < 1:
            raise ValueError("后处理最大worker数必须大于0")
        if v > 10:
            raise ValueError("后处理最大worker数不建议超过10")
        return v

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="ignore"
    )

class Settings(BaseSettings):
    """应用配置类 - 只从环境变量加载配置"""

    # 应用信息
    app_name: str = Field("流媒体处理服务", alias="APP_NAME")
    app_version: str = Field("1.0.0", alias="APP_VERSION")

    # 环境标识
    env: str = Field("development", alias="APP_ENV")

    # API设置
    api_prefix: str = Field("/api/v1", alias="API_PREFIX")

    # API密钥
    API_KEY: str = Field("", alias="API_KEY")

    # 节点类型
    node_type: Literal["master", "worker"] = Field("master", alias="NODE_TYPE")

    # 兼容性属性 - 保持向后兼容
    ZLM_URL: str = Field("http://localhost:8080", alias="ZLM_URL")
    ZLM_SECRET: str = Field("", alias="ZLM_SECRET")

    USE_REDIS_QUEUE: bool = Field(False, alias="USE_REDIS_QUEUE")
    REDIS_HOST: str = Field("localhost", alias="REDIS_HOST")
    REDIS_PORT: int = Field(6379, alias="REDIS_PORT")
    REDIS_PASSWORD: Optional[str] = Field(None, alias="REDIS_PASSWORD")
    REDIS_DB: int = Field(0, alias="REDIS_DB")

    DEBUG: bool = Field(True, alias="DEBUG")
    SERVICE_NAME: str = Field("流媒体处理服务", alias="SERVICE_NAME")

    # 数据库配置 - 直接从环境变量加载
    DB_TYPE: str = Field("mysql", alias="DB_TYPE")
    DB_HOST: str = Field("localhost", alias="DB_HOST")
    DB_PORT: int = Field(3306, alias="DB_PORT")
    DB_USER: str = Field("root", alias="DB_USER")
    DB_PASSWORD: str = Field("", alias="DB_PASSWORD")
    DB_NAME: str = Field("mediafleet", alias="DB_NAME")
    DB_URL: str = Field("", alias="DB_URL")
    DB_AUTO_INIT: bool = Field(True, alias="DB_AUTO_INIT")
    DB_AUTO_MIGRATE: bool = Field(True, alias="DB_AUTO_MIGRATE")
    DB_INIT_CREATE_SCHEMA: bool = Field(True, alias="DB_INIT_CREATE_SCHEMA")
    DB_DM_MYSQL_COMPAT: bool = Field(True, alias="DB_DM_MYSQL_COMPAT")

    # 封面提取配置
    USE_OPENCV_EXTRACTOR: bool = Field(True, alias="USE_OPENCV_EXTRACTOR")
    COVER_EXTRACTION_CONCURRENCY: int = Field(10, alias="COVER_EXTRACTION_CONCURRENCY")
    OPENCV_THREAD_POOL_SIZE: int = Field(4, alias="OPENCV_THREAD_POOL_SIZE")

    # 腾讯云COS配置 - 兼容性
    COS_SECRET_ID: str = Field("", alias="COS_SECRET_ID")
    COS_SECRET_KEY: str = Field("", alias="COS_SECRET_KEY")
    COS_REGION: str = Field("ap-guangzhou", alias="COS_REGION")
    COS_BUCKET_NAME: str = Field("", alias="COS_BUCKET")
    COS_APPID: str = Field("", alias="COS_APPID")
    COS_CALLBACK_URL: str = Field("", alias="COS_CALLBACK_URL")





    # 流程轨迹服务地址（为空则禁用上报）
    flow_trace_base_url: str = Field("", alias="FLOW_TRACE_BASE_URL")

    # 配置验证
    @field_validator("node_type")
    @classmethod
    def validate_node_type(cls, v):
        if v not in ["master", "worker"]:
            raise ValueError("节点类型必须是 master 或 worker")
        return v

    @field_validator("env", mode="before")
    @classmethod
    def validate_env(cls, v):
        normalized = str(v).strip().lower()
        if normalized not in ["development", "testing", "production"]:
            logger.warning(
                "未知的环境类型: %s, 建议使用: development, testing, production",
                normalized,
            )
        return normalized

    @field_validator("DB_URL", mode="before")
    @classmethod
    def generate_db_url(cls, v, info):
        """根据基本配置生成数据库URL"""
        if v:
            return v

        from media_platform.infrastructure.database.url import (
            build_db_url,
            default_db_port,
        )

        data = info.data if hasattr(info, 'data') else {}
        db_type = (data.get("DB_TYPE") or "mysql").lower()
        host = data.get("DB_HOST", "localhost")
        port = data.get("DB_PORT") or default_db_port(db_type)
        user = data.get("DB_USER", "root")
        password = data.get("DB_PASSWORD", "")
        db_name = data.get("DB_NAME", "mediafleet")
        charset = data.get("DB_CHARSET", "utf8mb4")

        return build_db_url(db_type, user, password, host, port, db_name, charset)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 初始化嵌套配置对象
        self._init_nested_settings()
        # 同步嵌套配置到兼容性属性
        self._sync_compatibility_settings()

    def _init_nested_settings(self):
        """初始化嵌套配置对象，手动传递环境变量"""
        # 创建嵌套配置对象，手动传递环境变量以确保正确读取
        self.service = ServiceSettings()
        self.database = DatabaseSettings()
        self.redis = RedisSettings()
        self.rabbitmq = RabbitMQSettings()
        self.storage = StorageSettings()
        self.minio = MinioSettings()

        # COS配置需要特别处理，手动传递环境变量
        self.cos = CosSettings()
        #     secret_id=os.getenv('COS_SECRET_ID', ''),
        #     secret_key=os.getenv('COS_SECRET_KEY', ''),
        #     region=os.getenv('COS_REGION', 'ap-guangzhou'),
        #     bucket=os.getenv('COS_BUCKET', ''),
        #     domain=os.getenv('COS_DOMAIN', '')
        # )

        self.zlm = ZlmSettings()
        self.node_manager = NodeManagerSettings()
        self.queue = QueueSettings()
        self.task = TaskSettings()
        self.task_cleanup = TaskCleanupSettings()
        self.master = MasterSettings()
        self.media = MediaSettings()
        self.asr = ASRSettings()
        self.funasr = FunASRSettings()
        self.glm_asr = GLMASRSettings()

        # 初始化监控和告警配置
        self.monitoring = MonitoringSettings()
        self.alert = AlertSettings()

        # 初始化后处理配置
        self.post_processing = PostProcessingSettings()

        # 初始化录制文件清理配置
        self.record_cleanup = RecordCleanupSettings()

    def _sync_compatibility_settings(self):
        """同步嵌套配置到顶级兼容性属性"""
        # ZLM配置同步 - 优先使用主配置中的值
        if hasattr(self, 'ZLM_URL') and self.ZLM_URL != "http://localhost:8080":
            # 如果主配置中的ZLM_URL不是默认值，则使用主配置的值
            pass
        else:
            self.ZLM_URL = self.zlm.api_url

        if hasattr(self, 'ZLM_SECRET') and self.ZLM_SECRET != "":
            # 如果主配置中的ZLM_SECRET不是默认值，则使用主配置的值
            pass
        else:
            self.ZLM_SECRET = self.zlm.secret

        # Redis配置同步 - 优先使用主配置中的值
        self.USE_REDIS_QUEUE = (self.queue.type == "redis")

        if hasattr(self, 'REDIS_HOST') and self.REDIS_HOST != "localhost":
            # 如果主配置中的REDIS_HOST不是默认值，则使用主配置的值
            pass
        else:
            self.REDIS_HOST = self.redis.host

        if hasattr(self, 'REDIS_PORT') and self.REDIS_PORT != 6379:
            # 如果主配置中的REDIS_PORT不是默认值，则使用主配置的值
            pass
        else:
            self.REDIS_PORT = self.redis.port

        if hasattr(self, 'REDIS_PASSWORD') and self.REDIS_PASSWORD is not None:
            # 如果主配置中的REDIS_PASSWORD不是默认值，则使用主配置的值
            pass
        else:
            self.REDIS_PASSWORD = self.redis.password if self.redis.password else None

        if hasattr(self, 'REDIS_DB') and self.REDIS_DB != 0:
            # 如果主配置中的REDIS_DB不是默认值，则使用主配置的值
            pass
        else:
            self.REDIS_DB = self.redis.db

        # 服务配置同步
        self.DEBUG = self.service.debug
        self.SERVICE_NAME = self.app_name

        # 数据库配置同步 - 优先使用主配置中的值
        if hasattr(self, 'DB_TYPE') and self.DB_TYPE != "mysql":
            self.database.type = self.DB_TYPE
        else:
            self.DB_TYPE = self.database.type

        if hasattr(self, 'DB_AUTO_INIT'):
            self.database.auto_init = self.DB_AUTO_INIT
        else:
            self.DB_AUTO_INIT = self.database.auto_init

        if hasattr(self, 'DB_AUTO_MIGRATE'):
            self.database.auto_migrate = self.DB_AUTO_MIGRATE
        else:
            self.DB_AUTO_MIGRATE = self.database.auto_migrate

        if hasattr(self, 'DB_INIT_CREATE_SCHEMA'):
            self.database.init_create_schema = self.DB_INIT_CREATE_SCHEMA
        else:
            self.DB_INIT_CREATE_SCHEMA = self.database.init_create_schema

        if hasattr(self, 'DB_DM_MYSQL_COMPAT'):
            self.database.dm_mysql_compat = self.DB_DM_MYSQL_COMPAT
        else:
            self.DB_DM_MYSQL_COMPAT = self.database.dm_mysql_compat

        if hasattr(self, 'DB_HOST') and self.DB_HOST != "localhost":
            # 如果主配置中的DB_HOST不是默认值，则使用主配置的值，并同步到嵌套配置
            self.database.host = self.DB_HOST
        else:
            self.DB_HOST = self.database.host

        if hasattr(self, 'DB_PORT') and self.DB_PORT != 3306:
            # 如果主配置中的DB_PORT不是默认值，则使用主配置的值，并同步到嵌套配置
            self.database.port = self.DB_PORT
        else:
            self.DB_PORT = self.database.port

        if hasattr(self, 'DB_USER') and self.DB_USER != "root":
            # 如果主配置中的DB_USER不是默认值，则使用主配置的值，并同步到嵌套配置
            self.database.username = self.DB_USER
        else:
            self.DB_USER = self.database.username

        if hasattr(self, 'DB_PASSWORD') and self.DB_PASSWORD != "":
            # 如果主配置中的DB_PASSWORD不是默认值，则使用主配置的值，并同步到嵌套配置
            self.database.password = self.DB_PASSWORD
        else:
            self.DB_PASSWORD = self.database.password

        if hasattr(self, 'DB_NAME') and self.DB_NAME != "mediafleet":
            # 如果主配置中的DB_NAME不是默认值，则使用主配置的值，并同步到嵌套配置
            self.database.database = self.DB_NAME
        else:
            self.DB_NAME = self.database.database

        # COS配置同步 - 优先使用主配置中的值
        if hasattr(self, 'COS_SECRET_ID') and self.COS_SECRET_ID != "":
            # 如果主配置中的COS_SECRET_ID不是默认值，则使用主配置的值
            pass
        else:
            self.COS_SECRET_ID = self.cos.secret_id

        if hasattr(self, 'COS_SECRET_KEY') and self.COS_SECRET_KEY != "":
            # 如果主配置中的COS_SECRET_KEY不是默认值，则使用主配置的值
            pass
        else:
            self.COS_SECRET_KEY = self.cos.secret_key

        if hasattr(self, 'COS_REGION') and self.COS_REGION != "ap-guangzhou":
            # 如果主配置中的COS_REGION不是默认值，则使用主配置的值
            pass
        else:
            self.COS_REGION = self.cos.region

        if hasattr(self, 'COS_BUCKET_NAME') and self.COS_BUCKET_NAME != "mediafleet":
            # 如果主配置中的COS_BUCKET_NAME不是默认值，则使用主配置的值
            pass
        else:
            self.COS_BUCKET_NAME = self.cos.bucket

        # 重新生成数据库URL
        from media_platform.infrastructure.database.url import build_db_url

        if not os.getenv("DB_URL"):
            self.DB_URL = build_db_url(
                self.DB_TYPE,
                self.DB_USER,
                self.DB_PASSWORD,
                self.DB_HOST,
                self.DB_PORT,
                self.DB_NAME,
                self.database.charset,
            )

    def check_required_settings(self):
        """检查必要的配置项"""
        # 检查ZLM配置
        if not self.zlm.api_url or not self.zlm.secret:
            logger.warning("警告: ZLMediaKit配置不完整")

        # 根据存储类型检查对应配置
        if self.storage.type == "minio":
            if not self.minio.endpoint or not self.minio.access_key:
                logger.warning("警告: MinIO配置不完整")
        elif self.storage.type == "cos":
            if not self.cos.secret_id or not self.cos.bucket:
                logger.warning("警告: 腾讯云COS配置不完整")

        # 检查数据库配置
        if not self.database.host or not self.database.username:
            logger.warning("警告: 数据库配置不完整")

    model_config = ConfigDict(
        env_file=".env" if should_use_env_file() else None,
        case_sensitive=True,
        extra="allow"
    )

@lru_cache()
def get_settings() -> Settings:
    """
    获取应用配置，只从环境变量加载
    支持的环境变量格式：
    1. 直接映射：API_KEY, DB_HOST 等
    2. 嵌套格式：DATABASE__HOST, REDIS__PORT 等
    """
    logger.info("开始加载配置（仅从环境变量）")

    # 记录加载的环境变量及其值
    loaded_env_vars = []
    env_values = {}
    for key, value in os.environ.items():
        if key.upper() in [
            "APP_NAME", "APP_VERSION", "APP_ENV", "NODE_TYPE", "API_KEY",
            "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME",
            "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB",
            "ZLM_API_URL", "ZLM_SECRET", "STORAGE_TYPE",
            "COS_SECRET_ID", "COS_SECRET_KEY", "COS_REGION", "COS_BUCKET_NAME",
            "COS_FILEURL_HEAD",
            "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_BUCKET",
            "MINIO_FILEURL_HEAD",
            "SERVICE_HOST", "SERVICE_PORT", "SERVICE_DEBUG", "SERVICE_LOG_LEVEL",
            "CONTROL_CENTER_INSTANCE_ID",
            "TASK_DISPATCH_BATCH_SIZE", "TASK_DISPATCH_POLL_INTERVAL_SECONDS",
            "TASK_DISPATCH_LOCK_TIMEOUT_SECONDS", "TASK_DISPATCH_ERROR_BACKOFF_SECONDS",
            "CONTROL_CENTER_EVENT_CONSUMER_ENABLED", "CONTROL_CENTER_EVENT_QUEUE_NAME",
            "CONTROL_CENTER_EVENT_ROUTING_KEYS", "CONTROL_CENTER_EVENT_PREFETCH_COUNT",
            "CONTROL_CENTER_EVENT_RETRY_DELAY_MILLISECONDS",
            "CONTROL_CENTER_EVENT_MAX_ATTEMPTS",
            "CONTROL_CENTER_EVENT_RECONNECT_DELAY_SECONDS",
            "CONTROL_CENTER_CALLBACK_TIMEOUT_SECONDS",
            "MEDIA_WORKER_CALLBACK_TIMEOUT_SECONDS",
            "MEDIA_WORKER_CALLBACK_MAX_RETRIES",
            "MEDIA_WORKER_CALLBACK_RETRY_INTERVAL_SECONDS",
            "ASR_PROVIDER", "MEDIA_RECOG_CONCURRENCY", "YOLO_MODEL_PATH",
            "FUNASR_MODEL_TYPE", "FUNASR_DEVICE", "FUNASR_PRELOAD",
            "FUNASR_DISABLE_UPDATE", "FUNASR_NCPU",
            "FUNASR_GPU_CONCURRENCY", "FUNASR_CPU_CONCURRENCY",
            "FUNASR_BATCH_SIZE_S", "FUNASR_VAD_MAX_SEGMENT_MS",
            "FUNASR_MERGE_VAD", "FUNASR_MERGE_LENGTH_S",
            "FUNASR_PARAFORMER_ZH_MODEL", "FUNASR_PARAFORMER_EN_MODEL",
            "FUNASR_VAD_MODEL", "FUNASR_PUNC_MODEL", "FUNASR_SPK_MODEL",
            "FUNASR_SENSEVOICE_MODEL", "FUNASR_SENSEVOICE_LANGUAGE",
            "FUNASR_SENSEVOICE_USE_ITN", "FUNASR_SENSEVOICE_OUTPUT_TIMESTAMP",
            "FUNASR_SENSEVOICE_TRUST_REMOTE_CODE",
            "GLM_ASR_MODEL", "GLM_ASR_DEVICE", "GLM_ASR_PRELOAD",
            "GLM_ASR_VAD_MODEL", "GLM_ASR_BATCH_SIZE",
            "GLM_ASR_MAX_GAP_MS", "GLM_ASR_MAX_SEGMENT_MS",
            "GLM_ASR_MIN_SEGMENT_MS", "GLM_ASR_RECOGNITION_CONTEXT_MS",
            "GLM_ASR_MIN_RECOGNITION_MS", "GLM_ASR_MAX_NEW_TOKENS",
            "GLM_ASR_NO_REPEAT_NGRAM_SIZE", "GLM_ASR_REPETITION_PENALTY",
            "GLM_ASR_LENGTH_PENALTY", "GLM_ASR_PROMPT",
            "GLM_ASR_KEEP_CHUNKS", "GLM_ASR_GPU_CONCURRENCY",
            "GLM_ASR_CPU_CONCURRENCY"
        ] or "__" in key:  # 嵌套环境变量
            loaded_env_vars.append(key)
            env_values[key] = value

    if loaded_env_vars:
        logger.info(f"从环境变量加载的配置项: {', '.join(loaded_env_vars)}")
        # 记录关键环境变量的值
        for key in ["APP_NAME", "DB_HOST", "DB_PORT"]:
            if key in env_values:
                logger.info(f"环境变量 {key} = {env_values[key]}")
    else:
        logger.info("未找到相关环境变量，使用默认配置")

    # 创建设置实例，Pydantic会自动从环境变量加载
    settings = Settings()

    # 记录实际加载后的值
    logger.info(f"Settings实例创建后的值: app_name={settings.app_name}, DB_HOST={settings.DB_HOST}")
    logger.info(f"嵌套配置值: database.host={settings.database.host}")

    # 验证配置
    settings.check_required_settings()

    logger.info(f"配置加载完成: env={settings.env}, node_type={settings.node_type}, storage_type={settings.storage.type}")

    return settings

# 保持向后兼容的单例接口
_settings_instance = None

def get_settings_singleton() -> Settings:
    """获取设置单例 (用于兼容原有代码)"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = get_settings()
    return _settings_instance
