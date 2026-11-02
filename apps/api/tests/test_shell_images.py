"""The shell-image guards (app/shell_images.py): a logo is stored display-sized, and a large inline
raster never reaches the shell as its logo.

The reported fault: a 3481×624 PNG, stored as a base64 data URI, inlined into a 28px slot on every
page of platform.staging — decoding it was the Largest Contentful Paint two seconds after the page
had finished. Both ends are pinned here: the upload-time derivative and the read-time refusal.
"""

import base64
import math
import os
from io import BytesIO
from typing import Any

import pytest
from fastapi import HTTPException
from PIL import Image

from app import shell_images, storage
from app.routers import admin as admin_router
from app.routers import firm as firm_router


def _png(width: int, height: int, mode: str = "RGBA", *, noisy: bool = False) -> bytes:
    """A PNG of the given size. Solid colour by default (compresses to almost nothing, however
    large — the shape the pixel bound exists for); ``noisy`` fills it with random pixels so it
    carries real bytes, like a photographic logo."""
    out = BytesIO()
    if noisy:
        im = Image.frombytes(mode, (width, height), os.urandom(width * height * len(mode)))
    else:
        im = Image.new(mode, (width, height), (20, 40, 160, 255)[: len(mode)])
    im.save(out, "PNG")
    return out.getvalue()


def _jpeg(width: int, height: int) -> bytes:
    out = BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, "JPEG")
    return out.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(data)) as im:
        return im.size


def _fitted(width: int, height: int) -> tuple[int, int]:
    """The size ``fit_shell_image`` produces: long side at ``MAX_SHELL_SIDE``, aspect kept, the
    short side rounded the way Pillow's ``thumbnail`` does (``floor(n + 0.5)``)."""
    side = shell_images.MAX_SHELL_SIDE
    if max(width, height) <= side:
        return width, height
    if width >= height:
        return side, max(1, math.floor(height * side / width + 0.5))
    return max(1, math.floor(width * side / height + 0.5)), side


def _data_uri(data: bytes, content_type: str = "image/png") -> str:
    return f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"


# ── fit_shell_image: the upload-time derivative ────────────────────────────────


def test_a_wide_raster_logo_is_downscaled_to_the_shell_bound() -> None:
    """The staging shape — 3481×624 — comes out with its long side at MAX_SHELL_SIDE, its aspect
    kept, still a PNG under the same content type."""
    data, content_type = shell_images.fit_shell_image(_png(3481, 624), "image/png")
    assert content_type == "image/png"
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert _size(data) == _fitted(3481, 624)
    assert shell_images.MAX_SHELL_SIDE == 1024
    assert len(data) < len(_png(3481, 624))


def test_a_tall_raster_is_bounded_on_its_long_side() -> None:
    data, _ = shell_images.fit_shell_image(_png(300, 1200), "image/png")
    assert _size(data) == _fitted(300, 1200)


def test_a_raster_that_already_fits_is_stored_byte_for_byte() -> None:
    """No re-encode for an in-bounds image: the content hash — and so the stored key — is the
    uploader's bytes, and a 200px PNG is not made worse by a second pass through an encoder."""
    original = _png(200, 60)
    assert shell_images.fit_shell_image(original, "image/png") == (original, "image/png")


def test_jpeg_stays_jpeg_and_png_keeps_its_alpha() -> None:
    jpeg, jpeg_type = shell_images.fit_shell_image(_jpeg(1600, 400), "image/jpeg")
    assert jpeg_type == "image/jpeg"
    assert jpeg.startswith(b"\xff\xd8\xff")
    assert _size(jpeg) == _fitted(1600, 400)
    # 4:4:4: subsampled chroma is a soft edge on a logo's lettering, which is the blur this
    # derivative exists to avoid. Pillow reports it as the SOF component sampling factors.
    with Image.open(BytesIO(jpeg)) as im:
        # (component id, horizontal sample, vertical sample, quant table). 1,1 is 4:4:4.
        assert [(c[1], c[2]) for c in im.layer] == [(1, 1), (1, 1), (1, 1)]

    png, _ = shell_images.fit_shell_image(_png(1024, 1024), "image/png")
    with Image.open(BytesIO(png)) as im:
        assert im.mode == "RGBA"


def test_a_palette_png_is_resampled_in_true_colour() -> None:
    """Pillow resizes a palette image with NEAREST — stair-stepped edges at exactly the size they
    matter — so the derivative is converted first."""
    out = BytesIO()
    Image.new("P", (1800, 600)).save(out, "PNG")
    png, _ = shell_images.fit_shell_image(out.getvalue(), "image/png")
    with Image.open(BytesIO(png)) as im:
        assert im.mode == "RGBA"
        assert im.size == _fitted(1800, 600)


