from __future__ import annotations

import pytest

from mediafleet_cli.errors import ExitCode, LocalConfigurationError
from mediafleet_cli.output import classify_error
from mediafleet_sdk import (
    APIError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ResponseValidationError,
    ServiceUnavailableError,
    TransportError,
)


def api_error(error_type, status_code):
    return error_type(
        status_code=status_code,
        detail="test error",
        path="/api/v1/test",
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LocalConfigurationError("bad config"), ExitCode.CONFIGURATION),
        (api_error(AuthenticationError, 401), ExitCode.AUTHORIZATION),
        (api_error(NotFoundError, 404), ExitCode.NOT_FOUND),
        (api_error(ConflictError, 409), ExitCode.CONFLICT),
        (TransportError("/health"), ExitCode.UNAVAILABLE),
        (api_error(RateLimitError, 429), ExitCode.UNAVAILABLE),
        (api_error(ServiceUnavailableError, 503), ExitCode.UNAVAILABLE),
        (
            ResponseValidationError(path="/health", issues=[]),
            ExitCode.RESPONSE_CONTRACT,
        ),
        (api_error(APIError, 422), ExitCode.API_ERROR),
    ],
)
def test_stable_error_exit_codes(error, expected):
    exit_code, _ = classify_error(error)

    assert exit_code == expected
