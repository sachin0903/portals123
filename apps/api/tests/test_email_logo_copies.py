"""An email carries a picture a mail client draws inline — never the SVG or WebP itself.

The reported fault: the masthead attached the firm's stored logo as it was. Outlook showed the SVG
(Graphene) and the WebP (Marlborough) as FILE ATTACHMENTS above the message, and the SVG was broken
in the body too. A mail client draws PNG, JPEG and GIF inline and nothing else.

So the send path (`transport.email_assets`) swaps such a logo for its PNG email copy, stored at
upload (`app/branding_uploads.py`); converts a WebP or ICO in memory when an older upload has no
copy, and stores the result; keeps every answer in memory, because asset names are content-hashed
and the bytes behind one never change; and with no picture at all, lets the masthead fall back to
the icon, then to the firm's name in type.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Any

import pytest
from PIL import Image

from app import imaging, storage
from app.email import images as email_images
from app.email import transport

ORIGIN = "https://client.firm.test"
DIGEST = "628afb62f38871ae"
SVG_LOGO = f"logo-{DIGEST}.svg"
SVG_COPY = f"logo-email-{DIGEST}.png"
WEBP_LOGO = f"logo-{DIGEST}.webp"
WEBP_COPY = f"logo-email-{DIGEST}.png"
PNG_ICON = "icon-0123456789abcdef.png"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect/></svg>'


def _encode(fmt: str, size: tuple[int, int] = (900, 300)) -> bytes:
    out = BytesIO()
    Image.new("RGBA", size, (20, 40, 200, 255)).save(out, fmt)
    return out.getvalue()


PNG = _encode("PNG", (64, 64))


class _Bucket:
    """A tenant's public prefix: what `get_public_asset` reads and `put_email_copy` writes, with
    every read counted so a cache hit can be told from a read."""

    def __init__(self, objects: dict[str, tuple[bytes, str]]) -> None:
        self.objects = dict(objects)
        self.reads: list[str] = []
        self.copies: list[str] = []
        self.down = False

    async def get(self, _session: Any, tenant_id: int, name: str) -> tuple[bytes, str]:
        assert tenant_id == 7
        if self.down:
            raise RuntimeError("object store down")
        if storage.public_asset_name(f"/_assets/{name}") != name:
            raise ValueError("invalid asset")
        self.reads.append(name)
        if name not in self.objects:
            raise FileNotFoundError(name)
        return self.objects[name]

    async def put_copy(self, _session: Any, tenant_id: int, name: str, png: bytes) -> str:
        copy = storage.email_copy_name(name)
        assert copy is not None and png.startswith(b"\x89PNG")
        self.copies.append(copy)
        self.objects[copy] = (png, "image/png")
        return copy


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No database: `email_assets` opens a `system_transaction` only to lend `get_public_asset` a
    session, which the stub bucket ignores. Every test starts with an empty cache."""
    import app.db

    @asynccontextmanager
    async def _no_db() -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(app.db, "system_transaction", _no_db)
    transport.clear_caches()
    yield
    transport.clear_caches()


def _bucket(monkeypatch: pytest.MonkeyPatch, objects: dict[str, tuple[bytes, str]]) -> _Bucket:
    bucket = _Bucket(objects)
    monkeypatch.setattr(transport.storage, "get_public_asset", bucket.get)
    monkeypatch.setattr(transport.storage, "put_email_copy", bucket.put_copy)
    return bucket


