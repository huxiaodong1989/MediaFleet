"""媒体流粘性绑定领域对象。

调用中心只负责为 RTC 分配或复用 ZLMediaKit 所在录制节点，并把资源与节点、
`app`、`stream_id` 的关系持久化到 MySQL。摄像头拉流、断流和桌面推流仍由 RTC
或桌面客户端直接面向 ZLMediaKit 执行。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from media_platform.domain.node import MediaNodeView


class StreamResourceType(str, Enum):
    """需要绑定到录制节点的 RTC 资源类型。"""

    CAMERA = "CAMERA"
    DESKTOP = "DESKTOP"


class StreamMode(str, Enum):
    """ZLMediaKit 流接入方式。"""

    PULL = "PULL"
    PUSH = "PUSH"


class StreamBindingStatus(str, Enum):
    """流绑定状态。"""

    ACTIVE = "ACTIVE"
    MIGRATING = "MIGRATING"
    RELEASED = "RELEASED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class MediaStreamBindingCommand:
    """创建或获取媒体流绑定的应用命令。"""

    school_code: str
    resource_type: StreamResourceType
    space_id: str | None
    app: str
    stream_id: str
    stream_name: str | None = None
    created_by: str = "rtc-service"


@dataclass(frozen=True)
class MediaStreamBindingResult:
    """媒体流绑定结果。"""

    binding_id: str
    created: bool
    school_code: str
    resource_type: StreamResourceType
    space_id: str | None
    node_id: str
    node: MediaNodeView | None
    app: str
    stream_id: str
    stream_name: str | None
    stream_mode: StreamMode
    status: StreamBindingStatus
    binding_version: int
    affinity_matched: bool = False
    allocation_reason: str = "EXISTING_BINDING"


class StreamBindingConflictError(ValueError):
    """请求与已有绑定或流标识冲突。"""


class StreamBindingNodeUnavailableError(RuntimeError):
    """当前没有可承载新绑定的健康录制节点。"""


__all__ = [
    "MediaStreamBindingCommand",
    "MediaStreamBindingResult",
    "StreamBindingConflictError",
    "StreamBindingNodeUnavailableError",
    "StreamBindingStatus",
    "StreamMode",
    "StreamResourceType",
]
