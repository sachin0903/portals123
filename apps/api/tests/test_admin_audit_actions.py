"""GET /admin/audit/actions — the read surface over the action-event stream (0041).

`audit.actions` is `service_role`-only with no `authenticated` grant, so RLS is deliberately
NOT part of this story: the endpoint reads as service_role via `admin_session` (that IS the
point — the stream is cross-tenant evidence for Graphene operations) and the access control is
the `audit:read` operations permission, tested here as a matrix over real HTTP. Everything else
(filters, cursor, joins, the envelope) is exercised by calling the endpoint function directly
with a `service_role` session, the `test_admin_directory.py` convention.

Rows are seeded straight into `audit.actions` rather than through the middleware: the write
path (attribution, redaction, effects-only) is `test_actions.py`'s job; this file owns the READ
contract. Unique-per-run markers keep every assertion order-independent against the shared
session-scoped database.
"""

import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import audit_stream
from app import db as db_mod
from app.auth import Principal, get_current_principal
from app.config import settings
from app.main import app
from app.routers.admin import _entity_id_valid, _entity_refs, list_audit_actions
from tests.conftest import SECCL_PLATFORM_SQL

SYSTEM = Principal(user_id="user_ops", permissions=["system"])


# ---- seed --------------------------------------------------------------------------------------


async def _tenant(session: AsyncSession, *, uid: str, name: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :u, :n) RETURNING id"
                ),
                {"u": uid, "n": name},
            )
        ).scalar_one()
    )


async def _login(session: AsyncSession, *, tenant_id: int, email: str, first: str) -> str:
    return str(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.users "
                    "  (uid, tenant_id, email, first_name, last_name, enabled) "
                    "VALUES (:uid, :t, :e, :f, 'Feed', true) RETURNING id"
                ),
                {"uid": f"usr_{uuid.uuid4().hex[:12]}", "t": tenant_id, "e": email, "f": first},
            )
        ).scalar_one()
    )


async def _event(
    session: AsyncSession,
    *,
    event_type: str,
    actor_sub: str,
    tenant_id: int | None = None,
    target_tenant_id: int | None = None,
    actor_id: str | None = None,
    method: str = "POST",
    route: str = "/firm/contacts",
    status: int = 200,
    when: str | None = None,
    path_params: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> int:
    """One stream row, written the way the middleware writes it (service_role INSERT)."""
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO audit.actions (event_type, tenant_id, target_tenant_id, actor_id, "
                    "  actor_sub, method, route, status, path_params, metadata, created_at) "
                    "VALUES (:et, :t, :tt, CAST(:a AS uuid), :sub, :m, :r, :s, "
                    "  CAST(:pp AS jsonb), CAST(:md AS jsonb), "
                    "  COALESCE(CAST(:w AS timestamptz), now())) RETURNING id"
                ),
                {
                    "et": event_type,
                    "t": tenant_id,
                    "tt": target_tenant_id,
                    "a": actor_id,
                    "sub": actor_sub,
                    "m": method,
                    "r": route,
                    "s": status,
                    "pp": json.dumps(path_params or {}),
                    "md": json.dumps(metadata or {}),
                    "w": when,
                },
            )
        ).scalar_one()
    )


@pytest_asyncio.fixture
async def seed(service_session: AsyncSession) -> dict[str, object]:
    """A firm (with a named login), a second firm, and a run-unique event-type namespace."""
    s = uuid.uuid4().hex[:8]
    firm = await _tenant(service_session, uid=f"feedfirm_{s}", name=f"Feed Firm {s}")
    other = await _tenant(service_session, uid=f"feedother_{s}", name=f"Feed Other {s}")
    actor = await _login(
        service_session, tenant_id=firm, email=f"alice_{s}@feed.test", first="Alice"
    )
    return {
        "s": s,
        "firm": firm,
        "firm_uid": f"feedfirm_{s}",
        "other": other,
        "other_uid": f"feedother_{s}",
        "actor": actor,
        "actor_sub": f"wos_feed_{s}",
    }


def _mine(page: dict[str, object], s: str) -> list[dict[str, object]]:
    """Only this run's rows — the DB is shared across the whole suite."""
    return [r for r in page["items"] if str(r["event_type"]).startswith(f"action.feed{s}")]  # type: ignore[union-attr]


# ---- shape: the envelope, the joins, the lookup -------------------------------------------------


@pytest.mark.asyncio
async def test_feed_joins_names_and_ships_the_tenant_lookup(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """One query joins the actor and BOTH tenant sides for display; the `tenants` lookup rides
    along so the filter dropdown never needs `tenants:read` (the caller holds `audit:read`)."""
    s = str(seed["s"])
    await _event(
        service_session,
        event_type=f"action.feed{s}-firm-create-contact",
        actor_sub=str(seed["actor_sub"]),
        actor_id=str(seed["actor"]),
        tenant_id=int(str(seed["firm"])),
        target_tenant_id=int(str(seed["other"])),
    )

    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}", limit=200
    )
    assert set(page.keys()) == {"items", "has_more", "tenants", "facets"}
    rows = _mine(page, s)
    assert len(rows) == 1
    e = rows[0]
    assert e["actor_email"] == f"alice_{s}@feed.test"
    assert e["actor_name"] == "Alice Feed"
    assert (e["tenant_uid"], e["tenant_name"]) == (seed["firm_uid"], f"Feed Firm {s}")
    assert (e["target_tenant_uid"], e["target_tenant_name"]) == (
        seed["other_uid"],
        f"Feed Other {s}",
    )
    by_uid = {t["uid"]: t["name"] for t in page["tenants"]}  # type: ignore[union-attr]
    assert by_uid[str(seed["firm_uid"])] == f"Feed Firm {s}"
    assert by_uid[str(seed["other_uid"])] == f"Feed Other {s}"


async def _set_theme(session: AsyncSession, tenant_id: int, theme: dict[str, object]) -> None:
    await session.execute(
        text("UPDATE auth.tenants SET theme = CAST(:th AS jsonb) WHERE id = :t"),
        {"th": json.dumps(theme), "t": tenant_id},
    )


@pytest.mark.asyncio
async def test_tenant_branding_icons_ride_both_sides(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """Each side of an event carries its firm's branding icon (`tenant_icon_url` /
    `target_tenant_icon_url`) so the trail can render firm chips without a second lookup — from
    BOTH stored theme shapes: the current per-portal `{client: {icon_url}}` and the legacy flat
    `{icon_url}` (the SQL mirror of `parse_theme`'s migration)."""
    s = str(seed["s"])
    firm, other = int(str(seed["firm"])), int(str(seed["other"]))
    await _set_theme(
        service_session, firm, {"v": 2, "client": {"icon_url": f"https://cdn.test/{s}/firm.png"}}
    )
    await _set_theme(service_session, other, {"icon_url": f"https://cdn.test/{s}/other.png"})
    await _event(
        service_session,
        event_type=f"action.feed{s}-icons",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=firm,
        target_tenant_id=other,
    )

    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}-icons", limit=200
    )
    rows = _mine(page, s)
    assert len(rows) == 1
    assert rows[0]["tenant_icon_url"] == f"https://cdn.test/{s}/firm.png"
    assert rows[0]["target_tenant_icon_url"] == f"https://cdn.test/{s}/other.png"