def _is_png(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n")


# ── What travels for one asset ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_png_travels_as_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    _bucket(monkeypatch, {PNG_ICON: (PNG, "image/png")})
    out = await transport.email_assets(7, [PNG_ICON])
    assert out[PNG_ICON] == transport.EmailAsset(PNG, "image/png", PNG_ICON)


@pytest.mark.asyncio
async def test_an_svg_travels_as_its_stored_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    copy = _encode("PNG")
    _bucket(monkeypatch, {SVG_LOGO: (SVG, "image/svg+xml"), SVG_COPY: (copy, "image/png")})
    out = await transport.email_assets(7, [SVG_LOGO])
    assert out[SVG_LOGO] == transport.EmailAsset(copy, "image/png", SVG_COPY)


@pytest.mark.asyncio
@pytest.mark.skipif(not imaging.svg_supported(), reason="no Cairo library on this machine")
async def test_an_old_svg_is_drawn_once_and_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ask: the firms' current logos are mostly SVG, uploaded before copies existed. The first
    send draws the PNG (CairoSVG), stores it as the copy, and every later send reads that."""
    bucket = _bucket(monkeypatch, {SVG_LOGO: (SVG, "image/svg+xml")})
    asset = (await transport.email_assets(7, [SVG_LOGO]))[SVG_LOGO]
    assert _is_png(asset.data) and asset.content_type == "image/png"
    assert bucket.copies == [SVG_COPY] and asset.stored_as == SVG_COPY

    transport.clear_caches()  # a new process
    bucket.reads.clear()
    assert (await transport.email_assets(7, [SVG_LOGO]))[SVG_LOGO].stored_as == SVG_COPY
    assert bucket.reads == [SVG_COPY] and bucket.copies == [SVG_COPY]


@pytest.mark.asyncio
async def test_without_cairo_an_old_svg_travels_as_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine with no Cairo library cannot draw it: no picture — and never the SVG itself."""
    monkeypatch.setattr(imaging, "_cairosvg", lambda: None)
    bucket = _bucket(monkeypatch, {SVG_LOGO: (SVG, "image/svg+xml")})
    assert await transport.email_assets(7, [SVG_LOGO]) == {}
    assert bucket.copies == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "fmt"), [(WEBP_LOGO, "WEBP"), (f"icon-{DIGEST}.ico", "ICO")])
async def test_an_older_webp_or_ico_is_converted_once_and_stored(
    monkeypatch: pytest.MonkeyPatch, name: str, fmt: str
) -> None:
    """The Marlborough case, for a logo uploaded before copies existed: converted in memory on the
    first send, stored as the copy so the next process reads it, and the copy is what is named."""
    bucket = _bucket(monkeypatch, {name: (_encode(fmt, (256, 256)), f"image/{fmt.lower()}")})
    out = await transport.email_assets(7, [name])
    asset = out[name]
    assert _is_png(asset.data) and asset.content_type == "image/png"
    assert bucket.copies == [storage.email_copy_name(name)]
    assert asset.stored_as == storage.email_copy_name(name)

    # A new process (an empty cache) reads the stored copy and converts nothing.
    transport.clear_caches()
    bucket.reads.clear()
    again = await transport.email_assets(7, [name])
    assert again[name].data == bucket.objects[storage.email_copy_name(name) or ""][0]
    assert bucket.reads == [storage.email_copy_name(name)]
    assert len(bucket.copies) == 1


