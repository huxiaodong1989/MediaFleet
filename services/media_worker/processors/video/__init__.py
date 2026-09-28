"""视频处理器。"""

from services.media_worker.processors.video.clip_extract import (
    VideoClipExtractProcessor,
)
from services.media_worker.processors.video.cover_extract import (
    VideoCoverExtractProcessor,
)
from services.media_worker.processors.video.frame_extract import (
    VideoFrameExtractProcessor,
)
from services.media_worker.processors.video.watermark import (
    VideoWatermarkProcessor,
)

__all__ = [
    "VideoClipExtractProcessor",
    "VideoCoverExtractProcessor",
    "VideoFrameExtractProcessor",
    "VideoWatermarkProcessor",
]
