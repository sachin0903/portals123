"""IntellifloSettings: `basic_auth` encoding and the `INTELLIFLO_` env var prefix."""

from __future__ import annotations

import base64

import pytest

from intelliflo.settings import IntellifloSettings


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real `INTELLIFLO_*` vars in the developer's shell would mask what a test sets."""
    for name in (
        "CLIENT_ID",
        "CLIENT_SECRET",
        "GRANT_TYPE",
        "SCOPE",
        "TENANT_ID",
        "BASE_URL",
        "API_KEY",
        "TOKEN_URL",
        "TIMEOUT",
    ):
        monkeypatch.delenv(f"INTELLIFLO_{name}", raising=False)


def test_basic_auth_encodes_client_id_and_secret() -> None:
    settings = IntellifloSettings(client_id="cid", client_secret="s3cret")
    expected = base64.b64encode(b"cid:s3cret").decode()
    assert settings.basic_auth == expected


def test_basic_auth_updates_when_credentials_do() -> None:
    """Not memoised -- a property, recomputed each read."""
    settings = IntellifloSettings(client_id="a", client_secret="b")
    first = settings.basic_auth
    settings.client_secret = "c"
    assert settings.basic_auth != first
    assert settings.basic_auth == base64.b64encode(b"a:c").decode()


def test_defaults_are_blank_and_unconfigured_import_succeeds() -> None:
    """Importing/instantiating with no environment configured must not raise -- settings are
    meant to be created lazily, inside functions, not at import time."""
    settings = IntellifloSettings()
    assert settings.client_id == ""
    assert settings.client_secret == ""
    assert settings.scope == ""
    assert settings.tenant_id == ""
    assert settings.base_url == ""
    assert settings.api_key == ""
    assert settings.grant_type == "client_credentials"
    assert settings.token_url == "https://identity.gb.intelliflo.net/core/connect/token"
    assert settings.timeout == 30.0


def test_env_vars_are_read_with_the_intelliflo_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTELLIFLO_CLIENT_ID", "from-env")
    monkeypatch.setenv("INTELLIFLO_CLIENT_SECRET", "secret-from-env")
    monkeypatch.setenv("INTELLIFLO_API_KEY", "key-from-env")
    monkeypatch.setenv("INTELLIFLO_BASE_URL", "https://api.intelliflo.test")

    settings = IntellifloSettings()

    assert settings.client_id == "from-env"
    assert settings.client_secret == "secret-from-env"
    assert settings.api_key == "key-from-env"
    assert settings.base_url == "https://api.intelliflo.test"
