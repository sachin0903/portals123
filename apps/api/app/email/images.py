"""Inline an email's OWN images at send, so an author's uploaded picture actually arrives.

THE PROBLEM. A firm uploads a logo through the editor; it lands in that firm's object-storage bucket
and the template carries its URL. Whether the recipient ever sees it then depends on their mail
client being able to reach that URL — which needs the bucket published on a real https origin. Until
that exists the picture is stored, shown in the canvas, previewed correctly, and arrives as a broken
image in the inbox. "Upload an image" is not a feature you can half-ship.

THE PATTERN, taken from document templates. A document render resolves its own resources
server-side: `render/renderers.py::_safe_url_fetcher` fetches the image and WeasyPrint bakes it into
the PDF, with `data:` URIs passing "straight through (the common case — logos, fonts)". The reason
that works is that the SERVER produces the final artefact. An email's final artefact is produced by
the RECIPIENT's client, so the same principle needs the one mail-native equivalent: send the bytes
WITH the message and reference them by content id (`<img src="cid:…">`). The server resolves the
image; the recipient needs no access to our storage at all.

WHY THIS IS NOT AN SSRF HOLE, which is the obvious objection. Template HTML is author-supplied, so a
general "fetch whatever the src says" would be exactly the hole `_safe_url_fetcher` was written to
close. This fetches nothing of the sort: a src is inlined ONLY when its origin is one of OUR OWN
configured storage origins (`allowed_origins`). Anything else — any other host, any other scheme,
`file:`, a redirect target — is left exactly as authored and travels as an ordinary remote
image. The allowlist is the control, so there is no address-classification logic to get wrong,
and it holds in dev where the bucket IS on loopback — the document fetcher must refuse loopback;
here it is the one address we are certain about.

BOUNDED, because inlining grows the message: at most `MAX_INLINE_IMAGES` pictures and
`MAX_INLINE_TOTAL_BYTES` of them per email. Past either, the remaining images stay as URLs rather
than being dropped — a remote image that might load beats no image at all. Resend states inline
images are unsupported on its BATCH endpoint; every send here goes through `/emails` (one message
per recipient), so that limit does not apply to us.

TWO KINDS OF OUR OWN IMAGE, resolved differently. A legacy absolute object-store URL is FETCHED
over http from an allowlisted origin (above). An asset on the firm's own relay —
`/_assets/{name}`, which is how every logo, icon and library image is stored now — is not fetched
at all: `relay_asset_names` reads the names out of the markup and the caller resolves them
straight from object storage (`storage.get_public_asset`, the same read the portal's relay does),
handing the bytes back through `own_assets`.

That is deliberate, and better than fetching the portal:

  * no HTTP request to an author-influenced URL, so the SSRF question does not arise for it at all;
  * the API needs no route to the public portal host — which it may not have in production, and
    definitely does not have over https on a dev box, where the masthead otherwise arrives as a
    broken image;
  * and the recipient needs no route to the firm's host either, which is the whole point of a
    content id: a remote image is blocked by default in most clients until the reader allows it.

This module still opens no database connection and holds no credential — it takes bytes, not a
session. The read stays with the caller (`transport.send_email`), which is the one place that knows
the tenant.

ONLY A PICTURE A MAIL CLIENT DRAWS INLINE IS ATTACHED — PNG, JPEG or GIF, with its content type
stated. Anything else (SVG, WebP, ICO) is shown by Outlook as a FILE ATTACHMENT above the message
rather than as the picture, and an SVG is broken in the body too. A firm's own SVG/WebP/ICO logo
reaches this module already swapped for its PNG email copy (`transport.email_assets`); anything
else of those types keeps its URL.
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app import imaging, storage
from app.config import settings

logger = logging.getLogger("email.images")

#: Per email. Small on purpose: an email carries a masthead and perhaps a figure, and every inlined
#: byte is paid for by the recipient's download and by every spam filter that weighs message size.
MAX_INLINE_IMAGES = 4
#: 2 MiB of images per message, comfortably above one logo and well under any provider limit.
MAX_INLINE_TOTAL_BYTES = 2 * 1024 * 1024
#: One image. The editor's own upload cap is 512 KiB; this allows for an image that predates it.
MAX_INLINE_ONE_BYTES = 1024 * 1024

#: `src` on an `<img>`, single or double quoted. Deliberately not an HTML parser: this runs on every
#: send, the markup is our own compiled output, and a parser round-trip would rewrite it.
_IMG_SRC = re.compile(r"(<img\b[^>]*?\bsrc\s*=\s*)(\"([^\"]*)\"|'([^']*)')", re.IGNORECASE)

#: Content ids are ours, so they need no entropy — only to be unique within one message and legal in
#: a header. Resend requires under 128 characters.
_CID_PREFIX = "img"


@dataclass(frozen=True)
class InlineAttachment:
    """One image travelling with the message. Field names are Resend's own (snake_case)."""

    filename: str
    content: str  # base64, no data: prefix
    content_id: str
    #: Always one of `imaging.MAILABLE_TYPES`. Stated rather than left to Resend to guess from the
    #: file name, which its own docs recommend so the client renders the part as a picture.
    content_type: str

    def payload(self) -> dict[str, str]:
        return {
            "filename": self.filename,
            "content": self.content,
            "content_id": self.content_id,
            "content_type": self.content_type,
        }


