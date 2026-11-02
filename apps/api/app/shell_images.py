"""Shell images — the firm logo, icon and favicon the portal chrome paints on EVERY page.

A branding upload used to be stored at whatever size the firm chose, and the shell rendered it
in a 28px slot. On staging that was a 3481×624 PNG, inlined as a base64 ``data:`` URI (the
no-object-storage rung of ``storage.put_branding_asset``), and decoding that bitmap on the main
thread was the Largest Contentful Paint on 29 of 30 pages — two seconds after the page had
otherwise finished. The slot never needed more than a couple of hundred pixels.

Two guards, one at each end of the pipe:

* **Write.** ``fit_shell_upload`` downscales a raster logo so its long side is at most
  ``MAX_SHELL_SIDE`` before it is stored. The lockup is 36px tall (sidebar header, login splash),
  but a wordmark's long side is its width — about 180px of CSS for a wide logo, so a 2× display
  needs ~360 device pixels and a 3× display ~540. 256px sat under that painted width and the
  browser enlarged it, which is why the sidebar and splash looked soft. 1024px covers 3× of the
  lockup and is still a small cached asset. ``Branding`` has one ``logo_url``, so the stored
  asset IS the display-sized one; nothing keeps a multi-megapixel original because nothing reads
  one. SVG stays SVG (small and sharp at any size) and ICO is left alone (a container of
  pre-sized frames). Only the shell kinds are touched: content hero images and card artwork are
  full-bleed pictures with their own sizing.
* **Read.** ``shell_safe_src`` refuses to hand the shell an inline raster that is big: a
  ``data:`` URI cannot be made into a cached URL on read without writing an object, so the
  payload the portals receive carries ``None`` instead and the shell falls back to the built-in
  mark. Small inline images (icons, tests, a few KB) still pass. The same rule is applied again in
  ``pkgs/ui`` (``logo-sources.ts::shellSafeSrc``) so a stale or cached payload cannot reintroduce
  the bitmap.

Pillow is already in the environment — WeasyPrint depends on it — and is declared explicitly in
``pyproject.toml`` because this module imports it directly (the ``pytz`` lesson there).
"""

from __future__ import annotations

import asyncio
import base64
import re
from io import BytesIO

#: Longest side a stored raster logo/icon/favicon may have. The lockup is 36px of CSS (``h-9`` in
#: the sidebar header and on the login splash); the long side of a wordmark is its width, and a
#: 3× display of that width is about 540px. 1024px covers it. Past this the bytes buy nothing the
#: shell can show, and a multi-megapixel original is what made the logo the page's LCP.
MAX_SHELL_SIDE = 1024

#: A ``data:`` raster bigger than this (decoded) is withheld from the shell on read — decoding it
#: on the main thread becomes Largest Contentful Paint.
MAX_INLINE_SHELL_BYTES = 32 * 1024
#: ...as is one with a long side past this, however well it compresses: the cost is the bitmap the
#: browser has to allocate and paint, not the bytes on the wire.
#: Kept equal to ``MAX_SHELL_SIDE``. The no-storage rung stores the fitted derivative as a ``data:``
#: URI, and a derivative the shell then withholds is a firm uploading a logo and seeing the built-in
#: mark. A 1024px-wide wordmark is a fraction of a megapixel to paint; the original fault was a
#: 3481×624 bitmap.
MAX_INLINE_SHELL_SIDE = 1024

#: The branding asset kinds that are shell images (``_ASSET_KINDS`` in the firm/admin routers,
#: minus ``card-art``, which is a full-bleed picture with its own sizing).
SHELL_KINDS = frozenset({"logo", "logo-dark", "icon", "favicon"})

# Raster types the uploaders accept, mapped to Pillow's format names. SVG and ICO are deliberately
# absent: an SVG is rendered by the browser at the slot's size and an ICO is a container of frames.
_RASTER_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}

