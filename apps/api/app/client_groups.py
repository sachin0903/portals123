"""Client groups — assigned tags, such as a business stream, that can select a portal design.

A group is not a book. Books (``app/classifications.py``) are one label, mostly
derived from the accounts, and they decide whether the client may move money.
A group is something the firm puts on a person. A client may wear several.
Free-form contact tags (``app/tags.py``) stay the automation labels: no
catalogue and not shown to the client. Design: ``docs/client-groups.md``.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ids import new_id

_SLUG = re.compile(r"^[a-z0-9-]{1,32}$")
#: A person in twenty streams is a data error, not a portfolio. The replace
#: refuses rather than silently keeping the first twenty.
MAX_PER_CONTACT = 20


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    slug: str | None = None
    description: str = ""
    sort_order: int = 0

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Name a group.")
        return trimmed[:40]

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        slug = value.strip().lower()
        if not _SLUG.match(slug):
            raise ValueError("Slug must be lowercase letters, digits and hyphens, up to 32.")
        return slug

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) > 160:
            raise ValueError("Description must be 160 characters or fewer.")
        return cleaned


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:32]
    return slug or "group"


def _row(mapping: Any) -> dict[str, Any]:
    return {
        "id": mapping["id"],
        "uid": mapping["uid"],
        "name": mapping["name"],
        "slug": mapping["slug"],
        "description": mapping["description"] or "",
        "sort_order": mapping["sort_order"],
    }


def public_view(row: dict[str, Any]) -> dict[str, Any]:
    """What a client hook and a record badge need — no internal id."""
    return {
        "uid": row["uid"],
        "name": row["name"],
        "slug": row["slug"],
        "description": row["description"],
        "sort_order": row["sort_order"],
    }


async def list_groups(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, name, slug, description, sort_order "
                    "FROM crm.client_groups "
                    "WHERE tenant_id = :t ORDER BY sort_order, id"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return [_row(r) for r in rows]


async def get_by_uid(session: AsyncSession, tenant_id: int, uid: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, name, slug, description, sort_order "
                    "FROM crm.client_groups WHERE tenant_id = :t AND uid = :u"
                ),
                {"t": tenant_id, "u": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    return _row(row) if row else None


async def create_group(session: AsyncSession, tenant_id: int, body: GroupIn) -> dict[str, Any]:
    slug = body.slug or slugify(body.name)
    if not _SLUG.match(slug):
        raise ValueError("Slug must be lowercase letters, digits and hyphens, up to 32.")
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO crm.client_groups "
                    "(uid, tenant_id, name, slug, description, sort_order) "
                    "VALUES (:u, :t, :n, :s, :d, :o) "
                    "RETURNING id, uid, name, slug, description, sort_order"
                ),
                {
                    "u": new_id(),
                    "t": tenant_id,
                    "n": body.name,
                    "s": slug,
                    "d": body.description,
                    "o": body.sort_order,
                },
            )
        )
        .mappings()
        .one()
    )
    return _row(row)


async def update_group(
    session: AsyncSession, tenant_id: int, uid: str, body: GroupIn
) -> dict[str, Any] | None:
    existing = await get_by_uid(session, tenant_id, uid)
    if existing is None:
        return None
    slug = body.slug or existing["slug"]
    row = (
        (
            await session.execute(
                text(
                    "UPDATE crm.client_groups SET "
                    "name = :n, slug = :s, description = :d, sort_order = :o, "
                    "updated_at = now() "
                    "WHERE tenant_id = :t AND uid = :u "
                    "RETURNING id, uid, name, slug, description, sort_order"
                ),
                {
                    "t": tenant_id,
                    "u": uid,
                    "n": body.name,
                    "s": slug,
                    "d": body.description,
                    "o": body.sort_order,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    return _row(row) if row else None


async def delete_group(session: AsyncSession, tenant_id: int, uid: str) -> bool:
    deleted = (
        await session.execute(
            text("DELETE FROM crm.client_groups WHERE tenant_id = :t AND uid = :u RETURNING uid"),
            {"t": tenant_id, "u": uid},
        )
    ).scalar_one_or_none()
    return deleted is not None


async def groups_for_contact(
    session: AsyncSession, tenant_id: int, contact_id: int
) -> list[dict[str, Any]]:
    """The groups this contact wears, in the order a design is chosen (sort_order)."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT g.id, g.uid, g.name, g.slug, g.description, g.sort_order "
                    "FROM crm.contact_groups m "
                    "JOIN crm.client_groups g "
                    "  ON g.tenant_id = m.tenant_id AND g.id = m.group_id "
                    "WHERE m.tenant_id = :t AND m.contact_id = :c "
                    "ORDER BY g.sort_order, g.id"
                ),
                {"t": tenant_id, "c": contact_id},
            )
        )
        .mappings()
        .all()
    )
    return [public_view(_row(r)) for r in rows]


async def set_contact_groups(
    session: AsyncSession,
    tenant_id: int,
    contact_id: int,
    group_uids: list[str],
) -> list[dict[str, Any]]:
    """Replace the contact's groups. Unknown uids and an over-long set are refused
    before anything is deleted, so a bad request leaves the person as they were."""
    seen: list[str] = []
    for uid in group_uids:
        if uid and uid not in seen:
            seen.append(uid)
    if len(seen) > MAX_PER_CONTACT:
        raise ValueError(f"A client can be in at most {MAX_PER_CONTACT} groups.")
    ids: list[int] = []
    if seen:
        found = (
            (
                await session.execute(
                    text(
                        "SELECT id, uid FROM crm.client_groups "
                        "WHERE tenant_id = :t AND uid = ANY(:uids)"
                    ),
                    {"t": tenant_id, "uids": seen},
                )
            )
            .mappings()
            .all()
        )
        by_uid = {row["uid"]: int(row["id"]) for row in found}
        missing = [uid for uid in seen if uid not in by_uid]
        if missing:
            raise ValueError("Unknown client group.")
        ids = [by_uid[uid] for uid in seen]
    await session.execute(
        text("DELETE FROM crm.contact_groups WHERE tenant_id = :t AND contact_id = :c"),
        {"t": tenant_id, "c": contact_id},
    )
    for group_id in ids:
        await session.execute(
            text(
                "INSERT INTO crm.contact_groups (tenant_id, contact_id, group_id) "
                "VALUES (:t, :c, :g)"
            ),
            {"t": tenant_id, "c": contact_id, "g": group_id},
        )
    return await groups_for_contact(session, tenant_id, contact_id)
