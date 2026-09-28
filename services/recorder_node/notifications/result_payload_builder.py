"""录制结果通知消息体构造器。

该模块只负责把 recorder-node 已有任务状态转换为 RTC/业务端兼容的结果消息体。
RabbitMQ 发布和 HTTP 回调发送分别由其他组件负责，避免 ``StreamRecorder`` 大类
同时承担数据组装、消息发送和回调重试。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
import os
from typing import Any

LOGGER = logging.getLogger(__name__)


class RecorderResultPayloadBuilder:
    """构造录制完成后的 MQ/HTTP 结果消息体。"""

    def __init__(
        self,
        *,
        settings: Any,
        audio_info_extractor: Any,
        cover_info_extractor: Any,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.audio_info_extractor = audio_info_extractor
        self.cover_info_extractor = cover_info_extractor
        self.now_factory = now_factory or datetime.now

    async def build(
        self,
        *,
        task_id: str,
        task_status: dict[str, Any],
    ) -> dict[str, Any]:
        """根据任务状态构造兼容的录制结果数据。"""

        video_summary = self._build_video_summary(task_status)
        audio_summary = await self._build_audio_summary(
            task_id=task_id,
            task_status=task_status,
            video_summary=video_summary,
        )
        cover_summary = await self._build_cover_summary(
            task_id=task_id,
            task_status=task_status,
        )

        return {
            "task_id": task_id,
            "status": task_status["status"],
            "video_url": task_status.get("upload_url"),
            "video_file_id": task_status.get("video_file_id", ""),
            "audio_url": task_status.get("audio_result_url"),
            "audio_file_id": task_status.get("audio_file_id", ""),
            "cover_url": task_status.get("cover_url"),
            "cover_file_id": task_status.get("cover_file_id", ""),
            "created_at": self.now_factory().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "error": task_status.get("error", ""),
            "summary": {
                "video_info": video_summary,
                "audio_info": audio_summary,
                "cover_info": cover_summary,
                "start_time": task_status.get("actual_start_time"),
                "end_time": task_status.get("actual_end_time"),
                "stream_id": task_status.get("stream_id"),
                "original_stream_id": task_status.get("original_stream_id"),
            },
        }

    def _build_video_summary(self, task_status: dict[str, Any]) -> dict[str, Any]:
        """构造视频摘要，优先使用后处理阶段已提取的视频信息。"""

        if not task_status.get("result_url"):
            return {}

        video_bucket = task_status.get("video_bucket", "")
        video_key = task_status.get("video_key", "")
        video_info = task_status.get("video_info", {})
        segments = task_status.get("segments", [])

        if video_info:
            return {
                "duration": round(video_info.get("duration", 0), 2),
                "file_size": video_info.get("size", 0),
                "segments_count": len(segments),
                "bucket": video_bucket,
                "key": video_key,
                "storge_type": self.settings.storage.type.lower(),
                "resolution": {
                    "width": video_info.get("width", 0),
                    "height": video_info.get("height", 0),
                },
                "video": {
                    "codec": video_info.get("video_codec", ""),
                    "bitrate": video_info.get("video_bitrate", 0),
                    "frame_rate": video_info.get("frame_rate", 0.0),
                },
                "audio": {
                    "codec": video_info.get("audio_codec", ""),
                    "bitrate": video_info.get("audio_bitrate", 0),
                    "sample_rate": video_info.get("audio_sample_rate", 0),
                    "channels": video_info.get("audio_channels", 0),
                },
            }

        if segments:
            total_duration = sum(segment.get("time_len", 0) for segment in segments)
            total_size = sum(segment.get("file_size", 0) for segment in segments)
            return {
                "duration": round(total_duration, 2),
                "file_size": total_size,
                "segments_count": len(segments),
                "bucket": video_bucket,
                "key": video_key,
                "storge_type": self.settings.storage.type.lower(),
            }

        return {}

    async def _build_audio_summary(
        self,
        *,
        task_id: str,
        task_status: dict[str, Any],
        video_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """构造音频摘要，失败时保留基本文件信息。"""

        if not task_status.get("audio_result_url"):
            return {}

        audio_bucket = task_status.get("audio_bucket", "")
        audio_key = task_status.get("audio_key", "")
        audio_local_path = task_status.get("audio_local_path")
        audio_file_size = task_status.get("audio_file_size", 0)

        try:
            LOGGER.info("开始获取音频详细信息: %s", task_id)
            if audio_local_path and os.path.exists(audio_local_path):
                audio_info = await self.audio_info_extractor.get_audio_info(
                    audio_local_path,
                )
                summary = {
                    "duration": audio_info.get("duration", 0),
                    "file_size": audio_info.get("file_size", 0),
                    "bucket": audio_bucket,
                    "key": audio_key,
                    "storge_type": self.settings.storage.audio_type.lower(),
                    "audio": audio_info.get("audio", {}),
                }
                LOGGER.info("音频详细信息获取成功: %s", summary)
                return summary

            if video_summary and "audio" in video_summary:
                summary = {
                    "duration": video_summary.get("duration", 0),
                    "file_size": audio_file_size,
                    "bucket": audio_bucket,
                    "key": audio_key,
                    "storge_type": self.settings.storage.audio_type.lower(),
                    "audio": video_summary.get("audio", {}),
                }
                LOGGER.info(
                    "使用预先保存的音频文件大小和从视频推断的音频信息: %s",
                    summary,
                )
                return summary

            summary = {
                "duration": 0,
                "file_size": audio_file_size,
                "bucket": audio_bucket,
                "key": audio_key,
                "audio": {},
            }
            LOGGER.info("仅使用预先保存的音频文件大小: %s bytes", audio_file_size)
            return summary
        except Exception as exc:
            LOGGER.error("获取音频详细信息失败: %s, 错误: %s", task_id, exc)
            return {
                "duration": 0,
                "file_size": audio_file_size,
                "bucket": audio_bucket,
                "key": audio_key,
            }

    async def _build_cover_summary(
        self,
        *,
        task_id: str,
        task_status: dict[str, Any],
    ) -> dict[str, Any]:
        """构造封面摘要，失败时保留对象存储位置。"""

        cover_url = task_status.get("cover_url")
        if not cover_url:
            return {}

        cover_bucket = task_status.get("cover_bucket", "")
        cover_key = task_status.get("cover_key", "")
        cover_local_path = task_status.get("cover_local_path")

        try:
            LOGGER.info("开始获取封面详细信息: %s", task_id)
            cover_info = await self.cover_info_extractor.get_cover_info_from_url(
                cover_url,
                cover_local_path,
            )
            summary = {
                "file_size": cover_info.get("file_size", 0),
                "bucket": cover_bucket,
                "key": cover_key,
                "storge_type": self.settings.storage.type.lower(),
                "resolution": {
                    "width": cover_info.get("width", 0),
                    "height": cover_info.get("height", 0),
                },
                "format": cover_info.get("format", ""),
                "mode": cover_info.get("mode", ""),
            }
            LOGGER.info("封面详细信息获取成功: %s", summary)
            return summary
        except Exception as exc:
            LOGGER.error("获取封面详细信息失败: %s, 错误: %s", task_id, exc)
            return {
                "file_size": 0,
                "bucket": cover_bucket,
                "key": cover_key,
                "resolution": {"width": 0, "height": 0},
            }
