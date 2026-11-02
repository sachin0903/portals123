"""Application-level envelope encryption for secrets at rest (Fernet).

Per-tenant WorkOS environment secrets (api key + cookie password) are set via the admin UI
and stored as ciphertext in ``auth.environment_secrets``. Only the API holds the master key
(``SECRETS_MASTER_KEY``, a Fernet key) and decrypts them at runtime — the shared portal
fetches already-decrypted config from the internal endpoint. See docs/identity-and-access.md §0.

Fernet gives us authenticated symmetric encryption (AES-128-CBC + HMAC): tampered or
foreign-key ciphertext raises ``InvalidToken`` on decrypt rather than returning garbage.
"""

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

__all__ = ["InvalidToken", "SecretsNotConfigured", "decrypt", "encrypt", "seal", "unseal"]


class SecretsNotConfigured(RuntimeError):
    """``SECRETS_MASTER_KEY`` is unset or not a valid Fernet key, so secrets cannot be
    encrypted or decrypted. Raised (fail-closed) rather than silently degrading."""


def _fernet() -> Fernet:
    # Not cached: tests (and key rotation) swap the key at runtime, and constructing a
    # Fernet only validates the key — it is cheap.
    key = settings.secrets_master_key
    if not key:
        raise SecretsNotConfigured(
            "SECRETS_MASTER_KEY is not set; cannot handle environment secrets."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise SecretsNotConfigured(
            "SECRETS_MASTER_KEY is not a valid Fernet key "
            "(generate one with `Fernet.generate_key()`)."
        ) from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage; returns a urlsafe token. Raises ``SecretsNotConfigured``
    if no master key is set."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a stored secret. Raises ``InvalidToken`` if the ciphertext is tampered or was
    sealed with a different key, ``SecretsNotConfigured`` if no master key is set."""
    return _fernet().decrypt(token.encode()).decode()


def seal(data: bytes) -> bytes:
    """Encrypt bytes for storage (a client's identity photo, ``app/idv/held.py``). Raises
    ``SecretsNotConfigured`` if no master key is set."""
    return _fernet().encrypt(data)


def unseal(token: bytes) -> bytes:
    """Decrypt bytes sealed by ``seal``. Raises ``InvalidToken`` if they were tampered with or
    sealed with a different key, ``SecretsNotConfigured`` if no master key is set."""
    return _fernet().decrypt(token)
