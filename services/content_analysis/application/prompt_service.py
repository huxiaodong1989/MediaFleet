"""兼容导入：提示词管理已归入共享数据库控制面。"""

from media_platform.application.content_prompt_service import (
    PromptBundleView,
    PromptManagementService,
)

__all__ = ["PromptBundleView", "PromptManagementService"]