@pytest.mark.asyncio
async def test_oversized_inline_icon_is_withheld_never_truncated(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """A `data:` icon (the no-bucket branding fallback) can validly run to ~1.4M characters —
    repeated per row that is a response-size hazard, so anything over the cap ships as NULL
    (the UI's name-only chip). Withheld whole, never substring'd: a truncated data URI is a
    broken image, not a smaller one. An un-themed tenant is NULL too."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    huge = "data:image/png;base64," + "A" * 70_000
    await _set_theme(service_session, firm, {"v": 2, "client": {"icon_url": huge}})
    await _event(
        service_session,
        event_type=f"action.feed{s}-hugeicon",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=firm,
        target_tenant_id=int(str(seed["other"])),  # no theme at all on this side
    )

    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}-hugeicon", limit=200
    )
    rows = _mine(page, s)
    assert len(rows) == 1
    assert rows[0]["tenant_icon_url"] is None
    assert rows[0]["target_tenant_icon_url"] is None


@pytest.mark.asyncio
async def test_event_id_deep_link_is_scoped_by_tenant(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """`event=<id>` is the master-detail deep-link — one row by id — ANDed with the `tenant`
    scope: with no tenant filter it fetches the row, and naming a firm the event does not touch
    returns nothing rather than the row (a guessed id can never escape the operator's scope)."""
    s = str(seed["s"])
    ev = await _event(
        service_session,
        event_type=f"action.feed{s}-deeplink",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=int(str(seed["firm"])),
    )

    # No tenant scope: the id fetches exactly that row.
    got = await list_audit_actions(session=service_session, _p=SYSTEM, event=ev, limit=200)
    got_items = got["items"]
    assert isinstance(got_items, list)
    assert [r["id"] for r in got_items] == [ev]

    # Tenant scope naming the OWNING firm: still returned (the id is in scope).
    scoped = await list_audit_actions(
        session=service_session, _p=SYSTEM, event=ev, tenant=str(seed["firm_uid"]), limit=200
    )
    scoped_items = scoped["items"]
    assert isinstance(scoped_items, list)
    assert [r["id"] for r in scoped_items] == [ev]

    # Tenant scope naming ANOTHER firm: the scope ANDs with the id, so nothing comes back.
    miss = await list_audit_actions(
        session=service_session, _p=SYSTEM, event=ev, tenant=str(seed["other_uid"]), limit=200
    )
    assert miss["items"] == []


# ---- filters ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_filter_matches_acting_and_target_sides(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """A firm's slice of the stream includes proxied ops actions taken ON it — filtering by the
    firm's uid must surface the row whether the firm acted or was acted upon."""
    s = str(seed["s"])
    sub = str(seed["actor_sub"])
    firm, other = int(str(seed["firm"])), int(str(seed["other"]))
    acted = await _event(
        service_session, event_type=f"action.feed{s}-own", actor_sub=sub, tenant_id=firm
    )
    acted_on = await _event(
        service_session,
        event_type=f"action.feed{s}-proxied",
        actor_sub=sub,
        tenant_id=other,
        target_tenant_id=firm,
    )
    unrelated = await _event(
        service_session, event_type=f"action.feed{s}-elsewhere", actor_sub=sub, tenant_id=other
    )

    page = await list_audit_actions(
        session=service_session,
        _p=SYSTEM,
        tenant=str(seed["firm_uid"]),
        event_type=f"action.feed{s}",
        limit=200,
    )
    ids = {r["id"] for r in _mine(page, s)}
    assert ids == {acted, acted_on}
    assert unrelated not in ids


@pytest.mark.asyncio
async def test_after_is_scoped_by_the_tenant_filter_like_every_other_read(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """The live poll's keyset end, on the operations surface: newer rows only, and inside the firm
    scope the reader is looking at. The scope is the part worth pinning here — a poll that ignored
    `?tenant=` would tell an operator watching one firm that another firm's events are "new"."""
    s = str(seed["s"])
    sub = str(seed["actor_sub"])
    firm, other = int(str(seed["firm"])), int(str(seed["other"]))
    baseline = await _event(
        service_session, event_type=f"action.feed{s}-base", actor_sub=sub, tenant_id=firm
    )
    newer_here = await _event(
        service_session, event_type=f"action.feed{s}-newer", actor_sub=sub, tenant_id=firm
    )
    newer_elsewhere = await _event(
        service_session, event_type=f"action.feed{s}-other", actor_sub=sub, tenant_id=other
    )

    page = await list_audit_actions(
        session=service_session,
        _p=SYSTEM,
        tenant=str(seed["firm_uid"]),
        after=baseline,
        limit=200,
    )
    ids = {r["id"] for r in _mine(page, s)}
    assert ids == {newer_here}
    assert newer_elsewhere not in ids and baseline not in ids

    # Unscoped, the operator watching everything sees both.
    everything = await list_audit_actions(
        session=service_session, _p=SYSTEM, after=baseline, limit=200
    )
    assert {newer_here, newer_elsewhere} <= {r["id"] for r in _mine(everything, s)}


@pytest.mark.asyncio
async def test_actor_event_type_method_and_proxied_filters(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    await _event(
        service_session,
        event_type=f"action.feed{s}-firm-create-contact",
        actor_sub=str(seed["actor_sub"]),
        actor_id=str(seed["actor"]),
        tenant_id=firm,
        method="POST",
    )
    await _event(
        service_session,
        event_type=f"action.feed{s}-admin-update-tenant",
        actor_sub=f"wos_stranger_{s}",
        tenant_id=firm,
        target_tenant_id=int(str(seed["other"])),
        method="PATCH",
    )

    async def feed(**kw: object) -> list[dict[str, object]]:
        page = await list_audit_actions(
            session=service_session, _p=SYSTEM, event_type=f"action.feed{s}", limit=200, **kw
        )
        return _mine(page, s)

    # actor: substring over the WorkOS sub AND the joined email/name.
    assert {r["method"] for r in await feed(actor=f"wos_stranger_{s}")} == {"PATCH"}
    assert {r["method"] for r in await feed(actor=f"alice_{s}@feed")} == {"POST"}
    assert {r["method"] for r in await feed(actor="Alice Feed")} == {"POST"}

    # actor/method/tenant are comma-separated and ORed. A blank element (a stray comma) must not
    # become a filter for the empty substring, which `ILIKE '%%'` matches every row with.
    assert {r["method"] for r in await feed(actor=f"wos_stranger_{s},Alice Feed")} == {
        "PATCH",
        "POST",
    }
    assert {r["method"] for r in await feed(actor=f"wos_stranger_{s},,")} == {"PATCH"}

    # event_type is a PREFIX: a family filter, not exact-match.
    prefixed = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}-firm-", limit=200
    )
    assert {r["event_type"] for r in _mine(prefixed, s)} == {f"action.feed{s}-firm-create-contact"}

    assert {r["event_type"] for r in await feed(method="patch")} == {
        f"action.feed{s}-admin-update-tenant"
    }  # method is normalised to uppercase

    assert {r["target_tenant_id"] is not None for r in await feed(proxied=True)} == {True}
    assert {r["target_tenant_id"] is None for r in await feed(proxied=False)} == {True}


@pytest.mark.asyncio
async def test_date_window_is_since_inclusive_until_exclusive(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    sub = str(seed["actor_sub"])
    await _event(
        service_session,
        event_type=f"action.feed{s}-old",
        actor_sub=sub,
        tenant_id=firm,
        when="2026-01-01T00:00:00Z",
    )
    await _event(
        service_session,
        event_type=f"action.feed{s}-mid",
        actor_sub=sub,
        tenant_id=firm,
        when="2026-06-15T12:00:00Z",
    )

    windowed = await list_audit_actions(
        session=service_session,
        _p=SYSTEM,
        event_type=f"action.feed{s}",
        since="2026-06-01",
        until="2026-06-16",
        limit=200,
    )
    assert [r["event_type"] for r in _mine(windowed, s)] == [f"action.feed{s}-mid"]

    excluded = await list_audit_actions(
        session=service_session,
        _p=SYSTEM,
        event_type=f"action.feed{s}",
        until="2026-06-15",
        limit=200,
    )
    assert [r["event_type"] for r in _mine(excluded, s)] == [f"action.feed{s}-old"]

    # Unparseable dates are the caller's fault, not a 500 (`_parse_instant`, same as list_audit).
    for field in ("since", "until"):
        with pytest.raises(HTTPException) as exc:
            await list_audit_actions(session=service_session, _p=SYSTEM, **{field: "yesterday"})
        assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_like_wildcards_in_filters_stay_literal(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """A typed `%` must not widen a filter over the whole stream — the filters promise substring/
    prefix semantics on the operator's literal text, not a LIKE-pattern escape hatch."""
    s = str(seed["s"])
    await _event(
        service_session,
        event_type=f"action.feed{s}-wild",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=int(str(seed["firm"])),
    )
    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}", actor="%", limit=200
    )
    assert _mine(page, s) == []  # no actor contains a literal '%'


@pytest.mark.asyncio
async def test_contact_filter_matches_every_contact_naming_path(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """`?contact=<uid>` returns every event that NAMES the client — its own-record edit (the REAL
    route, `PATCH /firm/contacts/{contact_id}`), a contact id in the path or the allowlisted
    subject, and the contact_ids LIST — and nothing about another client or naming no client at all.

    The own-record case deliberately seeds the route/param shape a real request produces. It used to
    seed `/crm/contacts/{id}` with a bare `{id}` param, which no route in the app has ever used — so
    it only exercised a dead SQL branch and proved nothing about real own-record coverage."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    sub = str(seed["actor_sub"])
    cuid = f"con_target_{s}"
    cid = await _contact(
        service_session, tenant_id=firm, first="Target", surname="Client", uid=cuid
    )
    other = await _contact(service_session, tenant_id=firm, first="Other", surname="Client")

    async def ev(tag: str, **kw: object) -> int:
        return await _event(
            service_session, event_type=f"action.feed{s}-{tag}", actor_sub=sub, tenant_id=firm, **kw
        )

    want = {
        await ev("own", route="/firm/contacts/{contact_id}", path_params={"contact_id": cid}),
        await ev("path", route="/crm/contacts/{contact_id}/bank", path_params={"contact_id": cid}),
        await ev("subj-contact", metadata={"subject": {"contact_id": cid}}),
        await ev("subj-client", metadata={"subject": {"client_contact_id": cid}}),
        await ev("subj-participant", metadata={"subject": {"participant_contact_id": cid}}),
        await ev("subj-list", metadata={"subject": {"contact_ids": [999, cid]}}),
    }
    d1 = await ev("other", metadata={"subject": {"contact_id": other}})
    d2 = await ev("none", metadata={"subject": {}})

    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}", contact=cuid, limit=200
    )
    got = {r["id"] for r in _mine(page, s)}
    assert got == want
    assert d1 not in got and d2 not in got


@pytest.mark.asyncio
async def test_contact_scope_reaches_the_client_own_document_events(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """A client's trail includes the documents FILED UNDER them, not only the ones sent TO them.

    THE DEFECT. Most document activity names no contact: an upload, a view, an archive and a render
    all carry the document's `{uid}` and nothing else — only generate / send / notify / update /
    request-created capture a contact id. So a client's Events tab showed that a document had been
    SENT to them and never that one was uploaded for them or viewed, which reads as "this client has
    no document history" on a record whose Documents tab is full.

    The scope now reaches through `documents.document_contacts` — the same link the client-portal
    RLS branch reads to decide visibility, so this can never surface a document the client could not
    already open.
    """
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    sub = str(seed["actor_sub"])

    async def ev(et: str, **kw: object) -> int:
        return await _event(service_session, event_type=et, actor_sub=sub, tenant_id=firm, **kw)

    mine_uid = f"con_filed_{s}"
    mine = await _contact(
        service_session, tenant_id=firm, first="Ada", surname=f"Filed{s}", uid=mine_uid
    )
    theirs = await _contact(service_session, tenant_id=firm, first="Bo", surname=f"Other{s}")

    async def document_for(contact_id: int, uid: str) -> str:
        # `documents.documents.id` is a uuid, not a serial — the link table joins on it.
        doc_id = str(
            (
                await service_session.execute(
                    text(
                        "INSERT INTO documents.documents (tenant_id, uid, name, document_type, "
                        "  status) VALUES (:t, :u, :n, 'other', 'generated') RETURNING id"
                    ),
                    {"t": firm, "u": uid, "n": f"Report {uid}"},
                )
            ).scalar_one()
        )
        await service_session.execute(
            text(
                "INSERT INTO documents.document_contacts "
                "  (document_id, tenant_id, contact_id, role) "
                "VALUES (CAST(:d AS uuid), :t, :c, 'subject')"
            ),
            {"d": doc_id, "t": firm, "c": contact_id},
        )
        return uid

    my_doc = await document_for(mine, f"doc_mine_{s}")
    their_doc = await document_for(theirs, f"doc_theirs_{s}")

    # The events that carry NO contact id — the ones that were invisible.
    #
    # An UPLOAD is seeded in the shape a real upload actually produces, which an earlier version of
    # this test got wrong: `POST /documents` has no `{uid}` segment (the row does not exist when the
    # request arrives), so the middleware records `path_params = {}` and the created uids land in
    # `metadata.subject.document_uids` off the response. Seeding `path_params={"uid": …}` here made
    # the test pass against an input the route can never emit, while the real upload never reached
    # the client's feed at all.
    uploaded = await ev("document.uploaded", metadata={"subject": {"document_uids": [my_doc]}})
    viewed = await ev("document.viewed", path_params={"uid": my_doc})
    archived = await ev("document.archived", path_params={"uid": my_doc})
    # …another client's document (in the real upload shape too), and a TEMPLATE whose uid coincides.
    not_mine = await ev("document.uploaded", metadata={"subject": {"document_uids": [their_doc]}})
    template = await ev("template.version_published", path_params={"uid": my_doc})

    page = await list_audit_actions(session=service_session, _p=SYSTEM, contact=mine_uid, limit=200)
    got = {r["id"] for r in page["items"]}
    assert {uploaded, viewed, archived} <= got, (
        "an upload/view/archive of a document filed under this client must appear in their trail"
    )
    assert not_mine not in got, "another client's document must never reach this client's trail"
    assert template not in got, (
        "the arm is gated to the document family — a template uid that coincides must not match"
    )


@pytest.mark.asyncio
async def test_search_finds_a_row_by_the_client_name_not_just_the_machine_fields(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """`?q=` searches what a reader recognises a row by — above all, the CLIENT's name.

    THE POINT OF THE BOX. It used to filter on the `event_type` prefix, which is what the
    event-type picker beside it now does properly (whole vocabulary, several at once). So the box
    duplicated a control while the question people actually bring to an audit trail — "what happened
    to Margaret Cole" — had none. A contact id is not something anybody knows; the name is.

    Also pinned: it ORs over the machine fields (so a type pasted out of the detail panel works),
    and it ANDs with the other filters (so a search NARROWS the view rather than replacing it).
    """
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    sub = str(seed["actor_sub"])

    async def ev(et: str, **kw: object) -> int:
        return await _event(service_session, event_type=et, actor_sub=sub, tenant_id=firm, **kw)

    # A client whose name is unique to this run, and one who is not the subject of anything.
    # `_contact` is this file's own helper (defined below) — the same insert every other test uses.
    named_uid = f"con_q{s}"[:12]
    named = await _contact(
        service_session,
        tenant_id=firm,
        first="Marguerite",
        surname=f"Zeldabram{s}",
        uid=named_uid,
    )
    other = await _contact(
        service_session, tenant_id=firm, first="Quintin", surname=f"Otherperson{s}"
    )

    # The id arrives in a PATH param on one row and in a captured SUBJECT on another — both must be
    # searchable, because which field holds it is an implementation detail of the route.
    by_path = await ev("client.updated", path_params={"contact_id": str(named)})
    by_subject = await ev("document.sent", metadata={"subject": {"client_contact_id": str(named)}})
    # …and in the UID spelling, which is the only shape a client route records today: they became
    # uid-addressed, and the stream stores the param the route actually received. A serial-only
    # search matched nothing written since the rename, so the one control on the toolbar that
    # exists because an id is not something a reader knows was blind to the whole recent trail.
    by_path_uid = await ev("client.updated", path_params={"contact_uid": named_uid})
    by_subject_uid = await ev("document.sent", metadata={"subject": {"contact_uid": named_uid}})
    # …and a row naming a DIFFERENT client must not match.
    decoy = await ev("client.updated", path_params={"contact_id": str(other)})

    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, q=f"zeldabram{s}", limit=200
    )
    got = {r["id"] for r in page["items"]}
    assert got == {by_path, by_subject, by_path_uid, by_subject_uid}, (
        "the client's name must reach every id-carrying shape, in EITHER spelling"
    )
    assert decoy not in got

    # Case-insensitive and partial: a reader types part of a name, not the whole record.
    partial = await list_audit_actions(
        session=service_session, _p=SYSTEM, q=f"ZELDABRAM{s}"[:12], limit=200
    )
    assert {by_path, by_subject, by_path_uid, by_subject_uid} <= {r["id"] for r in partial["items"]}

    # The machine fields are in the same OR, so a pasted event type still works.
    by_type = await list_audit_actions(
        session=service_session, _p=SYSTEM, q="client.updated", limit=200
    )
    assert {by_path, decoy} <= {r["id"] for r in by_type["items"]}

    # AND, not OR, with the other filters: the search narrows the current view.
    narrowed = await list_audit_actions(
        session=service_session,
        _p=SYSTEM,
        q=f"zeldabram{s}",
        event_type="document.",
        limit=200,
    )
    assert {r["id"] for r in narrowed["items"]} == {by_subject, by_subject_uid}

    # Blank is no filter at all — never a filter for the empty string.
    unfiltered = await list_audit_actions(session=service_session, _p=SYSTEM, q="  ", limit=200)
    assert {by_path, by_subject, by_path_uid, by_subject_uid, decoy} <= {
        r["id"] for r in unfiltered["items"]
    }


@pytest.mark.asyncio
async def test_document_filter_matches_one_documents_events_only(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """`?document=<uid>` returns one document's events — the {uid} path param (gated to the
    document/shares families) and the reassign's superseded target — and NOT a template that
    happens to carry the same uid value. Scoped by the run-unique uid, no event-type prefix."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    sub = str(seed["actor_sub"])
    doc_uid = f"doc_{s}"

    async def ev(et: str, **kw: object) -> int:
        return await _event(service_session, event_type=et, actor_sub=sub, tenant_id=firm, **kw)

    want = {
        await ev("document.viewed", path_params={"uid": doc_uid}),
        await ev("document.sent", path_params={"uid": doc_uid}),
        await ev(
            "document_request.item_reassigned",
            metadata={"subject": {"target_document_uid": doc_uid}},
        ),
        # THE UPLOAD, which is how the document came to exist. Its uid cannot be a path param —
        # the row does not exist when `POST /documents` arrives — so it is captured off the
        # response as a LIST (one drop may carry several files). Without the `document_uids` arm a
        # document's Events tab began at its first LATER event, never at the upload that made it.
        await ev(
            "document.uploaded",
            metadata={"subject": {"document_uids": [doc_uid, f"doc_sibling_{s}"]}},
        ),
    }
    # A TEMPLATE event carrying the same uid VALUE — the family gate on the uid arm excludes it.
    decoy = await ev("template.version_published", path_params={"uid": doc_uid})
    # A document-family event for a DIFFERENT document. This is the other half of the gate and the
    # one that caught a real precedence bug: the family predicate is spliced in with AND, and `AND`
    # binds tighter than `OR`, so an un-parenthesised OR chain read as
    # `(uid = :document AND first_prefix) OR second_prefix OR …` and matched every document event in
    # the stream. One document's activity rail showed every document's, and answered 200 doing it.
    other_doc = await ev("document.sent", path_params={"uid": f"doc_other_{s}"})
    # An upload of OTHER documents: the list must be tested for membership, not merely for presence,
    # or every upload in the firm would appear on every document's tab.
    other_upload = await ev(
        "document.uploaded",
        metadata={"subject": {"document_uids": [f"doc_x_{s}", f"doc_y_{s}"]}},
    )

    page = await list_audit_actions(session=service_session, _p=SYSTEM, document=doc_uid, limit=200)
    got = {r["id"] for r in page["items"]}
    assert got == want
    assert other_upload not in got
    assert decoy not in got
    assert other_doc not in got


@pytest.mark.asyncio
async def test_document_family_filter_matches_documents_and_shares_only(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """`?document_family=true` narrows to the document family — the `action.documents-` and
    `action.shares-` prefixes together (what the old `documents.audit_log` feed carried), a
    two-prefix OR the single-prefix `event_type` filter cannot express — and composes with
    `contact` so a client's DOCUMENT events return without their tasks/instructions."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    sub = str(seed["actor_sub"])
    cuid = f"con_doc_{s}"
    cid = await _contact(service_session, tenant_id=firm, first="Dora", surname=f"Doc{s}", uid=cuid)

    async def ev(et: str, **kw: object) -> int:
        return await _event(service_session, event_type=et, actor_sub=sub, tenant_id=firm, **kw)

    # In the document family: a documents- event and a shares- event, both naming the client.
    doc_ev = await ev(
        "document.sent",
        path_params={"uid": f"doc_{s}"},
        metadata={"subject": {"contact_id": str(cid)}},
    )
    share_ev = await ev(
        "document_share.created",
        path_params={"uid": f"doc_{s}"},
        metadata={"subject": {"contact_id": str(cid)}},
    )
    # NOT in the family: a task on the same client — the family gate must exclude it.
    task_ev = await ev("task.created", metadata={"subject": {"contact_id": str(cid)}})

    fam = await list_audit_actions(
        session=service_session, _p=SYSTEM, document_family=True, limit=200
    )
    fam_ids = {r["id"] for r in fam["items"]}
    assert {doc_ev, share_ev} <= fam_ids
    assert task_ev not in fam_ids

    # Composed with `contact`: this client's DOCUMENT events only (never their task).
    scoped = await list_audit_actions(
        session=service_session, _p=SYSTEM, contact=cuid, document_family=True, limit=200
    )
    assert {r["id"] for r in scoped["items"]} == {doc_ev, share_ev}


# ---- entity resolution ---------------------------------------------------------------------------
# The stream stores raw ids; the read surface resolves them into CURRENT display labels
# (path_params AND metadata.subject), batched one query per entity kind per page.


async def _contact(
    session: AsyncSession, *, tenant_id: int, first: str, surname: str, uid: str | None = None
) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :uid, 'individual', :f, :s) RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "uid": uid or f"con_{uuid.uuid4().hex[:12]}",
                    "f": first,
                    "s": surname,
                },
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_entities_resolve_ids_to_labels_from_path_params_and_subject(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """The product question — "what client? what user login?" — answered per event: the
    contact_id path param resolves to the client's name, the subject's user_id to the login's
    email, and both ride the item as `entities` rows."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    con_uid = f"con_feed{s}"
    cid = await _contact(
        service_session, tenant_id=firm, first="Margaret", surname=f"Cole{s}", uid=con_uid
    )
    await _event(
        service_session,
        event_type=f"action.feed{s}-firm-link-contact-user",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=firm,
        route="/firm/contacts/{contact_id}/users",
        path_params={"contact_id": str(cid)},
        metadata={"subject": {"user_id": str(seed["actor"]), "capacity": "authorised"}},
    )

    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}-firm-link", limit=200
    )
    rows = _mine(page, s)
    assert len(rows) == 1
    entities = rows[0]["entities"]
    user_uid = str(
        (
            await service_session.execute(
                text("SELECT uid FROM auth.users WHERE id = CAST(:id AS uuid)"),
                {"id": str(seed["actor"])},
            )
        ).scalar_one()
    )
    # `id` is what the stream RECORDED (the serial the route was called with); `uid` is how a
    # portal addresses that record today. A contact's route is serial-typed, so the two differ --
    # and the entity chip can only be linked because the resolver sends both. A user event records
    # the UUID used by the API while the team route uses the login's public uid, so those differ
    # too.
    assert entities == [
        {"kind": "contact", "id": str(cid), "uid": con_uid, "label": f"Margaret Cole{s}"},
        {
            "kind": "user",
            "id": str(seed["actor"]),
            "uid": user_uid,
            "label": f"alice_{s}@feed.test",
        },
    ]


@pytest.mark.asyncio
async def test_entities_resolve_a_transaction_subject(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """The `transaction` kind: a captured `transaction_id` resolves to WHOSE money moved, what
    kind of movement it was, and how much.

    A transaction has no name, so the client is the identity — "Deposit 1500" is what a thousand
    rows of a book look like. Three things are asserted about the label beyond the join, each of
    which was wrong before: the type is rendered as WORDS (`docs/design-language.md` — a stored
    enum is never printed raw), the amount is rendered to TWO places (the column is 4dp, so the
    old label read "£1500.0000"), and the CURRENCY is the book's rather than a hardcoded `£`.
    """
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    contact_id = await _contact(service_session, tenant_id=firm, first="Margaret", surname="Cole")
    txn_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO platform.transactions "
                    "(tenant_id, contact_id, source_system, source_transaction_id, type, amount, "
                    " currency) "
                    "VALUES (:t, :c, 'seccl', :src, 'deposit', 1500, 'GBP') RETURNING id"
                ),
                {"t": firm, "c": contact_id, "src": f"txn-{s}"},
            )
        ).scalar_one()
    )
    await _event(
        service_session,
        event_type=f"action.feed{s}-contributions-classify-transaction",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=firm,
        route="/contributions/from-transaction",
        metadata={"subject": {"transaction_id": txn_id}},
    )

    page = await list_audit_actions(
        session=service_session,
        _p=SYSTEM,
        event_type=f"action.feed{s}-contributions-classify",
        limit=200,
    )
    rows = _mine(page, s)
    assert len(rows) == 1
    # `uid` is how a chip would ADDRESS the row. `platform.transactions` has no uid column — a
    # movement is not something a URL names — so the resolver falls back to the recorded serial,
    # and the UI renders such a chip unlinked.
    assert rows[0]["entities"] == [
        {
            "kind": "transaction",
            "id": str(txn_id),
            "uid": str(txn_id),
            "label": "Margaret Cole - Deposit GBP 1500.00",
        },
    ]


