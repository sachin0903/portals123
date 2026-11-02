"""PGP decrypt, exercised with a throwaway key generated in-test.

Deliberately never touches the real Graphene key: the point is that the code path
works, and a test that needed production key material could not run in CI.
"""

from __future__ import annotations

import base64

import pytest

from sei_sync.decrypt import decrypt_bytes, unlocked
from sei_sync.settings import SeiSettings

PASSPHRASE = "test-passphrase"
PAYLOAD = b"TYPE|CODE|DESCRIPTION|FIRM_ID\nFIRM_ID|3106|Select Platform Limited|3106\n"


@pytest.fixture(scope="module")
def keypair() -> tuple[str, bytes]:
    """(armoured private key, a message encrypted to it). RSA-2048 keeps it quick."""
    import pgpy
    from pgpy.constants import (
        CompressionAlgorithm,
        HashAlgorithm,
        KeyFlags,
        PubKeyAlgorithm,
        SymmetricKeyAlgorithm,
    )

    key = pgpy.PGPKey.new(PubKeyAlgorithm.RSAEncryptOrSign, 2048)
    key.add_uid(
        pgpy.PGPUID.new("SEI Test", email="sei-test@example.invalid"),
        usage={KeyFlags.EncryptStorage, KeyFlags.EncryptCommunications},
        hashes=[HashAlgorithm.SHA256],
        ciphers=[SymmetricKeyAlgorithm.AES256],
        compression=[CompressionAlgorithm.ZLIB],
    )
    encrypted = bytes(key.pubkey.encrypt(pgpy.PGPMessage.new(PAYLOAD, file=False)))
    key.protect(PASSPHRASE, SymmetricKeyAlgorithm.AES256, HashAlgorithm.SHA256)
    return str(key), encrypted


def _settings(armour: str, *, passphrase: str = PASSPHRASE) -> SeiSettings:
    return SeiSettings(
        pgp_private_key_base64=base64.b64encode(armour.encode()).decode(),
        pgp_passphrase=passphrase,
    )


def test_decrypt_round_trip(keypair: tuple[str, bytes]) -> None:
    armour, encrypted = keypair
    assert decrypt_bytes(_settings(armour), encrypted) == PAYLOAD


def test_unlocked_reuses_one_unlock_across_files(keypair: tuple[str, bytes]) -> None:
    """Unlocking is a KDF over the passphrase; the pull must pay it once per run."""
    armour, encrypted = keypair
    with unlocked(_settings(armour)) as decrypt:
        assert decrypt(encrypted) == PAYLOAD
        assert decrypt(encrypted) == PAYLOAD


def test_wrong_passphrase_is_reported_plainly(keypair: tuple[str, bytes]) -> None:
    armour, _ = keypair
    with pytest.raises(RuntimeError, match="passphrase"):
        with unlocked(_settings(armour, passphrase="wrong")) as decrypt:
            decrypt(b"")


def test_missing_passphrase_is_reported_before_any_work(keypair: tuple[str, bytes]) -> None:
    armour, _ = keypair
    with pytest.raises(RuntimeError, match="SEI_PGP_PASSPHRASE"):
        with unlocked(_settings(armour, passphrase="")) as decrypt:
            decrypt(b"")


def test_no_key_configured_names_both_env_vars() -> None:
    with pytest.raises(RuntimeError, match="SEI_PGP_PRIVATE_KEY_BASE64"):
        SeiSettings(pgp_private_key_base64="", pgp_private_key_path="").private_key_armour()


def test_invalid_base64_key_is_rejected_clearly() -> None:
    with pytest.raises(RuntimeError, match="not valid base64"):
        SeiSettings(pgp_private_key_base64="!!!not base64!!!").private_key_armour()


def test_unparseable_key_is_rejected_clearly() -> None:
    settings = SeiSettings(
        pgp_private_key_base64=base64.b64encode(b"definitely not a PGP key").decode(),
        pgp_passphrase=PASSPHRASE,
    )
    with pytest.raises(RuntimeError, match="could not parse"):
        with unlocked(settings) as decrypt:
            decrypt(b"")


def test_missing_key_path_is_reported() -> None:
    settings = SeiSettings(pgp_private_key_path="/nonexistent/sei.key")
    with pytest.raises(RuntimeError, match="does not exist"):
        settings.private_key_armour()
