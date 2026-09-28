"""录制节点 HTTP API。

这里只暴露录制节点本机职责相关的接口，例如后处理队列状态等。
调用中心 API 和通用媒体 Worker API 不应放入本包。
"""

from services.recorder_node.api.post_processing import router as post_processing_router

__all__ = ["post_processing_router"]