@pytest.mark.asyncio
async def test_entities_resolve_a_conversation_to_the_client(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """A chat send names WHO it was sent to, never the text: the `conversation_id` path param
    resolves through the 1:1 messaging.conversations → crm.contacts join to the client's name."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    cid = await _contact(service_session, tenant_id=firm, first="Margaret", surname=f"Cole{s}")
    convo_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO messaging.conversations (tenant_id, contact_id) "
                    "VALUES (:t, :c) RETURNING id"
                ),
                {"t": firm, "c": cid},
            )
        ).scalar_one()
    )
    # The DEFAULT minted it (0113): no INSERT in app/messaging.py names `uid`, so this reads back
    # what the database chose rather than asserting a value the test could not know.
    convo_uid = str(
        (
            await service_session.execute(
                text("SELECT uid FROM messaging.conversations WHERE id = :i"), {"i": convo_id}
            )
        ).scalar_one()
    )
    by_serial = await _event(
        service_session,
        event_type=f"action.feed{s}-messaging-messaging-conversation-send",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=firm,
        route="/messaging/conversations/{conversation_id}/messages",
        path_params={"conversation_id": str(convo_id)},
    )
    # …and the UID spelling, which is what every messaging route records TODAY — the seven
    # `{conversation_uid}` params (send, read, attachments) all write this shape, and the serial one
    # above only exists on rows predating the rename.
    #
    # THIS IS THE SHAPE THAT 500s IF THE RESOLVER IS WRONG, not the one that renders a bare id: the
    # resolver cast `:ids` to `bigint[]`, so one text uid anywhere in the page fails the statement
    # and the whole audit feed answers 500 — for every reader, not only the row concerned.
    by_uid = await _event(
        service_session,
        event_type=f"action.feed{s}-messaging-messaging-conversation-read",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=firm,
        route="/messaging/conversations/{conversation_uid}/read",
        path_params={"conversation_uid": convo_uid},
    )

    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}-messaging", limit=200
    )
    rows = {r["id"]: r for r in _mine(page, s)}
    assert set(rows) == {by_serial, by_uid}
    # The SAME chip from either spelling: same kind, same address, same client name. Only the `id`
    # differs, because that is the handle the row itself recorded.
    chip = {"kind": "conversation", "uid": convo_uid, "label": f"Margaret Cole{s}"}
    assert rows[by_serial]["entities"] == [{**chip, "id": str(convo_id)}]
    assert rows[by_uid]["entities"] == [{**chip, "id": convo_uid}]
    # The address is NOT the serial: that is the whole point of the column 0112 added.
    assert convo_uid != str(convo_id)


@pytest.mark.asyncio
async def test_feed_hides_ui_state_events_but_keeps_them_in_the_stream(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """A WS-ticket / read-state event stays in audit.actions (forensic) but never appears in the
    Events feed — the "keep in the log, hide from the feed" split (ROUTINE_EVENT_TYPES)."""
    firm = int(str(seed["firm"]))
    await _event(
        service_session,
        event_type="notification.stream_opened",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=firm,
        route="/notifications/ticket",
    )
    # Even queried for exactly that type, the feed returns nothing (every such row is hidden).
    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type="notification.stream_opened", limit=200
    )
    assert page["items"] == []
    # But the row is in the raw table.
    n = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM audit.actions "
                "WHERE event_type = 'notification.stream_opened' AND tenant_id = :t"
            ),
            {"t": firm},
        )
    ).scalar_one()
    assert n >= 1


@pytest.mark.asyncio
async def test_entities_resolve_previous_ids_too(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """`metadata.previous` (the PREVIOUS_FIELDS before-value capture) is an entity source like
    the subject: on a mandate change BOTH models — the one moved FROM and the one moved TO —
    resolve to labels, so the UI can render "Cautious → Balanced"."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    provider = str(
        (
            await service_session.execute(
                text(SECCL_PLATFORM_SQL),
                {"n": f"FeedPlatform {s}"},
            )
        ).scalar_one()
    )

    async def model(name: str) -> int:
        return int(
            (
                await service_session.execute(
                    text(
                        "INSERT INTO platform.models (tenant_id, provider, name) "
                        "VALUES (:t, :p, :n) RETURNING id"
                    ),
                    {"t": firm, "p": provider, "n": name},
                )
            ).scalar_one()
        )

    old_model = await model(f"Cautious {s}")
    new_model = await model(f"Balanced {s}")
    await _event(
        service_session,
        event_type=f"action.feed{s}-crm-set-account-model",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=firm,
        method="PUT",
        route="/crm/accounts/{account_id}/model",
        path_params={"account_id": "999999999"},  # deleted account: null label, still a row
        metadata={
            "subject": {"model_id": new_model},
            "previous": {"model_id": old_model},
        },
    )

    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}-crm-set", limit=200
    )
    rows = _mine(page, s)
    assert len(rows) == 1

    # The deleted account resolves to nothing, so it carries no uid either — the UI renders it
    # unlinked rather than pointing at an address that no longer exists.
    #
    # A model DOES carry its real uid. This used to assert the uid was the id, on the stated
    # grounds that "no model route publishes one" — which was not true (there are six
    # `{model_uid}` params) and was only being satisfied because the resolver selected no `uid`
    # and `_resolve_entities` fell back to the id. The chip could therefore only link to an
    # address the routes no longer accept.
    async def uid_of(model_id: int) -> str:
        return str(
            (
                await service_session.execute(
                    text("SELECT uid FROM platform.models WHERE id = :i"), {"i": model_id}
                )
            ).scalar_one()
        )

    assert rows[0]["entities"] == [
        {"kind": "account", "id": "999999999", "uid": None, "label": None},
        {
            "kind": "model",
            "id": str(new_model),
            "uid": await uid_of(new_model),
            "label": f"Balanced {s}",
        },
        {
            "kind": "model",
            "id": str(old_model),
            "uid": await uid_of(old_model),
            "label": f"Cautious {s}",
        },
    ]


