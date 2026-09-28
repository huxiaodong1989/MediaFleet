"""目标检测处理器的新旧参数兼容测试。"""

import pytest

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.application.runtime import register_default_processors
from services.media_worker.processors.object_detection import (
    ObjectDetectImageProcessor,
    ObjectTrackVideoProcessor,
)
from services.media_worker.registry import TaskProcessorRegistry


class FakeObjectDetection:
    """模拟旧 YOLO 检测器，不加载模型、不连接对象存储。"""

    def __init__(self):
        self.detect_calls = []
        self.track_calls = []

    async def object_detection(
        self,
        img_url,
        output_img_path="output/detection.png",
        target_classes=None,
    ):
        self.detect_calls.append((img_url, output_img_path, target_classes))
        return "https://files.example/detection.png"

    async def generate_trajectory_video_to_img(
        self,
        video_url,
        output_img_path="output/trajectory.png",
        target_classes=None,
    ):
        self.track_calls.append((video_url, output_img_path, target_classes))
        return "https://files.example/trajectory.png"


def _message(task_type, params):
    return TaskDispatchMessage(
        task_id="task-1",
        school_code="school-1",
        task_type=task_type,
        routing_key=task_type,
        params=params,
    )


def test_image_detection_accepts_direct_image_url_and_target_classes():
    fake = FakeObjectDetection()
    processor = ObjectDetectImageProcessor(lambda: fake)

    result = processor.process(
        _message(
            "object.detect.image",
            {
                "image_url": "https://files.example/image.jpg?signature=secret",
                "target_classes": ["person", "car"],
            },
        )
    )

    assert fake.detect_calls[0][0].startswith("https://files.example/image.jpg")
    assert fake.detect_calls[0][1].startswith("output")
    assert fake.detect_calls[0][2] == ["person", "car"]
    assert result.payload["detect_result_url"].endswith("detection.png")
    assert result.artifacts[0]["file_type"] == "IMAGE"


def test_image_detection_accepts_legacy_img_url_and_ex_params():
    fake = FakeObjectDetection()
    processor = ObjectDetectImageProcessor(lambda: fake)

    processor.process(
        _message(
            "obj_detect_img",
            {
                "img_url": "https://files.example/image.jpg",
                "ex_params": {"target_classes": "person, car"},
            },
        )
    )

    assert fake.detect_calls[0][2] == ["person", "car"]


def test_video_tracking_accepts_legacy_media_url_and_classes():
    fake = FakeObjectDetection()
    processor = ObjectTrackVideoProcessor(lambda: fake)

    result = processor.process(
        _message(
            "obj_track_v_to_img",
            {
                "media_url": "https://files.example/video.mp4?signature=secret",
                "classes": ["person"],
            },
        )
    )

    assert fake.track_calls[0][0].startswith("https://files.example/video.mp4")
    assert fake.track_calls[0][2] == ["person"]
    assert result.payload["trajectory_image_url"].endswith("trajectory.png")
    assert result.artifacts[0]["file_type"] == "IMAGE"


def test_image_detection_rejects_missing_url_before_loading_detector():
    processor = ObjectDetectImageProcessor(
        lambda: pytest.fail("参数无效时不应创建YOLO检测器")
    )

    with pytest.raises(TaskPermanentError, match="缺少image_url"):
        processor.process(_message("object.detect.image", {"target_classes": []}))


def test_video_tracking_rejects_invalid_target_classes():
    processor = ObjectTrackVideoProcessor(
        lambda: pytest.fail("参数无效时不应创建YOLO检测器")
    )

    with pytest.raises(TaskPermanentError, match="target_classes\\[0\\]"):
        processor.process(
            _message(
                "object.track.video",
                {
                    "video_url": "https://files.example/video.mp4",
                    "target_classes": [""],
                },
            )
        )


def test_default_processors_include_object_detection_tasks():
    registry = TaskProcessorRegistry()

    register_default_processors(registry)

    assert "object.detect.image" in registry.task_types
    assert "object_detection.detect" in registry.task_types
    assert "obj_detect_img" in registry.task_types
    assert "object.track.video" in registry.task_types
    assert "object_detection.track" in registry.task_types
    assert "obj_track_v_to_img" in registry.task_types


def test_object_detection_core_keeps_legacy_import_path():
    """目标检测真实实现归属 media-worker 新目录。"""

    from services.media_worker.processors.object_detection.yolo import (
        ObjectDetection,
    )

    assert ObjectDetection.__module__.startswith(
        "services.media_worker.processors.object_detection"
    )
