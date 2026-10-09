from unittest.mock import patch
import subprocess

import pytest

from services.image_analysis.video_frames import (
    FfmpegFrameExtractor,
    FfprobeVideoProbe,
    FrameExtractionError,
)


def test_ffmpeg_uses_input_seek_and_reports_actual_pts():
    frame = FfmpegFrameExtractor(threads=1)
    with patch("services.image_analysis.video_frames.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(
            [],
            0,
            b"\xff\xd8jpeg\xff\xd9",
            b"[Parsed_showinfo_0] n:0 pts:120040 pts_time:120.04",
        )
        result = frame.extract("https://test.invalid/private?signature=secret", 120_000)
    args = run.call_args.args[0]
    assert args.index("-ss") < args.index("-i")
    assert result.actual_pts_ms == 120_040
    assert result.jpeg.startswith(b"\xff\xd8")


def test_ffmpeg_errors_never_expose_source_url():
    frame = FfmpegFrameExtractor()
    with patch("services.image_analysis.video_frames.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(
            [], 1, b"", b"https://test.invalid/?signature=secret failed"
        )
        with pytest.raises(FrameExtractionError) as error:
            frame.extract("https://test.invalid/?signature=secret", 0)
    assert "secret" not in str(error.value)


def test_ffprobe_returns_duration_and_utc_creation_time():
    with patch("services.image_analysis.video_frames.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(
            [],
            0,
            b'{"format":{"duration":"3602.000000","tags":{"creation_time":"2026-03-13T02:28:03.000000Z"}}}',
            b"",
        )
        result = FfprobeVideoProbe().probe("https://test.invalid/?signature=secret")
    assert result.duration_ms == 3_602_000
    assert result.creation_time.isoformat() == "2026-03-13T02:28:03+00:00"
