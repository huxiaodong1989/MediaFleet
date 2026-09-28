from services.content_analysis.application.log_sanitizer import (
    mask_secret,
    sanitize_log_value,
    sanitize_url,
)


def test_model_configuration_log_values_are_sanitized():
    api_key = "secret-model-api-key-123456"
    url = "https://user:password@example.com/v1?api_key=url-secret&region=cn"

    masked_key = mask_secret(api_key)
    sanitized_url = sanitize_url(url)

    assert api_key not in masked_key
    assert masked_key.startswith("secr")
    assert masked_key.endswith("3456")
    assert "user" not in sanitized_url
    assert "password" not in sanitized_url
    assert "url-secret" not in sanitized_url
    assert sanitized_url == "https://example.com/v1"


def test_empty_model_api_key_is_reported_as_not_configured():
    assert mask_secret("") == "not-configured"


def test_business_log_value_is_single_line_and_limited():
    value = "课堂一\r\n伪造日志" + "很长" * 100

    sanitized = sanitize_log_value(value, max_length=20)

    assert "\r" not in sanitized
    assert "\n" not in sanitized
    assert sanitized.startswith("课堂一 伪造日志")
    assert sanitized.endswith("…")
    assert len(sanitized) == 21
