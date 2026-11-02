"""A logo upload keeps the firm's file, and gets a PNG twin for email (app/branding_uploads.py).

The reported fault: a firm's SVG logo (and another firm's WebP one) went into every email as an
attachment, and Outlook showed it as a FILE above the message — with the SVG broken in the body as
well. A mail client draws PNG, JPEG and GIF inline and nothing else. So a logo or icon stored as
SVG, WebP or ICO now gets a PNG copy at upload, named from the original
(`logo-{digest}.svg` → `logo-email-{digest}.png`), and the original is stored exactly as before.

Also pinned here: GIF, AVIF, BMP, TIFF and HEIC — refused before — are accepted and stored as a
PNG (or a JPEG for an opaque photograph), so the stored types never grow.
"""

from __future__ import annotations

import base64
import hashlib
import os
from io import BytesIO
from typing import Any

import pytest
from fastapi import HTTPException
from PIL import Image, ImageDraw

from app import branding_uploads, imaging, storage
from app.routers import admin as admin_router
from app.routers import firm as firm_router

#: A 96x32 HEIC (a blue bar on white), made with pillow-heif's encoder — pi-heif only decodes.
HEIC_96x32 = base64.b64decode(
    "AAAAHGZ0eXBoZWljAAAAAG1pZjFoZWljbWlhZgAAAX1tZXRhAAAAAAAAACFoZGxyAAAAAAAAAABwaWN0AAAAAAAAAAAA"
    "AAAAAAAAACJpbG9jAAAAAERAAAEAAQAAAAABoQABAAAAAAAAAGIAAAAjaWluZgAAAAAAAQAAABVpbmZlAgAAAAABAABo"
    "dmMxAAAAAA5waXRtAAAAAAABAAAA/WlwcnAAAADdaXBjbwAAAHZodmNDAQNwAAAAkAAAAAAAHvAA/P34+AAADwNgAAEA"
    "GEABDAH//wNwAAADAJAAAAMAAAMAHroCQGEAAQAqQgEBA3AAAAMAkAAAAwAAAwAeoDCBBZbq5Ka5uAhoMCAAAAMDIAAA"
    "AwAhYgABAAZEAcFzwYkAAAATY29scm5jbHgAAQANAAaAAAAAFGlzcGUAAAAAAAAAYAAAAEAAAAAoY2xhcAAAAGAAAAAB"
    "AAAAIAAAAAEAAAAAAAAAAv///+AAAAACAAAAEHBpeGkAAAAAAwgICAAAABhpcG1hAAAAAAAAAAEAAQWBAgMFhAAAAGpt"
    "ZGF0AAAAXigBrwnR6EAjwArPGcNRe/9T2a+dP9ydYbvIe6RGO6t5b82sgtRHesVSdsyT/3IMkKMZ/hyIjGNSRXbykVJv"
    "0NHAKyeQG4Tk6XVe7xA/oylE/dJv0vmzz9D4YLaiPWA="
)

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1740 312">'
    b'<rect width="9" height="9"/></svg>'
)


