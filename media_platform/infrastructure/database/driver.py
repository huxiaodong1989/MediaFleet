"""达梦数据库驱动加载与校验。"""

from __future__ import annotations

import importlib.util


def dm_driver_installed() -> bool:
    return (
        importlib.util.find_spec("dmPython") is not None
        and importlib.util.find_spec("dmSQLAlchemy") is not None
    )


def ensure_dm_driver() -> None:
    """
    注册达梦 SQLAlchemy 方言。
    DB_TYPE=dm 时必须在 create_engine 之前调用。
    """
    if not dm_driver_installed():
        raise RuntimeError(
            "DB_TYPE=dm 但未安装达梦驱动。请先执行: "
            "uv sync --extra dm"
        )

    import dmPython  # noqa: F401
    import dmSQLAlchemy  # noqa: F401
