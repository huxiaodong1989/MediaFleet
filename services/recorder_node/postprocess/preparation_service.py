"""录制结果准备服务。

录制主协程在边界时刻只停止 ZLMediaKit 并入队。本服务在受限并发的
后处理 Worker 中等待 MP4 收尾、合并 API 与本机目录扫描结果、探测文件，
并在多分片时产生合并文件。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
from typing import Any


LOGGER = logging.getLogger("post_processor")


class RecordingPreparationService:
    """将录像发现和结果文件准备从录制循环中拆出。"""

    def __init__(self, *, finalize_wait_seconds: float = 5.0) -> None:
        self.finalize_wait_seconds = max(0.0, finalize_wait_seconds)

    async def prepare(self, post_task: Any) -> None:
        """准备后续阶段所需的稳定本地视频文件。"""

        if post_task.recording_error:
            raise RuntimeError(post_task.recording_error)
        recorder = post_task.stream_recorder
        window = post_task.recording_window
        if recorder is None or window is None:
            raise RuntimeError("录制准备阶段缺少录制器或时间范围")

        stopped_at = window["stopped_at"]
        remaining = self.finalize_wait_seconds - (
            datetime.now() - stopped_at
        ).total_seconds()
        if remaining > 0:
            await asyncio.sleep(remaining)

        status = post_task.task_status
        LOGGER.info(
            "后处理队列开始发现录像: task_id=%s, app=%s, stream_id=%s, "
            "start=%s, end=%s",
            post_task.task_id,
            status.get("app", "live"),
            status.get("stream_id"),
            window["start_time"],
            window["end_time"],
        )
        files = await recorder._get_mp4_record_files_with_app(
            status["stream_id"],
            status.get("app", "live"),
            window["start_time"],
            window["end_time"],
        )
        if not files:
            raise RuntimeError(f"未发现有效录制文件: {post_task.task_id}")

        post_task.mp4_files = files
        status["segments"] = files
        status["source_recording_files"] = [item["file_path"] for item in files]
        status["work_dir"] = recorder._get_recording_work_dir(post_task.task_id)
        result_url = await recorder._process_result_files(post_task.task_id, files)
        if not result_url or not os.path.exists(result_url):
            raise RuntimeError(f"录制结果文件准备失败: {post_task.task_id}")

        post_task.result_url = result_url
        status["result_url"] = result_url
        status["result_is_source_file"] = result_url in status["source_recording_files"]
        status["post_processing_state"] = "processing"
        status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        LOGGER.info(
            "录像准备完成: task_id=%s, segment_count=%s, result=%s, source_file=%s",
            post_task.task_id,
            len(files),
            result_url,
            status["result_is_source_file"],
        )


__all__ = ["RecordingPreparationService"]
