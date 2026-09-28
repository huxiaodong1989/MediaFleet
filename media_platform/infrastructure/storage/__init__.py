"""COS 和 MinIO/S3 兼容对象存储实现。"""

from media_platform.infrastructure.storage.enhanced_storage_service import (
    EnhancedCosStorageService,
    EnhancedMinioStorageService,
    StorageServiceEnhanced,
    UploadResult,
    get_enhanced_storage_service,
)
from media_platform.infrastructure.storage.storage_service import (
    CosStorageService,
    MinioStorageService,
    StorageService,
    get_storage_service,
)

__all__ = [
    "CosStorageService",
    "EnhancedCosStorageService",
    "EnhancedMinioStorageService",
    "MinioStorageService",
    "StorageService",
    "StorageServiceEnhanced",
    "UploadResult",
    "get_enhanced_storage_service",
    "get_storage_service",
]
