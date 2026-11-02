"""A portal contact's ComplyCube client.

One provider client per portal contact, found by ``externalId`` (the contact uid) before one is
created, so a contact screened and identity-checked keeps one provider record. The AML console's
screen and the worker's identity submit both bind through ``bind_portal_client``. A client
ComplyCube no longer holds (made under another key, or deleted there) is bound again
(``rebind_portal_client``).

A client already bound is brought up to date before it is used (``refresh_bound_client``): a name,
date of birth or nationality corrected after the client was created would otherwise stay at
ComplyCube, and "Screen again" would screen the old details.

Runs on an elevated session: every statement names its tenant.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube import sync
from app.complycube.client import (
    ORIGIN_KEY,
    ScreeningClient,
    address_payload,
    client_metadata,
    client_payload,
    environment,
    environment_of,
)

#: ComplyCube's answers to client details it will not take.
REFUSED_DETAILS = frozenset({400, 422})
#: How much of ComplyCube's refusal message a conflict carries.
REFUSAL_LIMIT = 200

logger = logging.getLogger(__name__)

#: The ``crm.contacts`` columns a provider client is built from.
CONTACT_COLUMNS = (
    "id, uid, tenant_id, type, name, first_name, surname, dob, nationality, email, address"
)


class ClientConflict(Exception):
    """The provider client is held by another client record, or the contact has two."""


def _jsonb(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def person_payload(contact: dict[str, Any]) -> dict[str, Any]:
    """A portal contact in ``client_payload``'s terms. A person known only by a full name is split
    into first and last."""
    first = contact.get("first_name")
    last = contact.get("surname")
    name = (contact.get("name") or "").strip()
    if not (first and last) and name and contact.get("type") != "organisation":
        parts = name.split()
        first = first or parts[0]
        last = last or (" ".join(parts[1:]) or None)
    dob = contact.get("dob")
    return {
        "type": "company" if contact.get("type") == "organisation" else "person",
        "email": contact.get("email"),
        "nationality": contact.get("nationality") or [],
        "first_name": first,
        "last_name": last,
        "dob": dob.isoformat() if isinstance(dob, date) else dob,
        "company": {"name": name} if name else {},
    }


def contact_address(contact: dict[str, Any]) -> dict[str, Any]:
    """A portal contact's address in ``address_payload``'s terms."""
    raw = _jsonb(contact.get("address"))
    line = " ".join(str(raw[key]) for key in ("line1", "line2") if raw.get(key))
    return {
        "line": line or None,
        "city": raw.get("city"),
        "country": raw.get("country"),
        "postal_code": raw.get("postcode") or raw.get("postalCode"),
        "state": raw.get("county") or raw.get("region"),
    }


def _refusal(response: httpx.Response) -> str:
    """ComplyCube's own reason for a refusal, trimmed, or its status when it gave none."""
    try:
        body = response.json()
    except ValueError:
        body = None
    message = body.get("message") if isinstance(body, dict) else None
    if isinstance(message, str) and message.strip():
        return message.strip()[:REFUSAL_LIMIT]
    return f"HTTP {response.status_code}."


def provider_changes(held: dict[str, Any], wanted: dict[str, Any]) -> dict[str, Any]:
    """What to send ComplyCube so it holds ``wanted`` (a ``client_payload`` body): each field
    that differs from ``held`` (the client as ComplyCube answers it), and nothing else.

    Never ``type`` (ComplyCube refuses it, even unchanged) or ``externalId`` (what the client was
    found by). A field we hold no value for is left as ComplyCube has it, never blanked. Values
    compare trimmed, and an email without case, so a difference only in spacing or case is not a
    change worth an update. ``metadata`` is sent whole when ours is not all there, because an
    update replaces it whole: what ComplyCube holds, with ours over it, so a key someone else set
    survives. ``graphene_origin`` is never changed once set.
    """

    def same(ours: object, theirs: object, *, fold: bool = False) -> bool:
        if not (isinstance(ours, str) and isinstance(theirs, str)):
            return ours == theirs
        ours, theirs = ours.strip(), theirs.strip()
        return ours.casefold() == theirs.casefold() if fold else ours == theirs

    changes: dict[str, Any] = {}
    email = wanted.get("email")
    if isinstance(email, str) and email.strip() and not same(email, held.get("email"), fold=True):
        changes["email"] = email.strip()
    for key in ("personDetails", "companyDetails"):
        have = _jsonb(held.get(key))
        diff = {
            field: value.strip() if isinstance(value, str) else value
            for field, value in _jsonb(wanted.get(key)).items()
            if value is not None
            and not (isinstance(value, str) and not value.strip())
            and not same(value, have.get(field))
        }
        if diff:
            changes[key] = diff
    ours = _jsonb(wanted.get("metadata"))
    if ours:
        have = _jsonb(held.get("metadata"))
        if have.get(ORIGIN_KEY):
            ours.pop(ORIGIN_KEY, None)
        merged = {**have, **ours}
        if merged != have:
            changes["metadata"] = merged
    return changes


