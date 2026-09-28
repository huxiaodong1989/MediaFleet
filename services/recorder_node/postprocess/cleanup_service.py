"""录制后处理本地文件清理服务。

后处理编排完整成功后，录制节点需要立即删除本任务使用的 ZL 原始分片，以及合并
视频、音频和封面等本地派生文件。定时录像清理器只兜底处理这里删除失败的遗留文件，
不是正常任务的空间回收主链路。
"""

from __future__ import annotations

import logging
import os
from typing import Any


LOGGER = logging.getLogger("post_processor")


class RecordingCleanupService:
    """封装录制后处理本地文件清理逻辑。"""

    async def cleanup(
        self,
        *,
        task_id: str,
        result_url: str | None,
        task_status: dict[str, Any],
        mp4_files: list[dict[str, Any]],
        record_root: str | None = None,
    ) -> None:
        """清理已成功上传、落库并通知完成的任务本地文件。"""

        LOGGER.info("开始清理本地文件: %s", task_id)

        cleanup_errors: list[str] = []
        source_paths = self._unique_paths(
            file_info.get("file_path") for file_info in mp4_files
        )
        if source_paths and not record_root:
            message = "录像根目录未配置，拒绝删除任务原始分片"
            LOGGER.error("%s: task_id=%s", message, task_id)
            cleanup_errors.append(message)
        for source_path in source_paths if record_root else ():
            if not self._is_within_root(source_path, record_root):
                message = f"原始分片不在录像根目录内，拒绝删除: {source_path}"
                LOGGER.error("%s, task_id=%s", message, task_id)
                cleanup_errors.append(message)
                continue
            error = self._remove_file(
                source_path,
                success_log="已删除任务原始录像分片",
                failure_log="删除任务原始录像分片失败，等待定时清理器兜底",
            )
            if error:
                cleanup_errors.append(error)

        work_dir = task_status.get("work_dir")
        if (
            result_url
            and self._normalized_path(result_url) not in source_paths
            and self._is_within_work_dir(result_url, work_dir)
        ):
            error = self._remove_file(
                result_url,
                success_log="已删除任务派生视频文件",
                failure_log="删除任务派生视频文件失败",
            )
            if error:
                cleanup_errors.append(error)
        audio_path = task_status.get("audio_local_path")
        if self._is_within_work_dir(audio_path, work_dir):
            error = self._remove_file(
                audio_path,
                success_log="已删除音频文件",
                failure_log="删除音频文件失败",
            )
            if error:
                cleanup_errors.append(error)
        cover_path = task_status.get("cover_file_path")
        if self._is_within_work_dir(cover_path, work_dir):
            error = self._remove_file(
                cover_path,
                success_log="已删除封面文件",
                failure_log="删除封面文件失败",
            )
            if error:
                cleanup_errors.append(error)

        if cleanup_errors:
            task_status["cleanup_pending"] = True
            task_status["cleanup_errors"] = cleanup_errors
        else:
            task_status["cleanup_pending"] = False
            task_status.pop("cleanup_errors", None)

        was_canceled = task_status.get("early_termination", False)
        if task_status.get("should_delete_from_memory", False) and not was_canceled:
            LOGGER.info("录制完成后从内存中删除任务: %s", task_id)
        elif was_canceled:
            LOGGER.info("录制中取消的任务完成，保留内存记录: %s", task_id)
        else:
            LOGGER.info("录制任务完成: %s", task_id)

    @staticmethod
    def _remove_file(
        file_path: Any,
        *,
        success_log: str,
        failure_log: str,
    ) -> str | None:
        """删除单个文件；不存在视为幂等成功，失败返回可记录的错误。"""

        if not file_path:
            return None
        normalized_path = str(file_path)
        if not os.path.exists(normalized_path):
            return None
        try:
            os.remove(normalized_path)
            LOGGER.debug("%s: %s", success_log, normalized_path)
            return None
        except Exception as exc:
            LOGGER.warning("%s: %s, 错误: %s", failure_log, normalized_path, exc)
            return f"{failure_log}: {normalized_path}, 错误: {type(exc).__name__}"

    @classmethod
    def _unique_paths(cls, file_paths: Any) -> set[str]:
        """规范化并去重任务发现到的精确源文件路径。"""

        return {
            normalized
            for file_path in file_paths
            if file_path and (normalized := cls._normalized_path(file_path))
        }

    @staticmethod
    def _normalized_path(file_path: Any) -> str:
        """返回用于安全校验和去重的真实绝对路径。"""

        if not file_path:
            return ""
        return os.path.normcase(os.path.realpath(os.path.abspath(str(file_path))))

    @classmethod
    def _is_within_root(cls, file_path: Any, root_dir: Any) -> bool:
        """只允许删除 recorder 配置录像根目录内的源文件。"""

        if not file_path or not root_dir:
            return False
        try:
            path = cls._normalized_path(file_path)
            root = cls._normalized_path(root_dir)
            return os.path.commonpath([path, root]) == root and path != root
        except (OSError, ValueError):
            return False

    @classmethod
    def _is_within_work_dir(cls, file_path: Any, work_dir: Any) -> bool:
        """只允许清理任务工作目录中的派生文件。"""

        return cls._is_within_root(file_path, work_dir)


__all__ = ["RecordingCleanupService"]
