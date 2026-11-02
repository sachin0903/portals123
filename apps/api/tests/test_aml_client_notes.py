"""Internal notes on the AML console's client page (``app.routers.aml``), against a real Postgres.

Drives the router's own dependency and endpoint coroutines, as ``test_aml_router.py`` does. What
is worth proving:

* a note is kept on the client's contact when it has one, else on its compliance client, and the
  client page lists every note of either, newest first, under the operator who wrote it — so a note
  written before the client had a compliance client still shows once it has one.
* a blank or overlong note is refused, an unknown client is 404, and adding needs ``aml:write``
  where reading needs only ``aml:read``.
* a firm can never read a note (tipping off, docs/aml.md §5): ``compliance.client_notes`` has no
  grant to ``authenticated``, its DML trail is withheld from a firm, and the act is an ``aml.``
  event whose subject never carries the note's text.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import Principal
from app.routers import aml as aml_router
from tests.complycube_support import Bed, Maker, fetch, make_tenant, seed_bed
from tests.conftest import principal_conn

ALL = ["aml:read", "aml:write", "aml:adjudicate"]


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def console(maker: Maker, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    """A firm with one portal client, Graphene's operations tenant, an operator and a firm
    member."""
    monkeypatch.setattr(aml_router, "SessionLocal", maker)
    bed = await seed_bed(maker)
    users: dict[str, dict[str, str]] = {}
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        ops_id = await make_tenant(s, "cco", bed.nonce)
        for tag, tenant_id in (("ops", ops_id), ("firm", bed.tenant_id)):
            user = {"id": str(uuid.uuid4()), "workos": f"wos_{tag}_{bed.nonce}"}
            await s.execute(
                text(
                    "INSERT INTO auth.users (id, tenant_id, email, first_name, last_name, "
                    "  workos_user_id) VALUES (:id, :t, :email, 'Olive', 'Operator', :w)"
                ),
                {
                    "id": user["id"],
                    "t": tenant_id,
                    "email": f"{tag}-{bed.nonce}@notes.test",
                    "w": user["workos"],
                },
            )
            users[tag] = user
    bed.extra_tenants.append(ops_id)
    yield {"bed": bed, "ops": users["ops"], "firm": users["firm"]}
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("DELETE FROM compliance.client_notes WHERE tenant_id = :t"), {"t": bed.tenant_id}
        )
        await s.execute(
            text("DELETE FROM auth.users WHERE tenant_id = ANY(:ids)"),
            {"ids": [ops_id, bed.tenant_id]},
        )
    await bed.cleanup()


def _principal(workos_user: str, permissions: list[str]) -> Principal:
    return Principal(
        user_id=workos_user, organisation_id="org_test", role="member", permissions=permissions
    )


def _ops(console: dict[str, Any], permissions: list[str] | None = None) -> Principal:
    return _principal(console["ops"]["workos"], ALL if permissions is None else permissions)


@asynccontextmanager
async def _ctx(principal: Principal) -> AsyncIterator[aml_router.AmlContext]:
    """Drive ``aml_session`` as FastAPI does: commit when the handler returns, roll back when it
    raises."""
    generator = aml_router.aml_session(tenant=None, principal=principal)
    ctx = await anext(generator)
    try:
        yield ctx
    except BaseException:
        await generator.aclose()
        raise
    with pytest.raises(StopAsyncIteration):
        await anext(generator)


async def _add(
    console: dict[str, Any], key: str, body: str, permissions: list[str] | None = None
) -> dict[str, object]:
    async with _ctx(_ops(console, permissions)) as ctx:
        return await aml_router.add_client_note(
            key, aml_router.ClientNoteRequest(body=body), ctx=ctx
        )


async def _notes(
    console: dict[str, Any], key: str, permissions: list[str] | None = None
) -> list[dict[str, Any]]:
    async with _ctx(_ops(console, permissions)) as ctx:
        page = await aml_router.get_client(key, ctx=ctx)
    notes = page["notes"]
    assert isinstance(notes, list)
    return notes


async def _stored(bed: Bed) -> list[dict[str, Any]]:
    return await fetch(
        bed.maker,
        "SELECT contact_id, client_id, author_user_id, author_name, body "
        "FROM compliance.client_notes WHERE tenant_id = :t ORDER BY id",
        {"t": bed.tenant_id},
    )


async def _bare_contact(bed: Bed, tag: str) -> tuple[int, str]:
    """A client of the bed's firm with no compliance client yet. Returns ``(id, uid)``."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        uid = f"c{tag}{bed.nonce}"
        contact_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, name, type, service_type) "
                        "VALUES (:t, :u, :n, 'individual', 'advised') RETURNING id"
                    ),
                    {"t": bed.tenant_id, "u": uid, "n": f"Client {tag}"},
                )
            ).scalar_one()
        )
    return contact_id, uid


