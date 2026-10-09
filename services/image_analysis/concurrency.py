"""实例内资源额度；不是任务队列、分布式租约或模型提供方全局限流。"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from enum import Enum
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field


class ConcurrencyStage(str, Enum):
    CLASSROOM = "classroom"
    FFMPEG = "ffmpeg"
    DOWNLOAD = "download"
    UPLOAD = "upload"
    MODEL = "model"


ENV_FIELDS = {
    "IMAGE_ANALYSIS_MAX_CONCURRENT_CLASSROOMS": "max_concurrent_classrooms",
    "IMAGE_ANALYSIS_FFMPEG_CONCURRENCY": "ffmpeg_concurrency",
    "IMAGE_ANALYSIS_DOWNLOAD_CONCURRENCY": "download_concurrency",
    "IMAGE_ANALYSIS_UPLOAD_CONCURRENCY": "upload_concurrency",
    "IMAGE_ANALYSIS_MODEL_CONCURRENCY": "model_concurrency",
    "IMAGE_ANALYSIS_FFMPEG_THREADS": "ffmpeg_threads",
}


class ImageConcurrencySettings(BaseModel):
    """保守缺省值；64课堂是可配置目标，不是默认值或已通过压测的承诺。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_concurrent_classrooms: int = Field(default=4, strict=True, ge=1)
    ffmpeg_concurrency: int = Field(default=4, strict=True, ge=1)
    download_concurrency: int = Field(default=1, strict=True, ge=1)
    upload_concurrency: int = Field(default=4, strict=True, ge=1)
    model_concurrency: int = Field(default=4, strict=True, ge=1)
    ffmpeg_threads: int = Field(default=1, strict=True, ge=1)

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> ImageConcurrencySettings:
        """只读图像服务额度变量，不读取.env或共享业务配置；错误立即阻止启动。"""
        source = os.environ if environ is None else environ
        values = {}
        for env_name, field in ENV_FIELDS.items():
            if env_name not in source:
                continue
            raw = source[env_name]
            if not isinstance(raw, str) or not re.fullmatch(r"[0-9]+", raw.strip()):
                raise ValueError(f"{env_name} must be a positive integer")
            value = int(raw.strip())
            if value < 1:
                raise ValueError(f"{env_name} must be a positive integer")
            values[field] = value
        return cls(**values)

    @property
    def consumer_prefetch(self) -> int:
        """供后续单消费者装配使用，不能另设无限prefetch(0)。"""
        return self.max_concurrent_classrooms

    def stage_limits(self) -> dict[ConcurrencyStage, int]:
        return {
            ConcurrencyStage.CLASSROOM: self.max_concurrent_classrooms,
            ConcurrencyStage.FFMPEG: self.ffmpeg_concurrency,
            ConcurrencyStage.DOWNLOAD: self.download_concurrency,
            ConcurrencyStage.UPLOAD: self.upload_concurrency,
            ConcurrencyStage.MODEL: self.model_concurrency,
        }


class InstanceConcurrency:
    """跨线程安全、非等待的额度预留。同一实例所有课堂共享同一对象。

    未取得额度必须留在持久可恢复状态，不在Pika回调中等待，不无限新建任务。
    课堂额度不证明MySQL租约归属；后续执行器必须同时具备有效持久租约。
    一个进程一个实例；多个Uvicorn worker不能把各自额度误称容器总额度。
    """

    def __init__(self, settings: ImageConcurrencySettings):
        self._limits = settings.stage_limits()
        self._active = dict.fromkeys(self._limits, 0)
        self._lock = Lock()

    @contextmanager
    def try_slot(self, stage: ConcurrencyStage) -> Iterator[bool]:
        stage = ConcurrencyStage(stage)
        with self._lock:
            acquired = self._active[stage] < self._limits[stage]
            if acquired:
                self._active[stage] += 1
        try:
            yield acquired
        finally:
            if acquired:
                with self._lock:
                    self._active[stage] -= 1

    def snapshot(self) -> dict[str, dict[str, int]]:
        """本机观测快照，后续可供心跳使用；不能作为DB任务状态。"""
        with self._lock:
            return {
                stage.value: {"limit": limit, "active": self._active[stage]}
                for stage, limit in self._limits.items()
            }
