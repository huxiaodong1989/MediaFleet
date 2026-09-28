"""录制后处理上传服务。

该服务只负责把本地录制产物上传到对象存储，并把上传结果写回
``task_status``。它不保存数据库、不发送回调，也不做本地文件清理，避免
后处理编排器继续直接持有对象存储调用细节。
"""

from __future__ import annotations

import logging
from typing import Any


LOGGER = logging.getLogger("post_processor")


class RecordingUploadService:
    """封装录制后处理阶段的视频、音频和封面上传逻辑。"""

    async def upload_video(
        self,
        *,
        task_id: str,
        result_url: str | None,
        task_status: dict[str, Any],
        stream_recorder: Any,
    ) -> Any | None:
        """上传合并后的录像文件，并把视频上传结果写入任务状态。"""

        if not result_url or not stream_recorder:
            return None

        LOGGER.info("开始上传视频文件: %s", task_id)
        upload_result = await stream_recorder.enhanced_storage.upload_file_enhanced(
            result_url,
            upload_type="record",
        )
        task_status["upload_url"] = upload_result.file_url
        task_status["video_upload_result"] = upload_result
        task_status["video_bucket"] = upload_result.bucket
        task_status["video_key"] = upload_result.storage_key

        LOGGER.info(
            "视频文件上传成功: %s, fileId: %s, bucket: %s, key: %s",
            upload_result.file_url,
            upload_result.file_id,
            upload_result.bucket,
            upload_result.storage_key,
        )
        return upload_result

    async def upload_audio(
        self,
        *,
        task_id: str,
        task_status: dict[str, Any],
        stream_recorder: Any,
    ) -> Any | None:
        """上传音频提取结果，并把音频上传结果写入任务状态。"""

        audio_local_path = task_status.get("audio_local_path")
        if not audio_local_path or not stream_recorder:
            return None

        LOGGER.info("开始上传音频文件: %s", task_id)
        upload_result = await stream_recorder.audio_storage.upload_file_enhanced(
            audio_local_path,
            upload_type="audio",
        )
        task_status["audio_upload_result"] = upload_result
        task_status["audio_result_url"] = upload_result.file_url
        task_status["audio_bucket"] = upload_result.bucket
        task_status["audio_key"] = upload_result.storage_key

        LOGGER.info(
            "音频文件上传成功: %s, fileId: %s, bucket: %s, key: %s",
            upload_result.file_url,
            upload_result.file_id,
            upload_result.bucket,
            upload_result.storage_key,
        )
        return upload_result

    async def upload_cover(
        self,
        *,
        task_id: str,
        task_status: dict[str, Any],
        stream_recorder: Any,
    ) -> Any | None:
        """上传封面提取结果，并把封面上传结果写入任务状态。"""

        cover_local_path = task_status.get("cover_local_path")
        if not cover_local_path or not stream_recorder:
            return None

        LOGGER.info("开始上传封面文件: %s", task_id)
        upload_result = await stream_recorder.enhanced_storage.upload_file_enhanced(
            cover_local_path,
            upload_type="cover",
        )
        task_status["cover_upload_result"] = upload_result
        task_status["cover_url"] = upload_result.file_url
        task_status["cover_bucket"] = upload_result.bucket
        task_status["cover_key"] = upload_result.storage_key

        LOGGER.info(
            "封面文件上传成功: %s, fileId: %s, bucket: %s, key: %s",
            upload_result.file_url,
            upload_result.file_id,
            upload_result.bucket,
            upload_result.storage_key,
        )
        return upload_result

    async def record_cover_upload(
        self,
        *,
        task_id: str,
        task_status: dict[str, Any],
    ) -> None:
        """兼容旧测试或显式阶段调用，记录封面上传状态。

        主链路已改为 ``upload_cover`` 真正执行上传；该方法保留给历史调用点使用。
        """

        cover_url = task_status.get("cover_url")
        cover_file_id = task_status.get("cover_file_id")
        if cover_url and cover_file_id:
            LOGGER.info(
                "封面已在提取阶段上传: %s, URL: %s, fileId: %s",
                task_id,
                cover_url,
                cover_file_id,
            )


__all__ = ["RecordingUploadService"]
