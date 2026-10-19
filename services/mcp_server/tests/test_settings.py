from __future__ import annotations

import pytest

from mediafleet_mcp.settings import (
    MCPConfigurationError,
    MCPSettings,
    WRITE_TOOL_NAMES,
)


def test_settings_read_environment_and_redact_repr(tmp_path, monkeypatch):
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("test certificate placeholder", encoding="utf-8")
    monkeypatch.setenv("MEDIAFLEET_URL", "https://mediafleet.example.com/")
    monkeypatch.setenv("MEDIAFLEET_API_KEY", "secret-value")
    monkeypatch.setenv("MEDIAFLEET_CA_BUNDLE", str(ca_bundle))
    monkeypatch.setenv("MEDIAFLEET_TIMEOUT", "12.5")

    settings = MCPSettings.from_env()

    assert settings.base_url == "https://mediafleet.example.com/"
    assert settings.api_key == "secret-value"
    assert settings.verify == str(ca_bundle.resolve())
    assert settings.timeout == 12.5
    assert settings.write_tools == frozenset()
    assert "secret-value" not in repr(settings)


def test_settings_parse_explicit_write_tool_allowlist(monkeypatch):
    monkeypatch.setenv(
        "MEDIAFLEET_MCP_WRITE_TOOLS",
        "mediafleet_create_task, mediafleet_stop_recording,mediafleet_create_task",
    )

    assert MCPSettings.write_tools_from_env() == frozenset(
        {"mediafleet_create_task", "mediafleet_stop_recording"}
    )
    assert MCPSettings.write_tools_from_env() < WRITE_TOOL_NAMES


def test_settings_reject_unknown_write_tool(monkeypatch):
    monkeypatch.setenv(
        "MEDIAFLEET_MCP_WRITE_TOOLS",
        "mediafleet_create_task,mediafleet_run_shell",
    )

    with pytest.raises(MCPConfigurationError, match="mediafleet_run_shell"):
        MCPSettings.write_tools_from_env()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MEDIAFLEET_URL", ""),
        ("MEDIAFLEET_URL", "ftp://example.com"),
        (
            "MEDIAFLEET_URL",
            "https://" + "placeholder-user" + ":" + "placeholder-password@example.com",
        ),
        ("MEDIAFLEET_API_KEY", ""),
        ("MEDIAFLEET_API_KEY", "line1\nline2"),
        ("MEDIAFLEET_TIMEOUT", "0"),
        ("MEDIAFLEET_TIMEOUT", "121"),
        ("MEDIAFLEET_TIMEOUT", "not-a-number"),
    ],
)
def test_invalid_settings_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv("MEDIAFLEET_URL", "https://mediafleet.example.com")
    monkeypatch.setenv("MEDIAFLEET_API_KEY", "valid-secret")
    monkeypatch.setenv("MEDIAFLEET_TIMEOUT", "30")
    monkeypatch.setenv(name, value)

    with pytest.raises(MCPConfigurationError):
        MCPSettings.from_env()
