"""Which custodian organisation an adviser opens clients under.

``auth.users.provider_refs`` is the person's own id at the custodian. Seccl's other axis
is a node: the nodes the firm has enabled. SEI's is an employee the latest full grant
file already grants, named from ``ref.dsr_teams``. The conformed ``platform.dsr_grants``
table is only the newest warehouse drop, which is sometimes a thin extract, so it is
the fallback when that full file is absent. It is not the office team on ``dsr_ids``,
and not every login that happens to carry a SEI id.
That team is the firm's, and it is the same for every adviser. A payment for someone
already on the platform does not send a node. A firm with exactly one enabled node still
opens when the adviser has not chosen.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.lake import core as lake_core

log = logging.getLogger(__name__)

_NO_FIRM_NODE = "Seccl still needs a Seccl node on the firm."
_NO_ADVISER_NODE = "This client's adviser has no Seccl node. Choose one on their team record."

_CONFIG_FIELD = {"seccl": "node_ids", "sei": "dsr_ids"}

_CONFIGURED_SQL = """
SELECT jsonb_array_elements_text(
         CASE WHEN jsonb_typeof(config -> :field) = 'array'
              THEN config -> :field
              ELSE '[]'::jsonb END
       ) AS key
FROM platform.tenant_platforms
WHERE tenant_id = :t AND provider = :provider AND enabled
"""

_FIRM_SQL = """
SELECT config ->> 'firm_id'
FROM platform.tenant_platforms
WHERE tenant_id = :t AND provider = 'sei' AND enabled
"""

_OPTIONS_SQL = """
WITH chosen AS (
  SELECT ids.key, ids.ordinality
  FROM platform.tenant_platforms tp,
       jsonb_array_elements_text(
         CASE WHEN jsonb_typeof(tp.config -> :field) = 'array'
              THEN tp.config -> :field
              ELSE '[]'::jsonb END
       ) WITH ORDINALITY AS ids(key, ordinality)
  WHERE tp.tenant_id = :t AND tp.provider = :provider AND tp.enabled
)
SELECT c.key, n.name
FROM chosen c
LEFT JOIN platform.nodes n
  ON n.tenant_id = :t
 AND n.provider = :provider
 AND n.key = c.key
 AND (:provider <> 'sei' OR n.type::text = 'team')
ORDER BY c.ordinality
"""

_LEAD_ADVISER_SQL = """
SELECT u.{column} ->> :provider
FROM crm.client_advisers ca
JOIN auth.users u ON u.id = ca.adviser_id AND u.tenant_id = ca.tenant_id
WHERE ca.tenant_id = :t
  AND ca.contact_id = :c
  AND COALESCE(u.{column} ->> :provider, '') <> ''
ORDER BY (ca.role = 'lead') DESC, ca.created_at
LIMIT 1
"""

_CONTACT_NODE_SQL = """
SELECT n.key
FROM crm.contacts c
JOIN platform.nodes n
  ON n.id = c.node_id AND n.tenant_id = c.tenant_id AND n.provider = 'seccl'
WHERE c.id = :c AND c.tenant_id = :t
"""

_REMEMBER_CONTACT_SQL = """
UPDATE crm.contacts AS e
SET node_id = n.id
FROM platform.nodes n
WHERE e.id = :id AND e.tenant_id = :t AND e.node_id IS NULL
  AND n.tenant_id = e.tenant_id AND n.provider = 'seccl' AND n.key = :k
"""

_REMEMBER_ACCOUNT_SQL = """
UPDATE platform.accounts AS e
SET node_id = n.id
FROM platform.nodes n
WHERE e.id = :id AND e.tenant_id = :t AND e.node_id IS NULL
  AND n.tenant_id = e.tenant_id AND n.provider = 'seccl' AND n.key = :k
