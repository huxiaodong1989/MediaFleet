"""单视频相对时间采样；不推断多摄像头课堂时间轴。"""

DEFAULT_SAMPLE_INTERVAL_MS = 120_000


def sample_offsets_ms(
    duration_ms: int,
    interval_ms: int = DEFAULT_SAMPLE_INTERVAL_MS,
    *,
    start_ms: int = 0,
) -> range:
    """生成 [start_ms, duration_ms) 内的采样点，惰性返回避免大数组。

    duration_ms 由后续受限 ffprobe 取得；未知时长不能填 0。0 仅表示
    已确认的空视频，执行层应将空计划作为无有效视频处理，而非成功课堂。
    """
    for name, value in (
        ("duration_ms", duration_ms),
        ("interval_ms", interval_ms),
        ("start_ms", start_ms),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
    if duration_ms < 0 or start_ms < 0 or interval_ms <= 0:
        raise ValueError("duration/start must be non-negative and interval positive")
    return range(start_ms, duration_ms, interval_ms)