def allowed_origins() -> tuple[str, ...]:
    """The origins whose images we will fetch and inline — OUR OWN object storage, and nothing else.

    Both are read: `s3_endpoint_url` is where every per-tenant bucket lives (path-style, so one
    origin covers them all — `tenant_storage._public_base_url`), and `s3_public_base_url` is the
    legacy shared branding bucket some deployments still serve branding from.
    """
    origins = []
    for raw in (settings.s3_endpoint_url, settings.s3_public_base_url):
        origin = _origin_of(raw)
        if origin and origin not in origins:
            origins.append(origin)
    return tuple(origins)


def _origin_of(url: str | None) -> str | None:
    """`scheme://host[:port]`, or None for anything that is not an absolute http(s) URL."""
    parts = urlsplit((url or "").strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme.lower()}://{parts.netloc}"


def _is_ours(src: str, origins: tuple[str, ...]) -> bool:
    origin = _origin_of(src)
    return origin is not None and origin in origins


def relay_asset_name(src: str) -> str | None:
    """The opaque asset name a relay src carries, or None.

    Matched on the PATH, so both shapes resolve: the `/_assets/{name}` an editor stores and the
    absolute `https://firm-host/_assets/{name}` the send path makes of it. The host is deliberately
    not checked — the name is resolved against THIS tenant's own bucket by the caller, so a src
    naming another host cannot reach another firm's asset; it simply resolves this firm's, or
    nothing.

    The grammar is `storage.public_asset_name`'s, which is the relay's own: a name it would refuse
    to serve is left alone rather than dressed up as an attachment.
    """
    path = urlsplit(src.strip()).path
    return storage.public_asset_name(path)


def relay_asset_names(html: str) -> list[str]:
    """Every distinct relay asset named by an `<img src>` in ``html``, in first-seen order.

    Pure, and separate from the inlining, because the caller has to resolve these from object
    storage BEFORE the inliner runs — this module never opens a connection.
    """
    if "<img" not in html.lower():
        return []
    names: list[str] = []
    for match in _IMG_SRC.finditer(html):
        src = (match.group(3) if match.group(3) is not None else match.group(4)) or ""
        name = relay_asset_name(src)
        if name and name not in names:
            names.append(name)
    return names


_EXT_FOR_TYPE = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif"}


def _filename_of(src: str, content_type: str) -> str:
    """The object's own name, for the attachment, with the extension of the bytes that travel — an
    SVG logo's PNG copy goes as `logo-….png`, never as `….svg`. Falls back to something legal rather
    than empty — a nameless attachment is what some clients render as an unnamed file rather than
    an image."""
    tail = urlsplit(src).path.rsplit("/", 1)[-1].strip()
    stem = tail.rsplit(".", 1)[0] if "." in tail else tail
    return f"{stem or 'image'}.{_EXT_FOR_TYPE.get(content_type, 'png')}"


