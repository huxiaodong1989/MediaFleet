"""媒体处理基础设施。

本包放置可被录制节点和通用媒体 Worker 复用的媒体处理基础能力，例如封面生成
和 OpenCV 帧提取。调用中心不应导入本包执行 FFmpeg、OpenCV 或模型推理。

这里使用懒加载，避免导入包名时提前加载 OpenCV、FFmpeg 相关实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from media_platform.infrastructure.media_processing.audio_info import GetAudioInfo
from media_platform.infrastructure.media_processing.cover_info import GetCoverInfo
from media_platform.infrastructure.media_processing.cover_types import (
    CoverExtractStrategy,
    CoverTextConfig,
)
from media_platform.infrastructure.media_processing.filler_video_generator import (
    FillerVideoGenerator,
)
from media_platform.infrastructure.media_processing.ts_merge_helper import (
    merge_videos_ts_strategy,
)
from media_platform.infrastructure.media_processing.video_info import GetVideoInfo

if TYPE_CHECKING:
    from media_platform.infrastructure.media_processing.cover_extractor import (
        CoverExtractor,
    )
    from media_platform.infrastructure.media_processing.opencv_cover_extractor import (
        OpenCVCoverExtractor,
    )

__all__ = [
    "CoverExtractor",
    "CoverExtractStrategy",
    "CoverTextConfig",
    "FillerVideoGenerator",
    "GetAudioInfo",
    "GetCoverInfo",
    "GetVideoInfo",
    "OpenCVCoverExtractor",
    "merge_videos_ts_strategy",
]


def __getattr__(name: str):
    """按需导入重依赖实现，减少服务启动副作用。"""

    if name == "CoverExtractor":
        from media_platform.infrastructure.media_processing.cover_extractor import (
            CoverExtractor,
        )

        return CoverExtractor

    if name == "OpenCVCoverExtractor":
        from media_platform.infrastructure.media_processing.opencv_cover_extractor import (
            OpenCVCoverExtractor,
        )

        return OpenCVCoverExtractor

    raise AttributeError(name)
