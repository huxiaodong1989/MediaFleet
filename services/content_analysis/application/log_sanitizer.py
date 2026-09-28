"""Compatibility exports for shared log-redaction helpers."""

from media_platform.common.redaction import mask_secret, sanitize_url

__all__ = ["mask_secret", "sanitize_url"]
