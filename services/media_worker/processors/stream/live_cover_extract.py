"""直播流封面提取任务适配器。"""

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


class LiveCoverExtractProcessor:
    """从 RTMP/RTSP/HTTP-FLV 等直播流提取一张封面并上传对象存储。"""

    task_types = ("stream.cover.extract", "live_extract_cover")

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
    def _parse_params(message: TaskDispatchMessage) -> tuple[str, str, dict]:
        """兼容新旧直播封面参数。"""

        params = message.params
        ex_params = params.get("ex_params") or {}
        if not isinstance(ex_params, dict):
            raise TaskPermanentError("params.ex_params 必须是JSON对象")

        stream_url = (
            params.get("stream_url")
            or params.get("video_url")
            or params.get("media_url")
            or params.get("file_url")
        )
        if not isinstance(stream_url, str) or not stream_url.strip():
            raise TaskPermanentError("直播封面任务缺少stream_url")

        cover_strategy = (
            params.get("cover_strategy")
            or ex_params.get("cover_strategy")
            or "timestamp"
        )
        if not isinstance(cover_strategy, str) or not cover_strategy.strip():
            raise TaskPermanentError("cover_strategy 必须是非空字符串")

        cover_info = params.get("cover_info")
        if cover_info is None:
            cover_info = ex_params.get("cover_info") or {}
        if not isinstance(cover_info, dict):
            raise TaskPermanentError("cover_info 必须是JSON对象")

        return stream_url.strip(), cover_strategy.strip(), cover_info

    async def _process_async(
        self,
        stream_url: str,
        cover_strategy: str,
        cover_info: dict,
    ) -> ProcessorResult:
        """调用直播封面算法，并释放其 HTTP 客户端。"""

        processor = self._create_processor()
        try:
            cover_result = await processor.live_extract_cover_url(
                stream_url,
                cover_strategy,
                cover_info,
            )
            if cover_result is None:
                raise RuntimeError("直播封面提取未返回结果")

            cover_url = getattr(cover_result, "file_url", None)
            if cover_url is None and isinstance(cover_result, dict):
                cover_url = cover_result.get("file_url") or cover_result.get(
                    "cover_url"
                )
            if not cover_url:
                raise RuntimeError("直播封面提取结果缺少file_url")

            return ProcessorResult(
                payload={
                    "stream_url": stream_url,
                    "cover_url": str(cover_url),
                    "cover_strategy": cover_strategy,
                },
                artifacts=(
                    {
                        "file_type": "COVER",
                        "file_url": str(cover_url),
                    },
                ),
            )
        finally:
            http_client = getattr(processor, "http_client", None)
            close = getattr(http_client, "aclose", None)
            if callable(close):
                await close()

    def process(self, message: TaskDispatchMessage) -> ProcessorResult:
        """同步执行直播封面任务，适配 pika 阻塞式消费线程。"""

        stream_url, cover_strategy, cover_info = self._parse_params(message)
        LOGGER.info(
            "开始直播封面提取: task_id=%s, stream_url=%s, strategy=%s",
            message.task_id,
            self._safe_media_url(stream_url),
            cover_strategy,
        )
        result = asyncio.run(
            self._process_async(stream_url, cover_strategy, cover_info)
        )
        LOGGER.info(
            "直播封面提取完成: task_id=%s, cover_url=%s",
            message.task_id,
            self._safe_media_url(str(result.payload.get("cover_url", ""))),
        )
        return result


__all__ = ["LiveCoverExtractProcessor"]