@pytest.mark.asyncio
async def test_entities_deleted_or_unknown_id_keeps_a_null_label(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """A deleted/never-existed id still ships as an entity — with `label: null`, so the UI shows
    the bare id (honest about what no longer exists) rather than dropping the reference."""
    s = str(seed["s"])
    await _event(
        service_session,
        event_type=f"action.feed{s}-firm-update-contact",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=int(str(seed["firm"])),
        route="/firm/contacts/{contact_id}",
        method="PATCH",
        path_params={"contact_id": "999999999"},
    )
    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}-firm-update", limit=200
    )
    rows = _mine(page, s)
    assert rows[0]["entities"] == [
        {"kind": "contact", "id": "999999999", "uid": None, "label": None}
    ]


@pytest.mark.asyncio
async def test_entities_generic_uid_resolves_by_router_tag(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """The bare `{uid}` path param is ambiguous across routers — the event type's tag picks the
    table (tasks-… → tasks.tasks)."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    task_uid = f"tsk_{s}"
    await service_session.execute(
        text(
            "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, assignee_user_id, "
            "  title, created_by) "
            "VALUES (:uid, :t, 'user', CAST(:u AS uuid), :title, CAST(:u AS uuid))"
        ),
        {"uid": task_uid, "t": firm, "u": str(seed["actor"]), "title": f"Chase the P60 {s}"},
    )
    event_id = await _event(
        service_session,
        event_type="task.completed",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=firm,
        route="/tasks/{uid}/complete",
        path_params={"uid": task_uid},
    )
    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type="task.completed", limit=200
    )
    mine = [r for r in page["items"] if r["id"] == event_id]  # type: ignore[union-attr]
    assert mine[0]["entities"] == [
        {"kind": "task", "id": task_uid, "uid": task_uid, "label": f"Chase the P60 {s}"}
    ]


@pytest.mark.asyncio
async def test_a_notification_rule_event_resolves_to_the_rule_it_changed(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """The row that sent the whole event-content review back for a second pass.

    `PUT /firm/notification-rules/{uid}` rendered `uid nrule_8f0c26e854f24baba2c9296ef6ae5a1d` and
    named nothing, so a firm reading its own trail could not see WHICH notice had been changed —
    and a rule is exactly the kind of record a compliance reader is checking up on. Pinned
    end to end rather than at the resolver, because the three halves rot separately: the
    `{uid}` resource map, the label query, and the value guard that decides a prefixed uid is
    resolvable at all.
    """
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    rule_uid = f"nrule_{s}"
    await service_session.execute(
        text(
            "INSERT INTO notifications.rules (uid, tenant_id, trigger_key, audience, label, "
            "  title, body) "
            "VALUES (:uid, :t, 'task.assigned', 'adviser', :label, :title, 'b')"
        ),
        {"uid": rule_uid, "t": firm, "label": f"When a task is assigned {s}", "title": "New task"},
    )
    event_id = await _event(
        service_session,
        event_type="notification_rule.updated",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=firm,
        route="/firm/notification-rules/{uid}",
        method="PUT",
        path_params={"uid": rule_uid},
    )
    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type="notification_rule.updated", limit=200
    )
    mine = [r for r in page["items"] if r["id"] == event_id]  # type: ignore[union-attr]
    assert mine[0]["entities"] == [
        {
            "kind": "notification_rule",
            "id": rule_uid,
            "uid": rule_uid,
            "label": f"When a task is assigned {s}",
        }
    ]


@pytest.mark.asyncio
async def test_entity_resolution_is_batched_not_per_row(
    service_session: AsyncSession, seed: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No N+1: a page referencing many contacts/users resolves with ONE query per entity kind —
    page + tenants-lookup + one per kind, counted over the real session."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    cids = [
        await _contact(service_session, tenant_id=firm, first=f"C{i}", surname=f"Batch{s}")
        for i in range(3)
    ]
    for cid in cids:
        await _event(
            service_session,
            event_type=f"action.feed{s}-firm-link-contact-user",
            actor_sub=str(seed["actor_sub"]),
            tenant_id=firm,
            route="/firm/contacts/{contact_id}/users",
            path_params={"contact_id": str(cid)},
            metadata={"subject": {"user_id": str(seed["actor"])}},
        )

    executed: list[str] = []
    original_execute = service_session.execute

    async def counting_execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
        executed.append(str(statement))
        return await original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(service_session, "execute", counting_execute)
    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}-firm-link", limit=200
    )
    rows = _mine(page, s)
    assert len(rows) == 3
    labels = {e["label"] for r in rows for e in r["entities"] if e["kind"] == "contact"}
    assert labels == {f"C0 Batch{s}", f"C1 Batch{s}", f"C2 Batch{s}"}
    # 1 page query + 1 tenants lookup + 1 per kind present (contact, user) - never per row.
    # The tenants lookup is FIRST-PAGE ONLY, so "Load more" does not pay for it. The facets
    # scan is opt-in (`?facets=true`) and not in this count either.
    assert len(executed) == 4, "\n---\n".join(executed)

    # Opting in costs exactly ONE more query, whatever the page holds: a fixed cost paid once on
    # page entry, not a per-row one. That is the whole reason the facets are a single windowed CTE
    # with two aggregate sub-selects rather than a query per list.
    executed.clear()
    await list_audit_actions(
        session=service_session,
        _p=SYSTEM,
        event_type=f"action.feed{s}-firm-link",
        limit=200,
        facets=True,
    )
    assert len(executed) == 5, "\n---\n".join(executed)


# ---- the cursor ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cursor_walks_newest_first_without_gaps_or_repeats(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """`before` is keyset on the monotonic id; `has_more` comes from a one-row over-fetch, so the
    last page says so even when it happens to be exactly full."""
    s = str(seed["s"])
    ids = [
        await _event(
            service_session,
            event_type=f"action.feed{s}-walk",
            actor_sub=str(seed["actor_sub"]),
            tenant_id=int(str(seed["firm"])),
        )
        for _ in range(4)
    ]

    async def page(before: int | None) -> dict[str, object]:
        return await list_audit_actions(
            session=service_session,
            _p=SYSTEM,
            event_type=f"action.feed{s}-walk",
            limit=2,
            before=before,
        )

    first = await page(None)
    assert [r["id"] for r in _mine(first, s)] == [ids[3], ids[2]]
    assert first["has_more"] is True

    second = await page(ids[2])
    assert [r["id"] for r in _mine(second, s)] == [ids[1], ids[0]]
    assert second["has_more"] is False  # exactly full, yet correctly final

    assert _mine(await page(ids[0]), s) == []


@pytest.mark.asyncio
async def test_limit_is_clamped(service_session: AsyncSession, seed: dict[str, object]) -> None:
    s = str(seed["s"])
    for _ in range(2):
        await _event(
            service_session,
            event_type=f"action.feed{s}-clamp",
            actor_sub=str(seed["actor_sub"]),
            tenant_id=int(str(seed["firm"])),
        )
    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}-clamp", limit=0
    )
    assert len(_mine(page, s)) == 1  # limit=0 clamps to 1, not "everything"


# ---- the facets: the toolbar's VOCABULARY, and why it ignores the filters ------------------------
# The bug this exists for: every dropdown's options used to be derived from the LOADED rows, and
# most of these filters are applied by the SERVER — so picking an actor returned only their events
# and the event-type dropdown then offered only the types that actor produced. The value you wanted
# was gone, and the only way back was to clear a filter whose effect you could no longer see. So the
# server states the vocabulary of the caller's scope directly, independently of every filter.
#
# Every test below passes `facets=True` explicitly, because the block is OPT-IN: the surface asks
# once on page entry and no later fetch asks again. A test that forgot to opt in would read
# `facets: None` and fail at `_facets`, which is the shape of failure we want — never a silent skip.


def _facets(page: dict[str, object]) -> dict[str, list[str]]:
    """The `facets` block, shape-checked: `{event_types, actors}`, both lists of strings."""
    got = page["facets"]
    assert isinstance(got, dict), got
    assert set(got.keys()) == {"event_types", "actors"}
    return {
        "event_types": [str(v) for v in got["event_types"]],
        "actors": [str(v) for v in got["actors"]],
    }


@pytest.mark.asyncio
async def test_facets_list_the_distinct_types_and_actors_in_scope(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """The vocabulary of the caller's slice: every distinct event type, and every distinct actor as
    the DISPLAY name the actor filter matches — name else email else sub, `actorName`'s precedence,
    so a chosen option always matches the rows it came from. Both sorted, for a stable dropdown."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    named_sub, bare_sub = str(seed["actor_sub"]), f"wos_nameless_{s}"
    await _event(
        service_session,
        event_type=f"action.feed{s}-alpha",
        actor_sub=named_sub,
        actor_id=str(seed["actor"]),  # joins to a login with a name
        tenant_id=firm,
    )
    await _event(  # same actor, a second type — the type list is DISTINCT, not one per row
        service_session,
        event_type=f"action.feed{s}-beta",
        actor_sub=named_sub,
        actor_id=str(seed["actor"]),
        tenant_id=firm,
    )
    await _event(  # no actor_id: nothing to join, so the sub IS the display name
        service_session, event_type=f"action.feed{s}-alpha", actor_sub=bare_sub, tenant_id=firm
    )

    page = await list_audit_actions(
        session=service_session, _p=SYSTEM, tenant=str(seed["firm_uid"]), limit=200, facets=True
    )
    facets = _facets(page)
    assert {f"action.feed{s}-alpha", f"action.feed{s}-beta"} <= set(facets["event_types"])
    # The named login resolves to its full name, never its sub; the unjoined row falls back.
    assert "Alice Feed" in facets["actors"]
    assert bare_sub in facets["actors"]
    assert named_sub not in facets["actors"]
    # Sorted both, so the dropdown order is stable across requests.
    assert facets["event_types"] == sorted(facets["event_types"])
    assert facets["actors"] == sorted(facets["actors"])


@pytest.mark.asyncio
async def test_facets_do_not_shrink_when_a_filter_is_applied(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """THE REGRESSION THIS WHOLE CHANGE EXISTS FOR. Applying a filter narrows the ROWS and must
    leave the CHOICES untouched: with an event_type AND an actor filter set — each of which the
    server applies, so the page comes back holding one type and one actor — the facets are byte-for
    -byte what they were unfiltered. Otherwise the only way back to the value you wanted is to
    clear a filter whose effect you cannot see."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    other_sub = f"wos_second_{s}"
    await _event(
        service_session,
        event_type=f"action.feed{s}-one",
        actor_sub=str(seed["actor_sub"]),
        actor_id=str(seed["actor"]),
        tenant_id=firm,
    )
    await _event(
        service_session, event_type=f"action.feed{s}-two", actor_sub=other_sub, tenant_id=firm
    )

    async def page(**kw: object) -> dict[str, object]:
        return await list_audit_actions(
            session=service_session,
            _p=SYSTEM,
            tenant=str(seed["firm_uid"]),
            limit=200,
            facets=True,
            **kw,
        )

    wide = _facets(await page())
    assert {f"action.feed{s}-one", f"action.feed{s}-two"} <= set(wide["event_types"])
    assert {"Alice Feed", other_sub} <= set(wide["actors"])

    # One filter at a time…
    assert _facets(await page(event_type=f"action.feed{s}-one")) == wide
    assert _facets(await page(actor="Alice Feed")) == wide
    # …and the combination that used to leave one option in each dropdown.
    narrowed = await page(event_type=f"action.feed{s}-one", actor="Alice Feed", method="POST")
    assert _facets(narrowed) == wide
    # The ROWS did narrow — the filters still work; it is only the vocabulary that holds.
    assert {r["event_type"] for r in _mine(narrowed, s)} == {f"action.feed{s}-one"}

    # A date window that excludes everything is the sharpest form of the same question: an empty
    # page must still offer the full vocabulary, or a mis-set date range strands the operator.
    empty = await page(since="2099-01-01")
    assert empty["items"] == []
    assert _facets(empty) == wide


@pytest.mark.asyncio
async def test_facets_are_opt_in_and_never_inferred_from_the_cursor(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """OPT-IN, and deliberately NOT derived from `before`.

    Not asked for ⇒ not computed (`facets: null`), so the extra windowed scan is never a cost the
    common request pays — and every request but the first IS the common one, because the toolbar
    applies a filter in place rather than navigating.

    The cursor is the wrong signal for it, which is why this test asserts both halves: a filter
    applied in place re-reads page ONE with no cursor, so `before is None` would recompute the
    vocabulary on exactly the requests whose contract is that it does not change — and a caller
    that genuinely wants it WITH a cursor still gets it. The `tenants` lookup keeps the cursor
    test (it has no such second reader), which is why the two are asserted apart here."""
    s = str(seed["s"])
    ids = [
        await _event(
            service_session,
            event_type=f"action.feed{s}-cursorfacet",
            actor_sub=str(seed["actor_sub"]),
            tenant_id=int(str(seed["firm"])),
        )
        for _ in range(2)
    ]

    async def page(**kw: object) -> dict[str, object]:
        # Scoped to this run's firm, which no other test writes to — so the facet lists can be
        # asserted EXACTLY. Unscoped they would be the whole shared suite's vocabulary, which is
        # correct behaviour (the facets ignore `event_type`, so the marker cannot narrow them) and
        # useless to assert against.
        return await list_audit_actions(
            session=service_session,
            _p=SYSTEM,
            tenant=str(seed["firm_uid"]),
            event_type=f"action.feed{s}-cursorfacet",
            limit=1,
            **kw,
        )

    # The default is off — a first page that did not ask gets nothing, cursor or no cursor.
    assert (await page())["facets"] is None
    assert (await page(facets=False))["facets"] is None

    # Asked for, on the first page: the vocabulary of the scope.
    assert _facets(await page(facets=True))["event_types"] == [f"action.feed{s}-cursorfacet"]

    # Asked for WITH a cursor: still computed. The facets answer "what may this caller filter by",
    # a question the page position does not change — and the window is taken over the caller's
    # whole recent scope, never from the cursor, so the answer is the same one.
    deep = await page(facets=True, before=ids[1])
    assert _facets(deep)["event_types"] == [f"action.feed{s}-cursorfacet"]
    assert deep["tenants"] == []  # the cursor still suppresses the FIRM lookup — separate rule


@pytest.mark.asyncio
async def test_facets_respect_the_tenant_scope(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """The facets are computed over exactly the rows the caller may read — the SAME scope predicate
    the page applies (hoisted into one local and handed to both), so another firm's types and
    actors can never appear in a firm-scoped operator view."""
    s = str(seed["s"])
    firm, other = int(str(seed["firm"])), int(str(seed["other"]))
    foreign_sub = f"wos_foreign_{s}"
    await _event(
        service_session,
        event_type=f"action.feed{s}-mine",
        actor_sub=f"wos_mine_{s}",
        tenant_id=firm,
    )
    await _event(
        service_session,
        event_type=f"action.feed{s}-theirs",
        actor_sub=foreign_sub,
        tenant_id=other,
    )

    mine = _facets(
        await list_audit_actions(
            session=service_session, _p=SYSTEM, tenant=str(seed["firm_uid"]), limit=200, facets=True
        )
    )
    assert f"action.feed{s}-mine" in mine["event_types"]
    assert f"action.feed{s}-theirs" not in mine["event_types"]
    assert foreign_sub not in mine["actors"]

    # Unscoped, Graphene operations see both — the scope is the filter, not a hidden narrowing.
    everything = _facets(
        await list_audit_actions(session=service_session, _p=SYSTEM, limit=200, facets=True)
    )
    assert {f"action.feed{s}-mine", f"action.feed{s}-theirs"} <= set(everything["event_types"])


@pytest.mark.asyncio
async def test_facets_never_offer_a_feed_hidden_type(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """ROUTINE_EVENT_TYPES (WS tickets, read/dismiss state) never reach the feed, so offering
    one as a filterable option would be a dropdown entry that always yields zero rows."""
    await _event(
        service_session,
        event_type="notification.stream_opened",
        actor_sub=str(seed["actor_sub"]),
        tenant_id=int(str(seed["firm"])),
        route="/notifications/ticket",
    )
    facets = _facets(
        await list_audit_actions(
            session=service_session, _p=SYSTEM, tenant=str(seed["firm_uid"]), limit=200, facets=True
        )
    )
    assert "notification.stream_opened" not in facets["event_types"]


@pytest.mark.asyncio
async def test_facets_window_is_the_recent_stream_not_all_history(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """The stated bound: the facets describe the most recent `FACET_WINDOW` rows of the caller's
    scope, not all history — `audit.actions` is append-only and unbounded, so an un-windowed
    DISTINCT would get slower for ever. Proven with a window of 1 rather than 5000 rows of seed:
    the newest row's type is offered and the older one's is not. Nothing is LOST to a reader — the
    client merges the loaded rows' own options on top of this seed (mergeOptions), which is why a
    deliberate bound here is safe."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    sub = str(seed["actor_sub"])
    await _event(service_session, event_type=f"action.feed{s}-older", actor_sub=sub, tenant_id=firm)
    await _event(service_session, event_type=f"action.feed{s}-newer", actor_sub=sub, tenant_id=firm)

    facets = await audit_stream.action_facets(
        service_session,
        scope_sql="(t.uid = :tenant OR tt.uid = :tenant)",
        scope_params={"tenant": str(seed["firm_uid"])},
        window=1,
    )
    assert facets["event_types"] == [f"action.feed{s}-newer"]

    # The default is the documented 5000, and it is applied AFTER the scope/exclusions — so the
    # window is the most recent N rows the caller may actually READ, never N rows of hidden noise.
    assert audit_stream.FACET_WINDOW == 5000


# ---- the gate, over real HTTP -------------------------------------------------------------------
# RLS is deliberately irrelevant here: the table is service_role-only (0041) and admin_session
# reads as service_role — the access control IS the permission matrix below, which also exercises
# admin_session's coarse operations gate (a firm principal never opens it, audit:read or not).


@pytest_asyncio.fixture
async def http(db: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "SessionLocal", maker)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


ActAs = Callable[[str, list[str]], None]


@pytest.fixture
def gate_world(super_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[ActAs]:
    """An operations tenant (registered as the core WorkOS environment, so `admin_session`'s
    coarse gate resolves it) and a firm, one login each — seeded and COMMITTED via the sync
    superuser engine so the HTTP app's own sessions can see the rows. Yields an `act(who,
    perms)` installer."""
    s = uuid.uuid4().hex[:8]
    core_client_id = f"client_core_{s}"
    monkeypatch.setattr(settings, "workos_client_id", core_client_id)

    subs: dict[str, str] = {}
    with super_engine.connect() as c:

        def mk_tenant(uid: str, name: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :u, :n) RETURNING id"
                    ),
                    {"u": uid, "n": name},
                ).scalar_one()
            )

        ops = mk_tenant(f"feedops_{s}", f"Feed Ops {s}")
        # App-only portal (0107): admin_session requires a session classified portal='operations'
        # (unless is_system), so the core environment row declares the operations application and
        # the ops login holds an ACTIVE operations membership for it.
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  operations_client_id) VALUES (:t, 'production', :c, :oapp)"
            ),
            {"t": ops, "c": core_client_id, "oapp": f"app_gate_ops_{s}"},
        )
        firm = mk_tenant(f"feedgatefirm_{s}", f"Feed Gate Firm {s}")
        # The firm login is a REAL platform session (its own app + membership) — so the matrix's
        # firm refusal proves admin_session's coarse gate, not merely an unresolved portal.
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  platform_client_id) VALUES (:t, 'dev', :env, :papp)"
            ),
            {"t": firm, "env": f"env_gate_firm_{s}", "papp": f"app_gate_firm_{s}"},
        )
        apps = {"ops": f"app_gate_ops_{s}", "firm": f"app_gate_firm_{s}"}
        for who, tenant_id, portal in (("ops", ops, "operations"), ("firm", firm, "platform")):
            sub = f"wos_gate_{who}_{s}"
            uid = c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, "
                    "  first_name, last_name, enabled) "
                    "VALUES (:uid, :t, :e, :w, :f, 'Gate', true) RETURNING id"
                ),
                {
                    "uid": f"usr_gate_{who}_{s}",
                    "t": tenant_id,
                    "e": f"{who}_{s}@gate.test",
                    "w": sub,
                    "f": who.title(),
                },
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships "
                    "  (user_id, tenant_id, portal, status) VALUES (:u, :t, :p, 'active')"
                ),
                {"u": uid, "t": tenant_id, "p": portal},
            )
            subs[who] = sub
        c.commit()

    def act(who: str, perms: list[str]) -> None:
        def override() -> Principal:
            # client_id names the application the session signed in through (0107) — what
            # classifies the portal claim when admin_session mints resolve_claims.
            return Principal(user_id=subs[who], permissions=list(perms), client_id=apps[who])

        app.dependency_overrides[get_current_principal] = override

    yield act
    app.dependency_overrides.pop(get_current_principal, None)