"""


def _label(key: str, name: str | None) -> str:
    shown = (name or "").strip()
    return shown or key


async def configured_org_keys(
    session: AsyncSession, *, tenant_id: int, provider: Literal["seccl", "sei"]
) -> list[str]:
    """The node or DSR ids this firm has narrowed to, in config order."""
    rows = (
        await session.execute(
            text(_CONFIGURED_SQL),
            {"t": tenant_id, "provider": provider, "field": _CONFIG_FIELD[provider]},
        )
    ).scalars()
    return [str(key) for key in rows if str(key).strip()]


async def assignable_org_keys(
    session: AsyncSession, *, tenant_id: int, provider: Literal["seccl", "sei"]
) -> list[str]:
    """Ids an adviser may be given.

    Seccl is the enabled node list. SEI is an employee the book grants for this
    firm's SEI firm, not the office team on ``dsr_ids``.
    """
    if provider == "sei":
        return [item["value"] for item in await employee_options(session, tenant_id=tenant_id)]
    return await configured_org_keys(session, tenant_id=tenant_id, provider=provider)


def catalogue_employees(firm_id: str) -> list[tuple[str, str | None]]:
    """Employee ids the book grants for this SEI firm, with SEI's own name.

    A team grant is a different grain and is not an adviser. The list is the
    latest full grant file. A catalogue miss leaves the dropdown empty rather
    than offering office teams or logins.
    """
    from app.integrations.sei.enrol import employees_for_picker

    try:
        conn = lake_core.connect("sei")
    except lake_core.CoreUnavailable:
        return []
    try:
        picked = employees_for_picker(conn, firm_id)
        if picked is not None:
            return picked
        rows = conn.execute(
            "SELECT g.dsr_id, "
            "coalesce(nullif(any_value(g.dsr_name), ''), any_value(d.dsr_team_description)) "
            "FROM platform.dsr_grants g "
            "LEFT JOIN ref.dsr_teams d "
            "  ON d.dsr_team_id = g.dsr_id AND d.firm_id = g.firm_id "
            "WHERE g.firm_id = ? AND g.grantee = 'employee' AND g.dsr_id IS NOT NULL "
            "GROUP BY g.dsr_id",
            [firm_id],
        ).fetchall()
    except Exception:
        log.warning("SEI employee list unavailable for firm %s", firm_id, exc_info=True)
        return []
    finally:
        conn.close()
    found: list[tuple[str, str | None]] = []
    for row in rows:
        key = str(row[0]).strip()
        if not key:
            continue
        name = str(row[1]).strip() if row[1] else ""
        found.append((key, name or None))
    return found


async def employee_options(session: AsyncSession, *, tenant_id: int) -> list[dict[str, str]]:
    """SEI employees this firm can name an adviser as. One row per employee id."""
    firm_id = (await session.execute(text(_FIRM_SQL), {"t": tenant_id})).scalar_one_or_none()
    firm = str(firm_id or "").strip()
    if not firm:
        return []
    rows = await asyncio.to_thread(catalogue_employees, firm)
    options = [
        {"value": key, "label": f"{key} · {name}" if name else key} for key, name in rows if key
    ]
    options.sort(key=lambda item: item["label"].casefold())
    return options


async def provider_org_options(session: AsyncSession, *, tenant_id: int) -> dict[str, Any]:
    """Dropdown rows for the adviser card. Name only, and only for a custodian the firm has on."""
    options: dict[str, list[dict[str, str]]] = {}
    for provider in ("seccl", "sei"):
        if provider == "sei":
            options[provider] = await employee_options(session, tenant_id=tenant_id)
            continue
        rows = (
            (
                await session.execute(
                    text(_OPTIONS_SQL),
                    {"t": tenant_id, "provider": provider, "field": _CONFIG_FIELD[provider]},
                )
            )
            .mappings()
            .all()
        )
        options[provider] = [
            {"value": str(row["key"]), "label": _label(str(row["key"]), row["name"])}
            for row in rows
            if str(row["key"] or "").strip()
        ]
    return options


async def _lead_adviser_value(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    provider: Literal["seccl", "sei"],
    column: Literal["provider_orgs", "provider_refs"],
) -> str | None:
    found = (
        await session.execute(
            text(_LEAD_ADVISER_SQL.format(column=column)),
            {"t": tenant_id, "c": contact_id, "provider": provider},
        )
    ).scalar_one_or_none()
    text_value = str(found or "").strip()
    return text_value or None


async def adviser_provider_org(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    provider: Literal["seccl", "sei"],
) -> str | None:
    """The lead adviser's chosen node or DSR for this client, if they have one."""
    return await _lead_adviser_value(
        session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        provider=provider,
        column="provider_orgs",
    )