async def _compliance_client(bed: Bed, tag: str, contact_id: int | None) -> tuple[int, str]:
    """A compliance client of the bed's firm, on ``contact_id`` or (None) a partner client with
    no contact. Returns ``(id, uid)``."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            await s.execute(
                text(
                    "INSERT INTO compliance.clients (tenant_id, contact_id, provider_client_id, "
                    "  first_name, last_name) VALUES (:t, :c, :p, 'Grace', :last) "
                    "RETURNING id, uid"
                ),
                {
                    "t": bed.tenant_id,
                    "c": contact_id,
                    "p": f"cli_{tag}_{bed.nonce}",
                    "last": f"Hopper {tag}",
                },
            )
        ).one()
    return int(row.id), str(row.uid)


# ---- adding and reading -------------------------------------------------------------------------


async def test_a_note_on_a_portal_client_is_listed_newest_first_under_its_author(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    first = await _add(console, bed.contact_uid, "Called the adviser. Source of funds is a sale.")
    second = await _add(console, bed.contact_uid, "  EDD done.\nThe MLRO accepts the risk.  ")

    assert set(second) == {"uid", "body", "author", "created_at"}
    assert second["body"] == "EDD done.\nThe MLRO accepts the risk."
    assert second["author"] == "Olive Operator"
    assert isinstance(second["created_at"], str)

    notes = await _notes(console, bed.contact_uid)
    assert [note["uid"] for note in notes] == [second["uid"], first["uid"]]
    assert notes[0] == second
    # Kept on the contact, which the client has, and under the operator's own user.
    stored = await _stored(bed)
    assert [(row["contact_id"], row["client_id"]) for row in stored] == [(bed.contact_id, None)] * 2
    assert {str(row["author_user_id"]) for row in stored} == {console["ops"]["id"]}
    # The compliance uid opens the same client, with the same notes.
    by_uid = await fetch(
        bed.maker, "SELECT uid FROM compliance.clients WHERE id = :c", {"c": bed.client_pk}
    )
    assert await _notes(console, str(by_uid[0]["uid"])) == notes


async def test_a_note_written_before_the_compliance_client_still_shows(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    contact_id, contact_uid = await _bare_contact(bed, "early")
    early = await _add(console, contact_uid, "Due a first screen. Asked the firm for an address.")
    _client_pk, client_uid = await _compliance_client(bed, "early", contact_id)

    assert await _notes(console, contact_uid) == [early]
    assert await _notes(console, client_uid) == [early]


async def test_a_note_on_a_partner_client_sits_on_its_compliance_client(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    client_pk, client_uid = await _compliance_client(bed, "partner", None)
    note = await _add(console, client_uid, "Partner firm confirmed the date of birth by phone.")

    assert await _notes(console, client_uid) == [note]
    assert [(row["contact_id"], row["client_id"]) for row in await _stored(bed)] == [
        (None, client_pk)
    ]
    # Another client's page does not carry it.
    assert await _notes(console, bed.contact_uid) == []


@pytest.mark.parametrize("body", ["", "   \n\t ", "x" * (aml_router.NOTE_MAX_CHARS + 1)])
async def test_a_blank_or_overlong_note_is_refused(console: dict[str, Any], body: str) -> None:
    bed = console["bed"]
    with pytest.raises(HTTPException) as raised:
        await _add(console, bed.contact_uid, body)
    assert raised.value.status_code == 422
    assert await _stored(bed) == []


async def test_a_note_of_the_longest_length_is_kept(console: dict[str, Any]) -> None:
    bed = console["bed"]
    longest = "x" * aml_router.NOTE_MAX_CHARS
    note = await _add(console, bed.contact_uid, f"  {longest}  ")
    assert note["body"] == longest


async def test_a_note_on_an_unknown_client_is_404(console: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as raised:
        await _add(console, "no-such-client", "Anything.")
    assert raised.value.status_code == 404


# ---- the gate -----------------------------------------------------------------------------------


async def test_adding_needs_aml_write_and_reading_needs_only_aml_read(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    with pytest.raises(HTTPException) as raised:
        await _add(console, bed.contact_uid, "Should not be kept.", ["aml:read"])
    assert raised.value.status_code == 403
    assert await _stored(bed) == []

    note = await _add(console, bed.contact_uid, "Kept.", ["aml:read", "aml:write"])
    assert await _notes(console, bed.contact_uid, ["aml:read"]) == [note]


async def test_a_firm_principal_can_neither_add_nor_read_a_note(console: dict[str, Any]) -> None:
    bed = console["bed"]
    await _add(console, bed.contact_uid, "Suspected structuring.")
    principal = _principal(
        console["firm"]["workos"], ["contacts:read", "contacts:read_all", "screening:read"]
    )
    async with _ctx(principal) as ctx:
        with pytest.raises(HTTPException) as raised:
            await aml_router.add_client_note(
                bed.contact_uid, aml_router.ClientNoteRequest(body="x"), ctx=ctx
            )
        assert raised.value.status_code == 403
        with pytest.raises(HTTPException) as raised:
            await aml_router.get_client(bed.contact_uid, ctx=ctx)
        assert raised.value.status_code == 403


# ---- tipping off --------------------------------------------------------------------------------


def _firm_claims(console: dict[str, Any]) -> dict[str, object]:
    """The firm's own staff, as a request session publishes them."""
    bed = console["bed"]
    return {
        "sub": console["firm"]["workos"],
        "role": "adviser",
        "portal": "platform",
        "permissions": ["contacts:read_all"],
        "user_uuid": console["firm"]["id"],
        "tenant_id": bed.tenant_id,
        "tenant_ids": [bed.tenant_id],
        "contact_ids": [],
        "account_ids": [],
    }


