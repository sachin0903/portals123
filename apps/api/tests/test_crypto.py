"""Fernet envelope encryption for at-rest secrets (app.crypto). Pure — no DB."""

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app import crypto
from app.config import settings


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())


def test_round_trip_and_ciphertext_differs() -> None:
    ct = crypto.encrypt("sk_live_supersecret")
    assert ct != "sk_live_supersecret"
    assert crypto.decrypt(ct) == "sk_live_supersecret"


def test_tampered_ciphertext_rejected() -> None:
    with pytest.raises(InvalidToken):
        crypto.decrypt("not-a-valid-fernet-token")


def test_wrong_key_cannot_decrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    ct = crypto.encrypt("secret")
    # rotate to a different key -> the old ciphertext no longer decrypts
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        crypto.decrypt(ct)


def test_missing_master_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "secrets_master_key", "")
    with pytest.raises(crypto.SecretsNotConfigured):
        crypto.encrypt("secret")


def test_invalid_master_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "secrets_master_key", "not-a-fernet-key")
    with pytest.raises(crypto.SecretsNotConfigured):
        crypto.encrypt("secret")