async def refresh_bound_client(
    session: AsyncSession,
    screening: ScreeningClient,
    contact: dict[str, Any],
    *,
    client_pk: int,
    provider_client_id: str,
    metadata: dict[str, str],
) -> bool:
    """Send ComplyCube this contact's current details where they differ from what it holds, and
    record what it then holds. True when something was sent. ``metadata`` is ``client_metadata``'s,
    so a client made before it was stamped is stamped now.

    Only for a client this database already holds bound to the contact. Before writing it checks
    the client is this contact's: ComplyCube's ``externalId`` must be the contact uid, the type
    must match, and a client stamped with a deployment must be stamped with this one. Otherwise it
    raises ``ClientConflict`` and changes nothing, so a binding gone wrong stops the check rather
    than overwriting another person's record.

    ComplyCube refusing the details themselves (400, 422) is a ``ClientConflict`` too, carrying its
    message: only correcting the client record can fix it, so it goes to a person. New photos never
    would, and a check run on the old details would be the stale screen this exists to stop.
    """
    held = await screening.get_client(provider_client_id)
    held = held if isinstance(held, dict) else {}
    external_id = str(contact["uid"])
    wanted = client_payload(person_payload(contact), external_id=external_id, metadata=metadata)
    env, stamped = environment(), environment_of(held)
    if (
        held.get("externalId") != external_id
        or held.get("type") != wanted["type"]
        or (env and stamped is not None and stamped != env)
    ):
        raise ClientConflict(
            "That ComplyCube client is not this client's, so its details were not changed. "
            "Ask engineering to check it."
        )
    changes = provider_changes(held, wanted)
    if not changes:
        return False
    try:
        updated = await screening.update_client(provider_client_id, changes)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in REFUSED_DETAILS:
            raise
        raise ClientConflict(
            f"ComplyCube refused this client's details: {_refusal(exc.response)} "
            "Correct the client record, then try again."
        ) from exc
    if isinstance(updated, dict):
        await sync.store_client(
            session, updated, client_pk=client_pk, tenant_id=int(contact["tenant_id"])
        )
    return True