def _logo(size: tuple[int, int] = (1200, 400)) -> Image.Image:
    """A wide transparent logo: a blue bar on nothing."""
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    w, h = size
    im.paste((20, 40, 200, 255), (w // 20, h // 10, w * 19 // 20, h * 9 // 10))
    return im


def _encode(im: Image.Image, fmt: str, **kw: Any) -> bytes:
    out = BytesIO()
    im.save(out, fmt, **kw)
    return out.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(data)) as im:
        return im.size


def _format(data: bytes) -> str | None:
    with Image.open(BytesIO(data)) as im:
        return im.format


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


class _Upload:
    """The slice of `UploadFile` the routes read: `read()`, chunked or whole, and the declared
    type."""

    def __init__(self, data: bytes, content_type: str | None) -> None:
        self._buf = BytesIO(data)
        self.content_type = content_type

    async def read(self, size: int = -1, /) -> bytes:
        return self._buf.read(size)


def _capture_puts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Both writes — the original and its email copy — through the real key derivation, into a
    list, on the legacy shared-bucket rung (no tenant store)."""
    puts: list[dict[str, Any]] = []

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
        puts.append({"key": key, "data": data, "content_type": content_type})

    async def no_tenant_store(_session: Any, _tenant_id: int) -> None:
        return None

    monkeypatch.setattr(storage, "_put", fake_put)
    monkeypatch.setattr(storage.settings, "s3_bucket", "branding")
    monkeypatch.setattr(storage.settings, "s3_public_base_url", "")
    monkeypatch.setattr(storage.tenant_storage, "get_tenant_store", no_tenant_store)
    return puts


async def _admin_upload(
    kind: str, data: bytes, declared: str | None, email_copy: bytes | None = None
) -> dict[str, str]:
    return await admin_router._store_branding_asset(  # noqa: SLF001
        None,  # type: ignore[arg-type]
        7,
        kind,
        _Upload(data, declared),  # type: ignore[arg-type]
        _Upload(email_copy, "image/png") if email_copy is not None else None,  # type: ignore[arg-type]
    )


# ── The converter ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (_encode(_logo().convert("P"), "GIF"), "image/gif"),
        (_encode(_logo(), "AVIF"), "image/avif"),
        (_encode(_logo().convert("RGB"), "BMP"), "image/bmp"),
        (_encode(_logo(), "TIFF"), "image/tiff"),
        (HEIC_96x32, "image/heic"),
    ],
    ids=["gif", "avif", "bmp", "tiff", "heic"],
)
def test_each_new_type_is_read_from_its_bytes_and_made_a_png(data: bytes, kind: str) -> None:
    assert imaging.sniff_convertible(data) == kind
    png = imaging.email_png(data)
    assert png is not None and png.startswith(imaging.PNG_SIGNATURE)
    width, height = _size(png)
    assert width <= imaging.EMAIL_COPY_BOX[0] and height <= imaging.EMAIL_COPY_BOX[1]


@pytest.mark.parametrize(
    "data",
    [
        SVG,  # no rasteriser on the server: the studio draws an SVG's copy
        _encode(_logo(), "PNG"),  # already a type the store keeps
        _encode(_logo(), "WEBP"),  # likewise
        b"%PDF-1.7 not a picture",
        b"plain words",
    ],
    ids=["svg", "png", "webp", "pdf", "text"],
)
def test_only_the_new_types_are_converted_at_upload(data: bytes) -> None:
    assert imaging.sniff_convertible(data) is None


def test_an_email_copy_keeps_the_shape_and_the_transparency() -> None:
    mark = Image.new("RGBA", (1740, 312), (0, 0, 0, 0))
    ImageDraw.Draw(mark).ellipse((0, 0, 1739, 311), fill=(20, 40, 200, 255))
    png = imaging.email_png(_encode(mark, "WEBP"))
    assert png is not None
    with Image.open(BytesIO(png)) as im:
        assert im.mode == "RGBA"
        assert im.size == (640, 115)  # the email box's width, aspect kept
        assert im.getpixel((0, 0))[3] == 0  # the transparent corner is still transparent


def test_an_email_copy_loses_its_empty_border() -> None:
    """The ICO test email: a wide wordmark inside a square icon file. The masthead draws the
    picture 28px tall, so the empty canvas above and below made the logo a few pixels high."""
    square = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    square.paste((20, 40, 200, 255), (0, 110, 256, 146))  # a 256x36 band in the middle
    square.putpixel((0, 0), (0, 0, 0, 5))  # anti-aliasing haze does not hold the border open
    png = imaging.email_png(_encode(square, "ICO", sizes=[(256, 256)]))
    assert png is not None and _size(png) == (256, 36)


def test_an_opaque_picture_is_not_trimmed() -> None:
    """White is a colour, not an empty border: a JPEG-like logo on white keeps its edges."""
    flat = Image.new("RGB", (900, 300), (255, 255, 255))
    flat.paste((20, 40, 200), (300, 100, 600, 200))
    png = imaging.email_png(_encode(flat, "WEBP"))
    assert png is not None and _size(png) == (640, 213)


def test_an_email_copy_is_never_enlarged() -> None:
    png = imaging.email_png(_encode(Image.new("RGBA", (120, 40), (20, 40, 200, 255)), "WEBP"))
    assert png is not None and _size(png) == (120, 40)


def test_awkward_pixel_modes_still_convert() -> None:
    """Scanner TIFFs are 16-bit or CMYK, and Pillow has no direct route from either to RGBA."""
    for im in (
        Image.new("I;16", (300, 100), 40000),
        Image.new("F", (300, 100), 0.5),
        _logo().convert("RGB").convert("CMYK"),
        _logo().convert("LA"),
    ):
        assert imaging.email_png(_encode(im, "TIFF")) is not None, im.mode


def test_an_animated_gif_becomes_its_first_frame() -> None:
    frames = [Image.new("RGB", (60, 20), c) for c in ((255, 0, 0), (0, 255, 0))]
    out = BytesIO()
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], duration=100)
    png = imaging.email_png(out.getvalue())
    assert png is not None
    with Image.open(BytesIO(png)) as im:
        assert im.convert("RGB").getpixel((5, 5)) == (255, 0, 0)


def test_a_picture_too_large_to_decode_is_refused_before_it_is() -> None:
    huge = _encode(Image.new("1", (8000, 6000)), "TIFF", compression="tiff_lzw")  # 48 MP, ~KB
    assert imaging.email_png(huge) is None
    with pytest.raises(ValueError, match="too large"):
        imaging.to_png(huge, imaging.EMAIL_COPY_BOX)


def test_junk_is_no_copy_rather_than_an_error() -> None:
    assert imaging.email_png(b"\x89PNG\r\n\x1a\nnot really") is None


def test_an_opaque_photograph_is_stored_as_a_jpeg_and_a_logo_as_a_png() -> None:
    photo = Image.frombytes("RGB", (900, 600), os.urandom(900 * 600 * 3))
    data, content_type = imaging.to_web(
        _encode(photo, "TIFF"), (1024, 1024), max_bytes=storage.MAX_ASSET_BYTES
    )
    assert content_type == "image/jpeg" and _format(data) == "JPEG"
    data, content_type = imaging.to_web(
        _encode(_logo(), "TIFF"), (1024, 1024), max_bytes=storage.MAX_ASSET_BYTES
    )
    assert content_type == "image/png" and _format(data) == "PNG"


def test_a_big_transparent_picture_stays_a_png() -> None:
    """A JPEG has no transparency and would paint it black; the store's own cap answers for size."""
    noisy = Image.frombytes("RGBA", (900, 600), os.urandom(900 * 600 * 4))
    data, content_type = imaging.to_web(_encode(noisy, "TIFF"), (1024, 1024), max_bytes=1024)
    assert content_type == "image/png"


@pytest.mark.parametrize(
    ("value", "mailable", "known_unmailable"),
    [
        ("logo-0123456789abcdef.png", True, False),
        ("logo-0123456789abcdef.jpg", True, False),
        ("logo-0123456789abcdef.svg", False, True),
        ("logo-0123456789abcdef.webp", False, True),
        ("favicon-0123456789abcdef.ico", False, True),
        ("https://cdn.test/brand/logo.SVG?v=2#x", False, True),
        ("https://cdn.test/brand/logo", False, False),  # no extension: kept, as it always was
    ],
)
def test_what_a_mail_client_draws_is_read_off_the_extension(
    value: str, mailable: bool, known_unmailable: bool
) -> None:
    assert imaging.is_mailable(value) is mailable
    assert imaging.is_known_unmailable(value) is known_unmailable


# ── Drawing an SVG (CairoSVG) ─────────────────────────────────────────────────

ILLUSTRATOR = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n<svg id="Layer_2" data-name="Layer 2" '
    b'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1740.4 312"><defs><style>'
    b'.cls-1{fill:#1e3a8a}</style></defs><rect class="cls-1" width="1740.4" height="312"/></svg>'
)
needs_cairo = pytest.mark.skipif(not imaging.svg_supported(), reason="no Cairo library")


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (ILLUSTRATOR, True),
        (b"\xef\xbb\xbf<!-- made by hand --><svg/>", True),
        (b"<!DOCTYPE svg><svg/>", True),
        (b"<html><body><svg/></body></html>", False),  # an HTML page is not an SVG
        (_encode(Image.new("RGB", (4, 4)), "PNG"), False),
    ],
    ids=["illustrator", "bom-and-comment", "doctype", "html", "png"],
)
def test_an_svg_is_known_by_its_markup(data: bytes, expected: bool) -> None:
    assert imaging.is_svg(data) is expected


