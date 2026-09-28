"""ZL API 与本机录像目录合并发现测试。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import subprocess
from unittest.mock import AsyncMock

import httpx
import pytest

from services.recorder_node.recorder.record_file_discovery import (
    RecordingFilesUnavailable,
    discover_record_files,
)
from services.recorder_node.recorder.stream_recorder import StreamRecorder


STREAM_ID = "stream-1"
APP = "live"
DAY = "2026-09-08"
START = datetime(2026, 9, 8, 8, 30)
END = datetime(2026, 9, 8, 10, 10)


def _write_mp4(
    root: Path,
    name: str = "2026-09-08-08-30-18-0.mp4",
    *,
    day: str = DAY,
) -> Path:
    path = root / APP / STREAM_ID / day / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"recording")
    return path


@pytest.mark.asyncio
async def test_api_failure_falls_back_to_local_mapped_directory(tmp_path: Path) -> None:
    path = _write_mp4(tmp_path)
    api = AsyncMock(side_effect=httpx.PoolTimeout("pool busy"))
    probe = AsyncMock(return_value={"duration": 3600.0, "size": path.stat().st_size})

    result = await discover_record_files(
        stream_id=STREAM_ID,
        app=APP,
        start_time=START,
        end_time=END,
        list_files=api,
        probe=probe,
        local_roots=[tmp_path],
        attempts=1,
        retry_interval=0,
    )

    assert [item["file_path"] for item in result] == [str(path)]
    api.assert_awaited_once_with(DAY)


@pytest.mark.asyncio
async def test_api_partial_list_is_supplemented_and_deduplicated(tmp_path: Path) -> None:
    first = _write_mp4(tmp_path)
    second = _write_mp4(tmp_path, "2026-09-08-09-30-18-0.mp4")
    api = AsyncMock(
        return_value={"rootPath": str(first.parent), "paths": [first.name, first.name]}
    )
    probe = AsyncMock(return_value={"duration": 1800.0, "size": first.stat().st_size})

    result = await discover_record_files(
        stream_id=STREAM_ID,
        app=APP,
        start_time=START,
        end_time=END,
        list_files=api,
        probe=probe,
        local_roots=[tmp_path],
        attempts=1,
        retry_interval=0,
    )

    assert [item["file_path"] for item in result] == [str(first), str(second)]


@pytest.mark.asyncio
async def test_cross_midnight_uses_each_task_date_and_ignores_dotfiles(tmp_path: Path) -> None:
    first = _write_mp4(tmp_path, "23-59-30-0.mp4", day="2026-09-07")
    second = _write_mp4(tmp_path, "2026-09-08-00-00-30-2.mp4")
    _write_mp4(tmp_path, ".2026-09-08-00-01-00-0.mp4")
    api = AsyncMock(return_value={})
    probe = AsyncMock(return_value={"duration": 60.0, "size": first.stat().st_size})

    result = await discover_record_files(
        stream_id=STREAM_ID,
        app=APP,
        start_time=datetime(2026, 9, 7, 23, 59),
        end_time=datetime(2026, 9, 8, 0, 5),
        list_files=api,
        probe=probe,
        local_roots=[tmp_path],
        attempts=1,
        retry_interval=0,
    )

    assert [item["file_path"] for item in result] == [str(first), str(second)]
    assert [call.args[0] for call in api.await_args_list] == ["2026-09-07", DAY]


@pytest.mark.asyncio
async def test_file_that_changes_during_probe_is_retried(tmp_path: Path) -> None:
    path = _write_mp4(tmp_path)
    calls = 0

    async def probe(file_path: str) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            path.write_bytes(b"recording grew while probing")
        return {"duration": 60.0, "size": path.stat().st_size}

    result = await discover_record_files(
        stream_id=STREAM_ID,
        app=APP,
        start_time=START,
        end_time=END,
        list_files=AsyncMock(return_value={}),
        probe=probe,
        local_roots=[tmp_path],
        attempts=2,
        retry_interval=0,
    )

    assert result[0]["file_path"] == str(path)
    assert calls == 2


@pytest.mark.asyncio
async def test_invalid_stream_cannot_escape_recording_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        await discover_record_files(
            stream_id="../other",
            app=APP,
            start_time=START,
            end_time=END,
            list_files=AsyncMock(return_value={}),
            probe=AsyncMock(),
            local_roots=[tmp_path],
        )


@pytest.mark.asyncio
async def test_exhausted_retries_raise_instead_of_returning_empty_success(tmp_path: Path) -> None:
    with pytest.raises(RecordingFilesUnavailable, match="录制文件不可用"):
        await discover_record_files(
            stream_id=STREAM_ID,
            app=APP,
            start_time=START,
            end_time=END,
            list_files=AsyncMock(return_value={}),
            probe=AsyncMock(),
            local_roots=[tmp_path],
            attempts=2,
            retry_interval=0,
        )


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="需要 FFmpeg/ffprobe",
)
@pytest.mark.asyncio
async def test_real_mp4_is_scanned_and_probed_when_zl_api_times_out(
    tmp_path: Path,
) -> None:
    """用真实 MP4 验证目录兜底不只是 mock 级语法正确。"""

    path = tmp_path / APP / STREAM_ID / DAY / "2026-09-08-08-30-18-0.mp4"
    path.parent.mkdir(parents=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=10",
            "-t",
            "1",
            "-c:v",
            "mpeg4",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    recorder = StreamRecorder.__new__(StreamRecorder)

    result = await discover_record_files(
        stream_id=STREAM_ID,
        app=APP,
        start_time=START,
        end_time=END,
        list_files=AsyncMock(side_effect=httpx.PoolTimeout("pool busy")),
        probe=recorder._get_file_info,
        local_roots=[tmp_path],
        attempts=1,
        retry_interval=0,
    )

    assert len(result) == 1
    assert result[0]["file_path"] == str(path)
    assert result[0]["time_len"] == pytest.approx(1.0, abs=0.1)
    assert result[0]["file_size"] == path.stat().st_size
