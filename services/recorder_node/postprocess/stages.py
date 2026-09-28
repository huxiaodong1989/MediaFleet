"""录制后处理阶段定义。

该模块只描述 recorder-node 本机后处理队列的阶段顺序、中文名称和关键失败阶段。
它不执行 FFmpeg、对象存储、数据库或回调操作，便于后处理管理器和阶段执行器共享
同一份阶段定义，避免多个文件各自维护顺序。
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable


class PostProcessStage(str, Enum):
    """录制后处理阶段。

    阶段值是日志、状态接口和历史任务排障使用的稳定技术标识，不随中文说明变化。
    """

    RECORDING_PREPARE = "recording_prepare"  # 等待收尾、发现分片并必要时合并
    VIDEO_INFO = "video_info"          # 获取视频信息
    AUDIO_EXTRACT = "audio_extract"    # 音频提取
    COVER_EXTRACT = "cover_extract"    # 封面提取
    VIDEO_UPLOAD = "video_upload"      # 视频上传
    AUDIO_UPLOAD = "audio_upload"      # 音频上传
    COVER_UPLOAD = "cover_upload"      # 封面上传
    DB_SAVE = "db_save"                # 数据库保存
    CALLBACK = "callback"              # 回调通知
    CLEANUP = "cleanup"                # 删除任务原片和本地派生文件


DEFAULT_POST_PROCESS_STAGES: tuple[PostProcessStage, ...] = (
    PostProcessStage.VIDEO_INFO,
    PostProcessStage.AUDIO_EXTRACT,
    PostProcessStage.COVER_EXTRACT,
    PostProcessStage.VIDEO_UPLOAD,
    PostProcessStage.AUDIO_UPLOAD,
    PostProcessStage.COVER_UPLOAD,
    PostProcessStage.DB_SAVE,
    PostProcessStage.CALLBACK,
    PostProcessStage.CLEANUP,
)
"""默认后处理路线。

顺序必须保持为“本地读取/提取 -> 对象存储上传 -> 产物落库 -> 结果通知 -> 本地清理”。
调用方未显式指定阶段时使用该路线。
"""


STAGE_LABELS: dict[PostProcessStage, str] = {
    PostProcessStage.RECORDING_PREPARE: "发现并准备录像文件",
    PostProcessStage.VIDEO_INFO: "读取视频信息",
    PostProcessStage.AUDIO_EXTRACT: "提取音频",
    PostProcessStage.COVER_EXTRACT: "提取封面",
    PostProcessStage.VIDEO_UPLOAD: "上传视频",
    PostProcessStage.AUDIO_UPLOAD: "上传音频",
    PostProcessStage.COVER_UPLOAD: "上传封面",
    PostProcessStage.DB_SAVE: "保存产物到数据库",
    PostProcessStage.CALLBACK: "发送结果通知和回调",
    PostProcessStage.CLEANUP: "删除任务原片和本地派生文件",
}


CRITICAL_FAILURE_STAGES: frozenset[PostProcessStage] = frozenset(
    {
        PostProcessStage.RECORDING_PREPARE,
        PostProcessStage.VIDEO_UPLOAD,
        PostProcessStage.DB_SAVE,
    }
)
"""关键阶段集合。

这些阶段失败后不能继续伪装成功：必须终止后处理、写失败状态并发送失败通知。
"""


def stage_label(stage: PostProcessStage) -> str:
    """返回阶段中文名称，用于日志和本机状态接口。"""

    return STAGE_LABELS.get(stage, stage.value)


def format_stage_list(stages: Iterable[PostProcessStage]) -> str:
    """格式化阶段链路，便于提交任务时一次性打印完整后处理路线。"""

    return " -> ".join(f"{stage.value}({stage_label(stage)})" for stage in stages)
