"""Compatibility exports for shared log-redaction helpers."""

from media_platform.common.redaction import mask_secret, sanitize_url


def sanitize_log_value(value: object, *, max_length: int = 200) -> str:
    """压平外部业务字段，避免换行注入日志并限制单字段长度。"""

    normalized = " ".join(str(value or "").replace("\x00", "").split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + "…"

__all__ = ["mask_secret", "sanitize_log_value", "sanitize_url"]
