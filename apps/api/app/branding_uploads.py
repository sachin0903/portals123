"""A branding upload — the one path both upload routes share (``routers/firm.py``,
``routers/admin.py``).

Storing the file is unchanged: a type the store already keeps (SVG, PNG, JPEG, WebP, ICO) goes
through ``shell_images.fit_shell_upload`` and ``storage.put_branding_asset`` exactly as before. Two
things are added around it:

* **More types.** GIF, AVIF, BMP, TIFF and HEIC/HEIF — refused before — are accepted and stored as
  a PNG, or a JPEG for an opaque photograph (``imaging.to_web``; its module docstring says why they
  are not kept as themselves).
* **An email copy.** A LOGO or ICON stored as SVG, WebP or ICO gets a PNG twin beside it
  (``storage.put_email_copy``), because a mail client shows those types as a file attachment
  instead of a picture. WebP and ICO are converted here, and so is an SVG (CairoSVG) — unless the
  branding studio sent the PNG it drew of the SVG in the browser as ``email_copy``, which is
  preferred. Best effort: a copy that cannot be made never fails the upload — the send path makes
  it then, or falls back to the icon or the firm's name (``email/transport.py::email_assets``).
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app import imaging, shell_images, storage

logger = logging.getLogger(__name__)

#: The kinds an email reads (``email/transport.py::_email_asset_urls``). Not the dark logo (mail has
#: no colour-scheme swap), the favicon or card artwork.
EMAIL_COPY_KINDS = frozenset({"logo", "icon"})

#: The box a converted upload is fitted inside. A shell image is stored display-sized anyway, so it
#: goes straight to that bound; card artwork is a full-bleed picture and keeps more.
_SHELL_BOX = (shell_images.MAX_SHELL_SIDE, shell_images.MAX_SHELL_SIDE)
_OTHER_BOX = (2048, 2048)

#: What an ``email_copy`` part may weigh. It is a PNG of a logo the browser drew at email size —
#: a few KB — so anything near this is not one, and is ignored rather than read.
MAX_EMAIL_COPY_BYTES = 2 * 1024 * 1024


async def read_email_copy(file: storage.Readable | None) -> bytes | None:
    """The bytes of an upload's optional ``email_copy`` part, or None when there is none or it
    is over ``MAX_EMAIL_COPY_BYTES``. Never raises: a bad copy must not fail its upload."""
    if file is None:
        return None
    try:
        return await storage.read_capped(file, MAX_EMAIL_COPY_BYTES) or None
    except storage.PayloadTooLarge:
        logger.info("email copy over %d bytes ignored", MAX_EMAIL_COPY_BYTES)
        return None


async def store(
    session: AsyncSession,
    tenant_id: int,
    kind: str,
    data: bytes,
    declared: str,
    *,
    email_copy: bytes | None = None,
) -> str:
    """Store one branding upload and return its ``src``. Raises what ``put_branding_asset`` raises
    (``ValueError`` for a bad type or size, ``StorageUnavailable``), and ``ValueError`` for a
    converted type whose bytes cannot be read."""
    content_type = declared
    if not storage.is_branding_type(declared) and imaging.sniff_convertible(data):
        box = _SHELL_BOX if kind in shell_images.SHELL_KINDS else _OTHER_BOX
        data, content_type = await asyncio.to_thread(
            imaging.to_web, data, box, max_bytes=storage.MAX_ASSET_BYTES
        )
    data, content_type = await shell_images.fit_shell_upload(kind, data, content_type)
    src = await storage.put_branding_asset(session, tenant_id, kind, data, content_type, proxy=True)
    if kind in EMAIL_COPY_KINDS:
        await _store_email_copy(session, tenant_id, src, data, email_copy)
    return src


async def _store_email_copy(
    session: AsyncSession, tenant_id: int, src: str, stored: bytes, svg_copy: bytes | None
) -> None:
    """Write the email copy of the asset just stored at ``src``, when it needs one. Never raises."""
    name = storage.public_asset_name(src)
    if name is None:
        # A `data:` value (no object storage) — email refuses those anyway.
        return
    if name.endswith(".svg"):
        # The studio's drawing when it sent one (a browser draws SVG best); else Cairo's.
        studio = svg_copy if svg_copy and svg_copy.startswith(imaging.PNG_SIGNATURE) else None
        source = studio or stored
    elif imaging.mailable_type(stored) is not None:
        # A real PNG or JPEG travels as it is. Judged by the BYTES: a WebP named `logo.png` is
        # stored as `.png`, and needs a copy like any WebP.
        return
    else:
        source = stored
    png = await asyncio.to_thread(imaging.email_png, source)
    if png is None:
        return
    try:
        await storage.put_email_copy(session, tenant_id, name, png)
    except Exception:
        # The logo is stored; only its email twin is missing, which the send path survives.
        logger.warning("email copy of %s could not be stored", name, exc_info=True)
