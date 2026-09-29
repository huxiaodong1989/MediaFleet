import os

import pytest

from media_platform.common import get_env_bool, get_env_int, load_env_file


def test_get_env_int_uses_default_when_value_is_empty(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_PORT", "")

    assert get_env_int("SERVICE_PORT", 8008) == 8008


def test_get_env_int_rejects_invalid_value(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_PORT", "abc")

    with pytest.raises(ValueError, match="SERVICE_PORT 必须是整数"):
        get_env_int("SERVICE_PORT", 8008)


def test_get_env_bool_parses_common_values(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_DOTENV_ENABLED", "false")

    assert get_env_bool("SERVICE_DOTENV_ENABLED", True) is False


def test_settings_ignores_empty_service_port(monkeypatch) -> None:
    from media_platform.common.settings import ServiceSettings

    monkeypatch.setenv("SERVICE_PORT", "")

    settings = ServiceSettings()

    assert settings.port == 8008


@pytest.mark.parametrize("api_key", ["", "change-me", "replace-me", "short-key"])
def test_production_rejects_unsafe_api_key(monkeypatch, api_key: str) -> None:
    from media_platform.common.settings import Settings

    monkeypatch.setenv("IGNORE_ENV_FILE", "1")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEY", api_key)

    with pytest.raises(ValueError, match="production 环境必须配置"):
        Settings()


def test_production_accepts_non_placeholder_api_key(monkeypatch) -> None:
    from media_platform.common.settings import Settings

    monkeypatch.setenv("IGNORE_ENV_FILE", "1")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEY", "test-production-key-1234567890")

    assert Settings().env == "production"


def test_load_env_file_sets_missing_values_and_preserves_existing(
    tmp_path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# 本地服务配置",
                "SERVICE_PORT=8009",
                'MEDIA_WORKER_ROUTING_KEYS="#"',
                "MEDIA_WORKER_CONSUMER_ENABLED=true",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SERVICE_PORT", "9000")
    monkeypatch.delenv("MEDIA_WORKER_ROUTING_KEYS", raising=False)
    monkeypatch.delenv("MEDIA_WORKER_CONSUMER_ENABLED", raising=False)

    loaded_count = load_env_file(env_file)

    assert loaded_count == 2
    assert os.environ["SERVICE_PORT"] == "9000"
    assert os.environ["MEDIA_WORKER_ROUTING_KEYS"] == "#"
    assert os.environ["MEDIA_WORKER_CONSUMER_ENABLED"] == "true"


def test_load_env_file_can_be_disabled(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MEDIA_WORKER_CONSUMER_ENABLED=true", encoding="utf-8")
    monkeypatch.delenv("MEDIA_WORKER_CONSUMER_ENABLED", raising=False)

    loaded_count = load_env_file(env_file, enabled=False)

    assert loaded_count == 0
    assert "MEDIA_WORKER_CONSUMER_ENABLED" not in os.environ
