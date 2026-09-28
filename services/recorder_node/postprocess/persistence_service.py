"""录制后处理持久化服务。

该服务只负责把后处理阶段已经上传好的视频、音频产物写入国标媒体文件表。它不执行
对象存储上传、不发送业务回调、不清理本地文件，也不再同步旧分发器或旧任务表。
"""

from __future__ import annotations

import logging
from typing import Any
import uuid

from services.recorder_node.application.artifact_service import RecordingArtifactService


LOGGER = logging.getLogger("post_processor")


class RecordingPostProcessPersistenceService:
    """封装录制后处理的产物落库逻辑。"""

    def __init__(
        self,
        *,
        artifact_service: RecordingArtifactService | None = None,
        storage_type: str | None = None,
    ) -> None:
        """初始化录制产物持久化服务。

        Args:
            artifact_service: 录制产物国标表落库服务。测试可注入 fake，生产默认使用
                `RecordingArtifactService`。
            storage_type: 对象存储类型。未传时会从任务状态或 StreamRecorder 配置兜底
                读取，只用于保持原视频元数据字段。
        """

        self.artifact_service = artifact_service or RecordingArtifactService()
        self.storage_type = storage_type

    async def save(
        self,
        *,
        task_id: str,
        task_status: dict[str, Any],
        stream_recorder: Any,
    ) -> None:
        """保存后处理产物和任务完成状态。"""

        LOGGER.info("开始保存到数据库: %s", task_id)

        upload_url = task_status.get("upload_url")
        await self._save_video_file(
            task_id=task_id,
            task_status=task_status,
            stream_recorder=stream_recorder,
            upload_url=upload_url,
        )
        await self._save_audio_file(
            task_id=task_id,
            task_status=task_status,
            stream_recorder=stream_recorder,
        )
        await self._save_cover_file(
            task_id=task_id,
            task_status=task_status,
        )

    async def _save_video_file(
        self,
        *,
        task_id: str,
        task_status: dict[str, Any],
        stream_recorder: Any,
        upload_url: str | None,
    ) -> None:
        """保存视频产物文件信息。"""

        video_upload_result = task_status.get("video_upload_result")
        if not upload_url or not video_upload_result:
            return

        video_file_id = self._ensure_file_id(
            task_status.get("video_file_id") or video_upload_result.file_id,
            file_label="视频",
        )
        # 在提交数据库前先固定 ID；即使 COMMIT 已成功但连接响应丢失，阶段重试
        # 仍会使用同一主键并由 ArtifactService 按幂等成功处理。
        task_status["video_file_id"] = video_file_id
        video_metadata = self._video_metadata(
            task_status,
            stream_recorder,
            video_upload_result,
        )
        await self.artifact_service.save_file(
            file_name=video_upload_result.file_name,
            task_id=task_id,
            file_id=video_file_id,
            file_url=upload_url,
            file_path=task_status.get("result_url", ""),
            mime_type="video/mp4",
            storage_key=video_upload_result.storage_key,
            metadata=video_metadata,
        )

    async def _save_audio_file(
        self,
        *,
        task_id: str,
        task_status: dict[str, Any],
        stream_recorder: Any,
    ) -> None:
        """保存音频产物文件信息。"""

        audio_upload_result = task_status.get("audio_upload_result")
        if not audio_upload_result:
            return

        audio_file_id = self._ensure_file_id(
            task_status.get("audio_file_id") or audio_upload_result.file_id,
            file_label="音频",
        )
        task_status["audio_file_id"] = audio_file_id
        audio_metadata = {
            "extracted_from_video": True,
            "storage_key": audio_upload_result.storage_key,
            "key": audio_upload_result.storage_key,
            "bucket": audio_upload_result.bucket,
            "md5": audio_upload_result.md5,
            "audio_info": {
                "key": audio_upload_result.storage_key,
                "bucket": audio_upload_result.bucket,
            },
        }
        await self.artifact_service.save_file(
            file_name=audio_upload_result.file_name,
            task_id=task_id,
            file_id=audio_file_id,
            file_url=audio_upload_result.file_url,
            file_path=task_status.get("audio_local_path", ""),
            mime_type=f"audio/{task_status.get('audio_format', 'mp3')}",
            storage_key=audio_upload_result.storage_key,
            metadata=audio_metadata,
        )

    async def _save_cover_file(
        self,
        *,
        task_id: str,
        task_status: dict[str, Any],
    ) -> None:
        """保存封面产物文件信息。"""

        cover_upload_result = task_status.get("cover_upload_result")
        cover_url = task_status.get("cover_url")
        if not cover_upload_result or not cover_url:
            return

        cover_file_id = self._ensure_file_id(
            task_status.get("cover_file_id") or cover_upload_result.file_id,
            file_label="封面",
        )
        task_status["cover_file_id"] = cover_file_id
        cover_info = dict(task_status.get("cover_info", {}) or {})
        cover_info["key"] = cover_upload_result.storage_key
        cover_info["bucket"] = cover_upload_result.bucket
        cover_metadata = {
            "strategy": task_status.get("cover_strategy", ""),
            "generated_from_video": True,
            "cover_info": cover_info,
            "storage_key": cover_upload_result.storage_key,
            "key": cover_upload_result.storage_key,
            "bucket": cover_upload_result.bucket,
            "md5": cover_upload_result.md5,
        }
        await self.artifact_service.save_file(
            file_name=cover_upload_result.file_name,
            task_id=task_id,
            file_id=cover_file_id,
            file_url=cover_url,
            file_path=task_status.get("cover_local_path", ""),
            mime_type="image/jpeg",
            storage_key=cover_upload_result.storage_key,
            metadata=cover_metadata,
        )

    @staticmethod
    def _ensure_file_id(value: Any, *, file_label: str) -> str:
        """确保存储返回的文件 ID 非空；为空时生成 UUID。"""

        file_id = str(value or "").strip()
        if file_id:
            return file_id
        file_id = str(uuid.uuid4())
        LOGGER.warning("%s file_id 为空，自动生成 UUID: %s", file_label, file_id)
        return file_id

    @staticmethod
    def _duration(task_status: dict[str, Any]) -> float:
        """按分片时长汇总录制结果时长。"""

        return sum(segment.get("time_len", 0) for segment in task_status.get("segments", []))

    def _storage_type(
        self,
        task_status: dict[str, Any],
        stream_recorder: Any,
    ) -> str:
        """解析对象存储类型，用于保持原结果元数据结构。"""

        configured_type = self.storage_type or task_status.get("storage_type")
        if configured_type:
            return str(configured_type).lower()

        recorder_settings = getattr(stream_recorder, "settings", None)
        storage_settings = getattr(recorder_settings, "storage", None)
        recorder_storage_type = getattr(storage_settings, "type", None)
        if recorder_storage_type:
            return str(recorder_storage_type).lower()
        return "unknown"

    def _video_metadata(
        self,
        task_status: dict[str, Any],
        stream_recorder: Any,
        upload_result: Any,
    ) -> dict[str, Any]:
        """构造视频文件元数据，保持原回调和落库字段。"""

        storage_type = self._storage_type(task_status, stream_recorder)
        metadata = {
            "duration": self._duration(task_status),
            "storage_key": upload_result.storage_key,
            "key": upload_result.storage_key,
            "bucket": upload_result.bucket,
            "md5": upload_result.md5,
            "storge_type": storage_type,
        }
        video_info = task_status.get("video_info", {})
        if video_info:
            metadata["video_info"] = {
                "width": video_info.get("width", 0),
                "height": video_info.get("height", 0),
                "video_codec": video_info.get("video_codec", ""),
                "video_bitrate": video_info.get("video_bitrate", 0),
                "frame_rate": video_info.get("frame_rate", 0.0),
                "audio_codec": video_info.get("audio_codec", ""),
                "audio_bitrate": video_info.get("audio_bitrate", 0),
                "audio_sample_rate": video_info.get("audio_sample_rate", 0),
                "audio_channels": video_info.get("audio_channels", 0),
                "key": upload_result.storage_key,
                "bucket": upload_result.bucket,
                "storge_type": storage_type,
            }
        return metadata

__all__ = ["RecordingPostProcessPersistenceService"]
