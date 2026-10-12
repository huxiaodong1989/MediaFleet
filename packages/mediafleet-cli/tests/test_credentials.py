from __future__ import annotations

import pytest
from keyring.errors import NoKeyringError

from mediafleet_cli import credentials
from mediafleet_cli.errors import CredentialStoreError


def test_unavailable_keyring_never_falls_back_to_plaintext(monkeypatch):
    secret = "must-not-be-written"

    def fail(service, context, value):
        raise NoKeyringError("backend unavailable")

    monkeypatch.setattr(credentials.keyring, "set_password", fail)

    with pytest.raises(CredentialStoreError) as captured:
        credentials.set_api_key("dev", secret)

    assert secret not in str(captured.value)
