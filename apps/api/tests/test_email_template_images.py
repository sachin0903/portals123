"""The firm's email image library — `GET/POST /email-templates/images`.

Two rules carry this feature, and both are pinned here.

**The public prefix.** A mail client fetches an image with no credential of ours, whenever the
recipient happens to open the mail. `files/tenant/*` is the only anonymous-read prefix a tenant
bucket has (docs/storage.md §4), so it is the only prefix an email image can live under — a
document-store object, reachable solely through a short-lived proxied URL, is not a lesser email
image but not one at all. The listing may therefore ask for nothing else, and the upload may write
nowhere else.

**The declared Content-Type is never trusted.** The upload is magic-byte sniffed through
`storage.validate_upload`, the one validator; a body that says `image/png` and is not one is
refused.

No Postgres and no S3: the session claims (`_ctx`) and the two storage calls are stubbed, so these
are tests of the endpoints' own policy. That the CLAIMS cannot be forged is `test_rls.py`'s job;
what matters here is that the endpoints read their tenant from the session and offer no parameter
with which to name another.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app import storage
from app.routers import email_templates as et
from tests import email_support

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64

TENANT_ID = 7


class _Store:
    """The registry row `tenant_storage.get_tenant_store` returns (only the fields read here)."""

    def __init__(self, public_base_url: str | None = "http://rustfs:9000/tnt-abc") -> None:
        self.tenant_id = TENANT_ID
        self.bucket = "tnt-abc"
        self.endpoint = "http://rustfs:9000"
        self.region = None
        self.public_base_url = public_base_url
        self.status = "active"


class _Upload:
    """The slice of Starlette's `UploadFile` the handler touches."""

    def __init__(self, data: bytes, content_type: str = "image/png") -> None:
        self._data = data
        self._pos = 0
        self.content_type = content_type

    async def read(self, size: int = -1, /) -> bytes:
        if size < 0:
            chunk, self._pos = self._data[self._pos :], len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


def _obj(key: str, size: int = 1024, when: str = "2026-08-26T09:00:00+00:00") -> Any:
    return storage.StoredObject(key=key, size=size, last_modified=when)


@pytest.fixture
def firm() -> dict[str, str]:
    """The identifiers `email_support.principal` reads. Literals, not a provisioned firm: nothing
    here reaches the database, and the acting tenant arrives through the stubbed session claims."""
    return {"wos": "wos_author", "org": "org_1"}


@pytest.fixture(autouse=True)
def _stub_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    """The acting tenant, as the RLS session claims would report it."""

    async def _ctx(_session: object) -> tuple[int, str]:
        return TENANT_ID, "usr-1"

    monkeypatch.setattr(et, "_ctx", _ctx)


def _stub_store(monkeypatch: pytest.MonkeyPatch, store: object | None) -> list[int]:
    """Record which tenant id the registry is asked about."""
    asked: list[int] = []

    async def _get(_session: object, tenant_id: int) -> object | None:
        asked.append(tenant_id)
        return store

    monkeypatch.setattr(et.tenant_storage, "get_tenant_store", _get)
    return asked


def _stub_list(
    monkeypatch: pytest.MonkeyPatch,
    objects: list[Any] | None = None,
    *,
    pages: list[list[Any]] | None = None,
) -> list[dict[str, Any]]:
    """Record every listing call, so the prefix and the store it was aimed at can be asserted.

    Models a REAL continuation: each call answers the next page and the last one carries no token.
    A stub that answered the same token for ever would be drained to the cap by `list_images` and
    hand every test five copies of its own objects — and one that answered no token could never
    exercise the drain at all."""
    calls: list[dict[str, Any]] = []
    queue = [list(p) for p in pages] if pages is not None else [list(objects or [])]

    async def _list(bucket: str, prefix: str = "", **kw: Any) -> storage.ObjectPage:
        calls.append({"bucket": bucket, "prefix": prefix, **kw})
        batch = queue.pop(0) if queue else []
        # noqa on the literal: bandit reads any name with "token" as a credential (S3's is opaque).
        token = f"tok-{len(calls) + 1}" if queue else None  # noqa: S106
        return storage.ObjectPage(objects=batch, folders=[], next_token=token)

    monkeypatch.setattr(et.storage, "list_objects", _list)
    return calls