def test_svg_and_ico_are_not_rasterised() -> None:
    """An SVG is small and sharp at any size, and an ICO is a container of pre-sized frames —
    neither is touched, whatever its declared dimensions."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="4000" height="800"/>'
    assert shell_images.fit_shell_image(svg, "image/svg+xml") == (svg, "image/svg+xml")
    ico = b"\x00\x00\x01\x00\x01\x00 not really an icon"
    assert shell_images.fit_shell_image(ico, "image/x-icon") == (ico, "image/x-icon")
    assert shell_images.fit_shell_image(ico, "image/vnd.microsoft.icon") == (
        ico,
        "image/vnd.microsoft.icon",
    )


def test_bytes_that_are_not_the_declared_raster_are_refused() -> None:
    """A broken image would otherwise be stored as a broken image; 422 at upload is the one moment
    the firm can do something about it."""
    with pytest.raises(ValueError, match="could not be decoded"):
        shell_images.fit_shell_image(b"\x89PNG\r\n\x1a\nnot a png", "image/png")


def test_a_decompression_bomb_is_a_value_error_not_a_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under MAX_ASSET_BYTES a solid-colour PNG can still unpack to hundreds of megapixels; Pillow's
    bomb guard trips and the upload is refused like any other undecodable image. The guard's
    threshold is lowered so the test need not allocate a real bomb to prove the error maps."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1_000)
    with pytest.raises(ValueError, match="could not be decoded"):
        shell_images.fit_shell_image(_png(300, 300), "image/png")


@pytest.mark.asyncio
async def test_only_shell_kinds_are_fitted() -> None:
    """Card artwork and content pictures are full-bleed and keep their size; the four shell kinds
    — the set the firm/admin routers accept, minus `card-art` — are downscaled."""
    big = _png(1200, 400)
    assert shell_images.SHELL_KINDS == {"logo", "logo-dark", "icon", "favicon"}
    assert admin_router._ASSET_KINDS - shell_images.SHELL_KINDS == {"card-art"}
    assert firm_router._ASSET_KINDS == admin_router._ASSET_KINDS
    assert await shell_images.fit_shell_upload("card-art", big, "image/png") == (big, "image/png")
    for kind in sorted(shell_images.SHELL_KINDS):
        data, _ = await shell_images.fit_shell_upload(kind, big, "image/png")
        assert _size(data) == _fitted(1200, 400), kind


# ── The two upload handlers store the derivative ───────────────────────────────


class _Upload:
    def __init__(self, data: bytes, content_type: str) -> None:
        self._data = data
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._data


def _capture_puts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """`put_branding_asset` with the real key derivation (content hash, ext) and a captured put,
    so the test sees exactly what would reach the bucket and the exact `src` the handler returns."""
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

    monkeypatch.setattr(storage, "_put", fake_put)
    monkeypatch.setattr(storage.settings, "s3_bucket", "branding")
    monkeypatch.setattr(storage.settings, "s3_public_base_url", "")

    async def no_tenant_store(_session: Any, _tenant_id: int) -> None:
        return None

    monkeypatch.setattr(storage.tenant_storage, "get_tenant_store", no_tenant_store)
    return puts


@pytest.mark.asyncio
async def test_admin_upload_stores_a_display_sized_logo_at_a_proxy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raster wider than the shell bound lands with its long side at it, and the `src` the
    editor is handed is a `/_assets/` path — never a data URI."""
    puts = _capture_puts(monkeypatch)
    result = await admin_router._store_branding_asset(
        None,  # type: ignore[arg-type]
        7,
        "logo",
        _Upload(_png(3481, 624), "image/png"),  # type: ignore[arg-type]
    )
    assert len(puts) == 1
    assert puts[0]["content_type"] == "image/png"
    assert max(_size(puts[0]["data"])) <= shell_images.MAX_SHELL_SIDE
    assert puts[0]["key"].startswith("tenants/7/logo-") and puts[0]["key"].endswith(".png")
    assert result["src"].startswith("/_assets/logo-")
    assert not result["src"].startswith("data:")
    assert storage.public_asset_name(result["src"]) == puts[0]["key"].rsplit("/", 1)[-1]