@pytest.mark.asyncio
async def test_gate_matrix(http: AsyncClient, gate_world: ActAs) -> None:
    # system bypasses the granular permission (and passes the coarse gate by definition).
    gate_world("ops", ["system"])
    assert (await http.get("/admin/audit/actions")).status_code == 200

    # an operations principal needs audit:read — holding it opens the stream…
    gate_world("ops", ["audit:read"])
    r = await http.get("/admin/audit/actions")
    assert r.status_code == 200
    assert set(r.json().keys()) == {"items", "has_more", "tenants", "facets"}
    # …with the facets OFF by default. The opt-in lives on the query string, so the wire name is
    # pinned here rather than only through the function signature: `facets=true` is what the four
    # Events loaders send once on page entry, and a rename would silently leave every filter bar
    # back on its old row-derived options — a degradation no status code reports.
    assert r.json()["facets"] is None
    on = await http.get("/admin/audit/actions?facets=true")
    assert on.status_code == 200
    assert set(on.json()["facets"].keys()) == {"event_types", "actors"}

    # …and not holding it is a 403 from the granular gate.
    gate_world("ops", ["tenants:read"])
    assert (await http.get("/admin/audit/actions")).status_code == 403

    # a FIRM principal is refused by admin_session's coarse gate even WITH audit:read — the
    # stream is cross-tenant evidence, and no firm-scoped role can be granted a way in.
    gate_world("firm", ["audit:read"])
    assert (await http.get("/admin/audit/actions")).status_code == 403


