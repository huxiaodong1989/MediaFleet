"""运行配置白名单。新增服务注册 Schema，不开放任意环境变量写入。"""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    schema_version: int = Field(default=1, ge=1, le=1)


class ModelConnectionConfig(RuntimeConfig):
    base_url: str = Field(
        min_length=1,
        max_length=2048,
        title="模型 API 地址",
        json_schema_extra={"format": "uri"},
    )
    allowed_models: list[str] = Field(min_length=1, max_length=128, title="允许使用的模型")
    timeout_seconds: float = Field(default=180, ge=1, le=1800, title="请求超时（秒）")

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise ValueError("模型地址格式无效") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in value)
            or (port is not None and port < 1)
        ):
            raise ValueError("模型地址须为不含账号、查询参数或片段的HTTP(S)基础地址")
        if parsed.path.rstrip("/").endswith("/chat/completions"):
            raise ValueError("请填写基础地址，不包含 /chat/completions")
        return value.rstrip("/")

    @field_validator("allowed_models")
    @classmethod
    def validate_models(cls, values):
        if any(
            not v.strip() or len(v) > 128 or any(ord(c) < 32 for c in v) for v in values
        ):
            raise ValueError("模型名称无效")
        return sorted(set(v.strip() for v in values))


class ImagePerformanceConfig(RuntimeConfig):
    max_concurrent_classrooms: int = Field(
        default=4, strict=True, ge=1, le=128, title="课堂并发 / 实例"
    )
    ffmpeg_concurrency: int = Field(
        default=4, strict=True, ge=1, le=128, title="抽帧并发 / 实例"
    )
    download_concurrency: int = Field(
        default=1, strict=True, ge=1, le=32, title="下载并发 / 实例"
    )
    upload_concurrency: int = Field(
        default=4, strict=True, ge=1, le=128, title="上传并发 / 实例"
    )
    model_concurrency: int = Field(
        default=4, strict=True, ge=1, le=128, title="模型并发 / 实例"
    )


class ContentPerformanceConfig(RuntimeConfig):
    task_concurrency: int = Field(
        default=1, strict=True, ge=1, le=32, title="评课任务并发 / 实例"
    )
    model_concurrency: int = Field(
        default=1, strict=True, ge=1, le=32, title="模型并发 / 实例"
    )


# 首版作用域固定 GLOBAL；不同硬件部署组可以在同一表扩展，实例ID不是配置身份。
CONFIG_SCHEMAS = {
    ("image-analysis", "model_connection"): ModelConnectionConfig,
    ("content-analysis", "model_connection"): ModelConnectionConfig,
    ("image-analysis", "performance"): ImagePerformanceConfig,
    ("content-analysis", "performance"): ContentPerformanceConfig,
}
SERVICE_NAMES = {"image-analysis": "图像分析", "content-analysis": "内容分析"}
SERVICE_NODE_TYPES = {
    "image-analysis": "IMAGE_ANALYSIS",
    "content-analysis": "CONTENT_ANALYSIS",
}
CATEGORY_NAMES = {"model_connection": "模型连接", "performance": "并发性能"}


def config_schema(service: str, category: str):
    try:
        return CONFIG_SCHEMAS[(service, category)]
    except KeyError:
        raise ValueError("该服务配置尚未支持热更新") from None