@pytest.mark.parametrize(
    ("root", "size"),
    [
        (b'<svg viewBox="0 0 1740.4 312">', (1740.4, 312.0)),
        (b'<svg width="200" height="50px" viewBox="0 0 1 1">', (200.0, 50.0)),
        (b'<svg width="100%" height="100%" viewBox="0 0 400 100">', (400.0, 100.0)),
        (b'<svg width="200" viewBox="0 0 400 100">', (200.0, 50.0)),
        (b"<svg>", None),
        (b'<svg viewBox="0 0 0 10">', None),
    ],
)
def test_an_svg_s_size_is_read_like_the_studio_reads_it(root: bytes, size: object) -> None:
    """The same rules as `svgSize` in pkgs/ui `branding/email-copy.ts`."""
    assert imaging._svg_size(root + b"</svg>") == size  # noqa: SLF001


@needs_cairo
def test_the_illustrator_logo_is_drawn() -> None:
    png = imaging.email_png(ILLUSTRATOR)
    assert png is not None and _size(png) == (640, 115)
    with Image.open(BytesIO(png)) as im:
        assert im.convert("RGB").getpixel((320, 57)) == (30, 58, 138)  # the class's fill


@needs_cairo
def test_a_thin_wide_svg_is_drawn_bounded() -> None:
    """A 20000x10 strip is fitted by its width, not drawn tens of thousands of pixels across."""
    strip = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="20000" height="10">'
        b'<rect width="20000" height="10"/></svg>'
    )
    png = imaging.email_png(strip)
    assert png is not None and max(_size(png)) <= imaging.EMAIL_COPY_BOX[0]


