"""SDK 响应模型公共类型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


UtcDateTime = Annotated[datetime, AfterValidator(_as_utc)]


class SDKModel(BaseModel):
    """允许服务端增加字段，同时保持已知字段的类型校验。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


T = TypeVar("T")


class Page(SDKModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


__all__ = ["Page", "SDKModel", "UtcDateTime"]
