"""视频水印任务适配器。

本模块复用同目录下 ``VideoProcess.video_set_watermark_url``，只负责新旧任务参数兼容、
日志脱敏和结果标准化。旧算法仍负责下载视频、调用 FFmpeg 添加文字水印、上传结果
和清理临时文件。
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


class VideoWatermarkProcessor:
    """给视频添加文字水印并上传处理后的视频。"""

    task_types = ("video.watermark", "video_set_watermark")

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
        """输出用于排障的媒体地址，去掉可能包含签名或密钥的查询串。"""

        try:
            parts = urlsplit(value)
        except ValueError:
            return "<invalid-url>"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @staticmethod
    def _parse_params(message: TaskDispatchMessage) -> tuple[str, str]:
        """兼容新旧任务参数并返回视频地址和文字水印。"""

        params = message.params
        ex_params = params.get("ex_params") or {}
        if not isinstance(ex_params, dict):
            raise TaskPermanentError("params.ex_params 必须是JSON对象")

        nested_params = params.get("params") or {}
        if not isinstance(nested_params, dict):
            raise TaskPermanentError("params.params 必须是JSON对象")

        video_url = (
            params.get("video_url")
            or params.get("media_url")
            or params.get("file_url")
            or params.get("mp4_url")
            or params.get("mp4Url")
        )
        if not isinstance(video_url, str) or not video_url.strip():
            raise TaskPermanentError("视频水印任务缺少video_url")

        watermark = (
            params.get("watermark")
            or params.get("watermark_text")
            or params.get("text")
            or nested_params.get("watermark")
            or ex_params.get("watermark")
        )
        if not isinstance(watermark, str) or not watermark.strip():
            raise TaskPermanentError("视频水印任务缺少watermark")

        return video_url.strip(), watermark.strip()

    async def _process_async(
        self,
        video_url: str,
        watermark: str,
    ) -> ProcessorResult:
        """调用旧异步算法，并在同一事件循环中释放其 HTTP 客户端。"""

        processor = self._create_processor()
        try:
            watermark_result = await processor.video_set_watermark_url(
                video_url,
                watermark,
            )
            result_url = getattr(watermark_result, "file_url", None)
            if result_url is None and isinstance(watermark_result, dict):
                result_url = (
                    watermark_result.get("file_url")
                    or watermark_result.get("video_url")
                    or watermark_result.get("result_url")
                )
            if not result_url:
                raise RuntimeError("视频水印处理结果缺少file_url")

            return ProcessorResult(
                payload={
                    "video_url": video_url,
                    "watermark": watermark,
                    "result_url": str(result_url),
                },
                artifacts=(
                    {
                        "file_type": "VIDEO",
                        "file_url": str(result_url),
                    },
                ),
            )
        finally:
            http_client = getattr(processor, "http_client", None)
            close = getattr(http_client, "aclose", None)
            if callable(close):
                await close()

    def process(self, message: TaskDispatchMessage) -> ProcessorResult:
        """同步执行水印任务，适配 pika 的阻塞式消费线程。"""

        video_url, watermark = self._parse_params(message)
        LOGGER.info(
            "开始视频水印处理: task_id=%s, media_url=%s",
            message.task_id,
            self._safe_media_url(video_url),
        )
        result = asyncio.run(self._process_async(video_url, watermark))
        LOGGER.info(
            "视频水印处理完成: task_id=%s, result_url=%s",
            message.task_id,
            self._safe_media_url(str(result.payload.get("result_url", ""))),
        )
        return result


__all__ = ["VideoWatermarkProcessor"]
