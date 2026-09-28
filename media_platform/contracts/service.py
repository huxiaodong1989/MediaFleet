"""服务入口元信息契约。"""

from datetime import datetime
from enum import Enum

from pydantic import Field

from media_platform.contracts.base import ContractModel, utc_now


class ServiceRole(str, Enum):
    CONTROL_CENTER = "control_center"
    RECORDER_NODE = "recorder_node"
    MEDIA_WORKER = "media_worker"
    CONTENT_ANALYSIS = "content_analysis"


class HealthStatus(str, Enum):
    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"


class ServiceInfo(ContractModel):
    name: str = Field(min_length=1)
    role: ServiceRole
    version: str = Field(min_length=1)
    description: str
    compatibility_entrypoint: str | None = None


class ServiceHealth(ContractModel):
    service: str = Field(min_length=1)
    role: ServiceRole
    version: str = Field(min_length=1)
    status: HealthStatus
    checked_at: datetime = Field(default_factory=utc_now)