async def adviser_provider_ref(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    provider: Literal["seccl", "sei"],
) -> str | None:
    """The lead adviser's own id at the custodian, if one is recorded.

    This is ``provider_refs``, the person SEI already knows. It is not the team
    in ``provider_orgs``.
    """
    return await _lead_adviser_value(
        session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        provider=provider,
        column="provider_refs",
    )


_APPLICATION_ADVISER_SQL = """
SELECT u.provider_refs ->> :provider
FROM crm.applications a
JOIN auth.users u ON u.id = a.adviser_id AND u.tenant_id = a.tenant_id
WHERE a.tenant_id = :t
  AND a.uid = :u
  AND COALESCE(u.provider_refs ->> :provider, '') <> ''
"""


async def enrolment_employee_id(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    application_uid: str | None,
    provider: Literal["seccl", "sei"] = "sei",
) -> str | None:
    """The custodian employee id for the person opening this client.

    The client's lead adviser wins when they have one. A journey started for a
    new client records its adviser on the application before that relationship
    exists, and that person's id is the one the custodian is asked for.
    """
    found = await adviser_provider_ref(
        session, tenant_id=tenant_id, contact_id=contact_id, provider=provider
    )
    if found:
        return found
    uid = str(application_uid or "").strip()
    if not uid:
        return None
    row = (
        await session.execute(
            text(_APPLICATION_ADVISER_SQL),
            {"t": tenant_id, "u": uid, "provider": provider},
        )
    ).scalar_one_or_none()
    text_value = str(row or "").strip()
    return text_value or None


async def chosen_seccl_node(
    session: AsyncSession, *, tenant_id: int, contact_id: int
) -> tuple[str, str]:
    """``(key, refusal)`` for opening a client or account.

    A client already on the platform keeps their node. Otherwise the lead adviser's choice.
    A firm with one enabled node is that choice when the adviser has not made one. Several
    nodes and no adviser choice is a refusal that names the team record, not the firm.
    """
    book = (
        await session.execute(text(_CONTACT_NODE_SQL), {"c": contact_id, "t": tenant_id})
    ).scalar_one_or_none()
    if str(book or "").strip():
        return str(book).strip(), ""
    allowed = await configured_org_keys(session, tenant_id=tenant_id, provider="seccl")
    chosen = await adviser_provider_org(
        session, tenant_id=tenant_id, contact_id=contact_id, provider="seccl"
    )
    if chosen and chosen in allowed:
        return chosen, ""
    if len(allowed) == 1:
        return allowed[0], ""
    if not allowed:
        return "", _NO_FIRM_NODE
    return "", _NO_ADVISER_NODE


async def seccl_node_for_create(session: AsyncSession, *, tenant_id: int, contact_id: int) -> str:
    """The node a new Seccl client or account is opened under, or a refusal."""
    key, gap = await chosen_seccl_node(session, tenant_id=tenant_id, contact_id=contact_id)
    if gap:
        # Imported here: operations installs handlers, and those handlers import this module.
        from app.actions.attempt import OperationError

        raise OperationError(gap, permanent=True)
    return key


async def remember_seccl_node(
    session: AsyncSession,
    *,
    tenant_id: int,
    kind: Literal["contact", "account"],
    entity_id: int,
    key: str,
) -> None:
    """Point the book row at the node we just opened under, when that node is already synced.

    The next payment then finds it on the account or the client. A node the warehouse has
    not landed yet leaves the column empty; the adviser's choice still covers that call.
    """
    sql = _REMEMBER_CONTACT_SQL if kind == "contact" else _REMEMBER_ACCOUNT_SQL
    await session.execute(text(sql), {"id": entity_id, "t": tenant_id, "k": key})