def _stub_put(monkeypatch: pytest.MonkeyPatch, url: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def _put(
        _session: object,
        tenant_id: int,
        kind: str,
        data: bytes,
        content_type: str,
        *,
        proxy: bool = False,
    ) -> str:
        # `proxy` is RECORDED, not merely accepted: an email image is published through the
        # same-origin relay like every other asset, and a stub that swallowed the flag would let
        # that silently revert to an object-store URL the browser may not be able to reach.
        calls.append(
            {
                "tenant_id": tenant_id,
                "kind": kind,
                "size": len(data),
                "content_type": content_type,
                "proxy": proxy,
            }
        )
        return url

    monkeypatch.setattr(et.storage, "put_branding_asset", _put)
    return calls


# ── The permission gate ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listing_requires_email_templates_read(firm: dict[str, str]) -> None:
    with pytest.raises(HTTPException) as ei:
        await et.list_email_template_images(
            principal=email_support.principal(firm, []), session=object()
        )
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_upload_requires_write_not_merely_read(firm: dict[str, str]) -> None:
    """Reading the library is a read; adding to it is a write. A read-only author is refused —
    `require_permission`'s write⇒read implication runs one way only, which is the point."""
    for perms in ([], ["email_templates:read"]):
        with pytest.raises(HTTPException) as ei:
            await et.upload_email_template_image(
                file=_Upload(PNG), principal=email_support.principal(firm, perms), session=object()
            )
        assert ei.value.status_code == 403, perms


@pytest.mark.asyncio
async def test_upload_is_permitted_by_email_templates_write(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    """The gate is not vacuous: the same call with the write permission gets through. Without this,
    a refusal for the wrong reason (a broken stub, say) would read as a passing gate."""
    _stub_store(monkeypatch, _Store())
    _stub_put(monkeypatch, "http://rustfs:9000/tnt-abc/files/tenant/image-9f2c.png")
    out = await et.upload_email_template_image(
        file=_Upload(PNG),
        principal=email_support.principal(firm, ["email_templates:write"]),
        session=object(),
    )
    assert out.hosted is True


# ── The public prefix ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listing_reads_only_the_public_prefix_of_the_session_s_own_bucket(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    asked = _stub_store(monkeypatch, _Store())
    calls = _stub_list(monkeypatch, [_obj("files/tenant/logo-9f2c.png")])
    out = await et.list_email_template_images(
        principal=email_support.principal(firm, ["email_templates:read"]), session=object()
    )
    assert calls[0]["prefix"] == "files/tenant/"
    assert calls[0]["bucket"] == "tnt-abc"
    # The store, not the platform default: a tenant bucket on another endpoint would read as empty.
    assert calls[0]["endpoint"] == "http://rustfs:9000"
    assert asked == [TENANT_ID]
    # THE RELAY PATH, not the object-store URL. The store's own endpoint is internal — that is why
    # the relay exists — so a URL built from it is one the browser may not reach and a mail client
    # certainly cannot. `/_assets/{name}` is served by the portal the author is looking at, and the
    # send path makes it absolute against the firm's own host.
    assert [i.url for i in out.images] == ["/_assets/logo-9f2c.png"]
    # Fully drained in one page, so there is nothing left to continue from. A token here would mean
    # the listing stopped at its cap with more of the prefix unread.
    assert out.next_token is None
    assert out.hosted is True


def test_neither_endpoint_takes_a_tenant_argument() -> None:
    """Tenant isolation here is structural. The bucket is resolved from `auth.tenant_id()`, so a
    parameter that could name a tenant — or a bucket, or a key prefix — would be the whole boundary
    handed to the caller. `token` is S3's opaque continuation token, which names a position in a
    listing the server chose the target of."""
    import inspect

    listing = set(inspect.signature(et.list_email_template_images).parameters) - {
        "principal",
        "session",
    }
    upload = set(inspect.signature(et.upload_email_template_image).parameters) - {
        "principal",
        "session",
    }
    assert listing == {"token"}
    assert upload == {"file"}


@pytest.mark.asyncio
async def test_upload_lands_under_the_public_prefix_as_its_own_kind(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    """The upload goes through `put_branding_asset`, whose first rung is
    `files/tenant/{kind}-{hash}` — so the prefix is the storage helper's, not this router's, and
    cannot drift from it. The kind is a word no branding asset uses, so an email image cannot be
    mistaken for the firm's logo."""
    _stub_store(monkeypatch, _Store())
    put = _stub_put(monkeypatch, "http://rustfs:9000/tnt-abc/files/tenant/image-9f2c.png")
    out = await et.upload_email_template_image(
        file=_Upload(PNG),
        principal=email_support.principal(firm, ["email_templates:write"]),
        session=object(),
    )
    assert put[0]["kind"] == et._IMAGE_KIND
    assert put[0]["tenant_id"] == TENANT_ID
    assert et._IMAGE_KIND not in {"logo", "logo-dark", "icon", "favicon"}
    # The full object key, the SAME identity the listing publishes for this object — not the bare
    # filename, which named a different thing from `o.key` and left the two halves of one library
    # unmatchable. Pinned against the listing itself in
    # `test_an_upload_and_the_listing_name_the_same_object`.
    assert out.key == "files/tenant/image-9f2c.png"
    assert out.label == "Image"


@pytest.mark.asyncio
async def test_only_formats_a_mail_client_draws_are_offered(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    """The bucket's public prefix also holds SVG logos and `.ico` favicons. Neither renders in an
    email (Outlook and Gmail refuse SVG; a favicon is not a picture), so they are not offered —
    an image that arrives blank in an inbox is worse than one that was never on the shelf."""
    _stub_store(monkeypatch, _Store())
    _stub_list(
        monkeypatch,
        [
            _obj("files/tenant/logo-1.png"),
            _obj("files/tenant/logo-2.svg"),
            _obj("files/tenant/favicon-3.ico"),
            _obj("files/tenant/photo-4.jpg"),
            _obj("files/tenant/notes-5.pdf"),
        ],
    )
    out = await et.list_email_template_images(
        principal=email_support.principal(firm, ["email_templates:read"]), session=object()
    )
    assert [i.key for i in out.images] == ["files/tenant/logo-1.png", "files/tenant/photo-4.jpg"]


@pytest.mark.asyncio
async def test_a_logo_s_email_copy_is_not_on_the_shelf(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    """An SVG or WebP logo's PNG email copy (`storage.put_email_copy`) is the send path's, not a
    picture anyone uploaded. The shelf stays what the firm put on it."""
    _stub_store(monkeypatch, _Store())
    _stub_list(
        monkeypatch,
        [
            _obj("files/tenant/logo-0123456789abcdef.png"),
            _obj("files/tenant/logo-email-628afb62f38871ae.png"),
            _obj("files/tenant/icon-email-628afb62f38871ae.png"),
        ],
    )
    out = await et.list_email_template_images(
        principal=email_support.principal(firm, ["email_templates:read"]), session=object()
    )
    assert [i.key for i in out.images] == ["files/tenant/logo-0123456789abcdef.png"]


@pytest.mark.asyncio
async def test_newest_first(monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]) -> None:
    _stub_store(monkeypatch, _Store())
    _stub_list(
        monkeypatch,
        [
            _obj("files/tenant/a.png", when="2026-08-01T00:00:00+00:00"),
            _obj("files/tenant/c.png", when="2026-08-26T00:00:00+00:00"),
            _obj("files/tenant/b.png", when="2026-08-10T00:00:00+00:00"),
        ],
    )
    out = await et.list_email_template_images(
        principal=email_support.principal(firm, ["email_templates:read"]), session=object()
    )
    assert [i.key for i in out.images] == [
        "files/tenant/c.png",
        "files/tenant/b.png",
        "files/tenant/a.png",
    ]


@pytest.mark.asyncio
async def test_newest_first_across_pages_not_only_within_one(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    """The listing is DRAINED before it is sorted, because S3 orders keys lexicographically.

    A single-page stub cannot see this: sorting one page passes while the promise is false. The
    author's newest upload is on page two here — exactly where a `z`-named key lands — so a
    per-page sort would present the older page-one images in credible newest-first order and hide
    the upload the author came back for, which reads as "the upload did not work"."""
    _stub_store(monkeypatch, _Store())
    calls = _stub_list(
        monkeypatch,
        pages=[
            [
                _obj("files/tenant/a.png", when="2026-08-01T00:00:00+00:00"),
                _obj("files/tenant/b.png", when="2026-08-10T00:00:00+00:00"),
            ],
            [_obj("files/tenant/z-newest.png", when="2026-08-26T00:00:00+00:00")],
        ],
    )
    out = await et.list_email_template_images(
        principal=email_support.principal(firm, ["email_templates:read"]), session=object()
    )
    assert len(calls) == 2
    assert calls[1]["token"] == "tok-2"  # noqa: S105 — a continuation token, not a credential
    assert [i.key for i in out.images] == [
        "files/tenant/z-newest.png",
        "files/tenant/b.png",
        "files/tenant/a.png",
    ]
    assert out.next_token is None


@pytest.mark.asyncio
async def test_the_drain_stops_at_its_cap_and_says_so_rather_than_hiding_the_rest(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    """A bucket has no size limit, so the drain is bounded — and a bounded read must report what it
    did not reach. Returning no token past the cap would present a partial library as a whole one;
    the leftover token is what lets a caller go on."""
    _stub_store(monkeypatch, _Store())
    calls = _stub_list(
        monkeypatch,
        pages=[[_obj(f"files/tenant/p{n}.png")] for n in range(et._LIBRARY_MAX_PAGES + 3)],
    )
    out = await et.list_email_template_images(
        principal=email_support.principal(firm, ["email_templates:read"]), session=object()
    )
    assert len(calls) == et._LIBRARY_MAX_PAGES
    assert len(out.images) == et._LIBRARY_MAX_PAGES
    assert out.next_token is not None
    assert all(c["limit"] == et._LIBRARY_PAGE for c in calls)


@pytest.mark.asyncio
async def test_an_upload_and_the_listing_name_the_same_object(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    """One object, two endpoints, ONE identity.

    The upload used to answer a bare filename where the listing answers the full object key, so
    nothing could match an author's just-uploaded image to its library row. Asserted by running
    both halves over the same URL rather than against a literal, so the two cannot drift apart
    again.

    The URL is the RELAY PATH the upload now returns (`proxy=True`), and the identity still has to
    round-trip: `_library_key` maps that path back to the object key the listing publishes, which
    is the same key `put_branding_asset` wrote it under (`storage.TENANT_PUBLIC_PREFIX`)."""
    # A REAL content-hashed name (16 hex, as `put_branding_asset` writes), because this is the one
    # test that exercises the relay's own file-name grammar: `_library_key` claims a key only for a
    # name `get_public_asset` would actually serve, so a made-up short digest round-trips to "".
    url = "/_assets/image-0123456789abcdef.png"
    _stub_store(monkeypatch, _Store())
    _stub_put(monkeypatch, url)
    uploaded = await et.upload_email_template_image(
        file=_Upload(PNG),
        principal=email_support.principal(firm, ["email_templates:write"]),
        session=object(),
    )
    _stub_list(monkeypatch, [_obj(uploaded.key)])
    listed = await et.list_email_template_images(
        principal=email_support.principal(firm, ["email_templates:read"]), session=object()
    )
    assert [i.key for i in listed.images] == [uploaded.key]
    assert [i.url for i in listed.images] == [uploaded.url]


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("files/tenant/logo-9f2c.png", "Logo"),
        ("files/tenant/logo-dark-9f2c.png", "Logo dark"),
        ("files/tenant/image-9f2c.jpg", "Image"),
        ("files/tenant/favicon.png", "Favicon"),
    ],
)
def test_an_object_is_labelled_from_the_only_record_of_what_it_is(key: str, label: str) -> None:
    """Branding assets are content-hashed with no row anywhere naming their kind, so the key is the
    only thing that remembers."""
    assert et._image_label(key) == label


# ── Unconfigured storage: say so, never offer a data: URI as an email image ───


@pytest.mark.asyncio
async def test_no_bucket_reports_unhosted_rather_than_an_empty_shelf(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    """A firm with no resolved bucket has no library AND nowhere to upload one. `hosted=False` is
    what lets the editor say that, instead of showing an empty picker beside an Upload button whose
    result would never reach an inbox."""
    _stub_store(monkeypatch, None)
    out = await et.list_email_template_images(
        principal=email_support.principal(firm, ["email_templates:read"]), session=object()
    )
    assert (out.images, out.hosted) == ([], False)

    # A bucket with NO public base URL is hosted now: the relay serves the object and needs no
    # browser-reachable base (`storage.put_branding_asset(proxy=True)`). It reported unhosted while
    # the library published object-store URLs, which is the thing that changed — a firm whose store
    # has no public base could upload nothing usable, and now it can.
    _stub_store(monkeypatch, _Store(public_base_url=None))
    _stub_list(monkeypatch, [])
    out = await et.list_email_template_images(
        principal=email_support.principal(firm, ["email_templates:read"]), session=object()
    )
    assert (out.images, out.hosted) == ([], True)


@pytest.mark.asyncio
async def test_a_missing_bucket_is_an_empty_library_that_is_still_hosted(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    """The registry naming a bucket the store does not have is a repairable fault, not a firm
    without storage — so the answer is an empty shelf, not advice to go and find a URL."""
    _stub_store(monkeypatch, _Store())

    async def _boom(*_a: Any, **_kw: Any) -> storage.ObjectPage:
        raise storage.BucketMissing("tnt-abc")

    monkeypatch.setattr(et.storage, "list_objects", _boom)
    out = await et.list_email_template_images(
        principal=email_support.principal(firm, ["email_templates:read"]), session=object()
    )
    assert (out.images, out.hosted) == ([], True)


@pytest.mark.asyncio
async def test_a_data_uri_upload_is_reported_as_unhosted(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    """`put_branding_asset`'s last rung is a base64 `data:` URI, and mail clients do not render one
    (`email.masthead_image` refuses one for the same reason). It is stored and usable on the canvas,
    so the answer is the URL plus `hosted=False` — the editor warns rather than the API refusing
    work an author can still finish."""
    _stub_store(monkeypatch, _Store(public_base_url=None))
    _stub_put(monkeypatch, "data:image/png;base64,AAAA")
    out = await et.upload_email_template_image(
        file=_Upload(PNG),
        principal=email_support.principal(firm, ["email_templates:write"]),
        session=object(),
    )
    assert out.hosted is False
    assert out.url.startswith("data:")


# ── The bytes decide, not the header ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_declared_content_type_is_never_trusted(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    _stub_store(monkeypatch, _Store())
    put = _stub_put(monkeypatch, "http://x/files/tenant/image-1.png")
    for body in (PDF, b"GIF89a" + b"\x00" * 32, b"not an image at all"):
        with pytest.raises(HTTPException) as ei:
            await et.upload_email_template_image(
                file=_Upload(body, content_type="image/png"),
                principal=email_support.principal(firm, ["email_templates:write"]),
                session=object(),
            )
        assert ei.value.status_code == 422
        assert "PNG or JPEG" in str(ei.value.detail)
    assert put == [], "a refused upload must not have been stored"


@pytest.mark.asyncio
async def test_the_sniffed_type_is_what_gets_stored(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    """A JPEG declared as `image/png` is stored as a JPEG: the extension comes from the bytes, so
    the key cannot claim a format the object is not."""
    _stub_store(monkeypatch, _Store())
    put = _stub_put(monkeypatch, "http://x/files/tenant/image-1.jpg")
    await et.upload_email_template_image(
        file=_Upload(JPEG, content_type="image/png"),
        principal=email_support.principal(firm, ["email_templates:write"]),
        session=object(),
    )
    assert put[0]["content_type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_an_oversized_body_is_refused_while_it_is_being_read(
    monkeypatch: pytest.MonkeyPatch, firm: dict[str, str]
) -> None:
    """413 from `read_capped`, which stops at the cap rather than buffering the whole body first.
    The route also carries the ceiling at the ASGI layer (`_ImageUploadRoute`) — Starlette spools a
    file part to disk before any of this runs, so the in-handler cap is the second line, not the
    first."""
    _stub_store(monkeypatch, _Store())
    put = _stub_put(monkeypatch, "http://x/files/tenant/image-1.png")
    with pytest.raises(HTTPException) as ei:
        await et.upload_email_template_image(
            file=_Upload(PNG + b"\x00" * (storage.MAX_ASSET_BYTES + 1)),
            principal=email_support.principal(firm, ["email_templates:write"]),
            session=object(),
        )
    assert ei.value.status_code == 413
    assert put == []


# ── The listing reaches the tenant's OWN store ───────────────────────────────


@pytest.mark.asyncio
async def test_an_explicit_endpoint_lists_that_store_without_the_documents_bucket_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`storage.list_objects` was built for the admin browser, which reads the shared private
    documents bucket and is right to refuse when `S3_DOCUMENTS_BUCKET` is unset. A tenant bucket is
    resolved from the registry and may sit on another endpoint, where that guard is irrelevant and
    the default client would query the wrong host — an answer indistinguishable from an empty
    bucket. The documents bucket is blanked here explicitly: a local `.env.local` sets it, so
    relying on the environment would make the guard half of this test vacuous."""
    monkeypatch.setattr(storage.settings, "s3_documents_bucket", "")
    seen: list[tuple[str, str | None]] = []

    class _Client:
        @staticmethod
        def list_objects_v2(**kw: Any) -> dict[str, Any]:
            return {
                "Contents": [
                    {
                        "Key": "files/tenant/logo-1.png",
                        "Size": 12,
                        "LastModified": __import__("datetime").datetime(2026, 8, 26),
                    }
                ]
            }

    def _client_for(endpoint: str, region: str | None) -> Any:
        seen.append((endpoint, region))
        return _Client()

    monkeypatch.setattr(storage, "client_for", _client_for)
    page = await storage.list_objects(
        "tnt-abc", "files/tenant/", delimiter="", endpoint="http://rustfs:9000", region=None
    )
    assert seen == [("http://rustfs:9000", None)]
    assert [o.key for o in page.objects] == ["files/tenant/logo-1.png"]


@pytest.mark.asyncio
async def test_without_an_endpoint_the_documents_guard_still_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard is not removed, only scoped: a caller that names no store is the admin browser, and
    it must still refuse rather than query a client with no bucket configured."""
    monkeypatch.setattr(storage.settings, "s3_documents_bucket", "")
    with pytest.raises(storage.StorageUnavailable):
        await storage.list_objects("whatever", "files/")


def test_the_upload_route_carries_a_body_ceiling() -> None:
    """The ASGI-layer cap, which is the only one that runs before Starlette spools the part to
    disk. Asserted on the route class rather than by sending a body, which needs a live server."""
    assert issubclass(et._ImageUploadRoute, et.CappedBodyRoute)
    assert storage.MAX_ASSET_BYTES <= et._ImageUploadRoute.body_limit
    assert et._ImageUploadRoute.body_limit < storage.MAX_ASSET_BYTES * 2


# --- THE OPS DOOR ---------------------------------------------------------------------------
#
# The firm door scopes the bucket by the SESSION and offers no parameter with which to name another
# tenant, which is what the tests above pin. An operator curating a firm's templates has no firm
# session, so without a second door the picker degrades to the link field alone and every image they
# place has to be a URL found somewhere else. That door names the tenant in the PATH, so what has to
# hold is the scope gate — read to list, write to upload — and that both doors run the same body.


def test_both_doors_run_the_same_two_functions() -> None:
    """One implementation, two gates. A second copy of the listing or the upload is how the public
    prefix stops being the only prefix on one of them."""
    from app.routers import admin

    source = __import__("inspect").getsource(admin)
    assert "await list_images(session, tenant_id, token)" in source
    assert "await store_image(session, tenant_id, file)" in source
    # And the firm door delegates too, rather than keeping the body it used to hold.
    firm = __import__("inspect").getsource(et)
    assert "return await list_images(session, tenant_id, token)" in firm
    assert "return await store_image(session, tenant_id, file)" in firm


def test_the_ops_upload_asks_for_write_and_the_listing_for_read() -> None:
    """Listing a firm's own images is a read of theirs; putting one in their public prefix is a
    write, the bar the ops branding upload and send-test already clear."""
    from app.routers import admin

    listing = __import__("inspect").getsource(admin.admin_list_email_template_images)
    upload = __import__("inspect").getsource(admin.admin_upload_email_template_image)
    assert "write=False" in listing
    assert "write=True" in upload


def test_the_ops_upload_carries_the_same_body_ceiling() -> None:
    """The cap is on the ROUTE, so a second route needs it declared again — and the decorator form
    cannot pass a route class, which is why both are registered imperatively."""
    from app.routers import admin

    route = next(
        r
        for r in admin.router.routes
        if getattr(r, "name", "") == "upload-email-template-image"
        and "/email-templates/images" in getattr(r, "path", "")
    )
    assert type(route) is et._ImageUploadRoute


def test_both_doors_declare_images_before_their_uid_route() -> None:
    """ROUTE ORDER IS THE CONTRACT. FastAPI matches in declaration order, so an images path declared
    after `/{uid}` resolves as `uid="images"` and answers "Email template not found" — which the
    editor reads as a firm with no image library, not as a routing mistake. Cost one round of
    "still no upload": the endpoints were right and unreachable."""
    from app.routers import admin

    def order(router: object, images: str, uid: str) -> tuple[int, int]:
        paths = [getattr(r, "path", "") for r in router.routes]  # type: ignore[attr-defined]
        return paths.index(images), paths.index(uid)

    images, one = order(
        admin.router,
        "/admin/tenants/{tenant_uid}/email-templates/images",
        "/admin/tenants/{tenant_uid}/email-templates/{uid}",
    )
    assert images < one

    # The firm router carries its own prefix on each path.
    images, one = order(et.router, "/email-templates/images", "/email-templates/{uid}")
    assert images < one
