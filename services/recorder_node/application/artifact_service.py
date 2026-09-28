"""录制节点产物文件落库服务。

录制节点完成视频、音频、封面上传后，需要把对象存储产物写入国标媒体文件表
`media_artifact`。本服务只负责产物文件事实入库，不执行 FFmpeg、不上传文件、不发送
业务回调，避免 `StreamRecorder` 继续承担数据库细节。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import os
import uuid
from typing import Any

from sqlalchemy import select

from media_platform.infrastructure.database.models import MediaFileModel, MediaTaskModel
from media_platform.infrastructure.database.session import SessionLocal


LOGGER = logging.getLogger(__name__)


class RecordingArtifactService:
    """保存录制产物文件到国标媒体文件表。"""

    def __init__(
        self,
        async_session_factory: Callable[..., Any] | None = None,
        *,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        # 生产默认使用每次调用独立创建的同步 Session，并放到工作线程执行。
        # recorder 后处理协程来自长期事件循环，使用全局 aiomysql 连接池时曾在
        # 高并发下触发 PyMySQL ``Packet sequence number wrong``。保留异步工厂
        # 注入口只用于兼容现有测试和明确需要异步会话的调用方。
        self.async_session_factory = async_session_factory
        self.session_factory = session_factory or SessionLocal

    async def save_file(
        self,
        *,
        file_name: str,
        task_id: str,
        file_id: str | None,
        file_url: str,
        file_path: str,
        mime_type: str,
        storage_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """保存单个录制产物，并返回最终文件 ID。

        `task_id` 必须已经存在于 `media_task`。如果任务事实不存在，说明调用中心
        没有先创建录制任务上下文，此时直接失败，避免写出孤儿文件。
        """

        final_file_id = (file_id or "").strip()
        if not final_file_id:
            final_file_id = str(uuid.uuid4())
            LOGGER.warning("file_id 为空，自动生成 UUID: %s", final_file_id)

        metadata = dict(metadata or {})
        file_type = self.infer_media_file_type(mime_type)
        relative_path = storage_key or None
        file_size = self._safe_file_size(file_path)
        bucket_name = metadata.get("bucket") or self._extract_cos_bucket(file_url)

        try:
            if self.async_session_factory is not None:
                await self._save_file_async(
                    final_file_id=final_file_id,
                    task_id=task_id,
                    file_type=file_type,
                    file_name=file_name,
                    file_url=file_url,
                    relative_path=relative_path,
                    bucket_name=bucket_name,
                    file_size=file_size,
                    mime_type=mime_type,
                    metadata=metadata,
                )
            else:
                await asyncio.to_thread(
                    self._save_file_sync,
                    final_file_id=final_file_id,
                    task_id=task_id,
                    file_type=file_type,
                    file_name=file_name,
                    file_url=file_url,
                    relative_path=relative_path,
                    bucket_name=bucket_name,
                    file_size=file_size,
                    mime_type=mime_type,
                    metadata=metadata,
                )
        except Exception:
            LOGGER.exception("保存录制产物文件信息到数据库失败: task_id=%s, file_name=%s", task_id, file_name)
            raise

        LOGGER.info(
            "录制产物文件信息已保存到国标媒体文件表: task_id=%s, file_name=%s, "
            "file_id=%s, file_type=%s",
            task_id,
            file_name,
            final_file_id,
            file_type,
        )
        return final_file_id

    async def _save_file_async(self, **values: Any) -> None:
        """兼容测试和显式异步会话注入，并提供相同幂等语义。"""

        async with self.async_session_factory() as db:
            existing = await db.execute(
                select(MediaFileModel.id).where(
                    MediaFileModel.id == values["final_file_id"],
                    MediaFileModel.task_id == values["task_id"],
                )
            )
            if existing.scalar_one_or_none() is not None:
                LOGGER.info(
                    "录制产物已存在，按幂等成功处理: task_id=%s, file_id=%s",
                    values["task_id"],
                    values["final_file_id"],
                )
                return

            task_result = await db.execute(
                select(MediaTaskModel.school_code).where(
                    MediaTaskModel.id == values["task_id"]
                )
            )
            school_code = task_result.scalar_one_or_none()
            if not school_code:
                self._raise_missing_task(values["task_id"])
            db.add(self._build_media_file(values, school_code))
            await db.commit()

    def _save_file_sync(self, **values: Any) -> None:
        """使用独立同步 Session 保存产物，避免跨事件循环复用异步连接。"""

        with self.session_factory() as db:
            existing = db.scalar(
                select(MediaFileModel.id).where(
                    MediaFileModel.id == values["final_file_id"],
                    MediaFileModel.task_id == values["task_id"],
                )
            )
            if existing is not None:
                LOGGER.info(
                    "录制产物已存在，按幂等成功处理: task_id=%s, file_id=%s",
                    values["task_id"],
                    values["final_file_id"],
                )
                return

            school_code = db.scalar(
                select(MediaTaskModel.school_code).where(
                    MediaTaskModel.id == values["task_id"]
                )
            )
            if not school_code:
                self._raise_missing_task(values["task_id"])
            db.add(self._build_media_file(values, school_code))
            db.commit()

    @staticmethod
    def _build_media_file(values: dict[str, Any], school_code: str) -> MediaFileModel:
        """构造统一的国标媒体文件 ORM 实体。"""

        return MediaFileModel(
            id=values["final_file_id"],
            task_id=values["task_id"],
            file_type=values["file_type"],
            file_name=values["file_name"],
            file_url=values["file_url"],
            relative_path=values["relative_path"],
            bucket_name=values["bucket_name"],
            file_size=values["file_size"],
            mime_type=values["mime_type"],
            extra_info=values["metadata"],
            school_code=school_code,
            created_by="recorder-node",
            updated_by="recorder-node",
        )

    @staticmethod
    def _raise_missing_task(task_id: str) -> None:
        raise ValueError(
            f"录制任务事实不存在，无法保存媒体文件: task_id={task_id}, "
            "请确认调用中心已写入 media_task"
        )

    @staticmethod
    def infer_media_file_type(mime_type: str) -> str:
        """按 MIME 类型推断国标媒体文件类型。"""

        normalized = (mime_type or "").lower()
        if normalized.startswith("video/"):
            return "VIDEO"
        if normalized.startswith("audio/"):
            return "AUDIO"
        if normalized.startswith("image/"):
            return "COVER"
        if normalized in {"text/vtt", "application/x-subrip"}:
            return "SUBTITLE"
        return "OTHER"

    @staticmethod
    def _safe_file_size(file_path: str) -> int:
        """读取本地文件大小；文件已被清理或路径为空时返回 0。"""

        if not file_path:
            return 0
        try:
            return os.path.getsize(file_path) if os.path.exists(file_path) else 0
        except OSError:
            return 0

    @staticmethod
    def _extract_cos_bucket(file_url: str) -> str | None:
        """从 COS URL 域名中提取 bucket；无法识别时返回 None。"""

        normalized = (file_url or "").lower()
        if "cos" not in normalized or ".myqcloud.com" not in normalized:
            return None
        try:
            url_parts = file_url.split("/")
            domain_parts = url_parts[2].split(".")
            return domain_parts[0] if domain_parts else None
        except Exception:
            LOGGER.warning("从 COS URL 提取 bucket 失败: %s", file_url)
            return None


__all__ = ["RecordingArtifactService"]