# ── the tag (entity-kind) filter ────────────────────────────────────────────────────────────────
#
# The toolbar's tag picker asks "show me events touching a document / a client / an account". It was
# CLIENT-side, narrowing only the loaded page, which answered a smaller question than the one asked.
# Server-side it has to reproduce the read side's own resolution: a row carries a kind when one of
# the id-shaped field NAMES that mean it is a key of `path_params` / `metadata.subject` /
# `metadata.previous`, or when the generic `{uid}` param is present and the event type's router tag
# names that kind. Presence of the KEY is the test — the same keys `_resolve_entities` turns into
# the chips a reader sees, so a row is filterable exactly when it is labellable.


@pytest.mark.asyncio
async def test_tag_filter_matches_the_field_names_that_mean_a_kind(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    # A contact named in the captured subject, an account named in a path param, and a row naming
    # neither — the three cases the clause has to separate.
    await _event(
        service_session,
        actor_sub=f"wos_tag_{uuid.uuid4().hex[:8]}",
        event_type=f"action.feed{s}-touch-contact",
        tenant_id=firm,
        metadata={"subject": {"contact_id": 7}},
    )
    await _event(
        service_session,
        actor_sub=f"wos_tag_{uuid.uuid4().hex[:8]}",
        event_type=f"action.feed{s}-touch-account",
        tenant_id=firm,
        path_params={"account_id": 12},
    )
    await _event(
        service_session,
        actor_sub=f"wos_tag_{uuid.uuid4().hex[:8]}",
        event_type=f"action.feed{s}-touch-nothing",
        tenant_id=firm,
    )

    only_contacts = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}", tags="contact", limit=200
    )
    assert [e["event_type"] for e in _mine(only_contacts, s)] == [f"action.feed{s}-touch-contact"]

    only_accounts = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}", tags="account", limit=200
    )
    assert [e["event_type"] for e in _mine(only_accounts, s)] == [f"action.feed{s}-touch-account"]


