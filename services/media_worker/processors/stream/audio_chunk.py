"""直播流音频切片任务适配器。

旧 ``node/main.py`` 的 ``stream_extract_audio`` 分支会启动一个后台切片器，把直播
流音频按固定时长切成 WAV 分块并上传到业务接口。该能力明确归属通用媒体 Worker
的 stream 处理域，本轮迁移到 ``services/media_worker/processors/stream``，避免
继续保留 ``node/`` 入口。

注意：切片器 ``start()`` 会立即返回，真实切片与上传在后台线程中继续执行。该
任务后续如果需要更强的完成语义、进度查询或停止控制，应继续在 media_worker 内
增强状态管理；当前处理器只返回启动快照，保持旧行为。
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.registry import ProcessorResult


LOGGER = logging.getLogger(__name__)


class StreamAudioChunkProcessor:
    """启动直播流音频切片上传任务。"""

    task_types = ("stream.audio.chunk", "stream_extract_audio")

    def __init__(
        self,
        chunker_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._chunker_factory = chunker_factory

    def _create_chunker(self, **kwargs):
        """创建流音频切片器；生产依赖在真正执行任务时才加载。"""

        if self._chunker_factory is not None:
            return self._chunker_factory(**kwargs)

        from services.media_worker.processors.stream.extract_audio_chunk import (
            ExtractAudioChunk,
        )

        return ExtractAudioChunk(**kwargs)

    @staticmethod
    def _safe_media_url(value: str) -> str:
        """输出脱敏媒体地址，避免日志打印签名查询串。"""

        try:
            parts = urlsplit(value)
        except ValueError:
            return "<invalid-url>"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @staticmethod
    def _normalize_chunk_duration(value: Any) -> int | None:
        """清理切片时长；未传时交给旧切片器使用默认值。"""

        if value is None:
            return None
        try:
            duration = int(value)
        except (TypeError, ValueError) as exc:
            raise TaskPermanentError("chunk_duration 必须是正整数") from exc
        if duration <= 0:
            raise TaskPermanentError("chunk_duration 必须是正整数")
        return duration

    @classmethod
    def _parse_params(cls, message: TaskDispatchMessage) -> dict[str, Any]:
        """兼容新旧任务参数并返回切片器构造参数。"""

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
            raise TaskPermanentError("流音频切片任务缺少stream_url")

        api_endpoint = params.get("api_endpoint") or ex_params.get("api_endpoint")
        if not isinstance(api_endpoint, str) or not api_endpoint.strip():
            raise TaskPermanentError("流音频切片任务缺少api_endpoint")

        callback_url = (
            params.get("callback_url")
            or ex_params.get("callback_url")
            or message.callback_url
        )
        if callback_url is not None and (
            not isinstance(callback_url, str) or not callback_url.strip()
        ):
            raise TaskPermanentError("callback_url 必须是非空字符串")

        chunk_duration_value = (
            params["chunk_duration"]
            if "chunk_duration" in params
            else ex_params.get("chunk_duration")
        )

        return {
            "video_source": stream_url.strip(),
            "api_endpoint": api_endpoint.strip(),
            "callback_url": callback_url.strip() if callback_url else None,
            "start_time": params.get("start_time") or ex_params.get("start_time"),
            "end_time": params.get("end_time") or ex_params.get("end_time"),
            "chunk_duration": cls._normalize_chunk_duration(chunk_duration_value),
        }

    def process(self, message: TaskDispatchMessage) -> ProcessorResult:
        """启动流音频切片器并返回启动状态快照。"""

        kwargs = self._parse_params(message)
        LOGGER.info(
            "开始流音频切片任务: task_id=%s, stream_url=%s, has_callback=%s",
            message.task_id,
            self._safe_media_url(kwargs["video_source"]),
            bool(kwargs.get("callback_url")),
        )
        chunker = self._create_chunker(**kwargs)
        chunker.start()
        status = chunker.get_status()
        LOGGER.info(
            "流音频切片任务已启动: task_id=%s, running=%s, started=%s",
            message.task_id,
            status.get("running"),
            status.get("task_started"),
        )
        return ProcessorResult(
            payload={
                "stream_url": kwargs["video_source"],
                "api_endpoint": kwargs["api_endpoint"],
                "chunk_duration": kwargs.get("chunk_duration"),
                "status": status,
                "mode": "started_background_chunker",
            },
            artifacts=(),
        )


__all__ = ["StreamAudioChunkProcessor"]
