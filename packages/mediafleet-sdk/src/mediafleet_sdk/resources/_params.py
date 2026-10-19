"""资源查询参数构造。"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def compact_params(**values: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        result[key] = value.isoformat() if isinstance(value, datetime) else value
    return result


__all__ = ["compact_params"]