async def bind_portal_client(
    session: AsyncSession, screening: ScreeningClient, contact: dict[str, Any]
) -> int:
    """The ``compliance.clients`` row for a portal contact, bound to a ComplyCube client.

    A row stored without a firm, by the webhook consumer before it kept to clients we hold, is
    claimed rather than duplicated. A client already bound is brought up to date
    (``refresh_bound_client``); one only found by ``externalId`` is not written to, because
    ComplyCube does not keep ``externalId`` unique and the find is the one place a client could be
    another environment's. Raises ``ClientConflict`` when the provider client belongs to another
    record, and the provider's own errors (``httpx``) unchanged.
    """
    tenant_id = int(contact["tenant_id"])
    contact_id = int(contact["id"])
    existing = (
        await session.execute(
            text(
                "SELECT id, provider_client_id FROM compliance.clients "
                "WHERE tenant_id = :t AND contact_id = :c"
            ),
            {"t": tenant_id, "c": contact_id},
        )
    ).first()
    external_id = str(contact["uid"])
    firm = (
        await session.execute(
            text("SELECT uid, name FROM auth.tenants WHERE id = :t"), {"t": tenant_id}
        )
    ).first()
    metadata = client_metadata(
        origin="portal",
        firm_uid=str(firm.uid) if firm is not None else None,
        firm=str(firm.name) if firm is not None and firm.name else None,
        contact_uid=external_id,
    )
    if existing is not None and existing.provider_client_id:
        try:
            await refresh_bound_client(
                session,
                screening,
                contact,
                client_pk=int(existing.id),
                provider_client_id=str(existing.provider_client_id),
                metadata=metadata,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            # ComplyCube no longer holds the client (made under another key, or deleted there):
            # bound again to one it holds.
            return await rebind_portal_client(session, screening, int(existing.id), contact)
        return int(existing.id)
    provider_client_id = await screening.find_client_by_external_id(
        external_id, env=environment() or None
    )
    if provider_client_id is None:
        created = await screening.create_client(
            client_payload(person_payload(contact), external_id=external_id, metadata=metadata)
        )
        provider_client_id = str(created["id"])
    if not await screening.list_addresses(provider_client_id):
        sent = address_payload(provider_client_id, contact_address(contact))
        if sent is not None:
            await screening.create_address(sent)
    held = (
        await session.execute(
            text(
                "SELECT id, tenant_id, contact_id FROM compliance.clients "
                "WHERE provider_client_id = :p"
            ),
            {"p": provider_client_id},
        )
    ).first()
    if held is not None:
        if held.tenant_id is not None and (
            int(held.tenant_id) != tenant_id
            or (held.contact_id is not None and int(held.contact_id) != contact_id)
        ):
            raise ClientConflict("That ComplyCube client already belongs to another client record.")
        if existing is not None and int(existing.id) != int(held.id):
            raise ClientConflict(
                "This client has two screening records. Ask engineering to merge them."
            )
        await session.execute(
            text(
                "UPDATE compliance.clients SET tenant_id = :t, contact_id = :c, "
                "  external_id = :e, updated_at = now() WHERE id = :id"
            ),
            {"t": tenant_id, "c": contact_id, "e": external_id, "id": int(held.id)},
        )
        return int(held.id)
    if existing is not None:
        await session.execute(
            text(
                "UPDATE compliance.clients SET provider_client_id = :p, external_id = :e, "
                "  updated_at = now() WHERE id = :id"
            ),
            {"p": provider_client_id, "e": external_id, "id": int(existing.id)},
        )
        return int(existing.id)
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO compliance.clients "
                    "  (tenant_id, contact_id, provider_client_id, external_id) "
                    "VALUES (:t, :c, :p, :e) RETURNING id"
                ),
                {"t": tenant_id, "c": contact_id, "p": provider_client_id, "e": external_id},
            )
        ).scalar_one()
    )


async def held_at_provider(screening: ScreeningClient, provider_client_id: str) -> bool:
    """Whether ComplyCube holds this client. It does not when the record was made under another
    key (live and test are separate accounts) or was deleted there. Any other error raises."""
    try:
        await screening.get_client(provider_client_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return False
        raise
    return True


async def rebind_portal_client(
    session: AsyncSession, screening: ScreeningClient, client_pk: int, contact: dict[str, Any]
) -> int:
    """Bind a portal contact whose ComplyCube client ComplyCube no longer holds to one it holds:
    found by its ``externalId``, else created (``bind_portal_client``). Everything ComplyCube held
    under the old client is gone with it."""
    await session.execute(
        text(
            "UPDATE compliance.clients SET provider_client_id = NULL, updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": client_pk},
    )
    logger.warning(
        "complycube: client %s is not held by ComplyCube; bound again",
        client_pk,
        extra={"event": "complycube_client_rebound"},
    )
    return await bind_portal_client(session, screening, contact)


async def bound_provider_client(
    session: AsyncSession, *, tenant_id: int, contact_id: int
) -> str | None:
    """The ComplyCube client id a portal contact is bound to now, or None before one is bound."""
    found = (
        await session.execute(
            text(
                "SELECT provider_client_id FROM compliance.clients "
                "WHERE tenant_id = :t AND contact_id = :c"
            ),
            {"t": tenant_id, "c": contact_id},
        )
    ).scalar_one_or_none()
    return str(found) if found else None


async def provider_client_id(session: AsyncSession, client_pk: int) -> str | None:
    """The ComplyCube client id bound to a ``compliance.clients`` row."""
    found = (
        await session.execute(
            text("SELECT provider_client_id FROM compliance.clients WHERE id = :id"),
            {"id": client_pk},
        )
    ).scalar_one_or_none()
    return str(found) if found else None
