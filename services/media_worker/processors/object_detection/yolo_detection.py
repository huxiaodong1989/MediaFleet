"""目标检测任务适配器。

本模块复用同目录下 ``ObjectDetection`` 算法，负责把新 ``TaskDispatchMessage`` 契约
转换成算法需要的图片/视频地址、目标类别和临时输出路径。YOLO 模型和对象存储
客户端只在真正执行任务时加载，避免 Worker 启动或单元测试阶段产生重依赖副作用。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.registry import ProcessorResult


LOGGER = logging.getLogger(__name__)


class _ObjectDetectionBaseProcessor:
    """对象检测处理器公共能力。"""

    def __init__(self, detector_factory: Callable[[], Any] | None = None) -> None:
        self._detector_factory = detector_factory

    def _create_detector(self):
        """创建目标检测器；生产依赖在任务执行阶段才加载。"""

        if self._detector_factory is not None:
            return self._detector_factory()

        from services.media_worker.processors.object_detection.yolo import ObjectDetection

        return ObjectDetection()

    @staticmethod
    def _safe_media_url(value: str) -> str:
        """输出用于日志排障的媒体地址，去掉查询串中的签名或密钥。"""

        try:
            parts = urlsplit(value)
        except ValueError:
            return "<invalid-url>"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @staticmethod
    def _json_object(value: Any, field_name: str) -> dict[str, Any]:
        """读取嵌套 JSON 对象；缺省按空对象处理。"""

        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TaskPermanentError(f"{field_name} 必须是JSON对象")
        return value

    @classmethod
    def _target_classes(cls, params: dict[str, Any]) -> list[str] | None:
        """兼容新旧目标类别参数。

        新任务推荐使用 ``params.target_classes``；旧任务常放在
        ``params.ex_params.target_classes``。为了便于 Swagger 手工测试，也支持
        逗号分隔字符串，例如 ``person,car``。
        """

        ex_params = cls._json_object(params.get("ex_params"), "params.ex_params")
        value = (
            params.get("target_classes")
            if "target_classes" in params
            else params.get("classes")
        )
        if value is None:
            value = ex_params.get("target_classes", ex_params.get("classes"))

        if value is None:
            return None
        if isinstance(value, str):
            classes = [item.strip() for item in value.split(",") if item.strip()]
            return classes or None
        if not isinstance(value, list):
            raise TaskPermanentError("target_classes 必须是字符串数组或逗号分隔字符串")

        classes: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise TaskPermanentError(f"target_classes[{index}] 必须是非空字符串")
            classes.append(item.strip())
        return classes or None

    @staticmethod
    def _output_path(task_id: str, prefix: str) -> str:
        """生成本次任务独占的临时输出图片路径，避免并发任务互相覆盖。"""

        output_dir = Path("output") / "object_detection"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        safe_task_id = "".join(
            char if char.isalnum() or char in {"-", "_"} else "_"
            for char in task_id
        )
        return str(output_dir / f"{prefix}_{safe_task_id}_{timestamp}.png")


class ObjectDetectImageProcessor(_ObjectDetectionBaseProcessor):
    """图片目标检测处理器。"""

    task_types = ("object.detect.image", "object_detection.detect", "obj_detect_img")

    @classmethod
    def _parse_params(
        cls, message: TaskDispatchMessage
    ) -> tuple[str, list[str] | None]:
        """读取图片地址和目标类别参数。"""

        params = message.params
        image_url = (
            params.get("image_url")
            or params.get("img_url")
            or params.get("media_url")
            or params.get("file_url")
        )
        if not isinstance(image_url, str) or not image_url.strip():
            raise TaskPermanentError("图片目标检测任务缺少image_url")
        return image_url.strip(), cls._target_classes(params)

    async def _process_async(
        self,
        *,
        task_id: str,
        image_url: str,
        target_classes: list[str] | None,
    ) -> ProcessorResult:
        """调用异步图片检测算法并标准化返回值。"""

        detector = self._create_detector()
        result_url = await detector.object_detection(
            image_url,
            output_img_path=self._output_path(task_id, "detection"),
            target_classes=target_classes,
        )
        if not result_url:
            raise RuntimeError("图片目标检测未返回结果地址")

        return ProcessorResult(
            payload={
                "image_url": image_url,
                "detect_result_url": str(result_url),
                "target_classes": target_classes,
            },
            artifacts=(
                {
                    "file_type": "IMAGE",
                    "file_url": str(result_url),
                },
            ),
        )

    def process(self, message: TaskDispatchMessage) -> ProcessorResult:
        """同步执行图片目标检测任务，适配 pika 阻塞式消费线程。"""

        image_url, target_classes = self._parse_params(message)
        LOGGER.info(
            "开始图片目标检测: task_id=%s, image_url=%s, target_classes=%s",
            message.task_id,
            self._safe_media_url(image_url),
            target_classes or "ALL",
        )
        result = asyncio.run(
            self._process_async(
                task_id=message.task_id,
                image_url=image_url,
                target_classes=target_classes,
            )
        )
        LOGGER.info(
            "图片目标检测完成: task_id=%s, result_url=%s",
            message.task_id,
            self._safe_media_url(str(result.payload.get("detect_result_url", ""))),
        )
        return result


class ObjectTrackVideoProcessor(_ObjectDetectionBaseProcessor):
    """视频目标轨迹图处理器。"""

    task_types = ("object.track.video", "object_detection.track", "obj_track_v_to_img")

    @classmethod
    def _parse_params(
        cls, message: TaskDispatchMessage
    ) -> tuple[str, list[str] | None]:
        """读取视频地址和目标类别参数。"""

        params = message.params
        video_url = (
            params.get("video_url")
            or params.get("media_url")
            or params.get("file_url")
        )
        if not isinstance(video_url, str) or not video_url.strip():
            raise TaskPermanentError("视频目标轨迹任务缺少video_url")
        return video_url.strip(), cls._target_classes(params)

    async def _process_async(
        self,
        *,
        task_id: str,
        video_url: str,
        target_classes: list[str] | None,
    ) -> ProcessorResult:
        """调用异步视频轨迹算法并标准化返回值。"""

        detector = self._create_detector()
        result_url = await detector.generate_trajectory_video_to_img(
            video_url,
            output_img_path=self._output_path(task_id, "trajectory"),
            target_classes=target_classes,
        )
        if not result_url:
            raise RuntimeError("视频目标轨迹图未返回结果地址")

        return ProcessorResult(
            payload={
                "video_url": video_url,
                "trajectory_image_url": str(result_url),
                "target_classes": target_classes,
            },
            artifacts=(
                {
                    "file_type": "IMAGE",
                    "file_url": str(result_url),
                },
            ),
        )

    def process(self, message: TaskDispatchMessage) -> ProcessorResult:
        """同步执行视频目标轨迹图任务，适配 pika 阻塞式消费线程。"""

        video_url, target_classes = self._parse_params(message)
        LOGGER.info(
            "开始视频目标轨迹图生成: task_id=%s, video_url=%s, target_classes=%s",
            message.task_id,
            self._safe_media_url(video_url),
            target_classes or "ALL",
        )
        result = asyncio.run(
            self._process_async(
                task_id=message.task_id,
                video_url=video_url,
                target_classes=target_classes,
            )
        )
        LOGGER.info(
            "视频目标轨迹图生成完成: task_id=%s, result_url=%s",
            message.task_id,
            self._safe_media_url(
                str(result.payload.get("trajectory_image_url", ""))
            ),
        )
        return result


__all__ = ["ObjectDetectImageProcessor", "ObjectTrackVideoProcessor"]