@pytest.mark.asyncio
async def test_a_converted_copy_that_cannot_be_stored_still_travels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bucket(monkeypatch, {WEBP_LOGO: (_encode("WEBP"), "image/webp")})

    async def _refuse(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("bucket is read-only")

    monkeypatch.setattr(transport.storage, "put_email_copy", _refuse)
    asset = (await transport.email_assets(7, [WEBP_LOGO]))[WEBP_LOGO]
    assert _is_png(asset.data) and asset.stored_as is None


@pytest.mark.asyncio
async def test_a_webp_under_a_png_name_is_converted_not_sent_as_a_png(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Judged by the bytes: a WebP kept as `logo-….png` would otherwise travel labelled a PNG."""
    name = f"logo-{DIGEST}.png"
    bucket = _bucket(monkeypatch, {name: (_encode("WEBP"), "image/png")})
    asset = (await transport.email_assets(7, [name]))[name]
    assert _is_png(asset.data) and asset.content_type == "image/png"
    assert bucket.copies == [f"logo-email-{DIGEST}.png"]


# ── Kept in memory ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_second_send_reads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    bucket = _bucket(
        monkeypatch, {SVG_LOGO: (SVG, "image/svg+xml"), SVG_COPY: (_encode("PNG"), "image/png")}
    )
    first = await transport.email_assets(7, [SVG_LOGO])
    reads = list(bucket.reads)
    second = await transport.email_assets(7, [SVG_LOGO])
    assert second == first
    assert bucket.reads == reads  # nothing more was read


@pytest.mark.asyncio
async def test_a_missing_copy_is_looked_for_again_after_the_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process that could not draw an SVG (no Cairo library) misses; re-uploading the same SVG in
    the studio, which draws it in the browser, writes its copy under the SAME name — so a miss must
    not be remembered for ever, only for the brand cache's TTL."""
    monkeypatch.setattr(imaging, "_cairosvg", lambda: None)
    bucket = _bucket(monkeypatch, {SVG_LOGO: (SVG, "image/svg+xml")})
    now = [1000.0]
    monkeypatch.setattr(transport.time, "monotonic", lambda: now[0])
    assert await transport.email_assets(7, [SVG_LOGO]) == {}
    bucket.objects[SVG_COPY] = (_encode("PNG"), "image/png")  # the studio re-upload lands

    now[0] += transport.CACHE_TTL_SECONDS - 1
    assert await transport.email_assets(7, [SVG_LOGO]) == {}  # still the remembered miss
    now[0] += 2
    assert (await transport.email_assets(7, [SVG_LOGO]))[SVG_LOGO].stored_as == SVG_COPY


@pytest.mark.asyncio
async def test_an_outage_is_not_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    bucket = _bucket(monkeypatch, {PNG_ICON: (PNG, "image/png")})
    bucket.down = True
    assert await transport.email_assets(7, [PNG_ICON]) == {}
    bucket.down = False
    assert PNG_ICON in await transport.email_assets(7, [PNG_ICON])


@pytest.mark.asyncio
async def test_the_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    names = [f"image-{i:016x}.png" for i in range(transport._EMAIL_ASSETS_MAX + 5)]  # noqa: SLF001
    _bucket(monkeypatch, dict.fromkeys(names, (PNG, "image/png")))
    for n in names:
        await transport.email_assets(7, [n])
    assert len(transport._email_assets) == transport._EMAIL_ASSETS_MAX  # noqa: SLF001
    assert (7, names[0]) not in transport._email_assets  # noqa: SLF001  oldest out first
    assert (7, names[-1]) in transport._email_assets  # noqa: SLF001


@pytest.mark.asyncio
async def test_no_tenant_reads_nothing() -> None:
    assert await transport.email_assets(None, [PNG_ICON]) == {}


# ── The masthead ──────────────────────────────────────────────────────────────


def _branding(**kw: object) -> Any:
    from app import branding

    return branding.Branding(**kw)  # type: ignore[arg-type]


async def _urls(monkeypatch: pytest.MonkeyPatch, **kw: object) -> tuple[str | None, str | None]:
    async def origin(_session: Any, tenant_id: int | None) -> str:
        return ORIGIN

    monkeypatch.setattr(transport, "asset_origin", origin)
    return await transport._email_asset_urls(  # noqa: SLF001
        None,  # type: ignore[arg-type]
        7,
        "client",
        _branding(**kw),
    )


@pytest.mark.asyncio
async def test_an_svg_logo_is_stated_as_its_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Named as the PNG, so even if the bytes do not travel the URL is a picture a client draws."""
    _bucket(monkeypatch, {SVG_LOGO: (SVG, "image/svg+xml"), SVG_COPY: (PNG, "image/png")})
    masthead, _icon = await _urls(monkeypatch, logo_url=f"/_assets/{SVG_LOGO}")
    assert masthead == f"{ORIGIN}/_assets/{SVG_COPY}"


@pytest.mark.asyncio
async def test_an_svg_logo_with_no_copy_gives_way_to_the_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a machine that cannot draw it (no Cairo library) — with Cairo it is drawn instead."""
    monkeypatch.setattr(imaging, "_cairosvg", lambda: None)
    _bucket(monkeypatch, {SVG_LOGO: (SVG, "image/svg+xml"), PNG_ICON: (PNG, "image/png")})
    masthead, icon = await _urls(
        monkeypatch, logo_url=f"/_assets/{SVG_LOGO}", icon_url=f"/_assets/{PNG_ICON}"
    )
    assert masthead == icon == f"{ORIGIN}/_assets/{PNG_ICON}"


@pytest.mark.asyncio
async def test_with_no_picture_at_all_the_masthead_wears_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logo and icon both SVG with no copies, on a machine that cannot draw them. The firm's name
    in type, never a broken image and an attachment."""
    monkeypatch.setattr(imaging, "_cairosvg", lambda: None)
    _bucket(monkeypatch, {SVG_LOGO: (SVG, "image/svg+xml"), f"icon-{DIGEST}.svg": (SVG, "")})
    masthead, icon = await _urls(
        monkeypatch, logo_url=f"/_assets/{SVG_LOGO}", icon_url=f"/_assets/icon-{DIGEST}.svg"
    )
    assert masthead is None and icon is None


@pytest.mark.asyncio
async def test_a_webp_converted_in_memory_keeps_its_own_name_when_unstored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Converted but not stored (a read-only bucket): the URL stays the original's, and the send
    attaches the in-memory PNG under it."""
    _bucket(monkeypatch, {WEBP_LOGO: (_encode("WEBP"), "image/webp")})

    async def _refuse(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("read-only")

    monkeypatch.setattr(transport.storage, "put_email_copy", _refuse)
    masthead, _icon = await _urls(monkeypatch, logo_url=f"/_assets/{WEBP_LOGO}")
    assert masthead == f"{ORIGIN}/_assets/{WEBP_LOGO}"
    own = await transport._own_assets(f'<img src="{masthead}">', 7)  # noqa: SLF001
    data, content_type = own[WEBP_LOGO]
    assert _is_png(data) and content_type == "image/png"


@pytest.mark.asyncio
async def test_a_typed_in_svg_url_is_refused_and_an_extensionless_one_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bucket(monkeypatch, {})
    masthead, _ = await _urls(
        monkeypatch, logo_url="https://cdn.test/logo.svg", icon_url="https://cdn.test/mark.png"
    )
    assert masthead == "https://cdn.test/mark.png"
    masthead, _ = await _urls(monkeypatch, logo_url="https://cdn.test/brand/logo")
    assert masthead == "https://cdn.test/brand/logo"


def test_the_sync_resolver_refuses_a_typed_in_svg_too() -> None:
    assert (
        transport.masthead_image(
            _branding(logo_url="https://cdn.test/l.svg", icon_url="https://cdn.test/i.png")
        )
        == "https://cdn.test/i.png"
    )


@pytest.mark.asyncio
async def test_brand_for_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole resolve, from the stored theme: an SVG logo with a copy is a PNG masthead."""
    _bucket(monkeypatch, {SVG_LOGO: (SVG, "image/svg+xml"), SVG_COPY: (PNG, "image/png")})

    async def row(_session: Any, tenant_id: int) -> tuple[str, dict[str, object]]:
        return "Graphene Platforms", {"client": {"logo_url": f"/_assets/{SVG_LOGO}"}}

    async def origin(_session: Any, tenant_id: int | None) -> str:
        return ORIGIN

    monkeypatch.setattr(transport, "_tenant_row", row)
    monkeypatch.setattr(transport, "asset_origin", origin)
    brand = await transport.brand_for(None, 7)  # type: ignore[arg-type]
    assert brand.logo_url == f"{ORIGIN}/_assets/{SVG_COPY}"


# ── The wire ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_copy_goes_as_a_png_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    _bucket(monkeypatch, {SVG_COPY: (PNG, "image/png")})
    html = f'<img src="{ORIGIN}/_assets/{SVG_COPY}" alt="Graphene Platforms">'
    own = await transport._own_assets(html, 7)  # noqa: SLF001
    out, attachments = await email_images.inline_own_images(html, own_assets=own)
    assert 'src="cid:img1"' in out
    [a] = attachments
    assert a.payload() == {
        "filename": SVG_COPY,
        "content": base64.b64encode(PNG).decode("ascii"),
        "content_id": "img1",
        "content_type": "image/png",
    }


@pytest.mark.asyncio
async def test_bytes_converted_in_memory_are_named_for_what_they_are() -> None:
    html = f'<img src="{ORIGIN}/_assets/{WEBP_LOGO}">'
    result = await email_images.inline_own_images(html, own_assets={WEBP_LOGO: (PNG, "image/png")})
    [a] = result[1]
    assert a.filename == f"logo-{DIGEST}.png" and a.content_type == "image/png"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "content_type"),
    [(SVG, "image/svg+xml"), (_encode("WEBP"), "image/webp"), (b"junk", "")],
    ids=["svg", "webp", "junk"],
)
async def test_a_type_a_mail_client_does_not_draw_is_never_attached(
    data: bytes, content_type: str
) -> None:
    """Whatever reaches the inliner: an SVG or WebP keeps its URL rather than becoming a file
    attachment above the message."""
    html = f'<img src="/_assets/{SVG_LOGO}">'
    out, attachments = await email_images.inline_own_images(
        html, own_assets={SVG_LOGO: (data, content_type)}
    )
    assert out == html and attachments == []


@pytest.mark.asyncio
async def test_a_stored_type_is_never_trusted_over_the_bytes() -> None:
    html = f'<img src="/_assets/{PNG_ICON}">'
    out, attachments = await email_images.inline_own_images(
        html, own_assets={PNG_ICON: (_encode("WEBP"), "image/png")}
    )
    assert out == html and attachments == []


@pytest.mark.asyncio
async def test_a_stored_type_left_blank_is_read_from_the_bytes() -> None:
    """An object stored with no content type is still a PNG if its bytes say so."""
    html = f'<img src="/_assets/{PNG_ICON}">'
    result = await email_images.inline_own_images(
        html, own_assets={PNG_ICON: (PNG, "application/octet-stream")}
    )
    [a] = result[1]
    assert a.content_type == "image/png"
