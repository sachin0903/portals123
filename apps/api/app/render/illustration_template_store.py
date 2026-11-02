"""The GIA/ISA illustration as a document template the firm can open.

The layout is owned in :mod:`app.render.illustration_template`. This module publishes that
layout into ``documents.templates`` so it appears with the firm's other document templates,
and so a later edit of the published version is what the next illustration prints. A firm
that has started its own version is left alone.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ids import new_id
from app.render.illustration_template import TEMPLATE_CSS, TEMPLATE_HTML, TEMPLATE_VERSION

CODE = "gia_isa_illustration"
NAME = "GIA and ISA illustration"
DESCRIPTION = (
    "The PDF printed for a GIA or ISA illustration. The figures are filled in when an "
    "illustration is generated. Publishing a new version changes the next illustration."
)


async def ensure_illustration_template(session: AsyncSession) -> int | None:
    """The published ``template_versions.id`` for this tenant, creating it from the code layout
    when the firm has none. Returns none when the session has no signed-in user to attribute
    a new row to and the firm has no template yet."""
    tenant_id = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one_or_none()
    actor_id = (await session.execute(text("SELECT auth.uid()"))).scalar_one_or_none()
    if tenant_id is None:
        return None
    current = await _current(session, int(tenant_id))
    if current is None:
        if actor_id is None:
            return None
        return await _insert(session, int(tenant_id), str(actor_id))
    if (
        actor_id is None
        or current["seed"] == TEMPLATE_VERSION
        or await _firm_has_edited(session, int(current["template_id"]))
    ):
        return int(current["version_id"]) if current["version_id"] is not None else None
    return await _publish_seed(session, int(current["template_id"]), str(actor_id))


async def _current(session: AsyncSession, tenant_id: int) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT t.id AS template_id, t.current_published_version_id AS version_id, "
                    "       v.metadata ->> 'seed' AS seed "
                    "FROM documents.templates t "
                    "LEFT JOIN documents.template_versions v "
                    "  ON v.id = t.current_published_version_id "
                    "WHERE t.tenant_id = :t AND t.code = :code"
                ),
                {"t": tenant_id, "code": CODE},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def _firm_has_edited(session: AsyncSession, template_id: int) -> bool:
    """A draft, or any version that was not seeded from the code layout, means the firm owns it."""
    edited = (
        await session.execute(
            text(
                "SELECT 1 FROM documents.template_versions "
                "WHERE template_id = :id "
                "  AND (status = 'draft' OR metadata ->> 'seed' IS NULL) "
                "LIMIT 1"
            ),
            {"id": template_id},
        )
    ).scalar_one_or_none()
    return edited is not None


def _schema() -> str:
    return json.dumps({"html": TEMPLATE_HTML, "css": TEMPLATE_CSS})


def _seed_meta() -> str:
    return json.dumps({"seed": TEMPLATE_VERSION})


async def _insert(session: AsyncSession, tenant_id: int, actor_id: str) -> int:
    try:
        async with session.begin_nested():
            template_id = (
                await session.execute(
                    text(
                        "INSERT INTO documents.templates "
                        "  (uid, tenant_id, code, name, document_type, description, created_by) "
                        "VALUES (:uid, :t, :code, :name, 'illustration', :desc, :actor) "
                        "RETURNING id"
                    ),
                    {
                        "uid": new_id(),
                        "t": tenant_id,
                        "code": CODE,
                        "name": NAME,
                        "desc": DESCRIPTION,
                        "actor": actor_id,
                    },
                )
            ).scalar_one()
            version_id = await _publish_seed(session, int(template_id), actor_id)
    except IntegrityError:
        current = await _current(session, tenant_id)
        if current is None or current["version_id"] is None:
            raise
        return int(current["version_id"])
    return version_id


async def _publish_seed(session: AsyncSession, template_id: int, actor_id: str) -> int:
    version_id = (
        await session.execute(
            text(
                "INSERT INTO documents.template_versions "
                "  (uid, tenant_id, template_id, version_number, status, renderer, schema, "
                "   metadata, created_by, published_by, published_at) "
                "SELECT :uid, t.tenant_id, t.id, COALESCE(max(v.version_number), 0) + 1, "
                "  'published', 'weasy', CAST(:schema AS jsonb), CAST(:meta AS jsonb), "
                "  :actor, :actor, now() "
                "FROM documents.templates t "
                "LEFT JOIN documents.template_versions v ON v.template_id = t.id "
                "WHERE t.id = :tid "
                "GROUP BY t.tenant_id, t.id "
                "RETURNING documents.template_versions.id"
            ),
            {
                "uid": new_id(),
                "tid": template_id,
                "schema": _schema(),
                "meta": _seed_meta(),
                "actor": actor_id,
            },
        )
    ).scalar_one()
    await session.execute(
        text("UPDATE documents.templates SET current_published_version_id = :v WHERE id = :tid"),
        {"v": version_id, "tid": template_id},
    )
    return int(version_id)
