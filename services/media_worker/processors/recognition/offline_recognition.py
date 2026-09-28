"""离线语音识别任务适配器。

旧 Worker 的 ``media_recog`` 任务实际只在主流程中执行 ``stage=1``：下载音/视频、
转换为 16k 单声道 wav，然后通过 FunASR 或 GLM 生成 SRT 文本。本模块只开放该
稳定入口，不开放旧 ``stage=2`` 基于识别结果裁剪音视频的实验能力。
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


class SpeechOfflineRecognizeProcessor:
    """调用媒体 Worker 归属下的 ``MediaRecog`` 完成离线识别。"""

    task_types = ("speech.offline.recognize", "recognition.media", "media_recog")

    def __init__(self, recognizer_factory: Callable[[], Any] | None = None) -> None:
        self._recognizer_factory = recognizer_factory

    def _create_recognizer(self):
        """创建识别器；FunASR/GLM 依赖在执行任务时才加载。"""

        if self._recognizer_factory is not None:
            return self._recognizer_factory()

        from services.media_worker.processors.recognition.media_recog import MediaRecog

        return MediaRecog()

    @staticmethod
    def _safe_media_url(value: str) -> str:
        """输出用于排障的媒体地址，去掉可能包含签名或密钥的查询串。"""

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
    def _parse_params(cls, message: TaskDispatchMessage) -> tuple[str, str, str | None, dict]:
        """兼容新旧识别任务参数。

        新任务推荐：
        ``params.media_url``、``params.lang``、``params.provider``。

        旧任务兼容：
        ``params.ex_params.lang``、``params.ex_params.provider``、
        ``params.ex_params.asr_provider``。
        """

        params = message.params
        ex_params = cls._json_object(params.get("ex_params"), "params.ex_params")

        media_url = (
            params.get("media_url")
            or params.get("file_url")
            or params.get("video_url")
            or params.get("audio_url")
        )
        if not isinstance(media_url, str) or not media_url.strip():
            raise TaskPermanentError("离线识别任务缺少media_url")

        stage = params.get("stage", ex_params.get("stage", 1))
        if stage not in (1, "1", None):
            raise TaskPermanentError("当前新Worker只支持离线识别stage=1")

        lang = params.get("lang", ex_params.get("lang", "zh"))
        if not isinstance(lang, str) or not lang.strip():
            raise TaskPermanentError("lang 必须是非空字符串")

        provider = (
            params.get("asr_provider")
            or params.get("provider")
            or ex_params.get("asr_provider")
            or ex_params.get("provider")
        )
        if provider is not None and (
            not isinstance(provider, str) or not provider.strip()
        ):
            raise TaskPermanentError("provider 必须是非空字符串")

        recog_config = dict(ex_params)
        for key in ("hotwords", "hotword", "asr_provider", "provider"):
            if key in params and params[key] is not None:
                recog_config[key] = params[key]

        return media_url.strip(), lang.strip(), provider.strip() if provider else None, recog_config

    async def _process_async(
        self,
        *,
        media_url: str,
        lang: str,
        provider: str | None,
        config: dict,
    ) -> ProcessorResult:
        """调用旧异步识别算法，并在同一事件循环中释放 HTTP 客户端。"""

        recognizer = self._create_recognizer()
        try:
            srt_text = await recognizer.recog(
                stage=1,
                file=media_url,
                lang=lang,
                config=config,
                provider=provider,
            )
            if not srt_text:
                raise RuntimeError("离线识别未返回有效SRT")

            return ProcessorResult(
                payload={
                    "media_url": media_url,
                    "res_srt": str(srt_text),
                    "lang": lang,
                    "provider": provider,
                }
            )
        finally:
            http_client = getattr(recognizer, "http_client", None)
            close = getattr(http_client, "aclose", None)
            if callable(close):
                await close()

    def process(self, message: TaskDispatchMessage) -> ProcessorResult:
        """同步执行离线识别任务，适配 pika 的阻塞式消费线程。"""

        media_url, lang, provider, config = self._parse_params(message)
        LOGGER.info(
            "开始离线语音识别: task_id=%s, media_url=%s, lang=%s, provider=%s",
            message.task_id,
            self._safe_media_url(media_url),
            lang,
            provider or "DEFAULT",
        )
        result = asyncio.run(
            self._process_async(
                media_url=media_url,
                lang=lang,
                provider=provider,
                config=config,
            )
        )
        LOGGER.info(
            "离线语音识别完成: task_id=%s, srt_length=%s",
            message.task_id,
            len(str(result.payload.get("res_srt", ""))),
        )
        return result


__all__ = ["SpeechOfflineRecognizeProcessor"]
