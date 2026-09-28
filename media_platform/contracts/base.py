"""公共消息信封。"""

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """返回带时区的 UTC 时间。"""

    return datetime.now(timezone.utc)


def new_message_id() -> str:
    """生成跨服务唯一消息编号。"""

    return uuid4().hex


class ContractModel(BaseModel):
    """公共契约模型基类，拒绝未声明字段以尽早发现协议漂移。"""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=True,
    )


class MessageEnvelope(ContractModel):
    """RabbitMQ 消息的公共信封字段。"""

    schema_version: str = Field(default="1.0", min_length=1)
    message_id: str = Field(default_factory=new_message_id, min_length=1)
    trace_id: str | None = None
    source: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    idempotency_key: str | None = None
