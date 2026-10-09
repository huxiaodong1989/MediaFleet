"""一堂课执行期间按视频共享读取尝试与本地下载回退。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from services.image_analysis.infrastructure.repositories import ImageAnalysisRepository
from services.image_analysis.read_policy import ReadAction, SeekFailure, VideoReadPolicy
from services.image_analysis.video_frames import (
    ExtractedFrame,
    FrameExtractionError,
    FfmpegFrameExtractor,
)
from services.image_analysis.video_source import HttpVideoSource, VideoSourceError


class VideoReadSession:
    """同一视频的成功帧不重复消耗seek预算；失败才切换尝试。"""

    def __init__(
        self,
        session_factory,
        extractor: FfmpegFrameExtractor,
        source: HttpVideoSource,
        *,
        task_id: str,
        school_code: str,
        generation: int,
        lease_owner: str,
        seek_retry_limit: int = 2,
        max_download_attempts: int = 2,
    ) -> None:
        self.session_factory = session_factory
        self.extractor = extractor
        self.source = source
        self.task_id = task_id
        self.school_code = school_code
        self.generation = generation
        self.lease_owner = lease_owner
        self.policy = VideoReadPolicy(seek_retry_limit=seek_retry_limit)
        self.max_download_attempts = max_download_attempts
        self._temporary = TemporaryDirectory(prefix="image-video-")
        self._attempts = {}
        self._downloads: dict[str, Path] = {}

    def close(self) -> None:
        self._temporary.cleanup()

    def _claim(self, video_id: str):
        with self.session_factory.begin() as session:
            attempt = ImageAnalysisRepository(session).claim_video_read(
                task_id=self.task_id,
                school_code=self.school_code,
                video_id=video_id,
                generation=self.generation,
                lease_owner=self.lease_owner,
                now=datetime.now(timezone.utc),
                policy=self.policy,
                max_download_attempts=self.max_download_attempts,
            )
        self._attempts[video_id] = attempt
        return attempt

    def _failure(self, video_id: str, attempt, failure: SeekFailure) -> ReadAction:
        with self.session_factory.begin() as session:
            action = ImageAnalysisRepository(session).record_video_read_failure(
                task_id=self.task_id,
                school_code=self.school_code,
                video_id=video_id,
                generation=self.generation,
                lease_owner=self.lease_owner,
                now=datetime.now(timezone.utc),
                attempt=attempt,
                failure=failure,
                policy=self.policy,
                max_download_attempts=self.max_download_attempts,
                retry_delay=timedelta(0),
            )
        self._attempts.pop(video_id, None)
        return action

    @staticmethod
    def _classify(error: Exception) -> SeekFailure:
        if isinstance(error, VideoSourceError):
            if error.code == "source_access_denied":
                return SeekFailure.ACCESS_DENIED
            if error.code == "source_missing":
                return SeekFailure.SOURCE_MISSING
            if error.code == "source_changed":
                return SeekFailure.SOURCE_CHANGED
            if error.code == "source_not_allowed":
                return SeekFailure.INVALID_SOURCE
        return SeekFailure.TRANSIENT

    def extract(self, video_id: str, url: str, offset_ms: int) -> ExtractedFrame:
        while True:
            attempt = self._attempts.get(video_id) or self._claim(video_id)
            try:
                if attempt.mode == "SEEK":
                    return self.extractor.extract(url, offset_ms)
                local = self._downloads.get(video_id)
                if local is None:
                    local = Path(self._temporary.name) / f"{video_id}.mp4"
                    probe = self.source.probe_range(url)
                    self.source.download(url, target=local, probe=probe)
                    self._downloads[video_id] = local
                return self.extractor.extract(str(local), offset_ms)
            except (FrameExtractionError, VideoSourceError) as error:
                action = self._failure(video_id, attempt, self._classify(error))
                if action == ReadAction.FAIL:
                    raise FrameExtractionError("video_read_exhausted") from None
                if attempt.mode == "DOWNLOAD":
                    old_file = self._downloads.pop(video_id, None)
                    if old_file is not None:
                        old_file.unlink(missing_ok=True)

    def finish(self) -> None:
        for video_id, attempt in self._attempts.items():
            with self.session_factory.begin() as session:
                ImageAnalysisRepository(session).complete_video_read(
                    task_id=self.task_id,
                    school_code=self.school_code,
                    video_id=video_id,
                    generation=self.generation,
                    lease_owner=self.lease_owner,
                    now=datetime.now(timezone.utc),
                    attempt=attempt,
                )


__all__ = ["VideoReadSession"]
