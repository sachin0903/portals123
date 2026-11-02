"""PGP decrypt of the SEI drop.

SEI encrypts every file to the Graphene public key, so nothing in the drop is
readable until it passes through here. The key is unlocked **once per run** and
reused across files — unlocking is a KDF over the passphrase and doing it per
file dominated the runtime of the earlier sei_reports implementation.

Library note: PGPy 0.6.0 (what sei_reports uses) is broken on Python 3.13 —
``pgpy/constants.py`` imports ``imghdr``, removed from the stdlib by PEP 594.
``pgpy13`` is the maintained fork and imports under the same ``pgpy`` name;
verified against a real p04 ``.out.pgp`` drop.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

from sei_sync.settings import SeiSettings

logger = logging.getLogger(__name__)


# pgpy registers TripleDES/Camellia and uses CFB, all of which cryptography now warns
# about — on import (its constants table) *and* on every unlock/decrypt (symenc).
# They are part of the OpenPGP spec's cipher set, not choices we make (the SEI drop is
# AES-256), so left alone they emit several warnings per file and drown the ingest log.
# Set process-wide rather than per-call: scoped `catch_warnings` blocks mutate the filter
# state on every exit, which invalidates Python's duplicate-warning registry and makes
# the warnings reappear outside the suppressed region.
warnings.filterwarnings("ignore", message=".*has been moved to cryptography\\.hazmat.*")


def _import_pgpy() -> Any:
    import pgpy

    return pgpy


class Decryptor(Protocol):
    def __call__(self, blob: bytes) -> bytes: ...


def _as_bytes(message: Any) -> bytes:
    """pgpy hands back str for text literals and bytearray for binary ones."""
    if isinstance(message, str):
        return message.encode("utf-8")
    if isinstance(message, bytearray):
        return bytes(message)
    if isinstance(message, bytes):
        return message
    raise RuntimeError(f"unexpected decrypted payload type {type(message).__name__}")


@contextmanager
def unlocked(settings: SeiSettings) -> Iterator[Decryptor]:
    """Yield a decrypt callable with the private key unlocked for the duration.

    The key stays unlocked only inside this block, so the secret material has a
    bounded lifetime in the process rather than living on a module global.
    """
    pgpy = _import_pgpy()

    armour = settings.private_key_armour()
    try:
        key, _ = pgpy.PGPKey.from_blob(armour)
    except Exception as exc:  # pgpy raises a zoo of parse errors
        raise RuntimeError("could not parse the SEI PGP private key") from exc

    if not settings.pgp_passphrase:
        raise RuntimeError("SEI_PGP_PASSPHRASE is not set; the SEI key is passphrase-protected")

    try:
        with key.unlock(settings.pgp_passphrase):

            def decrypt(blob: bytes) -> bytes:
                message = pgpy.PGPMessage.from_blob(blob)
                return _as_bytes(key.decrypt(message).message)

            yield decrypt
    except Exception as exc:
        if "passphrase" in str(exc).lower() or "incorrect" in str(exc).lower():
            raise RuntimeError("SEI PGP passphrase rejected by the private key") from exc
        raise


def decrypt_bytes(settings: SeiSettings, blob: bytes) -> bytes:
    """One-shot decrypt. Prefer ``unlocked()`` when handling more than one file."""
    with unlocked(settings) as decrypt:
        return decrypt(blob)
