"""The people and accounts one onboarding application is setting up.

``client.setup`` used to carry a single ``account_id``. A household is not one
account: two parents can each hold a GIA, share a joint GIA, and one of them
can be the registered contact on a child's JISA. The plan is that whole
application. One SEI enrolment sends every client and every account together.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Parties the enrolment has to know. A beneficiary is not an owner and is not sent.
_PARTY_ROLES = frozenset({"owner", "registered_contact", "decision_maker", "trustee"})


def _name(row: dict[str, Any]) -> str:
    parts = [str(row.get("first_name") or "").strip(), str(row.get("surname") or "").strip()]
    return " ".join(part for part in parts if part)


def assemble(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Group membership rows into accounts (owners first) and one entry per person.

    ``rows`` is one ``account_contacts`` membership each, with the account columns
    repeated. The account's own ``contact_id`` is the primary owner when that
    person is an owner; the other owners are the joint owners.
    """
    accounts: dict[int, dict[str, Any]] = {}
    for row in rows:
        account_id = int(row["account_id"])
        account = accounts.get(account_id)
        if account is None:
            account = {
                "id": account_id,
                "uid": row.get("account_uid"),
                "name": row.get("account_name"),
                "product_type": row.get("product_type"),
                "custodian": row.get("custodian"),
                "contact_id": row.get("contact_id"),
                "product_id": row.get("product_id"),
                "model_id": row.get("model_id"),
                "data": row.get("data"),
                "payments": row.get("payments"),
                "withdrawals": row.get("withdrawals"),
                "transfer": row.get("transfer"),
                "parties": [],
            }
            accounts[account_id] = account
        role = str(row.get("role") or "")
        if role not in _PARTY_ROLES or row.get("party_contact_id") is None:
            continue
        account["parties"].append(
            {
                "contact_id": int(row["party_contact_id"]),
                "uid": row.get("party_uid"),
                "name": _name(row),
                "role": role,
            }
        )
    ordered = [accounts[key] for key in sorted(accounts)]
    for account in ordered:
        owners = [party for party in account["parties"] if party["role"] == "owner"]
        primary_id = account.get("contact_id")
        owners.sort(key=lambda party: (party["contact_id"] != primary_id, party["contact_id"]))
        account["owners"] = owners
    parties: dict[int, dict[str, Any]] = {}
    for account in ordered:
        for party in account["parties"]:
            person = parties.get(party["contact_id"])
            if person is None:
                person = {
                    "contact_id": party["contact_id"],
                    "uid": party["uid"],
                    "name": party["name"],
                    "accounts": [],
                }
                parties[party["contact_id"]] = person
            person["accounts"].append(
                {
                    "account_id": account["id"],
                    "uid": account["uid"],
                    "name": account["name"],
                    "product": account.get("product_type"),
                    "role": party["role"],
                }
            )
    return {
        "accounts": ordered,
        "parties": [parties[key] for key in sorted(parties)],
    }


def public_payload(household: dict[str, Any], *, tenant_id: int) -> dict[str, Any]:
    """What the actions page shows: the people, then each account and who is on it.

    ``application_id`` is ``crm.applications.id`` — the onboarding application whose
    memberships these rows are. It is not a custodian id.
    """
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "clients": [
            {"id": party["contact_id"], "uid": party["uid"], "name": party["name"]}
            for party in household["parties"]
        ],
        "accounts": [
            {
                "id": account["id"],
                "uid": account["uid"],
                "name": account["name"],
                "type": account.get("product_type"),
                "clients": [
                    {"id": party["contact_id"], "role": party["role"]}
                    for party in account["parties"]
                ],
            }
            for account in household["accounts"]
        ],
    }
    if household.get("application_id") is not None:
        payload["application_id"] = household["application_id"]
        payload["application_uid"] = household.get("application_uid")
    return payload


