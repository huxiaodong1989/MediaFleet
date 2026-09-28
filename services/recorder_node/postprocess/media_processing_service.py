"""录制后处理媒体处理阶段服务。

该服务负责录制结果进入对象存储前的本机媒体处理阶段：

- 读取合并后视频的媒体信息。
- 按任务参数提取音频。
- 按任务参数触发封面提取。

它不负责队列调度、对象存储上传、数据库落库、业务回调或本地文件清理。
这些职责分别由后处理管理器、上传服务、持久化服务、通知服务和清理服务承担。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
import platform
import subprocess
from typing import Any

import ffmpeg

from media_platform.infrastructure.media_processing import (
    CoverExtractor,
    CoverExtractStrategy,
    GetVideoInfo,
)


LOGGER = logging.getLogger("post_processor")


class RecordingMediaProcessingService:
    """封装录制后处理中的媒体信息读取、音频提取和封面提取逻辑。"""

    def __init__(
        self,
        *,
        video_info_extractor: Any | None = None,
        cover_extractor: Any | None = None,
        audio_extract_timeout_seconds: float = 300.0,
        cover_extract_timeout_seconds: float = 60.0,
    ) -> None:
        """初始化媒体处理阶段服务。

        Args:
            video_info_extractor: 视频信息读取器。生产默认使用公共基础能力
                `GetVideoInfo`，测试可注入 fake。
            cover_extractor: 封面提取器。生产默认使用公共基础能力 `CoverExtractor`，
                测试可注入 fake。
            audio_extract_timeout_seconds: 单次 FFmpeg 音频提取超时时间。
            cover_extract_timeout_seconds: 单次封面提取超时时间，沿用原 60 秒限制。
        """

        self.video_info_extractor = video_info_extractor
        self.cover_extractor = cover_extractor
        self.audio_extract_timeout_seconds = audio_extract_timeout_seconds
        self.cover_extract_timeout_seconds = cover_extract_timeout_seconds

    async def collect_video_info(
        self,
        *,
        task_id: str,
        result_url: str | None,
        task_status: dict[str, Any],
        stream_recorder: Any,
    ) -> None:
        """读取视频详细信息并写入 ``task_status['video_info']``。

        视频信息读取失败不是关键失败：沿用原行为，只记录错误到
        ``task_status['errors']``，后续上传、落库和回调继续执行。
        """

        if not result_url:
            return

        try:
            LOGGER.info("开始获取视频详细信息: %s", task_id)
            video_info_extractor = self._resolve_video_info_extractor(stream_recorder)
            video_info = await video_info_extractor.get_video_info(result_url)
            task_status["video_info"] = video_info
            LOGGER.info("视频详细信息获取成功: %s", video_info)
        except Exception as exc:
            LOGGER.error("获取视频详细信息失败: %s, 错误: %s", task_id, exc)
            self._append_task_error(
                task_status,
                f"获取视频详细信息失败: {exc}",
            )

    async def extract_audio(
        self,
        *,
        task_id: str,
        result_url: str | None,
        task_status: dict[str, Any],
        stream_recorder: Any,
    ) -> None:
        """按任务参数提取音频并写入音频本地路径和大小。"""

        extra_params = task_status.get("extra_params", {}) or {}
        extract_audio = extra_params.get(
            "extract_audio",
            task_status.get("extract_audio", False),
        )
        if not extract_audio or not result_url:
            return

        audio_format = extra_params.get(
            "audio_format",
            task_status.get("audio_format", "mp3"),
        )
        audio_format = self._normalize_audio_format(audio_format)
        task_status["audio_format"] = audio_format

        LOGGER.info("开始提取音频: %s, 格式: %s", task_id, audio_format)
        work_dir = task_status.get("work_dir")
        if work_dir:
            audio_result_url = await self._extract_audio_file(
                result_url,
                audio_format,
                output_directory=work_dir,
            )
        else:
            audio_result_url = await self._extract_audio_file(
                result_url,
                audio_format,
            )

        if not audio_result_url:
            LOGGER.error("音频提取失败: %s", task_id)
            self._append_task_error(
                task_status,
                f"音频提取失败，格式: {audio_format}",
            )
            return

        LOGGER.info("音频提取成功: %s", audio_result_url)
        task_status["audio_result_url"] = audio_result_url
        task_status["audio_local_path"] = audio_result_url
        self._record_audio_file_size(task_status, audio_result_url)

    async def extract_cover(
        self,
        *,
        task_id: str,
        result_url: str | None,
        task_status: dict[str, Any],
        stream_recorder: Any,
    ) -> None:
        """按任务参数触发封面提取。

        该阶段只负责生成本地封面文件，并把本地路径写入任务状态。封面上传由
        ``RecordingUploadService.upload_cover`` 处理，产物落库由
        ``RecordingPostProcessPersistenceService`` 处理，避免把提取、上传和落库
        继续混在 ``StreamRecorder`` 大类中。
        """

        extra_params = task_status.get("extra_params", {}) or {}
        extract_cover = extra_params.get("extract_cover", False)
        if not extract_cover or not result_url:
            return

        try:
            cover_strategy = extra_params.get(
                "cover_strategy",
                CoverExtractStrategy.TIMESTAMP.value,
            )
            cover_info = dict(extra_params.get("cover_info", {}) or {})
            task_status["cover_strategy"] = cover_strategy
            task_status["cover_info"] = cover_info

            LOGGER.info(
                "开始提取封面: %s, 策略: %s, 参数: %s",
                task_id,
                cover_strategy,
                cover_info,
            )
            cover_extractor = self._resolve_cover_extractor(stream_recorder)
            validation_result = cover_extractor.validate_cover_params(
                cover_strategy,
                cover_info,
            )
            LOGGER.info("封面参数验证结果: %s, task_id=%s", validation_result, task_id)

            if not validation_result:
                LOGGER.warning(
                    "封面参数验证失败: task_id=%s, strategy=%s",
                    task_id,
                    cover_strategy,
                )
                self._append_task_error(
                    task_status,
                    f"封面参数验证失败，策略: {cover_strategy}",
                )
                return

            if cover_strategy == CoverExtractStrategy.CUSTOM.value:
                cover_info["file_size_mb"] = self._recorded_video_size_mb(task_status)

            cover_output_dir = os.path.join(
                task_status.get("work_dir") or os.path.dirname(result_url),
                "covers",
            )
            LOGGER.info("封面输出目录: %s, task_id=%s", cover_output_dir, task_id)
            cover_file_path = await asyncio.wait_for(
                cover_extractor.extract_cover(
                    video_path=result_url,
                    strategy=cover_strategy,
                    cover_info=cover_info,
                    output_dir=cover_output_dir,
                    start_time=task_status.get("start_time"),
                    end_time=task_status.get("end_time"),
                ),
                timeout=self.cover_extract_timeout_seconds,
            )

            if cover_file_path and os.path.exists(cover_file_path):
                cover_file_size = os.path.getsize(cover_file_path)
                task_status["cover_local_path"] = cover_file_path
                task_status["cover_file_path"] = cover_file_path
                task_status["cover_file_size"] = cover_file_size
                LOGGER.info(
                    "封面提取成功: task_id=%s, path=%s, size=%s bytes",
                    task_id,
                    cover_file_path,
                    cover_file_size,
                )
                return

            LOGGER.error(
                "封面提取失败: task_id=%s, 返回路径=%s, 文件存在=%s",
                task_id,
                cover_file_path,
                os.path.exists(cover_file_path) if cover_file_path else False,
            )
            self._append_task_error(
                task_status,
                f"封面提取失败，策略: {cover_strategy}",
            )
        except asyncio.TimeoutError:
            LOGGER.error(
                "封面提取任务超时: task_id=%s, timeout=%s秒",
                task_id,
                self.cover_extract_timeout_seconds,
            )
            self._append_task_error(
                task_status,
                f"封面提取任务超时（{self.cover_extract_timeout_seconds:g}秒）",
            )
        except Exception as exc:
            LOGGER.error(
                "封面提取异常: task_id=%s, 错误: %s",
                task_id,
                exc,
                exc_info=True,
            )
            self._append_task_error(task_status, f"封面提取异常: {exc}")

    def _resolve_video_info_extractor(self, stream_recorder: Any) -> Any:
        """优先使用显式注入的视频信息读取器，必要时兼容录制器已有实例。"""

        if self.video_info_extractor is not None:
            return self.video_info_extractor
        recorder_extractor = getattr(stream_recorder, "video_info_extractor", None)
        if recorder_extractor is not None:
            return recorder_extractor
        self.video_info_extractor = GetVideoInfo()
        return self.video_info_extractor

    def _resolve_cover_extractor(self, stream_recorder: Any) -> Any:
        """优先使用显式注入的封面提取器，必要时兼容录制器已有实例。"""

        if self.cover_extractor is not None:
            return self.cover_extractor
        recorder_extractor = getattr(stream_recorder, "cover_extractor", None)
        if recorder_extractor is not None:
            return recorder_extractor
        self.cover_extractor = CoverExtractor()
        return self.cover_extractor

    @staticmethod
    def _normalize_audio_format(audio_format: Any) -> str:
        """归一化音频格式，保持原录制器只支持 mp3/wav 的行为。"""

        normalized = str(audio_format or "mp3").lower().strip()
        if normalized in {"mp3", "wav"}:
            return normalized
        LOGGER.warning("不支持的音频格式: %s，将使用默认格式 mp3", audio_format)
        return "mp3"

    async def _extract_audio_file(
        self,
        video_path: str,
        audio_format: str,
        *,
        output_directory: str | None = None,
    ) -> str | None:
        """使用 FFmpeg 从本地视频文件中提取音频。

        这里承接原 ``StreamRecorder._extract_audio`` 的真实实现，让录制后处理不再
        反向调用录制器私有方法。失败返回 ``None``，由上层按非关键阶段记录错误。
        """

        if not os.path.exists(video_path):
            LOGGER.error("视频文件不存在: %s", video_path)
            return None

        directory = output_directory or os.path.dirname(video_path)
        os.makedirs(directory, exist_ok=True)
        filename = os.path.basename(video_path)
        basename = os.path.splitext(filename)[0]
        audio_path = os.path.join(directory, f"{basename}_audio.{audio_format}")

        LOGGER.info(
            "开始执行 FFmpeg 音频提取: 视频=%s, 音频=%s, 格式=%s",
            video_path,
            audio_path,
            audio_format,
        )
        audio_codec = "libmp3lame" if audio_format == "mp3" else "pcm_s16le"
        audio_bitrate = "192k" if audio_format == "mp3" else None

        try:
            return await self._run_audio_extract_command(
                video_path=video_path,
                audio_path=audio_path,
                audio_codec=audio_codec,
                audio_bitrate=audio_bitrate,
            )
        except Exception as exc:
            LOGGER.error("提取音频过程异常: %s", exc, exc_info=True)
            try:
                LOGGER.info("尝试使用简化命令提取音频: %s", video_path)
                return await self._run_audio_extract_command(
                    video_path=video_path,
                    audio_path=audio_path,
                    audio_codec=None,
                    audio_bitrate=None,
                )
            except Exception as simple_exc:
                LOGGER.error("简化命令提取音频失败: %s", simple_exc, exc_info=True)
                return None

    async def _run_audio_extract_command(
        self,
        *,
        video_path: str,
        audio_path: str,
        audio_codec: str | None,
        audio_bitrate: str | None,
    ) -> str | None:
        """按当前系统执行一次 FFmpeg 音频提取命令。"""

        if platform.system() == "Windows":
            cmd = ["ffmpeg", "-i", video_path, "-vn"]
            if audio_codec:
                cmd.extend(["-acodec", audio_codec])
            if audio_bitrate:
                cmd.extend(["-ab", audio_bitrate])
            cmd.extend(["-y", audio_path])
            LOGGER.info("Windows 环境提取音频命令: %s", " ".join(cmd))
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=self.audio_extract_timeout_seconds,
            )
            if result.returncode != 0:
                LOGGER.error("音频提取失败: %s", result.stderr)
                return None
        else:
            stream = ffmpeg.input(video_path)
            output_kwargs: dict[str, Any] = {"vn": None}
            if audio_codec:
                output_kwargs["acodec"] = audio_codec
            if audio_bitrate:
                output_kwargs["ab"] = audio_bitrate
            stream = ffmpeg.output(stream, audio_path, **output_kwargs)
            await asyncio.wait_for(
                asyncio.to_thread(
                    stream.run,
                    capture_stdout=True,
                    capture_stderr=True,
                    overwrite_output=True,
                ),
                timeout=self.audio_extract_timeout_seconds,
            )

        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
            LOGGER.info("音频提取成功: %s", audio_path)
            return audio_path
        LOGGER.error("音频提取结果文件无效: %s", audio_path)
        return None

    @staticmethod
    def _recorded_video_size_mb(task_status: dict[str, Any]) -> float:
        """按录制分片大小计算视频文件总大小，供自定义封面策略使用。"""

        video_file_size = sum(
            segment.get("file_size", 0)
            for segment in task_status.get("segments", [])
        )
        return round(video_file_size / (1024 * 1024), 1) if video_file_size > 0 else 0

    @staticmethod
    def _append_task_error(task_status: dict[str, Any], message: str) -> None:
        """按原后处理错误结构追加错误信息。"""

        task_status.setdefault("errors", []).append(
            {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "error": message,
            }
        )

    @staticmethod
    def _record_audio_file_size(
        task_status: dict[str, Any],
        audio_local_path: str,
    ) -> None:
        """记录音频文件大小；获取失败只写日志，不中断后处理。"""

        if not os.path.exists(audio_local_path):
            return
        try:
            audio_file_size = os.path.getsize(audio_local_path)
            task_status["audio_file_size"] = audio_file_size
            LOGGER.info("音频文件大小: %s bytes", audio_file_size)
        except Exception as exc:
            LOGGER.warning("获取音频文件大小失败: %s", exc)


__all__ = ["RecordingMediaProcessingService"]
