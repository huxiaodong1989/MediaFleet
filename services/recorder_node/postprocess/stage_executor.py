"""录制后处理阶段执行器。

`PostProcessingManager` 只负责队列、并发、阶段日志和失败出口；本执行器负责把一个
阶段映射到实际的 recorder-node 私有服务调用。这样后续新增阶段时，只需要在这里
补充具体动作，不把管理器重新写成大型 if/elif 编排器。
"""

from __future__ import annotations

import logging
from typing import Any

from services.recorder_node.postprocess.cleanup_service import RecordingCleanupService
from services.recorder_node.postprocess.media_processing_service import (
    RecordingMediaProcessingService,
)
from services.recorder_node.postprocess.persistence_service import (
    RecordingPostProcessPersistenceService,
)
from services.recorder_node.postprocess.stages import PostProcessStage
from services.recorder_node.postprocess.upload_service import RecordingUploadService

LOGGER = logging.getLogger("post_processor")


class RecordingPostProcessStageExecutor:
    """执行录制后处理中的单个阶段。

    该类是 recorder-node 私有组件，不进入 `media_platform`。它只接收已经装配好的
    本机服务实例，避免管理器直接了解每个阶段的细节。
    """

    def __init__(
        self,
        *,
        media_processing_service: RecordingMediaProcessingService,
        upload_service: RecordingUploadService,
        persistence_service: RecordingPostProcessPersistenceService,
        cleanup_service: RecordingCleanupService,
        result_notification_service: Any | None = None,
    ) -> None:
        self.media_processing_service = media_processing_service
        self.upload_service = upload_service
        self.persistence_service = persistence_service
        self.cleanup_service = cleanup_service
        self.result_notification_service = result_notification_service

    async def execute(
        self,
        *,
        stage: PostProcessStage,
        task_id: str,
        result_url: str,
        task_status: dict[str, Any],
        stream_recorder: Any,
        mp4_files: list[dict[str, Any]],
    ) -> None:
        """执行指定阶段。

        Args:
            stage: 当前后处理阶段。
            task_id: 录制任务号。
            result_url: 合并后的本地视频文件路径。
            task_status: 本机任务状态字典，会在各阶段持续写入中间结果。
            stream_recorder: 当前录制器实例，提供历史兼容的薄委托方法。
            mp4_files: 本次录制命中的 MP4 分片列表。

        Raises:
            ValueError: 阶段未注册，说明编排和执行器定义不一致。
            Exception: 具体阶段执行失败时原样抛出，由管理器统一处理失败出口。
        """

        if stage == PostProcessStage.VIDEO_INFO:
            await self.media_processing_service.collect_video_info(
                task_id=task_id,
                result_url=result_url,
                task_status=task_status,
                stream_recorder=stream_recorder,
            )
            return

        if stage == PostProcessStage.AUDIO_EXTRACT:
            await self.media_processing_service.extract_audio(
                task_id=task_id,
                result_url=result_url,
                task_status=task_status,
                stream_recorder=stream_recorder,
            )
            return

        if stage == PostProcessStage.COVER_EXTRACT:
            await self.media_processing_service.extract_cover(
                task_id=task_id,
                result_url=result_url,
                task_status=task_status,
                stream_recorder=stream_recorder,
            )
            return

        if stage == PostProcessStage.VIDEO_UPLOAD:
            await self.upload_service.upload_video(
                task_id=task_id,
                result_url=result_url,
                task_status=task_status,
                stream_recorder=stream_recorder,
            )
            return

        if stage == PostProcessStage.AUDIO_UPLOAD:
            await self.upload_service.upload_audio(
                task_id=task_id,
                task_status=task_status,
                stream_recorder=stream_recorder,
            )
            return

        if stage == PostProcessStage.COVER_UPLOAD:
            await self.upload_service.upload_cover(
                task_id=task_id,
                task_status=task_status,
                stream_recorder=stream_recorder,
            )
            return

        if stage == PostProcessStage.DB_SAVE:
            await self.persistence_service.save(
                task_id=task_id,
                task_status=task_status,
                stream_recorder=stream_recorder,
            )
            return

        if stage == PostProcessStage.CALLBACK:
            await self._send_result_notification(
                task_id=task_id,
                task_status=task_status,
                stream_recorder=stream_recorder,
            )
            return

        if stage == PostProcessStage.CLEANUP:
            await self.cleanup_service.cleanup(
                task_id=task_id,
                result_url=result_url,
                task_status=task_status,
                mp4_files=mp4_files,
                record_root=self._resolve_record_root(stream_recorder),
            )
            return

        raise ValueError(f"未注册的录制后处理阶段: {stage}")

    @staticmethod
    def _resolve_record_root(stream_recorder: Any) -> str | None:
        """取得 recorder 进程实际可见的 ZL 录像卷根目录。"""

        settings = getattr(stream_recorder, "settings", None)
        zlm_settings = getattr(settings, "zlm", None)
        cleanup_settings = getattr(settings, "record_cleanup", None)
        config = getattr(stream_recorder, "config", None) or {}
        return (
            getattr(zlm_settings, "record_local_root", None)
            or config.get("ZLM_RECORD_LOCAL_ROOT")
            or getattr(cleanup_settings, "record_base_path", None)
        )

    async def _send_result_notification(
        self,
        *,
        task_id: str,
        task_status: dict[str, Any],
        stream_recorder: Any,
    ) -> None:
        """发送录制结果通知，并保持原取消后完成的业务状态补充字段。"""

        LOGGER.info("开始发送回调通知: %s", task_id)

        task_status["status"] = "completed"
        task_status["progress"] = 100.0
        notification_service = self._resolve_notification_service(stream_recorder)
        if notification_service is None:
            LOGGER.warning(
                "录制结果通知服务未初始化，跳过结果通知: task_id=%s",
                task_id,
            )
        else:
            await notification_service.notify(
                task_id=task_id,
                task_status=task_status,
            )

        was_canceled = task_status.get("early_termination", False)
        termination_reason = task_status.get("termination_reason", "")
        if was_canceled:
            task_status["completed_with_cancellation"] = True
            task_status["cancellation_note"] = (
                f"任务被取消但已完成录制和处理：{termination_reason}"
            )
            LOGGER.info(
                "被取消的任务已完成录制和后续处理: %s, 原因: %s",
                task_id,
                termination_reason,
            )

    def _resolve_notification_service(self, stream_recorder: Any) -> Any | None:
        """解析录制结果通知服务。

        正式链路中 `StreamRecorder` 已在初始化时装配 `result_notification_service`；
        后处理阶段直接使用该服务，避免继续通过
        `StreamRecorder._send_callback_notification()` 薄委托绕行。
        """

        if self.result_notification_service is not None:
            return self.result_notification_service
        return getattr(stream_recorder, "result_notification_service", None)
