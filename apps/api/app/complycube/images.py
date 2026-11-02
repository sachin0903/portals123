"""A client's identity images, read from ComplyCube when they are shown.

The AML console and the firm's compliance page fetch each image of a client's identity check
from ComplyCube when it is opened, and show Graphene's own encrypted copy, kept until the check is
decided (``app/idv/held.py``), for one ComplyCube cannot give (``decisions.identity_image``). The
bytes pass through the API to the viewer and are never written to storage or a log.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Literal, get_args

import httpx
from fastapi import Response

from app import storage
from app.complycube.client import ScreeningClient

#: One image of an identity check: a side of the document, or the live photo.
ImagePart = Literal["front", "back", "selfie"]
PARTS: tuple[ImagePart, ...] = get_args(ImagePart)

#: What an image read from ComplyCube may be served as, by its sniffed content type.
SERVED_TYPES = frozenset({"image/jpeg", "image/png", "application/pdf"})

#: Never cached: not by the browser, and not by anything between it and us.
NO_STORE = "private, no-store, max-age=0"


@dataclass(frozen=True)
class Image:
    data: bytes
    content_type: str


class ImageMissing(LookupError):
    """ComplyCube holds no such image: the check names none, or ComplyCube no longer has it."""


async def fetch(
    screening: ScreeningClient,
    part: ImagePart,
    *,
    document_id: str | None,
    live_photo_id: str | None,
) -> Image:
    """One image of an identity check, from ComplyCube.

    ``ImageMissing`` when the check names no such image, ComplyCube answers 404, or what it
    answers is not an image or PDF we serve. Any other provider failure raises as it is.
    """
    try:
        if part == "selfie":
            if not live_photo_id:
                raise ImageMissing(part)
            body = await screening.download_live_photo(live_photo_id)
        else:
            if not document_id:
                raise ImageMissing(part)
            body = await screening.download_document_image(document_id, side=part)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ImageMissing(part) from exc
        raise
    encoded = body.get("data") if isinstance(body, dict) else None
    if not isinstance(encoded, str) or not encoded:
        raise ImageMissing(part)
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageMissing(part) from exc
    try:
        # ComplyCube names a JPEG `image/jpg`; the bytes say what it is.
        content_type = storage.sniff_document_type(data[:4096])
    except ValueError as exc:
        raise ImageMissing(part) from exc
    if content_type not in SERVED_TYPES:
        raise ImageMissing(part)
    return Image(data=data, content_type=content_type)


def response(image: Image, part: ImagePart) -> Response:
    """The image as the viewer gets it: inline, never cached, never sniffed into another type."""
    ext = "pdf" if image.content_type == "application/pdf" else image.content_type.split("/")[1]
    return Response(
        content=image.data,
        media_type=image.content_type,
        headers={
            "Cache-Control": NO_STORE,
            "Content-Disposition": f'inline; filename="{part}.{ext}"',
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
