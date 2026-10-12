"""CLI context 配置文件。"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mediafleet_cli.errors import LocalConfigurationError

CONFIG_VERSION = 1
CONTEXT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class ContextConfig:
    url: str
    ca_bundle: str | None = None

    @classmethod
    def from_dict(cls, value: Any, *, name: str) -> ContextConfig:
        if not isinstance(value, dict):
            raise LocalConfigurationError(f"context {name!r} 必须是对象")
        unknown = set(value) - {"url", "ca_bundle"}
        if unknown:
            raise LocalConfigurationError(
                f"context {name!r} 包含未知字段: {', '.join(sorted(unknown))}"
            )
        return cls(
            url=validate_url(value.get("url")),
            ca_bundle=validate_ca_bundle(value.get("ca_bundle")),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"url": self.url}
        if self.ca_bundle is not None:
            result["ca_bundle"] = self.ca_bundle
        return result


@dataclass
class CLIConfig:
    current_context: str | None = None
    contexts: dict[str, ContextConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Any) -> CLIConfig:
        if not isinstance(value, dict):
            raise LocalConfigurationError("配置文件根节点必须是对象")
        unknown = set(value) - {"version", "current_context", "contexts"}
        if unknown:
            raise LocalConfigurationError(
                f"配置文件包含未知字段: {', '.join(sorted(unknown))}"
            )
        version = value.get("version", CONFIG_VERSION)
        if version != CONFIG_VERSION:
            raise LocalConfigurationError(f"不支持的配置文件版本: {version!r}")
        raw_contexts = value.get("contexts", {})
        if not isinstance(raw_contexts, dict):
            raise LocalConfigurationError("contexts 必须是对象")
        contexts: dict[str, ContextConfig] = {}
        for name, context in raw_contexts.items():
            validate_context_name(name)
            contexts[name] = ContextConfig.from_dict(context, name=name)
        current_context = value.get("current_context")
        if current_context is not None:
            validate_context_name(current_context)
            if current_context not in contexts:
                raise LocalConfigurationError(
                    f"当前 context {current_context!r} 不存在"
                )
        return cls(current_context=current_context, contexts=contexts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": CONFIG_VERSION,
            "current_context": self.current_context,
            "contexts": {
                name: context.to_dict()
                for name, context in sorted(self.contexts.items())
            },
        }


def default_config_path() -> Path:
    override = os.getenv("MEDIAFLEET_CONFIG_FILE")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "MediaFleet" / "config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MediaFleet" / "config.json"
    base = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "mediafleet" / "config.json"


def load_config(path: Path | None = None) -> CLIConfig:
    config_path = path or default_config_path()
    if not config_path.exists():
        return CLIConfig()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalConfigurationError(
            f"无法读取配置文件 {config_path}: {exc}"
        ) from exc
    return CLIConfig.from_dict(raw)


def save_config(config: CLIConfig, path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            dir=config_path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(
                    config.to_dict(),
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                temporary_path.chmod(0o600)
            os.replace(temporary_path, config_path)
        finally:
            temporary_path.unlink(missing_ok=True)
    except OSError as exc:
        raise LocalConfigurationError(
            f"无法写入配置文件 {config_path}: {exc}"
        ) from exc
    return config_path


def validate_context_name(value: Any) -> str:
    if not isinstance(value, str) or not CONTEXT_NAME_PATTERN.fullmatch(value):
        raise LocalConfigurationError(
            "context 名称必须以字母或数字开头，并且只能包含字母、数字、点、下划线和连字符"
        )
    return value


def validate_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LocalConfigurationError("控制中心 URL 不能为空")
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LocalConfigurationError("控制中心 URL 必须是完整的 HTTP 或 HTTPS 地址")
    if parsed.username is not None or parsed.password is not None:
        raise LocalConfigurationError("控制中心 URL 不能包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise LocalConfigurationError("控制中心 URL 不能包含查询参数或片段")
    return candidate.rstrip("/")


def validate_ca_bundle(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LocalConfigurationError("CA 文件路径不能为空")
    return str(Path(value).expanduser().resolve())


__all__ = [
    "CLIConfig",
    "CONFIG_VERSION",
    "ContextConfig",
    "default_config_path",
    "load_config",
    "save_config",
    "validate_ca_bundle",
    "validate_context_name",
    "validate_url",
]
