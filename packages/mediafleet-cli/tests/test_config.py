from __future__ import annotations

import json

import pytest

from mediafleet_cli.config import CLIConfig, ContextConfig, load_config, save_config
from mediafleet_cli.errors import LocalConfigurationError
from mediafleet_cli.runtime import ConnectionOptions, resolve_connection


def test_config_round_trip_and_no_secret_field(tmp_path):
    path = tmp_path / "config.json"
    config = CLIConfig(
        current_context="dev",
        contexts={
            "dev": ContextConfig(
                url="https://mediafleet.example.com",
                ca_bundle=str(tmp_path / "company-ca.pem"),
            )
        },
    )

    save_config(config, path)

    assert load_config(path) == config
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "api_key" not in json.dumps(raw)


def test_invalid_or_unknown_config_fields_are_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"contexts": {}, "api_key": "bad"}', encoding="utf-8")

    with pytest.raises(LocalConfigurationError):
        load_config(path)


def test_connection_precedence(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    save_config(
        CLIConfig(
            current_context="dev",
            contexts={"dev": ContextConfig(url="https://context.example.com")},
        ),
        path,
    )
    monkeypatch.setenv("MEDIAFLEET_CONFIG_FILE", str(path))
    monkeypatch.setenv("MEDIAFLEET_URL", "https://environment.example.com")
    monkeypatch.setenv("MEDIAFLEET_API_KEY", "environment-secret")

    resolved = resolve_connection(
        ConnectionOptions(url="https://command.example.com")
    )

    assert resolved.base_url == "https://command.example.com"
    assert resolved.api_key == "environment-secret"


def test_query_requires_api_key_but_public_endpoint_does_not(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    save_config(
        CLIConfig(
            current_context="dev",
            contexts={"dev": ContextConfig(url="https://mediafleet.example.com")},
        ),
        path,
    )
    monkeypatch.setenv("MEDIAFLEET_CONFIG_FILE", str(path))
    monkeypatch.delenv("MEDIAFLEET_API_KEY", raising=False)
    monkeypatch.setattr("mediafleet_cli.runtime.get_api_key", lambda context: None)

    with pytest.raises(LocalConfigurationError):
        resolve_connection(ConnectionOptions())

    public = resolve_connection(ConnectionOptions(), require_api_key=False)
    assert public.base_url == "https://mediafleet.example.com"
