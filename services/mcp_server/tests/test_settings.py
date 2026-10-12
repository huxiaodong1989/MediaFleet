from __future__ import annotations

import pytest

from mediafleet_mcp.settings import MCPConfigurationError, MCPSettings


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
    assert "secret-value" not in repr(settings)


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
