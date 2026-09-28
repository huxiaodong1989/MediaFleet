"""可靠发现 ZLMediaKit 已完成的 MP4 录像文件。

ZL API 可能超时、返回空列表、漏项，或者返回录制节点不可直接访问的宿主机路径。
因此录像发现始终合并 API 列表和本机映射目录扫描结果，并在文件仍写入时有限重试。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
import logging
import math
import os
from pathlib import Path
import re
from typing import Any, Awaitable, Callable, Iterable


LOGGER = logging.getLogger("stream_recorder")
_RECORD_FILE_NAME = re.compile(
    r"^((?P<date>\d{4}-\d{2}-\d{2})-)?"
    r"(?P<hour>\d{2})-(?P<minute>\d{2})-(?P<second>\d{2})"
    r"(?:-(?P<sequence>\d+))?\.mp4$",
    re.IGNORECASE,
)


class RecordingFilesUnavailable(RuntimeError):
    """没有找到可用录像，或匹配文件尚未完成写入。"""


def _parse_name(name: str, day: str) -> tuple[datetime, int] | None:
    if name.startswith("."):
        return None
    match = _RECORD_FILE_NAME.fullmatch(name)
    if match is None:
        return None
    parts = match.groupdict()
    try:
        file_day = datetime.strptime(parts["date"] or day, "%Y-%m-%d").date()
        stamp = datetime.combine(
            file_day,
            time(*(int(parts[key]) for key in ("hour", "minute", "second"))),
        )
        return stamp, int(parts["sequence"] or 0)
    except ValueError:
        return None


def _snapshot(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _scan(
    directories: Iterable[tuple[Path, str]],
) -> tuple[dict[tuple[str, str], tuple[Path, tuple[int, int]]], list[str]]:
    found: dict[tuple[str, str], tuple[Path, tuple[int, int]]] = {}
    failures: list[str] = []
    for directory, day in directories:
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if (
                        entry.is_file(follow_symlinks=False)
                        and _parse_name(entry.name, day) is not None
                    ):
                        stat = entry.stat(follow_symlinks=False)
                        # 本机挂载路径优先于 API 返回的服务器路径。
                        found.setdefault(
                            (day, entry.name),
                            (Path(entry.path), (stat.st_size, stat.st_mtime_ns)),
                        )
        except OSError as exc:
            failures.append(f"{directory}: {type(exc).__name__}")
    return found, failures


async def discover_record_files(
    *,
    stream_id: str,
    app: str,
    start_time: datetime | None,
    end_time: datetime | None,
    list_files: Callable[[str], Awaitable[dict[str, Any]]],
    probe: Callable[[str], Awaitable[dict[str, Any]]],
    local_roots: Iterable[str | os.PathLike[str]] = (),
    attempts: int = 3,
    retry_interval: float = 5.0,
) -> list[dict[str, Any]]:
    """合并 API 和本机目录候选，验证文件稳定且与任务时间窗口重叠。"""

    for component in (app, stream_id):
        if (
            not component
            or component in {".", ".."}
            or any(char in component for char in "/\\:\x00")
        ):
            raise ValueError("app 或 stream_id 非法")
    if attempts < 1 or retry_interval < 0:
        raise ValueError("录像发现重试配置非法")

    end_time = end_time or datetime.now()
    start_time = start_time or datetime.combine(end_time.date(), time.min)
    if end_time < start_time:
        raise ValueError("录像结束时间早于开始时间")

    earliest = start_time - timedelta(minutes=4)
    days: list[str] = []
    current_day = earliest.date()
    while current_day <= end_time.date():
        days.append(current_day.isoformat())
        current_day += timedelta(days=1)

    probe_cache: dict[tuple[str, tuple[int, int]], dict[str, Any]] = {}
    api_roots: dict[str, Path] = {}
    segments: list[dict[str, Any]] = []
    pending: list[str] = []
    api_errors: list[str] = []
    scan_errors: list[str] = []

    for attempt in range(1, attempts + 1):
        api_errors = []
        api_names: dict[tuple[str, str], Path] = {}
        for day in days:
            try:
                data = await list_files(day)
                root_value = data.get("rootPath")
                paths = data.get("paths") or []
                if root_value and Path(root_value).is_absolute():
                    root_path = Path(root_value)
                    api_roots[day] = root_path
                    for name in paths:
                        if isinstance(name, str) and _parse_name(name, day) is not None:
                            api_names[(day, name)] = root_path / name
                LOGGER.info(
                    "ZLM录像列表: app=%s, stream_id=%s, period=%s, root_path=%s, file_count=%s",
                    app,
                    stream_id,
                    day,
                    root_value,
                    len(paths),
                )
            except Exception as exc:
                api_errors.append(type(exc).__name__)
                LOGGER.warning(
                    "ZLM录像列表请求失败，继续扫描本机目录: app=%s, stream_id=%s, "
                    "period=%s, error=%s",
                    app,
                    stream_id,
                    day,
                    type(exc).__name__,
                )

        directories = [
            (Path(root) / app / stream_id / day, day)
            for root in local_roots
            if root
            for day in days
        ]
        directories.extend((root, day) for day, root in api_roots.items())
        directories = list(dict.fromkeys(directories))
        candidates, scan_errors = await asyncio.to_thread(_scan, directories)

        for key, path in api_names.items():
            if key in candidates:
                continue
            try:
                candidates[key] = path, await asyncio.to_thread(_snapshot, path)
            except OSError:
                candidates[key] = path, None

        segments = []
        pending = []
        for (day, name), (path, stat) in candidates.items():
            parsed = _parse_name(name, day)
            if parsed is None:
                continue
            file_time, sequence = parsed
            if not earliest <= file_time <= end_time:
                continue
            if stat is None or stat[0] <= 0:
                pending.append(str(path))
                continue

            cache_key = (str(path), stat)
            info = probe_cache.get(cache_key)
            if info is None:
                info = await probe(str(path))
                duration = float(info.get("duration", 0))
                size = int(info.get("size", 0))
                if not math.isfinite(duration) or duration <= 0 or size <= 0:
                    pending.append(str(path))
                    continue
                probe_cache[cache_key] = info

            try:
                after_probe = await asyncio.to_thread(_snapshot, path)
            except OSError:
                after_probe = None
            if after_probe != stat:
                pending.append(str(path))
                continue
            if file_time + timedelta(seconds=float(info["duration"])) < start_time:
                continue
            segments.append(
                {
                    "file_name": name,
                    "file_path": str(path),
                    "start_time": file_time,
                    "time_len": float(info["duration"]),
                    "file_size": int(info["size"]),
                    "sequence": sequence,
                }
            )

        LOGGER.info(
            "录像发现结果: app=%s, stream_id=%s, attempt=%s/%s, valid=%s, "
            "pending=%s, api_errors=%s, scan_errors=%s, directories=%s",
            app,
            stream_id,
            attempt,
            attempts,
            len(segments),
            len(pending),
            api_errors,
            scan_errors,
            [str(path) for path, _ in directories],
        )
        if segments and not pending:
            return sorted(
                segments,
                key=lambda segment: (segment["start_time"], segment["sequence"]),
            )
        if attempt < attempts:
            await asyncio.sleep(retry_interval)

    raise RecordingFilesUnavailable(
        f"录制文件不可用: app={app}, stream_id={stream_id}, "
        f"time_range={start_time}~{end_time}, valid={len(segments)}, "
        f"pending={pending}, api_errors={api_errors}, scan_errors={'; '.join(scan_errors)}; "
        "已重试ZL接口及本机映射目录，请检查MP4收尾和ZLM_RECORD_LOCAL_ROOT"
    )


__all__ = ["RecordingFilesUnavailable", "discover_record_files"]