async def inline_own_images(
    html: str,
    *,
    client: httpx.AsyncClient | None = None,
    own_assets: Mapping[str, tuple[bytes, str]] | None = None,
) -> tuple[str, list[InlineAttachment]]:
    """``html`` with our own images rewritten to ``cid:`` references, plus those attachments.

    Never raises and never blocks a send: an image that cannot be fetched, is too large, or pushes
    the message past the budget is LEFT AS ITS URL. The worst outcome is the behaviour we had before
    this existed, which is the right floor for something on the send path.
    """
    origins = allowed_origins()
    assets = own_assets or {}
    # Nothing to do only when BOTH kinds are absent: a deployment with no object-store origin
    # configured still has relay assets, which is the common case now.
    if (not origins and not assets) or "<img" not in html.lower():
        return html, []

    attachments: list[InlineAttachment] = []
    total = 0
    # src → cid, so one image used twice is fetched once and travels once.
    seen: dict[str, str] = {}
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=10.0)
    try:
        # Collected first: the replacement is a second pass, so the regex is never re-run over
        # markup this function has already rewritten.
        candidates = []
        for match in _IMG_SRC.finditer(html):
            src = (match.group(3) if match.group(3) is not None else match.group(4)) or ""
            src = src.strip()
            if src in candidates:
                continue
            # A relay asset the caller resolved, or an image on one of our own object-store
            # origins. Anything else is somebody else's picture and travels as it was authored.
            name = relay_asset_name(src)
            if (name and name in assets) or _is_ours(src, origins):
                candidates.append(src)

        # src -> cid is what the swap needs; ASSET -> cid is what stops one picture travelling
        # twice. The two differ whenever a relay asset is referenced in both of its shapes — the
        # stored `/_assets/{name}` and the absolute URL the send path makes of it — which is one
        # image and was two attachments of identical bytes.
        cid_by_asset: dict[str, str] = {}
        for src in candidates:
            name = relay_asset_name(src)
            already = cid_by_asset.get(name) if name else None
            if already:
                seen[src] = already
                continue
            if len(attachments) >= MAX_INLINE_IMAGES:
                logger.info("email image inlining stopped at %d images", MAX_INLINE_IMAGES)
                break
            resolved = assets.get(name) if name else None
            # No fetch for a relay asset: the caller read it from storage already.
            data = resolved[0] if resolved is not None else await _fetch(http, src)
            if data is None:
                continue
            # The BYTES decide, never a stored or declared type: a WebP kept under a `.png` name
            # would otherwise travel labelled as a PNG.
            content_type = imaging.mailable_type(data)
            if content_type is None:
                logger.info("email image left as a URL — not a type a mail client draws inline")
                continue
            if len(data) > MAX_INLINE_ONE_BYTES or total + len(data) > MAX_INLINE_TOTAL_BYTES:
                logger.info(
                    "email image left as a URL — %d bytes would exceed the inline budget", len(data)
                )
                continue
            cid = f"{_CID_PREFIX}{len(attachments) + 1}"
            seen[src] = cid
            if name:
                cid_by_asset[name] = cid
            total += len(data)
            attachments.append(
                InlineAttachment(
                    filename=_filename_of(src, content_type),
                    content=base64.b64encode(data).decode("ascii"),
                    content_id=cid,
                    content_type=content_type,
                )
            )
    finally:
        if owns_client:
            await http.aclose()

    if not seen:
        return html, []

    def swap(match: re.Match[str]) -> str:
        raw = (match.group(3) if match.group(3) is not None else match.group(4)) or ""
        cid = seen.get(raw.strip())
        return f'{match.group(1)}"cid:{cid}"' if cid else match.group(0)

    return _IMG_SRC.sub(swap, html), attachments


async def _fetch(client: httpx.AsyncClient, src: str) -> bytes | None:
    """The image's bytes, or None. Redirects are NOT followed: the origin allowlist is the whole
    control, and a redirect is how a caller would leave it."""
    try:
        resp = await client.get(src, follow_redirects=False)
        if resp.status_code != 200:
            logger.info(
                "email image %s answered %s — left as a URL", _redact(src), resp.status_code
            )
            return None
        if not resp.headers.get("content-type", "").lower().startswith("image/"):
            logger.info("email image %s is not served as an image — left as a URL", _redact(src))
            return None
        return resp.content
    except Exception:
        logger.info("email image %s could not be fetched — left as a URL", _redact(src))
        return None


def _redact(src: str) -> str:
    """The object's path, never the query — a signed URL would carry its credential there."""
    parts = urlsplit(src)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"
