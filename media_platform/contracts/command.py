"""录制节点定向命令契约。"""

from enum import Enum
from typing import Any

from pydantic import Field

from media_platform.contracts.base import MessageEnvelope


class RecorderCommandType(str, Enum):
    """录制节点首批命令类型。

    摄像头拉流和断流由 RTC 在取得调度中心返回的绑定/ZL 地址后直接调用
    ZLMediaKit API 完成，不进入本命令通道。本通道只承载必须路由到录像文件
    所在节点执行的录像、本地后处理和安全清理命令。
    """

    RECORD_START = "record.start"
    RECORD_STOP = "record.stop"
    RECORD_POSTPROCESS = "record.postprocess"
    RECORD_DELETE = "record.delete"


class RecorderCommandMessage(MessageEnvelope):
    """必须路由到指定录制节点的命令。"""

    source: str = "control_center"
    task_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    command: RecorderCommandType
    params: dict[str, Any] = Field(default_factory=dict)
