from __future__ import annotations

import base64
import datetime as dt
from collections.abc import Iterator

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID

from sei import SeiSettings, clear_token_cache
from sei._base import ssl_context

PFX_PASSWORD = "test-pfx-password"


@pytest.fixture(autouse=True)
def _isolate_caches() -> Iterator[None]:
    """Both caches are process-wide by design (see ``_base``), so tests must not inherit
    each other's tokens or certificate contexts."""
    clear_token_cache()
    ssl_context.cache_clear()
    yield
    clear_token_cache()
    ssl_context.cache_clear()


@pytest.fixture(scope="session")
def pfx_password() -> str:
    return PFX_PASSWORD


@pytest.fixture(scope="session")
def pfx_bundle() -> bytes:
    """A throwaway self-signed PKCS#12 bundle, so the real mTLS unwrap path is exercised
    rather than stubbed. Session-scoped: RSA keygen is slow and the identity never varies."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "svc-GrapheneApiTest")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    bundle = pkcs12.serialize_key_and_certificates(
        name=b"graphene-test",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=BestAvailableEncryption(PFX_PASSWORD.encode()),
    )
    return bundle


@pytest.fixture(scope="session")
def pfx_base64(pfx_bundle: bytes) -> str:
    """The bundle in the ONLY form the client accepts (`SEI_PFX_BASE64`). A file-path form
    existed until Aug 2026; the fixture followed it out, so the tests now exercise the one
    configuration a deployment can actually have."""
    return base64.b64encode(pfx_bundle).decode()


@pytest.fixture
def settings(pfx_base64: str) -> SeiSettings:
    return SeiSettings(
        environment="i31",
        base_url="https://test.mtls.api.seic.com/api/v1/swp",
        pfx_base64=pfx_base64,
        pfx_password=PFX_PASSWORD,
        oauth_username="i31pographeneapi50",
        oauth_password="secret",
        appkey="app-key",
        appsecret="app-secret",
    )