async def load_household(
    session: AsyncSession, *, tenant_id: int, account_id: int
) -> dict[str, Any] | None:
    """The application this account belongs to, or just that account when it has none."""
    from app.providers import custodians

    # The column is what the firm stamped. A synced account often has only the
    # primary link, which is the same custodian the instruction path already uses.
    # Two custodian links are not a choice: enum order would pick SEI. Without a
    # single answer, client.setup omits the account steps rather than guessing.
    params = {"a": account_id, "t": tenant_id, "custodians": list(custodians())}
    result = await session.execute(
        text(
            "SELECT a.id, a.uid, a.name, a.contact_id, a.product_id, a.model_id, "
            "       COALESCE(a.custodian::text, ( "
            "         SELECT min(l.provider::text) FROM platform.links l "
            "          WHERE l.entity_type = 'account' AND l.entity_id = a.id "
            "            AND l.tenant_id = a.tenant_id AND l.link_type = 'primary' "
            "            AND l.provider::text = ANY(:custodians) "
            "         HAVING count(DISTINCT l.provider) = 1 "
            "       )) AS custodian, a.data, a.payments, a.withdrawals, "
            "       a.transfer, p.product_type::text AS product_type, "
            "       c.uid AS party_uid, c.first_name, c.surname, "
            "       app.id AS application_id, app.uid AS application_uid "
            "FROM platform.accounts a "
            "LEFT JOIN platform.products p ON p.id = a.product_id "
            "LEFT JOIN crm.contacts c ON c.id = a.contact_id "
            "LEFT JOIN crm.account_contacts link "
            "  ON link.account_id = a.id AND link.tenant_id = a.tenant_id "
            "LEFT JOIN crm.applications app ON app.id = link.application_id "
            "WHERE a.id = :a AND a.tenant_id = :t "
            "ORDER BY app.id NULLS LAST LIMIT 1"
        ),
        params,
    )
    # A session that cannot run a query (the setup handler's unit double) has no
    # result to read. That is "no household", which the setup report already treats
    # as a skipped administrator sync.
    read = getattr(result, "mappings", None)
    if not callable(read):
        return None
    found = read().one_or_none()
    if found is None:
        return None
    application_id = found["application_id"]
    if application_id is None:
        household = assemble(
            [
                {
                    **dict(found),
                    "account_id": found["id"],
                    "account_uid": found["uid"],
                    "account_name": found["name"],
                    "party_contact_id": found["contact_id"],
                    "party_uid": found["party_uid"],
                    "first_name": found["first_name"],
                    "surname": found["surname"],
                    "role": "owner" if found["contact_id"] is not None else None,
                }
            ]
        )
        household["application_id"] = None
        household["application_uid"] = None
        return household
    rows = (
        (
            await session.execute(
                text(
                    "SELECT a.id AS account_id, a.uid AS account_uid, a.name AS account_name, "
                    "       a.contact_id, a.product_id, a.model_id, "
                    "       COALESCE(a.custodian::text, ( "
                    "         SELECT min(l.provider::text) FROM platform.links l "
                    "          WHERE l.entity_type = 'account' AND l.entity_id = a.id "
                    "            AND l.tenant_id = a.tenant_id AND l.link_type = 'primary' "
                    "            AND l.provider::text = ANY(:custodians) "
                    "         HAVING count(DISTINCT l.provider) = 1 "
                    "       )) AS custodian, a.data, a.payments, a.withdrawals, "
                    "       a.transfer, p.product_type::text AS product_type, "
                    "       ac.contact_id AS party_contact_id, ac.role::text AS role, "
                    "       c.uid AS party_uid, c.first_name, c.surname "
                    "FROM crm.account_contacts ac "
                    "JOIN platform.accounts a "
                    "  ON a.id = ac.account_id AND a.tenant_id = ac.tenant_id "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "LEFT JOIN crm.contacts c ON c.id = ac.contact_id "
                    "WHERE ac.application_id = :app AND ac.tenant_id = :t "
                    "ORDER BY a.id, ac.contact_id"
                ),
                {**params, "app": application_id},
            )
        )
        .mappings()
        .all()
    )
    household = assemble([dict(row) for row in rows])
    household["application_id"] = int(application_id)
    household["application_uid"] = found["application_uid"]
    return household
