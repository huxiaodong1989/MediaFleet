"""无 IO 的视频级 seek 失败决策；计数/租约必须由后续 MySQL 执行层保存。"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SeekFailure(str, Enum):
    TRANSIENT = "transient"
    SEEK_UNSUPPORTED = "seek_unsupported"
    ACCESS_DENIED = "access_denied"
    SOURCE_MISSING = "source_missing"
    SOURCE_CHANGED = "source_changed"
    INVALID_SOURCE = "invalid_source"


class ReadAction(str, Enum):
    RETRY_SEEK = "retry_seek"
    DOWNLOAD = "download"
    FAIL = "fail"


class VideoReadPolicy(BaseModel):
    """默认初次 seek 后最多重试两轮；不是每帧各重试两次。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seek_retry_limit: int = Field(default=2, strict=True, ge=0, le=5)

    def after_seek_failure(
        self, failure: SeekFailure, *, retries_used: int
    ) -> ReadAction:
        """输入已领取的重试轮数（不含初次）；只决策，不改变预算。

        执行层应在开始重试前以租约条件更新递增持久化计数；成功一帧不能
        重置预算。此方法不用于下载/模型/COS/MQ 失败，避免回退环路。
        """
        if (
            isinstance(retries_used, bool)
            or not isinstance(retries_used, int)
            or retries_used < 0
        ):
            raise ValueError("retries_used must be a non-negative integer")
        failure = SeekFailure(failure)
        if failure == SeekFailure.SEEK_UNSUPPORTED:
            return ReadAction.DOWNLOAD
        if failure != SeekFailure.TRANSIENT:
            return ReadAction.FAIL
        if retries_used < self.seek_retry_limit:
            return ReadAction.RETRY_SEEK
        return ReadAction.DOWNLOAD