@needs_cairo
def test_an_svg_cannot_reach_a_file_or_the_network(tmp_path: Any) -> None:
    """An uploaded SVG is the firm's markup on our server: it must not pull a file off the disk
    into a public email copy, nor make the server fetch a URL. An embedded image still draws."""
    secret = tmp_path / "secret.png"
    Image.new("RGB", (50, 50), (255, 0, 0)).save(secret)
    embedded = base64.b64encode(_encode(Image.new("RGB", (50, 50), (0, 255, 0)), "PNG")).decode()
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'width="400" height="100"><rect width="400" height="100" fill="#ffffff"/>'
        f'<image xlink:href="file://{secret.as_posix()}" x="0" width="100" height="100"/>'
        f'<image xlink:href="{secret.as_posix()}" x="100" width="100" height="100"/>'
        '<image xlink:href="https://example.invalid/x.png" x="200" width="100" height="100"/>'
        f'<image xlink:href="data:image/png;base64,{embedded}" x="300" width="100" height="100"/>'
        "</svg>"
    ).encode()
    png = imaging.email_png(svg)
    assert png is not None
    with Image.open(BytesIO(png)) as im:
        rgb = im.convert("RGB")
        quarter = rgb.width // 4
        middle = rgb.height // 2
        for i in range(3):  # the file (two ways) and the URL: nothing drawn
            assert rgb.getpixel((quarter * i + quarter // 2, middle)) == (255, 255, 255), i
        assert rgb.getpixel((quarter * 3 + quarter // 2, middle)) == (0, 255, 0)  # embedded


def test_without_cairo_an_svg_has_no_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imaging, "_cairosvg", lambda: None)
    assert imaging.email_png(ILLUSTRATOR) is None


def test_a_broken_svg_is_no_copy_rather_than_an_error() -> None:
    assert imaging.email_png(b"<svg><rect width=") is None


# ── Naming and storing the copy ───────────────────────────────────────────────


def test_the_copy_is_named_from_the_original() -> None:
    assert storage.email_copy_name("logo-628afb62f38871ae.svg") == "logo-email-628afb62f38871ae.png"
    assert (
        storage.email_copy_name("logo-dark-99ff8a16cbf41e1c.webp")
        == "logo-dark-email-99ff8a16cbf41e1c.png"
    )
    copy = storage.email_copy_name("icon-02d9dbc53780a4d7.svg")
    assert copy is not None and storage.public_asset_name(f"/_assets/{copy}") == copy
    assert storage.email_copy_name("../logo-628afb62f38871ae.svg") is None
    assert storage.email_copy_name("logo.svg") is None


def test_a_copy_is_told_apart_from_an_upload() -> None:
    assert storage.is_email_copy("logo-email-628afb62f38871ae.png")
    assert not storage.is_email_copy("logo-628afb62f38871ae.png")
    assert not storage.is_email_copy("image-628afb62f38871ae.png")
    assert not storage.is_email_copy("logo-email.png")


@pytest.mark.asyncio
async def test_the_copy_lands_on_the_same_rung_as_the_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    puts = _capture_puts(monkeypatch)
    png = _encode(_logo(), "PNG")
    assert await storage.put_email_copy(None, 7, "logo-628afb62f38871ae.svg", png) == (  # type: ignore[arg-type]
        "logo-email-628afb62f38871ae.png"
    )
    assert puts == [
        {
            "key": "tenants/7/logo-email-628afb62f38871ae.png",
            "data": png,
            "content_type": "image/png",
        }
    ]


@pytest.mark.asyncio
async def test_the_copy_prefers_the_tenant_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    puts = _capture_puts(monkeypatch)

    async def store(_session: Any, _tenant_id: int) -> Any:
        return SimpleNamespace(bucket="tnt-7", endpoint="http://s3", region=None)

    monkeypatch.setattr(storage.tenant_storage, "get_tenant_store", store)
    await storage.put_email_copy(None, 7, "icon-628afb62f38871ae.svg", _encode(_logo(), "PNG"))  # type: ignore[arg-type]
    assert [p["key"] for p in puts] == ["files/tenant/icon-email-628afb62f38871ae.png"]


@pytest.mark.asyncio
async def test_only_a_png_is_ever_stored_as_a_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    puts = _capture_puts(monkeypatch)
    with pytest.raises(ValueError):
        await storage.put_email_copy(None, 7, "logo-628afb62f38871ae.svg", SVG)  # type: ignore[arg-type]
    assert puts == []


@pytest.mark.asyncio
async def test_with_no_object_storage_there_is_no_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    puts = _capture_puts(monkeypatch)
    monkeypatch.setattr(storage.settings, "s3_bucket", "")
    png = _encode(_logo(), "PNG")
    assert await storage.put_email_copy(None, 7, "logo-628afb62f38871ae.svg", png) is None  # type: ignore[arg-type]
    assert puts == []


# ── The upload ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_webp_logo_is_kept_and_gets_a_png_twin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Marlborough case. The portals keep the WebP; email gets a PNG named from it."""
    puts = _capture_puts(monkeypatch)
    webp = _encode(_logo((900, 300)), "WEBP")  # under the shell bound: stored as is
    result = await _admin_upload("logo", webp, "image/webp")
    original = f"logo-{_digest(webp)}.webp"
    assert result == {"src": f"/_assets/{original}"}
    assert [p["key"] for p in puts] == [
        f"tenants/7/{original}",
        f"tenants/7/logo-email-{_digest(webp)}.png",
    ]
    assert puts[0]["data"] == webp  # the firm's own file, untouched
    assert puts[1]["content_type"] == "image/png" and _format(puts[1]["data"]) == "PNG"


@pytest.mark.asyncio
async def test_an_svg_logo_takes_the_copy_the_studio_drew(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Graphene case. No SVG rasteriser on the server: the browser draws the PNG and sends it
    with the file, and it is re-encoded here at email size like any other copy."""
    puts = _capture_puts(monkeypatch)
    drawn = _encode(Image.new("RGBA", (1740, 312), (20, 40, 200, 255)), "PNG")
    result = await _admin_upload("logo", SVG, "image/svg+xml", email_copy=drawn)
    assert result == {"src": f"/_assets/logo-{_digest(SVG)}.svg"}
    assert puts[0]["data"] == SVG and puts[0]["content_type"] == "image/svg+xml"
    assert puts[1]["key"] == f"tenants/7/logo-email-{_digest(SVG)}.png"
    assert _size(puts[1]["data"]) == (640, 115)


@pytest.mark.asyncio
async def test_the_firm_route_takes_the_copy_too(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(firm_router, "require_permission", lambda *_a, **_k: None)
    puts = _capture_puts(monkeypatch)
    ctx = SimpleNamespace(principal=None, session=None, tenant_id=9)
    result = await firm_router.upload_branding_asset(
        "icon",
        _Upload(SVG, "image/svg+xml"),  # type: ignore[arg-type]
        ctx,  # type: ignore[arg-type]
        _Upload(_encode(_logo((256, 256)), "PNG"), "image/png"),  # type: ignore[arg-type]
    )
    assert result["src"] == f"/_assets/icon-{_digest(SVG)}.svg"
    assert [p["key"] for p in puts] == [
        f"tenants/9/icon-{_digest(SVG)}.svg",
        f"tenants/9/icon-email-{_digest(SVG)}.png",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "copy",
    [None, SVG, b"\x89PNG\r\n\x1a\nnot really a png"],
    ids=["none", "not-a-png", "broken-png"],
)
async def test_an_svg_without_a_usable_copy_still_uploads(
    monkeypatch: pytest.MonkeyPatch, copy: bytes | None
) -> None:
    """The copy is best effort: on a machine that cannot draw an SVG and with no usable studio
    copy, the upload still lands. The send path survives without one (the icon, or the name)."""
    monkeypatch.setattr(imaging, "_cairosvg", lambda: None)
    puts = _capture_puts(monkeypatch)
    result = await _admin_upload("logo", SVG, "image/svg+xml", email_copy=copy)
    assert result == {"src": f"/_assets/logo-{_digest(SVG)}.svg"}
    assert [p["key"] for p in puts] == [f"tenants/7/logo-{_digest(SVG)}.svg"]


@pytest.mark.asyncio
@pytest.mark.skipif(not imaging.svg_supported(), reason="no Cairo library on this machine")
async def test_an_svg_with_no_studio_copy_is_drawn_on_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upload that did not come from the studio (or a studio that could not draw it)."""
    puts = _capture_puts(monkeypatch)
    await _admin_upload("logo", SVG, "image/svg+xml")
    assert puts[1]["key"] == f"tenants/7/logo-email-{_digest(SVG)}.png"
    assert _format(puts[1]["data"]) == "PNG"


@pytest.mark.asyncio
async def test_an_oversized_copy_is_ignored_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    puts = _capture_puts(monkeypatch)
    monkeypatch.setattr(imaging, "_cairosvg", lambda: None)
    big = imaging.PNG_SIGNATURE + b"\x00" * (branding_uploads.MAX_EMAIL_COPY_BYTES + 1)
    await _admin_upload("logo", SVG, "image/svg+xml", email_copy=big)
    assert len(puts) == 1


@pytest.mark.asyncio
async def test_a_copy_that_cannot_be_stored_does_not_fail_the_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    puts = _capture_puts(monkeypatch)

    async def down(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("object store hiccup")

    monkeypatch.setattr(storage, "put_email_copy", down)
    webp = _encode(_logo((900, 300)), "WEBP")  # under the shell bound: stored as is
    result = await _admin_upload("logo", webp, "image/webp")
    assert result == {"src": f"/_assets/logo-{_digest(webp)}.webp"}
    assert len(puts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["logo-dark", "favicon", "card-art"])
async def test_only_the_assets_email_reads_get_a_copy(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    puts = _capture_puts(monkeypatch)
    await _admin_upload(kind, _encode(_logo(), "WEBP"), "image/webp")
    await _admin_upload(kind, SVG, "image/svg+xml", email_copy=_encode(_logo(), "PNG"))
    assert len(puts) == 2
    assert not any(storage.is_email_copy(p["key"].rsplit("/", 1)[-1]) for p in puts)


@pytest.mark.asyncio
async def test_a_png_or_jpeg_logo_needs_no_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    puts = _capture_puts(monkeypatch)
    await _admin_upload("logo", _encode(_logo(), "PNG"), "image/png")
    await _admin_upload("icon", _encode(_logo().convert("RGB"), "JPEG"), "image/jpeg")
    assert len(puts) == 2
    assert [p["content_type"] for p in puts] == ["image/png", "image/jpeg"]


@pytest.mark.asyncio
async def test_a_webp_saved_as_logo_png_still_gets_a_twin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A logo saved from a website as `logo.png` is often a WebP. The browser declares it a PNG and
    the store keeps it as `.png` — so whether email needs a copy is judged by the BYTES."""
    puts = _capture_puts(monkeypatch)
    webp = _encode(_logo((900, 300)), "WEBP")
    result = await _admin_upload("logo", webp, "image/png")
    assert result == {"src": f"/_assets/logo-{_digest(webp)}.png"}
    assert puts[0]["data"] == webp  # stored as uploaded, as before
    assert puts[1]["key"] == f"tenants/7/logo-email-{_digest(webp)}.png"
    assert _format(puts[1]["data"]) == "PNG"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "declared"),
    [
        (_encode(_logo().convert("P"), "GIF"), "image/gif"),
        (_encode(_logo(), "AVIF"), "image/avif"),
        (_encode(_logo().convert("RGB"), "BMP"), "image/bmp"),
        (_encode(_logo(), "TIFF"), "image/tiff"),
        (HEIC_96x32, "image/heic"),
        # A browser that does not know the type sends none at all; the bytes decide.
        (HEIC_96x32, None),
        (_encode(_logo(), "TIFF"), "application/octet-stream"),
    ],
    ids=["gif", "avif", "bmp", "tiff", "heic", "heic-undeclared", "tiff-octet-stream"],
)
async def test_a_new_type_is_stored_as_a_png(
    monkeypatch: pytest.MonkeyPatch, data: bytes, declared: str | None
) -> None:
    """Stored as a PNG, so the stored types — and the five places that spell the asset grammar —
    never grow, and an iPhone photo's metadata never reaches the public prefix. A PNG travels in an
    email as it is, so there is no copy."""
    puts = _capture_puts(monkeypatch)
    result = await _admin_upload("logo", data, declared)
    assert len(puts) == 1
    assert puts[0]["content_type"] == "image/png" and _format(puts[0]["data"]) == "PNG"
    assert result["src"].startswith("/_assets/logo-") and result["src"].endswith(".png")
    assert storage.public_asset_name(result["src"])


@pytest.mark.asyncio
async def test_a_converted_upload_is_stored_display_sized(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import shell_images

    puts = _capture_puts(monkeypatch)
    await _admin_upload("logo", _encode(_logo((3000, 1000)), "TIFF"), "image/tiff")
    assert max(_size(puts[0]["data"])) == shell_images.MAX_SHELL_SIDE


@pytest.mark.asyncio
async def test_a_phone_photo_as_card_artwork_fits_the_asset_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full-size photograph is megabytes; stored as a JPEG at the best quality that fits."""
    w, h = 3000, 2000
    grad = Image.linear_gradient("L").resize((w, h)).convert("RGB")
    photo = Image.blend(grad, Image.effect_noise((w, h), 40).convert("RGB"), 0.35)
    tiff = _encode(photo, "TIFF", compression="tiff_lzw")
    assert len(tiff) > storage.MAX_ASSET_BYTES
    puts = _capture_puts(monkeypatch)
    result = await _admin_upload("card-art", tiff, "image/tiff")
    assert puts[0]["content_type"] == "image/jpeg"
    assert len(puts[0]["data"]) <= storage.MAX_ASSET_BYTES
    assert result["src"].endswith(".jpg")


@pytest.mark.asyncio
async def test_an_unknown_type_is_still_refused_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    puts = _capture_puts(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await _admin_upload("logo", b"%PDF-1.7 a document", "application/pdf")
    assert exc.value.status_code == 422
    assert "unsupported asset type" in str(exc.value.detail)
    assert puts == []


@pytest.mark.asyncio
async def test_a_corrupt_new_type_is_a_422(monkeypatch: pytest.MonkeyPatch) -> None:
    puts = _capture_puts(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await _admin_upload("logo", b"II*\x00 not really a tiff", "image/tiff")
    assert exc.value.status_code == 422
    assert puts == []


@pytest.mark.asyncio
async def test_a_type_the_store_keeps_is_untouched_by_any_of_this(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bytes of an SVG, a small PNG and an ICO land exactly as uploaded, as they did before."""
    puts = _capture_puts(monkeypatch)
    small_png = _encode(_logo((300, 100)), "PNG")
    ico = _encode(_logo((64, 64)), "ICO")
    for data, declared in ((SVG, "image/svg+xml"), (small_png, "image/png"), (ico, "image/x-icon")):
        await _admin_upload("favicon", data, declared)
    assert [p["data"] for p in puts] == [SVG, small_png, ico]
