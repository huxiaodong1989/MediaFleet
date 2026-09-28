"""历史存储服务兼容出口。

真实 COS/MinIO 实现已迁移到当前包。旧业务代码如仍通过该兼容出口导入，也会拿到
同一份平台层实现；本地存储不再作为业务结果存储能力导出。
"""

from media_platform.infrastructure.storage.enhanced_storage_service import (
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
    "MinioStorageService",
    "StorageService",
    "UploadResult",
    "get_enhanced_storage_service",
    "get_storage_service",
]
