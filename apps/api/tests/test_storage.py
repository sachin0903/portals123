"""Branding asset storage: the tenant-bucket → legacy-shared-bucket → data-URI resolution
ladder (docs/storage.md §8), plus type/size validation and addressing-style resolution.

Pure unit tests: the registry lookup and the boto3 put are monkeypatched (the DB-backed
provisioning behaviour lives in test_tenant_storage.py)."""

import base64
from typing import Any

import pytest

from app import storage, tenant_storage
from app.config import settings
from app.tenant_storage import TenantStore


def _store(**overrides: Any) -> TenantStore:
    defaults: dict[str, Any] = {
        "tenant_id": 7,
        "kind": "rustfs",
        "endpoint": "http://rustfs:9000",
        "region": None,
        "bucket": "t-7",
        "public_base_url": "http://rustfs:9000/t-7",
        "status": "active",
    }
    return TenantStore(**{**defaults, **overrides})


@pytest.fixture
def puts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture storage._put calls instead of touching boto3."""
    calls: list[dict[str, Any]] = []

    def fake_put(
        bucket: str,
        endpoint: str,
        region: str | None,
        key: str,
        data: bytes,
        content_type: str,
        *,
        acl: str = "",
    ) -> None:
        calls.append({"bucket": bucket, "endpoint": endpoint, "key": key, "data": data, "acl": acl})

    monkeypatch.setattr(storage, "_put", fake_put)
    return calls


def _resolve_to(monkeypatch: pytest.MonkeyPatch, store: TenantStore | None) -> None:
    async def fake_get(_session: Any, _tenant_id: int) -> TenantStore | None:
        return store

    monkeypatch.setattr(tenant_storage, "get_tenant_store", fake_get)


@pytest.mark.asyncio
async def test_tenant_bucket_first(
    monkeypatch: pytest.MonkeyPatch, puts: list[dict[str, Any]]
) -> None:
    """An active store with a public base URL wins: relative key under files/tenant/, no ACL
    (anonymous read comes from the prefix bucket policy)."""
    _resolve_to(monkeypatch, _store())
    url = await storage.put_branding_asset(None, 7, "logo", b"<svg/>", "image/svg+xml")
    assert len(puts) == 1
    assert puts[0]["bucket"] == "t-7"
    assert puts[0]["key"].startswith("files/tenant/logo-")
    assert puts[0]["key"].endswith(".svg")
    assert puts[0]["acl"] == ""
    assert url == f"http://rustfs:9000/t-7/{puts[0]['key']}"


@pytest.mark.asyncio
async def test_store_without_public_url_falls_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch, puts: list[dict[str, Any]]
) -> None:
    """public_base_url is only set once the prefix policy applied — without it the tenant
    bucket is NOT public, so branding stays on the legacy shared bucket."""
    _resolve_to(monkeypatch, _store(public_base_url=None))
    monkeypatch.setattr(settings, "s3_bucket", "branding")
    monkeypatch.setattr(settings, "s3_public_base_url", "http://cdn.example/branding")
    monkeypatch.setattr(settings, "s3_acl", "public-read")
    url = await storage.put_branding_asset(None, 7, "logo", b"<svg/>", "image/svg+xml")
    assert puts[0]["bucket"] == "branding"
    assert puts[0]["key"].startswith("tenants/7/logo-")
    assert puts[0]["acl"] == "public-read"  # legacy path keeps per-object ACLs
    assert url == f"http://cdn.example/branding/{puts[0]['key']}"


@pytest.mark.asyncio
async def test_proxy_asset_needs_no_public_object_store_url(
    monkeypatch: pytest.MonkeyPatch, puts: list[dict[str, Any]]
) -> None:
    """Browser assets stay in the tenant bucket and expose only an opaque same-origin source."""
    _resolve_to(monkeypatch, _store(public_base_url=None))
    src = await storage.put_branding_asset(None, 7, "logo", b"<svg/>", "image/svg+xml", proxy=True)
    assert puts[0]["bucket"] == "t-7"
    assert puts[0]["key"].startswith("files/tenant/logo-")
    assert src == f"/_assets/{puts[0]['key'].rsplit('/', 1)[-1]}"
    assert "rustfs" not in src
    assert "t-7" not in src


def test_legacy_public_asset_urls_are_rewritten_without_touching_external_images() -> None:
    old = "https://objects.internal/t-7/files/tenant/logo-0123456789abcdef.svg"
    assert storage.public_asset_src(old) == "/_assets/logo-0123456789abcdef.svg"
    html = f'<img src="{old}"><img src="https://example.com/photo.jpg">'
    assert storage.proxy_asset_urls(html) == (
        '<img src="/_assets/logo-0123456789abcdef.svg"><img src="https://example.com/photo.jpg">'
    )


@pytest.mark.asyncio
async def test_public_asset_read_resolves_tenant_without_storage_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(public_base_url=None)
    _resolve_to(monkeypatch, store)
    seen: list[tuple[TenantStore, str]] = []

    async def fake_get(got_store: TenantStore, key: str) -> tuple[bytes, str]:
        seen.append((got_store, key))
        return b"image", "image/png"

    monkeypatch.setattr(storage, "get_tenant_object", fake_get)
    got = await storage.get_public_asset(None, 7, "content-aB3Z-0123456789abcdef.png")
    assert got == (b"image", "image/png")
    assert seen == [(store, "files/tenant/content-aB3Z-0123456789abcdef.png")]
    with pytest.raises(ValueError):
        await storage.get_public_asset(None, 7, "../secret")


@pytest.mark.asyncio
async def test_no_store_no_legacy_returns_data_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no object storage at all, assets inline as a base64 data URI (stored in the
    branding jsonb). Type/size validation still applies."""
    _resolve_to(monkeypatch, None)
    url = await storage.put_branding_asset(None, 1, "logo", b"<svg/>", "image/svg+xml")
    assert url.startswith("data:image/svg+xml;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"<svg/>"
    with pytest.raises(ValueError):
        await storage.put_branding_asset(None, 1, "logo", b"x", "application/pdf")
    with pytest.raises(ValueError):
        await storage.put_branding_asset(
            None, 1, "logo", b"x" * (storage.MAX_ASSET_BYTES + 1), "image/png"
        )


def test_storage_unconfigured_guard() -> None:
    # the autouse fixture blanks the S3 settings
    assert storage.is_configured() is False


@pytest.mark.parametrize(
    ("force", "endpoint", "expected"),
    [
        ("", "", False),  # blank + no endpoint => AWS virtual-host
        ("", "http://minio:9000", True),  # blank + custom endpoint => path-style
        ("true", "", True),  # explicit override wins
        ("false", "http://minio:9000", False),  # explicit override wins
        ("  YES ", "", True),  # tolerant parsing
    ],
)
def test_path_style_resolution(
    monkeypatch: pytest.MonkeyPatch, force: str, endpoint: str, expected: bool
) -> None:
    """Blank S3_FORCE_PATH_STYLE must not crash (it's a str) and resolves sensibly."""
    monkeypatch.setattr(settings, "s3_force_path_style", force)
    monkeypatch.setattr(settings, "s3_endpoint_url", endpoint)
    assert storage._path_style() is expected