async def test_a_firm_session_cannot_read_a_note_or_its_trail(
    console: dict[str, Any], app_engine: Engine, super_engine: Engine
) -> None:
    """The table has no grant to ``authenticated`` (the DENY_ALL list in
    ``test_schema_invariants.py`` pins the absent policy and grant structurally), so a firm's read
    fails on privilege rather than returning rows. Its audit.log rows carry the note's text, and
    the firm branch of ``audit_log_read`` withholds every ``compliance.%`` row (0221)."""
    bed = console["bed"]
    await _add(console, bed.contact_uid, "Suspected structuring. Do not tell the firm.")
    with super_engine.connect() as c:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            granted = c.execute(
                text("SELECT has_table_privilege('authenticated', 'compliance.client_notes', :p)"),
                {"p": privilege},
            ).scalar_one()
            assert granted is False, privilege
        trail = c.execute(
            text(
                "SELECT count(*) FROM audit.log "
                "WHERE entity_type = 'compliance.client_notes' AND tenant_id = :t"
            ),
            {"t": bed.tenant_id},
        ).scalar_one()
    assert trail >= 1, "precondition: the note's insert is audited"

    with principal_conn(app_engine, _firm_claims(console)) as conn:
        own = conn.execute(
            text("SELECT count(*) FROM audit.log WHERE entity_type = 'crm.contacts'")
        ).scalar_one()
        withheld = conn.execute(
            text("SELECT count(*) FROM audit.log WHERE entity_type = 'compliance.client_notes'")
        ).scalar_one()
        with pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(text("SELECT count(*) FROM compliance.client_notes"))
    assert own > 0, "precondition: the firm principal reads its own trail"
    assert withheld == 0, "TIPPING OFF: a firm principal read the trail of an internal note"


def test_adding_a_note_is_an_aml_event_that_keeps_the_text_out() -> None:
    """The middleware records the route as ``aml.client_note_added``: the ``aml.`` prefix keeps it
    from every firm, and no subject is captured, so the note never reaches the audit row."""
    from app.audit_actions import event_type_for, extract_subject
    from app.main import app
    from app.routers.firm import FIRM_EXCLUDED_EVENT_PREFIXES
    from tests.conftest import api_routes

    route = next(
        r for r in api_routes(app) if r.path == "/aml/clients/{key}/notes" and "POST" in r.methods
    )
    event_type = event_type_for(route)
    assert event_type == "aml.client_note_added"
    assert event_type.startswith(FIRM_EXCLUDED_EVENT_PREFIXES)
    body = json.dumps({"body": "Suspected structuring."}).encode()
    assert extract_subject(event_type, body, {}) is None
