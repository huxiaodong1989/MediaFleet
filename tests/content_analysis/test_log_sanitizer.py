from services.content_analysis.application.log_sanitizer import mask_secret, sanitize_url


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
