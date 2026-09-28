"""视频封面提取任务适配器。

本模块不重写已经稳定运行的封面算法，只把新 ``TaskDispatchMessage`` 契约转换
为同目录下 ``VideoProcess.video_extract_cover_url`` 所需参数。实现被延迟导入，
因此导入 Worker 服务入口时不会立即加载 OpenCV、FFmpeg 或存储客户端。
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


class VideoCoverExtractProcessor:
    """复用现有视频处理代码完成封面提取和上传。"""

    task_types = ("video.cover.extract", "video_extract_cover")

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
    def _parse_params(message: TaskDispatchMessage) -> tuple[str, str, dict]:
        """兼容新旧请求参数位置并返回算法需要的三个参数。

        新调用方可以把 ``cover_strategy`` 和 ``cover_info`` 直接放在 ``params``；
        旧 RTC/主节点仍可放在 ``params.ex_params``。视频地址兼容 ``video_url``
        和旧任务中可能使用的 ``media_url``。
        """

        params = message.params
        ex_params = params.get("ex_params") or {}
        if not isinstance(ex_params, dict):
            raise TaskPermanentError("params.ex_params 必须是JSON对象")

        video_url = params.get("video_url") or params.get("media_url")
        if not isinstance(video_url, str) or not video_url.strip():
            raise TaskPermanentError("封面提取任务缺少video_url")

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

        return video_url.strip(), cover_strategy.strip(), cover_info

    async def _process_async(
        self,
        video_url: str,
        cover_strategy: str,
        cover_info: dict,
    ) -> ProcessorResult:
        """调用旧异步算法，并在同一事件循环中释放其 HTTP 客户端。"""

        processor = self._create_processor()
        try:
            cover_result = await processor.video_extract_cover_url(
                video_url,
                cover_strategy,
                cover_info,
            )
            if cover_result is None:
                raise RuntimeError("视频封面提取未返回结果")

            cover_url = getattr(cover_result, "file_url", None)
            if cover_url is None and isinstance(cover_result, dict):
                cover_url = cover_result.get("file_url") or cover_result.get(
                    "cover_url"
                )
            if not cover_url:
                raise RuntimeError("视频封面提取结果缺少file_url")

            return ProcessorResult(
                payload={
                    "video_url": video_url,
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
        """同步执行封面任务，适配 pika 的阻塞式消费线程。"""

        video_url, cover_strategy, cover_info = self._parse_params(message)
        LOGGER.info(
            "开始视频封面提取: task_id=%s, media_url=%s, strategy=%s",
            message.task_id,
            self._safe_media_url(video_url),
            cover_strategy,
        )
        result = asyncio.run(
            self._process_async(video_url, cover_strategy, cover_info)
        )
        LOGGER.info(
            "视频封面提取完成: task_id=%s, cover_url=%s",
            message.task_id,
            self._safe_media_url(str(result.payload.get("cover_url", ""))),
        )
        return result


__all__ = ["VideoCoverExtractProcessor"]
