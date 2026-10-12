"""查询命令的运行时配置解析。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mediafleet_sdk import MediaFleetClient

from mediafleet_cli.config import (
    CLIConfig,
    load_config,
    validate_ca_bundle,
    validate_context_name,
    validate_url,
)
from mediafleet_cli.credentials import get_api_key
from mediafleet_cli.errors import LocalConfigurationError

PUBLIC_ENDPOINT_PLACEHOLDER_KEY = "public-endpoint-no-auth"


@dataclass(frozen=True)
class ConnectionOptions:
    url: str | None = None
    context: str | None = None
    ca_bundle: str | None = None


@dataclass(frozen=True)
class ResolvedConnection:
    base_url: str
    api_key: str
    verify: bool | str
    context_name: str | None


def selected_context_name(config: CLIConfig, explicit: str | None = None) -> str | None:
    candidate = explicit or os.getenv("MEDIAFLEET_CONTEXT") or config.current_context
    return validate_context_name(candidate) if candidate is not None else None


def resolve_connection(
    options: ConnectionOptions,
    *,
    require_api_key: bool = True,
) -> ResolvedConnection:
    config = load_config()
    context_name = selected_context_name(config, options.context)
    context_config = config.contexts.get(context_name) if context_name else None
    if context_name and context_config is None:
        raise LocalConfigurationError(f"context {context_name!r} 不存在")

    raw_url = options.url or os.getenv("MEDIAFLEET_URL")
    if raw_url is None and context_config is not None:
        raw_url = context_config.url
    if raw_url is None:
        raise LocalConfigurationError(
            "未配置控制中心 URL，请设置 MEDIAFLEET_URL 或创建 context"
        )
    base_url = validate_url(raw_url)

    raw_ca_bundle = options.ca_bundle or os.getenv("MEDIAFLEET_CA_BUNDLE")
    if raw_ca_bundle is None and context_config is not None:
        raw_ca_bundle = context_config.ca_bundle
    ca_bundle = validate_ca_bundle(raw_ca_bundle)
    if ca_bundle is not None and not Path(ca_bundle).is_file():
        raise LocalConfigurationError(f"CA 文件不存在: {ca_bundle}")

    api_key = os.getenv("MEDIAFLEET_API_KEY")
    if api_key is not None:
        api_key = api_key.strip()
        if not api_key:
            api_key = None
    if api_key is None and context_name is not None:
        api_key = get_api_key(context_name)
    if require_api_key and api_key is None:
        raise LocalConfigurationError(
            "未配置 API Key，请设置 MEDIAFLEET_API_KEY 或执行 auth login"
        )

    return ResolvedConnection(
        base_url=base_url,
        api_key=api_key or PUBLIC_ENDPOINT_PLACEHOLDER_KEY,
        verify=ca_bundle or True,
        context_name=context_name,
    )


def create_client(
    options: ConnectionOptions,
    *,
    require_api_key: bool = True,
) -> MediaFleetClient:
    resolved = resolve_connection(options, require_api_key=require_api_key)
    return MediaFleetClient(
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        verify=resolved.verify,
        user_agent="mediafleet-cli/0.1.0",
    )


__all__ = [
    "ConnectionOptions",
    "ResolvedConnection",
    "create_client",
    "resolve_connection",
    "selected_context_name",
]
