"""Firm mark for a GIA or ISA illustration, frozen onto the display at filing.

The PDF is printed later, in the worker, from stored outputs. The colour and logo are copied
here so a reprint matches the firm as it was when the illustration was issued. Logos are
embedded: the worker's URL fetcher will not load a same-origin ``/_assets/`` path.
"""

from __future__ import annotations

import base64
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.branding import _COLOUR, _DATA_URI, _URL, Branding, parse_lenient
from app.storage import get_public_asset, public_asset_name, public_asset_src

logger = logging.getLogger(__name__)

#: Design-system defaults from the light theme, used when the firm has set no colour.
#: ``DEFAULT_INK`` is ``--primary``; ``DEFAULT_ACCENT`` is ``--brand``.
DEFAULT_INK = "oklch(0.26 0.055 256)"
DEFAULT_ACCENT = "oklch(0.64 0.115 202)"

_IMAGE_TYPES = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/webp": "image/webp",
    "image/svg+xml": "image/svg+xml",
    "image/x-icon": "image/x-icon",
    "image/vnd.microsoft.icon": "image/vnd.microsoft.icon",
}
_MAX_LOGO_BYTES = 512 * 1024


def safe_brand_color(value: str | None, *, fallback: str = DEFAULT_INK) -> str:
    """A CSS colour that cannot break out of a ``style`` attribute, or ``fallback``."""
    if value and _COLOUR.match(value):
        return value
    return fallback


def _token(branding: Branding, name: str) -> str | None:
    value = getattr(branding.colors.light, name)
    return value if isinstance(value, str) else None


def _logo_ref(branding: Branding) -> str | None:
    return branding.logo_url or branding.icon_url


async def brand_for_illustration(session: AsyncSession, tenant_id: int) -> dict[str, str | None]:
    """Client-portal colour and logo, falling back to the platform portal, then plain ink."""
    theme = (
        await session.execute(
            text("SELECT theme FROM auth.tenants WHERE id = :t"),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    client = parse_lenient(theme, "client")
    platform = parse_lenient(theme, "platform")
    primary = _token(client, "primary") or _token(platform, "primary")
    accent = _token(client, "brand") or _token(platform, "brand") or primary
    return {
        "ink_color": safe_brand_color(primary, fallback=DEFAULT_INK),
        "brand_color": safe_brand_color(accent, fallback=DEFAULT_ACCENT),
        "logo": await _embed_logo(session, tenant_id, _logo_ref(client) or _logo_ref(platform)),
    }


async def _embed_logo(session: AsyncSession, tenant_id: int, logo_url: str | None) -> str | None:
    if not logo_url:
        return None
    if _DATA_URI.match(logo_url):
        return logo_url
    if _URL.match(logo_url):
        return logo_url
    name = public_asset_name(public_asset_src(logo_url))
    if name is None:
        return None
    try:
        data, content_type = await get_public_asset(session, tenant_id, name)
    except Exception:
        logger.warning("illustration logo could not be embedded", exc_info=True)
        return None
    if not data or len(data) > _MAX_LOGO_BYTES:
        return None
    media = _IMAGE_TYPES.get(content_type.split(";", 1)[0].strip().lower())
    if media is None:
        return None
    uri = f"data:{media};base64,{base64.b64encode(data).decode()}"
    return uri if _DATA_URI.match(uri) else None