@pytest.mark.asyncio
async def test_tag_filter_is_AND_so_a_second_tag_narrows(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """The behaviour asked for, and the one an OR gets backwards.

    Under OR, adding a tag WIDENED the result set — so a second choice made the list longer, which
    reads as a broken control. Every other filter in the bar narrows, and so must this."""
    s = str(seed["s"])
    firm = int(str(seed["firm"]))
    await _event(
        service_session,
        actor_sub=f"wos_tag_{uuid.uuid4().hex[:8]}",
        event_type=f"action.feed{s}-both",
        tenant_id=firm,
        path_params={"account_id": 12},
        metadata={"subject": {"contact_id": 7}},
    )
    await _event(
        service_session,
        actor_sub=f"wos_tag_{uuid.uuid4().hex[:8]}",
        event_type=f"action.feed{s}-contact-only",
        tenant_id=firm,
        metadata={"subject": {"contact_id": 7}},
    )

    both = await list_audit_actions(
        session=service_session,
        _p=SYSTEM,
        event_type=f"action.feed{s}",
        tags="contact,account",
        limit=200,
    )
    # Only the row carrying BOTH. The contact-only row matched `contact` alone and must now drop.
    assert [e["event_type"] for e in _mine(both, s)] == [f"action.feed{s}-both"]

    contact_alone = await list_audit_actions(
        session=service_session, _p=SYSTEM, event_type=f"action.feed{s}", tags="contact", limit=200
    )
    assert len(_mine(contact_alone, s)) == 2, "one tag is the wider answer; two must narrow it"


@pytest.mark.asyncio
async def test_tag_filter_resolves_the_generic_uid_by_router_tag(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """A document event names its document as `{uid}`, not `document_id`.

    Filtering only on the field names would miss every `documents-*` row — the largest family the
    tag is used on — so the clause also reads the event type's router tag, exactly as
    `_resolve_entities` does to disambiguate an otherwise meaningless `uid`."""
    firm = int(str(seed["firm"]))
    await _event(
        service_session,
        actor_sub=f"wos_tag_{uuid.uuid4().hex[:8]}",
        event_type="document.archived",
        tenant_id=firm,
        path_params={"uid": "doc_1"},
    )
    # A `uid` under a router whose uid is NOT a document must not match.
    await _event(
        service_session,
        actor_sub=f"wos_tag_{uuid.uuid4().hex[:8]}",
        event_type="task.completed",
        tenant_id=firm,
        path_params={"uid": "tsk_1"},
    )

    docs = await list_audit_actions(
        session=service_session, _p=SYSTEM, tags="document", limit=200, method="POST"
    )
    types = {e["event_type"] for e in docs["items"]}
    assert "document.archived" in types
    assert "task.completed" not in types


@pytest.mark.asyncio
async def test_an_unknown_tag_returns_nothing_rather_than_silently_not_filtering(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """A kind no map knows cannot be honoured, so it must answer EMPTY.

    Ignoring it would show the unfiltered page under a filter the reader can see is set — the worst
    outcome, because the answer looks authoritative."""
    s = str(seed["s"])
    await _event(
        service_session,
        actor_sub=f"wos_tag_{uuid.uuid4().hex[:8]}",
        event_type=f"action.feed{s}-anything",
        tenant_id=int(str(seed["firm"])),
        metadata={"subject": {"contact_id": 7}},
    )
    page = await list_audit_actions(
        session=service_session,
        _p=SYSTEM,
        event_type=f"action.feed{s}",
        tags="not_a_kind",
        limit=200,
    )
    assert _mine(page, s) == []


def test_the_tag_map_covers_every_kind_the_read_side_resolves() -> None:
    """The filter and the chips must agree about the words.

    `audit_stream.TAG_FIELDS` is `admin._ENTITY_FIELD_KINDS` inverted, kept separately because the
    read-side SQL module has no app imports. Nothing enforces that at runtime, so a field added to
    one and not the other would leave the tag filter silently unable to match rows whose chips show
    that kind — the exact drift this asserts away."""
    from app import audit_stream
    from app.routers.admin import _ENTITY_FIELD_KINDS, _UID_KIND_BY_RESOURCE

    for field, kind in _ENTITY_FIELD_KINDS.items():
        assert field in audit_stream.TAG_FIELDS.get(kind, ()), (
            f"{field!r} resolves to {kind!r} on the read side but is not in TAG_FIELDS[{kind!r}]"
        )
    for resource, kind in _UID_KIND_BY_RESOURCE.items():
        assert resource in audit_stream.TAG_UID_RESOURCES.get(kind, ()), (
            f"a {resource!r} `uid` resolves to {kind!r} but is not in TAG_UID_RESOURCES[{kind!r}]"
        )


def test_no_resolver_casts_the_mixed_array_to_a_number() -> None:
    """`:ids` CARRIES UIDS, so no resolver may cast it to a numeric array.

    THE FAILURE THIS FORBIDS IS A 500 ON THE WHOLE PAGE, not a chip that reads badly. `:ids` is
    every value collected off the page, uids and serials together; `CAST(:ids AS bigint[])` raises
    `invalid input syntax for type bigint` on the first uid in it, and one statement error answers
    500 for the entire audit feed — every row, every reader. It reached the branch that way: the
    `conversation` resolver kept its single `bigint[]` arm while `conversation_uid` (seven live
    route params) was taught to validate, so the fix for a cosmetic gap armed an outage.

    The serial arm reads `:int_ids`, which `_resolve_entities` filters to the numeric values, so a
    text value cannot reach a numeric cast however the kind is fed. Asserted over the SQL text
    because that is where the mistake is made, and it is invisible until a real uid arrives.
    """
    import re

    from app.routers.admin import _ENTITY_RESOLVERS

    # EVERY cast of `:ids`, found by pattern rather than by a list of spellings. The first version
    # of this test named `int[]`/`bigint[]`/`integer[]` and would have passed the `user` resolver's
    # `CAST(:ids AS uuid[])`, which raises `invalid input syntax for type uuid` exactly as fatally
    # — the rule is that the MIXED array is text, not that it is not an integer, and a test narrower
    # than its own rule is how the next author gets a green gate and a broken feed.
    for kind, sql in _ENTITY_RESOLVERS.items():
        for cast in re.findall(r"CAST\(:ids AS ([a-z0-9_ ]+\[\])\)", sql):
            assert cast.strip() == "text[]", (
                f"the {kind!r} resolver casts the mixed array to {cast} — `:ids` carries uids, so "
                "a text value in it raises and 500s the whole feed; bind the serial arm to "
                ":int_ids (numeric and range-bound) and keep :ids as text[]"
            )
        # …and a shorthand cast, which the pattern above cannot see.
        assert ":ids::" not in sql, (
            f"the {kind!r} resolver uses a `::` shorthand cast on the mixed array — write it as "
            "CAST(:ids AS text[]) so the rule above can see it"
        )


def test_every_uid_field_names_a_kind_whose_resolver_can_read_a_uid() -> None:
    """A `*_uid` field that validates must reach a resolver with a UID ARM, or it resolves to
    nothing — which is the original defect wearing the fix as a disguise: validation passes, the
    value reaches the SQL, and the SQL has no arm that can match it.

    The pair of tests is deliberate. The one above says the uid cannot CRASH the resolver; this one
    says it cannot be silently DROPPED by it.
    """
    from app.routers.admin import _ENTITY_FIELD_KINDS, _ENTITY_RESOLVERS

    for field, kind in _ENTITY_FIELD_KINDS.items():
        if not (field == "uid" or field.endswith("_uid")):
            continue
        sql = _ENTITY_RESOLVERS[kind]
        assert "CAST(:ids AS text[])" in sql or "ANY(:ids)" in sql, (
            f"{field!r} validates and names {kind!r}, but that resolver has no arm reading :ids as "
            "text — everything the field names resolves to nothing"
        )


@pytest.mark.parametrize(
    ("field", "kind", "value"),
    [
        # THE REAL SHAPE: a bare 12-character nanoid over the 62 alphanumerics, NO prefix
        # (`app/ids.py`, pinned by `tests/test_public_ids.py`). An earlier version of these cases
        # used the retired `acc_`/`mod_`/`cnv_` convention — one of them 13 characters — so they
        # asserted that a value no route can carry survives validation, and taught the next reader
        # the wrong convention for the next `*_uid` kind.
        ("contact_uid", "contact", "aNdY9TTi5uQT"),
        ("account_uid", "account", "Xy7Qm2LpVs4B"),
        ("model_uid", "model", "pQ456rs78TzK"),
        ("conversation_uid", "conversation", "AIc1iu2zFvBC"),
        # The ALL-DIGIT nanoid, which is legal (the alphabet has ten digits) and is the one uid
        # shape that reaches the serial arm's numeric cast — see `_fits_a_serial`.
        ("contact_uid", "contact", "820184096473"),
        # The tenant kind is the one held-back prefix, and `ten_<hex>` is not `isdigit()` either —
        # which is why this case had been failing silently since the field was added.
        ("tenant_uid", "tenant", "ten_ab12cd34"),
    ],
)
def test_a_uid_path_param_survives_validation(field: str, kind: str, value: str) -> None:
    """The gap that made a whole commit inert, and it had been silent for `tenant_uid` far longer.

    `_INT_ID_KINDS` says "a contact's id is an integer", which was true of every field naming one
    until the routes became uid-addressed. After the rename `contact_uid` still maps to kind
    `contact`, so a digits-only rule DROPPED every uid before it reached the resolver — the entity
    chip that should carry the client's name showed a bare id, and adding a UNION arm to the SQL
    changed nothing because nothing ever got that far.

    Asserted per FIELD rather than per kind, because that is the rule: a `*_uid` field carries a
    public uid whatever kind it names, so the next renamed route needs no change here.
    """
    assert _entity_id_valid(kind, value, field), (
        f"{field} was rejected, so nothing it names will ever resolve"
    )


def test_an_oversized_digit_string_never_reaches_the_numeric_cast() -> None:
    """`isdigit()` is not enough to hand a value to `CAST(... AS int[])`.

    The serial arms used to compare `id::text`, which cannot raise whatever the value; casting is
    what made them use the primary key, and it also made an oversized digit string FATAL —
    `CAST('{999999999999}' AS int[])` raises `value out of range for type integer`, a statement
    error, which is a 500 for the whole audit feed rather than one unlabelled chip.

    Two live ways in, which is why the bound sits in `_resolve_entities` rather than in one
    validator: `_entity_id_valid` admits any all-digit `*_id` with no upper bound, and its `*_uid`
    arm admits an all-digit uid — legal, since `app/ids.py`'s alphabet includes the ten digits.
    """
    from app.routers.admin import _MAX_SERIAL, _fits_a_serial

    assert _fits_a_serial("96")
    assert _fits_a_serial(str(_MAX_SERIAL))
    assert not _fits_a_serial(str(_MAX_SERIAL + 1)), "int4 overflow raises for the whole page"
    assert not _fits_a_serial("999999999999")
    # A 12-digit nanoid: `_entity_id_valid` rightly admits it as a uid, and this is what stops it
    # being treated as a serial as well.
    assert not _fits_a_serial("820184096473")
    assert not _fits_a_serial("aNdY9TTi5uQT")
    assert not _fits_a_serial("")


def test_a_serial_field_still_refuses_junk() -> None:
    """The other half: relaxing the uid case must not relax the id case, or a junk value reaches a
    `CAST(... AS int[])` and raises for the whole page."""
    assert _entity_id_valid("contact", "96", "contact_id")
    assert not _entity_id_valid("contact", "not-a-number", "contact_id")
    assert not _entity_id_valid("contact", "", "contact_id")


def test_a_uid_addressed_route_yields_its_entity() -> None:
    """End to end through `_entity_refs`, which is what the feed actually calls — the unit above
    could pass while the ref was dropped somewhere else in the walk."""
    for route, params, expected in [
        (
            "/crm/contacts/{contact_uid}/email",
            {"contact_uid": "aNdY9TTi5uQT"},
            [("contact", "aNdY9TTi5uQT")],
        ),
        (
            "/crm/accounts/{account_uid}/model",
            {"account_uid": "acc_xyz789ab"},
            [("account", "acc_xyz789ab")],
        ),
    ]:
        refs = _entity_refs({"route": route, "path_params": params, "metadata": {}})
        assert refs == expected, f"{route} resolved to {refs}"
