"""Open-ended document-type tag helpers (shared).

`documents.document_type` is free text; the per-tenant registry `documents.document_types` (0037) is
the set of KNOWN tags — the picker suggestions and the pool auto-classify draws from. It is not an
allowlist: any new tag used on upload / reclassify / generate / template-create is normalised to a
slug and registered here (auto-registering, open-ended). Lives in its own module so both the
documents and templates routers can register types without a circular import.
"""

import re

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def slugify_tag(raw: str) -> str:
    """Normalise a free-text tag to a stable slug: lowercase, runs of non-alphanumerics → a single
    underscore, trimmed, capped at 64 chars (matching the document_types.slug CHECK). Case-folding
    dedupes 'Report'/'report' to one tag. Empty result ⇒ '' (the caller rejects it)."""
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")[:64].strip("_")


def labelize(slug: str) -> str:
    """A human label for a bare slug ('suitability_report' → 'Suitability Report')."""
    return " ".join(w.capitalize() for w in slug.split("_")) or slug


async def register_document_type(
    session: AsyncSession, raw: str, *, label: str | None = None, tenant_id: int | None = None
) -> str:
    """Types are open-ended: normalise ``raw`` to a slug and ensure it exists in the tenant's tag
    registry (documents.document_types) so it becomes a reusable suggestion, then return the slug.
    Idempotent (ON CONFLICT DO NOTHING) — an existing/archived tag keeps its stored label + state.
    Rejects a blank type (required), the reserved ``auto`` sentinel (resolve to a concrete tag
    client-side first), and ``other`` (a document is never bucketed as 'other' — the adviser must
    pick a specific type).

    ``tenant_id`` is for callers with no ambient identity: the request path leaves it None and
    ``auth.tenant_id()`` answers for the signed-in staffer, while the engine (which runs as
    ``service_role``, where that function returns NULL and the INSERT would violate NOT NULL) says
    which firm it is acting for."""
    if raw.strip().lower() == "auto":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Confirm the auto-classified type before saving.",
        )
    slug = slugify_tag(raw)
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A document type is required.",
        )
    if slug == "other":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Choose or create a specific document type instead of ‘Other’.",
        )
    # A slug-shaped input with no explicit label gets a prettified label; a typed label is kept.
    display = (label if label is not None else raw).strip()
    if label is None and re.fullmatch(r"[a-z0-9_]+", raw.strip()):
        display = labelize(slug)
    await session.execute(
        text(
            # Auto-registration is for DOCUMENT types (files are managed via the types page); the
            # ON CONFLICT target must name the (tenant, kind, slug) key added in 0040.
            "INSERT INTO documents.document_types (tenant_id, kind, slug, label, created_by) "
            "VALUES (COALESCE(CAST(:tenant AS int), auth.tenant_id()), 'document', :slug, :label, "
            "  auth.uid()) "
            "ON CONFLICT (tenant_id, kind, slug) DO NOTHING"
        ),
        {"tenant": tenant_id, "slug": slug, "label": display[:128] or labelize(slug)},
    )
    return slug
