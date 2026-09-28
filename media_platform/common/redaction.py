"""Small, dependency-free helpers for safe operational logging."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def mask_secret(value: str) -> str:
    """Mask a credential while retaining enough shape for diagnostics."""

    if not value:
        return "not-configured"
    length = len(value)
    if length <= 4:
        return "*" * length
    if length <= 8:
        keep_start, keep_end = 1, 1
    elif length <= 16:
        keep_start, keep_end = 2, 2
    elif length <= 32:
        keep_start, keep_end = 4, 4
    else:
        keep_start, keep_end = 6, 4
    return value[:keep_start] + "*" * (length - keep_start - keep_end) + value[-keep_end:]


def sanitize_url(value: str | None) -> str | None:
    """Remove user information, query parameters, and fragments from a URL."""

    if not value:
        return value
    try:
        parts = urlsplit(value)
        hostname = parts.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = f":{parts.port}" if parts.port is not None else ""
    except ValueError:
        return "<invalid-url>"
    if not parts.scheme or not hostname:
        return "<relative-or-invalid-url>"
    return urlunsplit((parts.scheme, f"{hostname}{port}", parts.path, "", ""))


__all__ = ["mask_secret", "sanitize_url"]
