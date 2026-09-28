"""媒体文件领域兼容模型。"""

from media_platform.domain.media_file.legacy_models import (
    MediaFile,
    MediaFileBase,
    MediaFileCreate,
    MediaFileUpdate,
)

__all__ = [
    "MediaFile",
    "MediaFileBase",
    "MediaFileCreate",
    "MediaFileUpdate",
]
