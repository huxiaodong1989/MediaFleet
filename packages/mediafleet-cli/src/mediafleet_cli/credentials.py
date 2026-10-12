"""操作系统凭据库适配器。"""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from mediafleet_cli.errors import CredentialStoreError

SERVICE_NAME = "mediafleet-cli"


def get_api_key(context_name: str) -> str | None:
    try:
        value = keyring.get_password(SERVICE_NAME, context_name)
    except KeyringError as exc:
        raise CredentialStoreError("无法读取操作系统凭据库") from exc
    return value.strip() if value and value.strip() else None


def set_api_key(context_name: str, api_key: str) -> None:
    normalized = api_key.strip()
    if not normalized:
        raise CredentialStoreError("API Key 不能为空")
    if "\r" in normalized or "\n" in normalized:
        raise CredentialStoreError("API Key 不能包含换行符")
    try:
        keyring.set_password(SERVICE_NAME, context_name, normalized)
    except KeyringError as exc:
        raise CredentialStoreError(
            "无法写入操作系统凭据库，请先配置可用的 keyring 后端"
        ) from exc


def delete_api_key(context_name: str) -> bool:
    try:
        keyring.delete_password(SERVICE_NAME, context_name)
    except PasswordDeleteError:
        return False
    except KeyringError as exc:
        raise CredentialStoreError("无法访问操作系统凭据库") from exc
    return True


__all__ = ["SERVICE_NAME", "delete_api_key", "get_api_key", "set_api_key"]
