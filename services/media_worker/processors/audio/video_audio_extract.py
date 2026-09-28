"""视频音频提取任务处理器。

本处理器面向通用媒体 Worker 的离线任务：从可下载的视频地址中提取音频，上传到
COS 或 MinIO/S3，并把音频地址和基础音频信息写回任务结果。

处理器只在真正执行任务时创建旧存储服务和音频信息工具，避免 Worker 入口导入阶段
连接外部服务或加载 FFmpeg 相关依赖。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import os
import subprocess
import tempfile
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from media_platform.contracts.task import TaskDispatchMessage
from media_platform.infrastructure.messaging import TaskPermanentError
from services.media_worker.registry import ProcessorResult


LOGGER = logging.getLogger(__name__)


class VideoAudioExtractProcessor:
    """从视频文件中提取音频并上传对象存储。"""

    task_types = ("video.audio.extract", "video_extract_audio")
    _SUPPORTED_FORMATS = {"mp3", "aac", "m4a", "wav", "flac"}
    _FORMAT_CODECS = {
        "mp3": "libmp3lame",
        "aac": "aac",
        "m4a": "aac",
        "wav": "pcm_s16le",
        "flac": "flac",
    }
    _MIME_TYPES = {
        "mp3": "audio/mpeg",
        "aac": "audio/aac",
        "m4a": "audio/mp4",
        "wav": "audio/wav",
        "flac": "audio/flac",
    }

    def __init__(
        self,
        *,
        storage_factory: Callable[[], Any] | None = None,
        audio_info_factory: Callable[[], Any] | None = None,
        ffmpeg_runner: Callable[[list[str], int], None] | None = None,
    ) -> None:
        self._storage_factory = storage_factory
        self._audio_info_factory = audio_info_factory
        self._ffmpeg_runner = ffmpeg_runner or self._run_ffmpeg

    def _create_storage(self):
        """创建对象存储服务；生产依赖延迟到任务执行阶段加载。"""

        if self._storage_factory is not None:
            return self._storage_factory()

        from media_platform.infrastructure.storage import (
            get_enhanced_storage_service,
        )

        return get_enhanced_storage_service()

    def _create_audio_info(self):
        """创建音频信息提取工具；测试可注入轻量 fake。"""

        if self._audio_info_factory is not None:
            return self._audio_info_factory()

        from media_platform.infrastructure.media_processing import GetAudioInfo

        return GetAudioInfo()

    @staticmethod
    def _safe_media_url(value: str) -> str:
        """输出用于排障的媒体地址，去掉可能包含签名或密钥的查询串。"""

        try:
            parts = urlsplit(value)
        except ValueError:
            return "<invalid-url>"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @classmethod
    def _normalize_audio_format(cls, value: Any) -> str:
        """清理并校验音频格式。"""

        if value is None:
            return "mp3"
        if not isinstance(value, str) or not value.strip():
            raise TaskPermanentError("audio_format 必须是非空字符串")

        audio_format = value.strip().lower().lstrip(".")
        if audio_format not in cls._SUPPORTED_FORMATS:
            supported = ", ".join(sorted(cls._SUPPORTED_FORMATS))
            raise TaskPermanentError(
                f"不支持的audio_format={audio_format}，支持：{supported}"
            )
        return audio_format

    @classmethod
    def _parse_params(cls, message: TaskDispatchMessage) -> tuple[str, str]:
        """兼容新旧任务参数，返回视频地址和目标音频格式。

        新任务推荐使用：
        ``params.video_url`` 和 ``params.audio_format``。

        旧任务兼容：
        ``params.media_url``、``params.mp4_url``、``params.mp4Url``，以及
        ``params.ex_params.audio_format``。
        """

        params = message.params
        ex_params = params.get("ex_params") or {}
        if not isinstance(ex_params, dict):
            raise TaskPermanentError("params.ex_params 必须是JSON对象")

        video_url = (
            params.get("video_url")
            or params.get("media_url")
            or params.get("mp4_url")
            or params.get("mp4Url")
        )
        if not isinstance(video_url, str) or not video_url.strip():
            raise TaskPermanentError("音频提取任务缺少video_url")

        audio_format = cls._normalize_audio_format(
            params.get("audio_format") or ex_params.get("audio_format")
        )
        return video_url.strip(), audio_format

    @classmethod
    def _build_ffmpeg_command(
        cls,
        video_path: str,
        audio_path: str,
        audio_format: str,
    ) -> list[str]:
        """生成只提取音频轨的 FFmpeg 命令。"""

        ffmpeg_path = os.getenv("MEDIA_FFMPEG_PATH", "ffmpeg")
        command = [
            ffmpeg_path,
            "-y",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            cls._FORMAT_CODECS[audio_format],
        ]
        if audio_format == "mp3":
            command.extend(["-b:a", "192k"])
        command.append(audio_path)
        return command

    @staticmethod
    def _run_ffmpeg(command: list[str], timeout_seconds: int) -> None:
        """执行 FFmpeg 命令，并把失败转换为可重试异常。"""

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("未找到FFmpeg，请配置MEDIA_FFMPEG_PATH或PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"FFmpeg音频提取超时: {timeout_seconds}s") from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if len(stderr) > 1000:
                stderr = stderr[-1000:]
            raise RuntimeError(f"FFmpeg音频提取失败: {stderr}")

    async def _process_async(
        self,
        message: TaskDispatchMessage,
        video_url: str,
        audio_format: str,
    ) -> ProcessorResult:
        """下载视频、提取音频、上传音频并返回标准处理结果。"""

        storage = self._create_storage()
        audio_info_reader = self._create_audio_info()
        timeout_seconds = int(os.getenv("MEDIA_AUDIO_EXTRACT_TIMEOUT_SECONDS", "600"))
        if timeout_seconds <= 0:
            raise RuntimeError("MEDIA_AUDIO_EXTRACT_TIMEOUT_SECONDS 必须大于0")

        with tempfile.TemporaryDirectory(prefix="media-worker-audio-") as temp_dir:
            video_path = await storage.download_file(video_url, temp_dir)
            if not video_path or not os.path.exists(video_path):
                raise RuntimeError("视频文件下载失败，无法提取音频")

            audio_path = os.path.join(temp_dir, f"{message.task_id}.{audio_format}")
            command = self._build_ffmpeg_command(
                video_path,
                audio_path,
                audio_format,
            )
            await asyncio.to_thread(self._ffmpeg_runner, command, timeout_seconds)
            if not os.path.exists(audio_path) or os.path.getsize(audio_path) <= 0:
                raise RuntimeError("FFmpeg执行完成但音频结果文件无效")

            upload_result = await storage.upload_file_enhanced(
                audio_path,
                upload_type="audio",
            )
            audio_url = getattr(upload_result, "file_url", None)
            if audio_url is None and isinstance(upload_result, dict):
                audio_url = upload_result.get("file_url") or upload_result.get(
                    "audio_url"
                )
            if not audio_url:
                raise RuntimeError("音频上传结果缺少file_url")

            audio_info = await audio_info_reader.get_audio_info(audio_path)
            file_size = os.path.getsize(audio_path)
            mime_type = self._MIME_TYPES[audio_format]
            return ProcessorResult(
                payload={
                    "video_url": video_url,
                    "audio_url": str(audio_url),
                    "audio_format": audio_format,
                    "audio_info": audio_info,
                },
                artifacts=(
                    {
                        "file_type": "AUDIO",
                        "file_url": str(audio_url),
                        "file_name": os.path.basename(audio_path),
                        "file_size": file_size,
                        "mime_type": mime_type,
                    },
                ),
            )

    def process(self, message: TaskDispatchMessage) -> ProcessorResult:
        """同步执行音频提取任务，适配 pika 阻塞式消费线程。"""

        video_url, audio_format = self._parse_params(message)
        LOGGER.info(
            "开始视频音频提取: task_id=%s, media_url=%s, audio_format=%s",
            message.task_id,
            self._safe_media_url(video_url),
            audio_format,
        )
        result = asyncio.run(self._process_async(message, video_url, audio_format))
        LOGGER.info(
            "视频音频提取完成: task_id=%s, audio_url=%s",
            message.task_id,
            self._safe_media_url(str(result.payload.get("audio_url", ""))),
        )
        return result


__all__ = ["VideoAudioExtractProcessor"]
