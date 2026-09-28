"""视频片段提取任务适配器。

本模块把新 ``TaskDispatchMessage`` 契约转换为同目录下
``VideoProcess.video_extract_clips_url`` 所需参数。算法已经覆盖下载、FFmpeg
裁剪、片段上传、OpenCV 封面提取和清理；这里不重写算法，只做参数校验、日志脱敏
和结果标准化。
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


class VideoClipExtractProcessor:
    """复用现有视频处理代码完成片段裁剪和片段封面上传。"""

    task_types = ("video.clip.extract", "video_extract_clips")

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
    def _validate_clip(clip: Any, index: int) -> dict[str, Any]:
        """校验单个片段参数，保留旧算法支持的时间格式。"""

        if not isinstance(clip, dict):
            raise TaskPermanentError(f"clips[{index}] 必须是JSON对象")

        if "start_time" not in clip or "end_time" not in clip:
            raise TaskPermanentError(
                f"clips[{index}] 必须包含start_time和end_time"
            )

        start_time = clip["start_time"]
        end_time = clip["end_time"]
        valid_types = (int, float, str)
        if not isinstance(start_time, valid_types) or (
            isinstance(start_time, str) and not start_time.strip()
        ):
            raise TaskPermanentError(f"clips[{index}].start_time 格式无效")
        if not isinstance(end_time, valid_types) or (
            isinstance(end_time, str) and not end_time.strip()
        ):
            raise TaskPermanentError(f"clips[{index}].end_time 格式无效")

        normalized = dict(clip)
        if isinstance(start_time, str):
            normalized["start_time"] = start_time.strip()
        if isinstance(end_time, str):
            normalized["end_time"] = end_time.strip()
        return normalized

    @classmethod
    def _parse_params(cls, message: TaskDispatchMessage) -> tuple[str, list[dict]]:
        """兼容新旧任务参数并返回视频地址和片段列表。

        新任务推荐使用：
        ``params.video_url`` 和 ``params.clips``。

        旧任务兼容：
        ``params.media_url``、``params.file_url``、``params.ex_params.clips``、
        ``params.params.clips``。
        """

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
            raise TaskPermanentError("片段提取任务缺少video_url")

        clips = (
            params.get("clips")
            or nested_params.get("clips")
            or ex_params.get("clips")
        )
        if not isinstance(clips, list) or not clips:
            raise TaskPermanentError("片段提取任务缺少clips数组")

        return video_url.strip(), [
            cls._validate_clip(clip, index) for index, clip in enumerate(clips)
        ]

    @staticmethod
    def _artifact_items(result: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        """把旧算法返回的 clips 转为通用产物描述。"""

        artifacts: list[dict[str, Any]] = []
        for clip in result.get("clips") or []:
            if not isinstance(clip, dict):
                continue
            clip_url = clip.get("clip_url")
            if clip_url:
                artifacts.append(
                    {
                        "file_type": "VIDEO",
                        "file_url": str(clip_url),
                        "extra": {
                            "buss_id": clip.get("buss_id"),
                            "start_time": clip.get("start_time"),
                            "end_time": clip.get("end_time"),
                            "duration": clip.get("duration"),
                        },
                    }
                )
            cover_url = clip.get("cover_url")
            if cover_url:
                artifacts.append(
                    {
                        "file_type": "COVER",
                        "file_url": str(cover_url),
                        "extra": {
                            "buss_id": clip.get("buss_id"),
                            "clip_url": clip_url,
                        },
                    }
                )
        return tuple(artifacts)

    async def _process_async(
        self,
        video_url: str,
        clips: list[dict],
    ) -> ProcessorResult:
        """调用旧异步算法，并在同一事件循环中释放其 HTTP 客户端。"""

        processor = self._create_processor()
        try:
            clip_result = await processor.video_extract_clips_url(video_url, clips)
            if not isinstance(clip_result, dict):
                raise RuntimeError("视频片段提取未返回JSON对象")
            if not clip_result.get("clips"):
                raise RuntimeError("视频片段提取未产生任何有效片段")

            return ProcessorResult(
                payload=clip_result,
                artifacts=self._artifact_items(clip_result),
            )
        finally:
            http_client = getattr(processor, "http_client", None)
            close = getattr(http_client, "aclose", None)
            if callable(close):
                await close()

    def process(self, message: TaskDispatchMessage) -> ProcessorResult:
        """同步执行片段提取任务，适配 pika 的阻塞式消费线程。"""

        video_url, clips = self._parse_params(message)
        LOGGER.info(
            "开始视频片段提取: task_id=%s, media_url=%s, clip_count=%s",
            message.task_id,
            self._safe_media_url(video_url),
            len(clips),
        )
        result = asyncio.run(self._process_async(video_url, clips))
        LOGGER.info(
            "视频片段提取完成: task_id=%s, success_clips=%s",
            message.task_id,
            result.payload.get("success_clips"),
        )
        return result


__all__ = ["VideoClipExtractProcessor"]
