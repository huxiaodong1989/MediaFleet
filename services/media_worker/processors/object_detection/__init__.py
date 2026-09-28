"""目标检测处理器。"""

from services.media_worker.processors.object_detection.yolo_detection import (
    ObjectDetectImageProcessor,
    ObjectTrackVideoProcessor,
)

__all__ = ["ObjectDetectImageProcessor", "ObjectTrackVideoProcessor"]
