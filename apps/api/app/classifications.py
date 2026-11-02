"""Client classifications — which portal design (and which capabilities) a client gets.

A classification is a tenant-owned book label, not a WorkOS role. Auth profiles answer
"who may log in"; this answers "which shopfront, and what may they do in it". The two
books of one firm (advised read-only, D2C self-serve) live in one tenant, so a role on
the login cannot split them. Design: ``docs/client-classifications.md``.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ids import new_id

_SLUG = re.compile(r"^[a-z0-9-]{1,32}$")
_SERVICE = frozenset({"advised", "execution_only", "discretionary"})
_SERVICE_ALIASES = {
    "advised": "advised",
    "advisory": "advised",
    "execution_only": "execution_only",
    "execution-only": "execution_only",
    "discretionary": "discretionary",
}


def _normalise_service(raw: str) -> str:
    return _SERVICE_ALIASES.get(raw.strip().lower().replace(" ", "_"), raw.strip().lower())


_CONTACT_TYPES = frozenset({"individual", "organisation"})


class ClassificationMatch(BaseModel):
    contact_types: list[str] = Field(default_factory=list)
    any_account_service_types: list[str] = Field(default_factory=list)
    account_service_types: list[str] = Field(default_factory=list)
    min_value: float | None = None
    max_value: float | None = None

    @field_validator("contact_types")
    @classmethod
    def _contact_types(cls, value: list[str]) -> list[str]:
        return [item for item in value if item in _CONTACT_TYPES]

    @field_validator("any_account_service_types", "account_service_types")
    @classmethod
    def _services(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            normalised = _normalise_service(item)
            if normalised in _SERVICE and normalised not in seen:
                seen.append(normalised)
        return seen


class ClassificationCapabilities(BaseModel):
    money_movement: bool = False


class ClassificationIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    slug: str | None = None
    sort_order: int = 0
    is_default: bool = False
    match: ClassificationMatch = Field(default_factory=ClassificationMatch)
    capabilities: ClassificationCapabilities = Field(default_factory=ClassificationCapabilities)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Name a classification.")
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


class ClassificationFacts(BaseModel):
    contact_type: str | None = None
    contact_service_type: str | None = None
    account_service_types: set[str] = Field(default_factory=set)
    portfolio_value: float | None = None


Source = Literal["pinned", "matched", "default"]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:32]
    return slug or "classification"


def match_rule(rule: ClassificationMatch, facts: ClassificationFacts) -> bool:
    """True when this classification's predicates accept the contact's facts.

    An empty rule matches everyone — that is the last-resort / default shape, not a
    "match nothing". A non-default row with an empty rule would steal every unmatched
    client; ``resolve`` walks ``sort_order`` so the firm puts those last (or marks
    ``is_default`` and leaves match empty).
    """
    if rule.contact_types and (facts.contact_type or "") not in rule.contact_types:
        return False
    if rule.any_account_service_types:
        if not (facts.account_service_types & set(rule.any_account_service_types)):
            return False
    if rule.account_service_types:
        allowed = set(rule.account_service_types)
        held = facts.account_service_types
        if held:
            if not held.issubset(allowed):
                return False
        elif (facts.contact_service_type or "") not in allowed:
            return False
    if rule.max_value is not None:
        if facts.portfolio_value is None or facts.portfolio_value > rule.max_value:
            return False
    if rule.min_value is not None:
        if facts.portfolio_value is None or facts.portfolio_value < rule.min_value:
            return False
    return True


def _row(mapping: Any) -> dict[str, Any]:
    match = ClassificationMatch.model_validate(mapping["match"] or {})
    caps = ClassificationCapabilities.model_validate(mapping["capabilities"] or {})
    return {
        "id": mapping["id"],
        "uid": mapping["uid"],
        "name": mapping["name"],
        "slug": mapping["slug"],
        "sort_order": mapping["sort_order"],
        "is_default": mapping["is_default"],
        "match": match.model_dump(),
        "capabilities": caps.model_dump(),
    }


def public_view(row: dict[str, Any], *, source: Source) -> dict[str, Any]:
    """What a client (and the branding hook) may see — no match predicates."""
    return {
        "uid": row["uid"],
        "name": row["name"],
        "slug": row["slug"],
        "capabilities": row["capabilities"],
        "source": source,
    }


async def list_classifications(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, name, slug, sort_order, is_default, match, capabilities "
                    "FROM crm.client_classifications "
                    "WHERE tenant_id = :t "
                    "ORDER BY sort_order, id"
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
                    "SELECT id, uid, name, slug, sort_order, is_default, match, capabilities "
                    "FROM crm.client_classifications "
                    "WHERE tenant_id = :t AND uid = :u"
                ),
                {"t": tenant_id, "u": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    return _row(row) if row else None


async def create_classification(
    session: AsyncSession, tenant_id: int, body: ClassificationIn
) -> dict[str, Any]:
    slug = body.slug or slugify(body.name)
    if not _SLUG.match(slug):
        raise ValueError("Slug must be lowercase letters, digits and hyphens, up to 32.")
    if body.is_default:
        await session.execute(
            text(
                "UPDATE crm.client_classifications SET is_default = false "
                "WHERE tenant_id = :t AND is_default"
            ),
            {"t": tenant_id},
        )
    uid = new_id()
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO crm.client_classifications "
                    "(uid, tenant_id, name, slug, sort_order, is_default, match, capabilities) "
                    "VALUES (:u, :t, :n, :s, :o, :d, CAST(:m AS jsonb), CAST(:c AS jsonb)) "
                    "RETURNING id, uid, name, slug, sort_order, is_default, match, capabilities"
                ),
                {
                    "u": uid,
                    "t": tenant_id,
                    "n": body.name,
                    "s": slug,
                    "o": body.sort_order,
                    "d": body.is_default,
                    "m": ClassificationMatch.model_validate(body.match).model_dump_json(),
                    "c": ClassificationCapabilities.model_validate(
                        body.capabilities
                    ).model_dump_json(),
                },
            )
        )
        .mappings()
        .one()
    )
    return _row(row)


async def update_classification(
    session: AsyncSession, tenant_id: int, uid: str, body: ClassificationIn
) -> dict[str, Any] | None:
    existing = await get_by_uid(session, tenant_id, uid)
    if existing is None:
        return None
    slug = body.slug or existing["slug"]
    if body.is_default:
        await session.execute(
            text(
                "UPDATE crm.client_classifications SET is_default = false "
                "WHERE tenant_id = :t AND is_default AND uid <> :u"
            ),
            {"t": tenant_id, "u": uid},
        )
    row = (
        (
            await session.execute(
                text(
                    "UPDATE crm.client_classifications SET "
                    "name = :n, slug = :s, sort_order = :o, is_default = :d, "
                    "match = CAST(:m AS jsonb), capabilities = CAST(:c AS jsonb), "
                    "updated_at = now() "
                    "WHERE tenant_id = :t AND uid = :u "
                    "RETURNING id, uid, name, slug, sort_order, is_default, match, capabilities"
                ),
                {
                    "t": tenant_id,
                    "u": uid,
                    "n": body.name,
                    "s": slug,
                    "o": body.sort_order,
                    "d": body.is_default,
                    "m": ClassificationMatch.model_validate(body.match).model_dump_json(),
                    "c": ClassificationCapabilities.model_validate(
                        body.capabilities
                    ).model_dump_json(),
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    return _row(row) if row else None


async def delete_classification(session: AsyncSession, tenant_id: int, uid: str) -> bool:
    deleted = (
        await session.execute(
            text(
                "DELETE FROM crm.client_classifications "
                "WHERE tenant_id = :t AND uid = :u RETURNING uid"
            ),
            {"t": tenant_id, "u": uid},
        )
    ).scalar_one_or_none()
    return deleted is not None


async def facts_for(session: AsyncSession, tenant_id: int, contact_id: int) -> ClassificationFacts:
    """Account service is the captured ``investment_capacity`` — the same expression
    the accounts listing uses. A model on the account is not a service agreement.
    There is no ``platform.accounts.service_type`` column. Value is the sum of open,
    non-closed accounts, the book's ``_VALUED`` cut."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT c.type::text AS contact_type, "
                    "       c.service_type::text AS contact_service_type, "
                    "       COALESCE("
                    "         (SELECT array_agg(DISTINCT "
                    "            NULLIF(a.investment_capacity, '')) "
                    "            FROM platform.accounts a "
                    "           WHERE a.tenant_id = :t AND a.contact_id = :c "
                    "             AND a.status NOT IN ('draft','closed')), "
                    "         '{}'::text[]) AS account_service_types, "
                    "       (SELECT SUM(a.value) "
                    "          FROM platform.accounts a "
                    "         WHERE a.tenant_id = :t AND a.contact_id = :c "
                    "           AND a.status NOT IN ('draft','closed')) AS portfolio_value "
                    "FROM crm.contacts c "
                    "WHERE c.tenant_id = :t AND c.id = :c AND c.deleted_at IS NULL"
                ),
                {"t": tenant_id, "c": contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return ClassificationFacts()
    return ClassificationFacts(
        contact_type=row["contact_type"],
        contact_service_type=row["contact_service_type"],
        account_service_types={
            _normalise_service(s) for s in (row["account_service_types"] or []) if s
        },
        portfolio_value=(
            float(row["portfolio_value"]) if row["portfolio_value"] is not None else None
        ),
    )


async def resolve_for_contact(
    session: AsyncSession, tenant_id: int, contact_id: int
) -> tuple[dict[str, Any], Source] | None:
    """Pinned wins; else first matching rule in sort_order; else the default."""
    pin = (
        (
            await session.execute(
                text(
                    "SELECT cl.id, cl.uid, cl.name, cl.slug, cl.sort_order, cl.is_default, "
                    "       cl.match, cl.capabilities "
                    "FROM crm.contacts c "
                    "JOIN crm.client_classifications cl "
                    "  ON cl.tenant_id = c.tenant_id AND cl.id = c.classification_id "
                    "WHERE c.tenant_id = :t AND c.id = :c AND c.deleted_at IS NULL"
                ),
                {"t": tenant_id, "c": contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if pin is not None:
        return _row(pin), "pinned"

    catalogue = await list_classifications(session, tenant_id)
    if not catalogue:
        return None
    facts = await facts_for(session, tenant_id, contact_id)
    for row in catalogue:
        if row["is_default"]:
            continue
        if match_rule(ClassificationMatch.model_validate(row["match"]), facts):
            return row, "matched"
    default = next((row for row in catalogue if row["is_default"]), None)
    return (default, "default") if default else None


async def pin_contact(
    session: AsyncSession,
    tenant_id: int,
    contact_id: int,
    classification_uid: str | None,
) -> dict[str, Any] | None:
    """Set or clear the explicit pin. ``None`` rematches on the next resolve."""
    classification_id: int | None = None
    if classification_uid:
        found = await get_by_uid(session, tenant_id, classification_uid)
        if found is None:
            return None
        classification_id = int(found["id"])
    await session.execute(
        text(
            "UPDATE crm.contacts SET classification_id = :cl "
            "WHERE tenant_id = :t AND id = :c AND deleted_at IS NULL"
        ),
        {"t": tenant_id, "c": contact_id, "cl": classification_id},
    )
    resolved = await resolve_for_contact(session, tenant_id, contact_id)
    if resolved is None:
        return {
            "uid": None,
            "name": None,
            "slug": None,
            "capabilities": ClassificationCapabilities().model_dump(),
            "source": "default",
        }
    row, source = resolved
    return public_view(row, source=source)