_INLINE_RASTER = re.compile(r"^data:image/(?:png|jpeg|webp);base64,(?P<b64>[A-Za-z0-9+/=]*)$")


def fit_shell_image(data: bytes, content_type: str) -> tuple[bytes, str]:
    """``(bytes, content_type)`` of a raster image downscaled so its long side is at most
    ``MAX_SHELL_SIDE``; the input untouched when it already fits or is not a raster (SVG, ICO).

    The format is preserved — a PNG stays a PNG (alpha and all), a JPEG a JPEG — so the content
    type the uploader declared keeps describing the stored bytes. EXIF orientation is applied
    before resizing, because a phone photo of a logo arrives rotated otherwise.

    Raises ``ValueError`` when the bytes do not decode as the declared type: a broken image would
    be stored as a broken image, and a 422 at upload is the one moment the firm can act on it.
    """
    fmt = _RASTER_FORMATS.get(content_type)
    if fmt is None:
        return data, content_type
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(BytesIO(data)) as im:
            # `Image.open` reads only the header, so an in-bounds image costs no decode at all.
            if max(im.size) <= MAX_SHELL_SIDE:
                return data, content_type
            upright = ImageOps.exif_transpose(im) or im
            # Resize in a true-colour mode: Pillow resamples palette ("P") and 1-bit images with
            # NEAREST, which turns a logo's edges into stair-steps at exactly the size they matter.
            converted = upright.convert("RGB" if fmt == "JPEG" else "RGBA")
            converted.thumbnail((MAX_SHELL_SIDE, MAX_SHELL_SIDE), Image.Resampling.LANCZOS)
            out = BytesIO()
            if fmt == "JPEG":
                # quality >= 95 is what makes Pillow keep 4:4:4 chroma; below that it subsamples,
                # and subsampled chroma is a soft edge on a logo's lettering. subsampling=0 states
                # it outright so a later quality tweak cannot bring the blur back.
                converted.save(out, "JPEG", quality=95, subsampling=0, optimize=True)
            elif fmt == "WEBP":
                converted.save(out, "WEBP", quality=95, method=6)
            else:
                converted.save(out, "PNG", optimize=True)
            return out.getvalue(), content_type
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise ValueError(f"image could not be decoded as {content_type}") from exc


async def fit_shell_upload(kind: str, data: bytes, content_type: str) -> tuple[bytes, str]:
    """``fit_shell_image`` for an upload of ``kind``, off the event loop; a non-shell kind
    (card artwork, content pictures) passes straight through."""
    if kind not in SHELL_KINDS:
        return data, content_type
    return await asyncio.to_thread(fit_shell_image, data, content_type)


def shell_safe_src(value: str | None) -> str | None:
    """The image source the SHELL may paint, or ``None`` for an inline raster that is too big to
    be one. A data URI in the staff shell is decoded on the main thread and becomes Largest
    Contentful Paint, so a ``data:image/(png|jpeg|webp)`` whose decoded bytes exceed
    ``MAX_INLINE_SHELL_BYTES`` — or whose long side exceeds ``MAX_INLINE_SHELL_SIDE`` — is
    withheld and the shell falls back to the built-in mark. URLs, proxy paths, SVG and small inline
    images pass unchanged.

    The byte test is done on the base64 length (4 characters per 3 bytes) so a megabyte of text
    is never decoded just to be refused; only an in-bounds image is opened, and ``Image.open``
    reads the header alone."""
    if not value:
        return None
    m = _INLINE_RASTER.match(value)
    if m is None:
        return value
    b64 = m.group("b64")
    if (len(b64) * 3) // 4 > MAX_INLINE_SHELL_BYTES:
        return None
    from PIL import Image

    try:
        with Image.open(BytesIO(base64.b64decode(b64, validate=True))) as im:
            width, height = im.size
    except Exception:  # whatever is wrong with it, it is not a picture the shell can paint
        return None
    return None if max(width, height) > MAX_INLINE_SHELL_SIDE else value