@pytest.mark.asyncio
async def test_firm_upload_stores_a_display_sized_logo_at_a_proxy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.routers.firm import upload_branding_asset

    monkeypatch.setattr(firm_router, "require_permission", lambda *_a, **_k: None)
    puts = _capture_puts(monkeypatch)
    ctx = SimpleNamespace(principal=None, session=None, tenant_id=9)
    result = await upload_branding_asset(
        "logo-dark",
        _Upload(_jpeg(2400, 600), "image/jpeg"),  # type: ignore[arg-type]
        ctx,  # type: ignore[arg-type]
    )
    assert len(puts) == 1
    assert puts[0]["content_type"] == "image/jpeg"
    assert _size(puts[0]["data"]) == _fitted(2400, 600)
    assert puts[0]["key"].startswith("tenants/9/logo-dark-") and puts[0]["key"].endswith(".jpg")
    assert result["src"].startswith("/_assets/logo-dark-")


@pytest.mark.asyncio
async def test_svg_upload_is_stored_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    puts = _capture_puts(monkeypatch)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="4000" height="800"><rect/></svg>'
    result = await admin_router._store_branding_asset(
        None,  # type: ignore[arg-type]
        7,
        "logo",
        _Upload(svg, "image/svg+xml"),  # type: ignore[arg-type]
    )
    assert puts[0]["data"] == svg
    assert puts[0]["content_type"] == "image/svg+xml"
    assert puts[0]["key"].endswith(".svg")
    assert result["src"].endswith(".svg")


@pytest.mark.asyncio
async def test_an_undecodable_upload_is_a_422(monkeypatch: pytest.MonkeyPatch) -> None:
    puts = _capture_puts(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await admin_router._store_branding_asset(
            None,  # type: ignore[arg-type]
            7,
            "logo",
            _Upload(b"\x89PNG\r\n\x1a\nnot a png", "image/png"),  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 422
    assert "could not be decoded" in str(exc.value.detail)
    assert puts == []


# ── shell_safe_src: the read-time refusal ──────────────────────────────────────


def test_a_large_inline_raster_is_withheld_from_the_shell() -> None:
    """The stored staging value: a multi-megapixel PNG as a data URI. It is NOT the URL the shell
    inlines — the payload carries None and the shell falls back to the built-in mark."""
    big = _png(3481, 624, noisy=True)
    assert len(big) > shell_images.MAX_INLINE_SHELL_BYTES
    assert shell_images.shell_safe_src(_data_uri(big)) is None


def test_the_byte_bound_is_judged_without_decoding() -> None:
    """A megabyte of base64 that is not even an image is refused on its length alone."""
    junk = "data:image/png;base64," + "A" * (shell_images.MAX_INLINE_SHELL_BYTES * 4 // 3 + 8)
    assert shell_images.shell_safe_src(junk) is None


def test_a_well_compressed_but_huge_bitmap_is_withheld_too() -> None:
    """A solid-colour 2000px PNG is a few KB on the wire and eight megapixels to paint; the pixel
    bound catches what the byte bound cannot."""
    flat = _png(2000, 400, mode="L")
    assert len(flat) < shell_images.MAX_INLINE_SHELL_BYTES
    assert shell_images.shell_safe_src(_data_uri(flat)) is None


def test_small_inline_images_urls_and_proxy_paths_pass() -> None:
    small = _data_uri(_png(64, 64))
    assert shell_images.shell_safe_src(small) == small
    svg = "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>" * 20_000).decode()
    assert shell_images.shell_safe_src(svg) == svg  # SVG is not a bitmap to decode
    for passthrough in (
        "/_assets/logo-0123456789abcdef.png",
        "https://cdn.example/logo.png",
    ):
        assert shell_images.shell_safe_src(passthrough) == passthrough
    assert shell_images.shell_safe_src(None) is None
    assert shell_images.shell_safe_src("") is None


def test_an_inline_raster_that_does_not_decode_is_withheld() -> None:
    assert shell_images.shell_safe_src("data:image/png;base64,AAAA") is None


def test_upload_derivatives_pass_the_read_guard() -> None:
    """The two bounds agree: what the uploader now stores as a data URI (the no-storage rung) is
    what the shell will paint — otherwise a firm with no bucket would upload a logo and see the
    built-in mark."""
    for source in (_png(3481, 624), _jpeg(2400, 600)):
        fitted, content_type = shell_images.fit_shell_image(
            source, "image/png" if source.startswith(b"\x89PNG") else "image/jpeg"
        )
        uri = _data_uri(fitted, content_type)
        assert shell_images.shell_safe_src(uri) == uri
