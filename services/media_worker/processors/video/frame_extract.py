"""视频截图任务适配器。

该处理器迁移旧 ``node/main.py`` 的 ``video_extract_imgs`` 分支，负责把任务契约
参数转换为 ``VideoProcess.get_video_imgs_url`` 需要的入参，并把提取出的图片
统一写入任务结果和媒体产物列表。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.registry import ProcessorResult


LOGGER = logging.getLogger(__name__)


class VideoFrameExtractProcessor:
    """从离线视频按固定时间间隔提取截图并上传对象存储。"""

    task_types = ("video.frames.extract", "video_extract_imgs")

    def __init__(self, processor_factory: Callable[[], Any] | None = None) -> None:
        self._processor_factory = processor_factory

    def _create_processor(self):
        """创建视频处理器；生产依赖在真正执行任务时才加载。"""

        if self._processor_factory is not None:
            return self._processor_factory()

        from services.media_worker.processors.video.video_process import VideoProcess

        return VideoProcess()

    @staticmethod
    def _safe_media_url(value: str) -> str:
        """输出脱敏媒体地址，避免日志打印签名查询串。"""

        try:
            parts = urlsplit(value)
        except ValueError:
            return "<invalid-url>"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @staticmethod
    def _normalize_interval(value: Any) -> int:
        """清理截图间隔，旧接口未传时默认 5 秒。"""

        if value is None:
            return 5
        try:
            interval = int(value)
        except (TypeError, ValueError) as exc:
            raise TaskPermanentError("interval 必须是正整数") from exc
        if interval <= 0:
            raise TaskPermanentError("interval 必须是正整数")
        return interval

    @classmethod
    def _parse_params(cls, message: TaskDispatchMessage) -> tuple[str, int]:
        """兼容新旧任务参数，返回视频地址和截图间隔。"""

        params = message.params
        ex_params = params.get("ex_params") or {}
        if not isinstance(ex_params, dict):
            raise TaskPermanentError("params.ex_params 必须是JSON对象")

        video_url = (
            params.get("video_url")
            or params.get("media_url")
            or params.get("file_url")
            or params.get("mp4_url")
            or params.get("mp4Url")
        )
        if not isinstance(video_url, str) or not video_url.strip():
            raise TaskPermanentError("视频截图任务缺少video_url")

        interval_value = (
            params["interval"] if "interval" in params else ex_params.get("interval")
        )
        interval = cls._normalize_interval(interval_value)
        return video_url.strip(), interval

    @staticmethod
    def _artifact_items(images: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
        """把旧算法返回的图片列表转换为媒体产物描述。"""

        artifacts: list[dict[str, Any]] = []
        for image in images:
            if not isinstance(image, dict):
                continue
            image_url = image.get("url") or image.get("file_url")
            if not image_url:
                continue
            artifacts.append(
                {
                    "file_type": "COVER",
                    "file_url": str(image_url),
                    "extra": {"time": image.get("time")},
                }
            )
        return tuple(artifacts)

    async def _process_async(
        self,
        video_url: str,
        interval: int,
    ) -> ProcessorResult:
        """调用视频截图算法，并释放其 HTTP 客户端。"""

        processor = self._create_processor()
        try:
            images = await processor.get_video_imgs_url(video_url, interval)
            if not isinstance(images, list):
                raise RuntimeError("视频截图任务未返回图片列表")

            return ProcessorResult(
                payload={
                    "video_url": video_url,
                    "interval": interval,
                    "imgs": images,
                },
                artifacts=self._artifact_items(images),
            )
        finally:
            http_client = getattr(processor, "http_client", None)
            close = getattr(http_client, "aclose", None)
            if callable(close):
                await close()

    def process(self, message: TaskDispatchMessage) -> ProcessorResult:
        """同步执行视频截图任务，适配 pika 阻塞式消费线程。"""

        video_url, interval = self._parse_params(message)
        LOGGER.info(
            "开始视频截图: task_id=%s, media_url=%s, interval=%s",
            message.task_id,
            self._safe_media_url(video_url),
            interval,
        )
        result = asyncio.run(self._process_async(video_url, interval))
        LOGGER.info(
            "视频截图完成: task_id=%s, image_count=%s",
            message.task_id,
            len(result.payload.get("imgs") or []),
        )
        return result


__all__ = ["VideoFrameExtractProcessor"]
