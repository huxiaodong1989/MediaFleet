"""受限的 FFmpeg 输入侧 seek；视频级重试和下载回退由上层编排。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import subprocess


_PTS = re.compile(r"\bpts_time:([0-9]+(?:\.[0-9]+)?)")


class FrameExtractionError(RuntimeError):
    """错误文本不得包含完整视频URL或带签名的FFmpeg日志。"""


@dataclass(frozen=True)
class ExtractedFrame:
    jpeg: bytes
    actual_pts_ms: int


@dataclass(frozen=True)
class ProbedVideo:
    duration_ms: int
    creation_time: datetime | None


class FfprobeVideoProbe:
    def __init__(self, *, executable: str = "ffprobe", timeout_seconds: int = 30):
        if timeout_seconds < 1:
            raise ValueError("ffprobe超时必须大于0")
        self.executable, self.timeout_seconds = executable, timeout_seconds

    def probe(self, source: str) -> ProbedVideo:
        if not source:
            raise ValueError("视频地址为空")
        try:
            completed = subprocess.run(
                [
                    self.executable,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:format_tags=creation_time",
                    "-of",
                    "json",
                    source,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FrameExtractionError("ffprobe_timeout") from exc
        except OSError as exc:
            raise FrameExtractionError("ffprobe_unavailable") from exc
        if completed.returncode != 0:
            raise FrameExtractionError("ffprobe_failed")
        try:
            format_info = json.loads(completed.stdout)["format"]
            duration_ms = round(float(format_info["duration"]) * 1000)
            if duration_ms < 1:
                raise ValueError("duration")
            raw_time = (format_info.get("tags") or {}).get("creation_time")
            creation_time = None
            if raw_time:
                creation_time = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                if creation_time.tzinfo is None:
                    raise ValueError("creation_time")
                creation_time = creation_time.astimezone(timezone.utc)
            return ProbedVideo(duration_ms, creation_time)
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise FrameExtractionError("ffprobe_invalid_metadata") from exc


class FfmpegFrameExtractor:
    def __init__(
        self,
        *,
        executable: str = "ffmpeg",
        timeout_seconds: int = 30,
        threads: int = 1,
        max_width: int = 1280,
        max_image_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if min(timeout_seconds, threads, max_width, max_image_bytes) < 1:
            raise ValueError("FFmpeg抽帧资源上限必须为正数")
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.threads = threads
        self.max_width = max_width
        self.max_image_bytes = max_image_bytes

    def extract(self, source: str, offset_ms: int) -> ExtractedFrame:
        if not source or offset_ms < 0:
            raise ValueError("视频地址和目标偏移无效")
        command = [
            self.executable,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "info",
            "-threads",
            str(self.threads),
            "-ss",
            f"{offset_ms / 1000:.3f}",
            "-copyts",
            "-i",
            source,
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-frames:v",
            "1",
            "-vf",
            f"showinfo,scale={self.max_width}:-2:force_original_aspect_ratio=decrease",
            "-q:v",
            "5",
            "-f",
            "image2pipe",
            "-c:v",
            "mjpeg",
            "pipe:1",
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FrameExtractionError("ffmpeg_timeout") from exc
        except OSError as exc:
            raise FrameExtractionError("ffmpeg_unavailable") from exc
        if completed.returncode != 0 or not completed.stdout:
            raise FrameExtractionError("ffmpeg_decode_failed")
        if len(completed.stdout) > self.max_image_bytes:
            raise FrameExtractionError("frame_size_limit_exceeded")
        matches = _PTS.findall(completed.stderr.decode("utf-8", errors="replace"))
        if not matches:
            raise FrameExtractionError("frame_pts_missing")
        return ExtractedFrame(completed.stdout, round(float(matches[0]) * 1000))


__all__ = [
    "ExtractedFrame",
    "FfmpegFrameExtractor",
    "FfprobeVideoProbe",
    "FrameExtractionError",
    "ProbedVideo",
]
