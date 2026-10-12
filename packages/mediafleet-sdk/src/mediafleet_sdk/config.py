"""SDK 客户端配置。"""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike, fspath
from urllib.parse import urlsplit

import httpx

from mediafleet_sdk.errors import ConfigurationError


@dataclass(frozen=True)
class RetryPolicy:
    """只读请求的有限重试策略。"""

    max_attempts: int = 3
    backoff_factor: float = 0.25
    max_delay: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ConfigurationError("max_attempts 必须大于等于 1")
        if self.backoff_factor < 0:
            raise ConfigurationError("backoff_factor 不能小于 0")
        if self.max_delay < 0:
            raise ConfigurationError("max_delay 不能小于 0")


@dataclass(frozen=True, repr=False)
class ClientConfig:
    """经过校验的客户端配置，字符串表示不会包含 API Key。"""

    base_url: str
    api_key: str
    timeout: float | httpx.Timeout
    verify: bool | str
    user_agent: str
    retry: RetryPolicy

    @classmethod
    def create(
        cls,
        *,
        base_url: str,
        api_key: str,
        timeout: float | httpx.Timeout = 30.0,
        verify: bool | str | PathLike[str] = True,
        user_agent: str = "mediafleet-sdk/0.1.0",
        retry: RetryPolicy | None = None,
    ) -> ClientConfig:
        normalized_url = _validate_base_url(base_url)
        normalized_key = api_key.strip() if isinstance(api_key, str) else ""
        if not normalized_key:
            raise ConfigurationError("api_key 不能为空")
        if "\r" in normalized_key or "\n" in normalized_key:
            raise ConfigurationError("api_key 不能包含换行符")
        if isinstance(timeout, bool) or (
            isinstance(timeout, (int, float)) and timeout <= 0
        ):
            raise ConfigurationError("timeout 必须大于 0")
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ConfigurationError("user_agent 不能为空")
        if "\r" in user_agent or "\n" in user_agent:
            raise ConfigurationError("user_agent 不能包含换行符")
        return cls(
            base_url=normalized_url,
            api_key=normalized_key,
            timeout=timeout,
            verify=fspath(verify) if isinstance(verify, PathLike) else verify,
            user_agent=user_agent.strip(),
            retry=retry or RetryPolicy(),
        )

    def __repr__(self) -> str:
        return (
            "ClientConfig("
            f"base_url={self.base_url!r}, api_key='********', "
            f"timeout={self.timeout!r}, verify={self.verify!r}, "
            f"user_agent={self.user_agent!r}, retry={self.retry!r})"
        )


def _validate_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("base_url 不能为空")
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("base_url 必须是完整的 HTTP 或 HTTPS 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigurationError("base_url 不能包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise ConfigurationError("base_url 不能包含查询参数或片段")
    return candidate.rstrip("/") + "/"


__all__ = ["ClientConfig", "RetryPolicy"]
