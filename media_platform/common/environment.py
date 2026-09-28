"""运行时环境变量读取工具。

服务入口可以先把本服务目录下的 `.env` 加载到 `os.environ`，后续配置层仍然只从
环境变量读取。这样既满足独立部署时“配置来自进程环境”的原则，也方便本地直接
执行 `uv run python -m services.<service>.main`。
"""

from __future__ import annotations

import os
from pathlib import Path


def get_env_bool(name: str, default: bool = False) -> bool:
    """读取布尔环境变量，空值使用默认值，非法值给出清晰错误。"""

    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是布尔值，当前值为 {raw_value!r}")


def load_env_file(
    path: str | os.PathLike[str],
    *,
    override: bool = False,
    enabled: bool = True,
) -> int:
    """把 `.env` 文件中的键值加载到当前进程环境变量。

    Args:
        path: `.env` 文件路径；文件不存在时直接返回 0。
        override: 为 `True` 时覆盖已存在的进程环境变量。本地服务入口默认不覆盖，
            让命令行或启动脚本显式传入的值拥有更高优先级。
        enabled: 为 `False` 时跳过加载，供 Docker Compose、Kubernetes 等真实部署
            显式关闭应用内 `.env` fallback。

    Returns:
        实际写入 `os.environ` 的变量数量。

    Notes:
        这里只实现项目本地配置需要的简单 `.env` 语法：`KEY=VALUE`、空行和 `#`
        注释。函数不输出变量值，避免泄露数据库密码、RabbitMQ 密码或对象存储密钥。
    """

    if not enabled:
        return 0

    env_path = Path(path)
    if not env_path.exists():
        return 0

    loaded_count = 0
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        if override or key not in os.environ:
            os.environ[key] = value
            loaded_count += 1

    return loaded_count


def get_env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """读取整数环境变量，空值使用默认值，非法值给出清晰错误。

    Args:
        name: 环境变量名称。
        default: 变量不存在或为空字符串时使用的默认值。
        minimum: 允许的最小值。

    Returns:
        解析后的整数值。

    Raises:
        ValueError: 环境变量不是整数，或小于最小值。
    """

    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        value = default
    else:
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(
                f"{name} 必须是整数，当前值为 {raw_value!r}"
            ) from exc

    if value < minimum:
        raise ValueError(f"{name} 必须大于等于 {minimum}，当前值为 {value}")
    return value
