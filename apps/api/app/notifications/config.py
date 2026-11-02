"""Per-tenant notification configuration — the firm-level layer, read/written by the admin path.

The firm-facing categories are those a firm's own people can receive (client or platform audience);
the operations-only categories (AML/compliance, which notify Graphene ops, not the firm) are
deliberately excluded — a firm does not configure Graphene's internal screening notices.

Reads merge the stored rows with the registry defaults so the admin surface always shows a full,
sensible matrix even before anything is configured. Writes upsert explicit values.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.notifications.registry import CATEGORIES, default_channels

#: The audiences a firm's own people belong to (operations = Graphene, excluded from firm config).
_FIRM_AUDIENCES = {"client", "platform"}


def _firm_categories() -> list[Any]:
    return [c for c in CATEGORIES.values() if set(c.portals) & _FIRM_AUDIENCES]


def _category_audiences(cat: Any) -> list[str]:
    """The coarse audiences a category can notify — 'adviser' (staff, from the platform portal) and
    'client'. Advisers first so the admin matrix shows that section on top."""
    auds: list[str] = []
    if "platform" in cat.portals:
        auds.append("adviser")
    if "client" in cat.portals:
        auds.append("client")
    return auds


async def get_tenant_config(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    """The firm's config for every firm-facing category × audience — stored rows merged over the
    registry defaults. One row per (category, audience): ``enabled`` defaults true, ``mandatory``
    false; ``in_app``/``email`` fall back to the registry default when unset."""
    rows = {
        (r["category"], r["audience"]): r
        for r in (
            await session.execute(
                text(
                    "SELECT category, audience, enabled, in_app, email, mandatory "
                    "FROM notifications.tenant_config WHERE tenant_id = :t"
                ),
                {"t": tenant_id},
            )
        ).mappings()
    }
    out: list[dict[str, Any]] = []
    for cat in _firm_categories():
        d = default_channels(cat.key)
        for aud in _category_audiences(cat):
            r = rows.get((cat.key, aud))
            out.append(
                {
                    "category": cat.key,
                    "label": cat.label,
                    "description": cat.description,
                    "audience": aud,
                    "enabled": bool(r["enabled"]) if r else True,
                    "in_app": d["in_app"] if not r or r["in_app"] is None else bool(r["in_app"]),
                    "email": d["email"] if not r or r["email"] is None else bool(r["email"]),
                    "mandatory": bool(r["mandatory"]) if r else False,
                    # The email channel is selectable when the deployment gate is on; the per-row
                    # value still defaults off, so exposing the toggle sends nothing on its own.
                    "email_available": settings.email_notifications_enabled,
                }
            )
    return out


async def clone_config(session: AsyncSession, src_tenant_id: int, dst_tenant_id: int) -> int:
    """Copy one tenant's config rows to another (model → a new firm on creation). Convenience, not
    load-bearing: an absent row falls back to the registry default, so a firm with no clone still
    behaves sensibly. Returns the number of rows copied; ON CONFLICT keeps any existing dst row."""
    result = await session.execute(
        text(
            "INSERT INTO notifications.tenant_config "
            "  (tenant_id, category, audience, enabled, in_app, email, mandatory) "
            "SELECT :dst, category, audience, enabled, in_app, email, mandatory "
            "  FROM notifications.tenant_config WHERE tenant_id = :src "
            "ON CONFLICT (tenant_id, category, audience) DO NOTHING RETURNING id"
        ),
        {"src": src_tenant_id, "dst": dst_tenant_id},
    )
    return len(result.fetchall())


async def set_tenant_config(
    session: AsyncSession, tenant_id: int, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Upsert the firm's per-(category, audience) config. Unknown/operations-only categories and
    audiences a category doesn't serve are ignored, so a malformed item can't create a stray row."""
    allowed = {c.key: set(_category_audiences(c)) for c in _firm_categories()}
    for it in items:
        cat = it.get("category")
        aud = it.get("audience", "client")
        if cat not in allowed or aud not in allowed[cat]:
            continue
        await session.execute(
            text(
                "INSERT INTO notifications.tenant_config "
                "  (tenant_id, category, audience, enabled, in_app, email, mandatory) "
                "VALUES (:t, :cat, :aud, :enabled, :in_app, :email, :mandatory) "
                "ON CONFLICT (tenant_id, category, audience) DO UPDATE "
                "  SET enabled = EXCLUDED.enabled, in_app = EXCLUDED.in_app, "
                "      email = EXCLUDED.email, mandatory = EXCLUDED.mandatory, updated_at = now()"
            ),
            {
                "t": tenant_id,
                "cat": cat,
                "aud": aud,
                "enabled": bool(it.get("enabled", True)),
                "in_app": bool(it["in_app"]) if it.get("in_app") is not None else None,
                "email": bool(it["email"]) if it.get("email") is not None else None,
                "mandatory": bool(it.get("mandatory", False)),
            },
        )
    return await get_tenant_config(session, tenant_id)
