"""GET /firm/audit/actions — the firm's own view of the action-event stream.

The waterfall counterpart of `test_admin_audit_actions.py`: same table, same shared query
(`app/audit_stream.py`), but the scope is HARD-CODED to the caller's own tenant (both sides —
its staff's actions AND proxied ops actions taken ON it) and the AML event family is withheld.
The shared filter/cursor semantics are pinned in the admin file; this one owns what DIFFERS:

* the both-sides tenant scope, with no `?tenant=` escape hatch (the param does not exist, and a
  supplied one is ignored);
* **the safety rail** — an AML-typed event about the firm is visible to Graphene operations at
  the admin endpoint and NEVER here, no matter what filters the firm sends (the audit stream
  must not become a side-channel around the screening withholding posture, docs/aml.md);
* the gate: the platform-portal `activity:read` permission on a firm principal; an ops/system
  principal gets the OPERATIONS tenant's own slice, never a cross-tenant view.

Rows are seeded straight into `audit.actions` (the write path is `test_actions.py`'s job);
unique-per-run tenants keep every assertion order-independent against the shared database.
"""

import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import db as db_mod
from app.auth import Principal, build_jwt_claims, get_current_principal
from app.db import FirmContext
from app.main import app
from app.routers.admin import list_audit_actions
from app.routers.firm import FIRM_EXCLUDED_EVENT_PREFIXES, list_firm_audit_actions

SYSTEM = Principal(user_id="user_ops", permissions=["system"])


async def _firm_ctx(
    session: AsyncSession,
    tenant_id: int,
    *perms: str,
    contacts: Sequence[int] | None = None,
) -> FirmContext:
    """The context `firm_session` would yield for a firm principal holding `perms`.

    It PUBLISHES `request.jwt.claims` exactly as `firm_session` does, because the trail's contact
    gate (`audit_stream.CONTACT_SCOPE_SQL`) is pure SQL reading those claims. A context that
    skipped them left every firm test running against a principal the database read as scopeless,
    so the gate silently withheld everything -- or, before the gate existed, proved nothing about
    it. `portal` matters as much as the permissions: `auth.can_read_all()` (0107) requires BOTH.

    Default is a member who sees the whole firm (`contacts:read_all`), which is what these tests
    mean by "a firm reading its own trail". Pass `contacts=[...]` for a SCOPED principal -- an
    adviser who holds only their own clients -- and the gate then applies as it does in production.
    """
    held = list(perms) if contacts is not None else [*perms, "contacts:read_all"]
    principal = Principal(user_id="user_fw", organisation_id="org_fw", permissions=held)
    claims = {
        **build_jwt_claims(principal),
        "portal": "platform",
        "tenant_id": tenant_id,
        "contact_ids": list(contacts or []),
    }
    await session.execute(
        text("SELECT set_config('request.jwt.claims', :claims, true)"),
        {"claims": json.dumps(claims)},
    )
    return FirmContext(session=session, tenant_id=tenant_id, principal=principal)


# ---- seed (the test_admin_audit_actions conventions) --------------------------------------------


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


async def _event(
    session: AsyncSession,
    *,
    event_type: str,
    actor_sub: str,
    tenant_id: int | None = None,
    target_tenant_id: int | None = None,
    method: str = "POST",
    route: str = "/firm/contacts",
    status: int = 200,
    metadata: dict[str, Any] | None = None,
) -> int:
    """One stream row, written the way the middleware writes it (service_role INSERT)."""
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO audit.actions (event_type, tenant_id, target_tenant_id, "
                    "  actor_sub, method, route, status, metadata) "
                    "VALUES (:et, :t, :tt, :sub, :m, :r, :s, CAST(:md AS jsonb)) RETURNING id"
                ),
                {
                    "et": event_type,
                    "t": tenant_id,
                    "tt": target_tenant_id,
                    "sub": actor_sub,
                    "m": method,
                    "r": route,
                    "s": status,
                    "md": json.dumps(metadata or {}),
                },
            )
        ).scalar_one()
    )


@pytest_asyncio.fixture
async def seed(service_session: AsyncSession) -> dict[str, object]:
    """A firm, another firm, and an operations-flavoured tenant to act as the proxy actor."""
    s = uuid.uuid4().hex[:8]
    firm = await _tenant(service_session, uid=f"fwfirm_{s}", name=f"FW Firm {s}")
    other = await _tenant(service_session, uid=f"fwother_{s}", name=f"FW Other {s}")
    ops = await _tenant(service_session, uid=f"fwops_{s}", name=f"FW Ops {s}")
    return {
        "s": s,
        "firm": firm,
        "firm_uid": f"fwfirm_{s}",
        "other": other,
        "ops": ops,
        "sub": f"wos_fw_{s}",
    }


# ---- the live poll's keyset end ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_returns_only_newer_rows_and_composes_with_every_filter(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """``after`` is the poll's question: "what has happened since the newest row I hold".

    It must return rows STRICTLY newer (the reader already has the boundary row — including it would
    make every tick report one new event for ever), and it must compose with the other filters,
    because the count it feeds a "3 new events" pill has to be a count of rows that will actually
    appear when the reader clicks it. A filter-blind count offers three and reveals none."""
    sub = str(seed["sub"])
    firm = int(str(seed["firm"]))
    first = await _event(
        service_session, event_type="client.created", actor_sub=sub, tenant_id=firm
    )
    second = await _event(
        service_session, event_type="client.created", actor_sub=sub, tenant_id=firm
    )
    third = await _event(
        service_session, event_type="document.uploaded", actor_sub=sub, tenant_id=firm
    )

    ctx = await _firm_ctx(service_session, firm, "activity:read")
    page = await list_firm_audit_actions(ctx=ctx, after=first, limit=200)
    ids = [r["id"] for r in page["items"]]
    # Strictly newer, and still newest-first — the poll prepends in the order it is handed.
    assert ids == [third, second]
    assert first not in ids

    # …and narrowed by the filters exactly as the page is.
    typed = await list_firm_audit_actions(ctx=ctx, after=first, event_type="document.", limit=200)
    assert [r["id"] for r in typed["items"]] == [third]

    # The newest row means "nothing new", which is the answer on almost every tick.
    assert (await list_firm_audit_actions(ctx=ctx, after=third, limit=200))["items"] == []


@pytest.mark.asyncio
async def test_after_cannot_count_an_aml_event_the_firm_view_withholds(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """THE RAIL, at the poll's door. A "how many since?" count is a number, and a number derived
    from rows the firm may not see would leak their existence — a firm learning that three things
    happened to a client between 14:02 and 14:03, on a surface that refuses to say what, is exactly
    the tipping-off the AML control exists to prevent (docs/aml.md). The deny list is ANDed after
    every filter, so it covers this end of the keyset too; this test is what keeps that true."""
    sub = str(seed["sub"])
    firm, ops = int(str(seed["firm"])), int(str(seed["ops"]))
    baseline = await _event(
        service_session, event_type="client.created", actor_sub=sub, tenant_id=firm
    )
    await _event(
        service_session,
        event_type="aml.screen_ran",
        actor_sub=sub,
        tenant_id=ops,
        target_tenant_id=firm,
        route="/aml/screen",
    )
    await _event(
        service_session,
        event_type="aml.hit_decided",
        actor_sub=sub,
        tenant_id=firm,
        route="/aml/hits/{hit_uid}/decision",
    )

    ctx = await _firm_ctx(service_session, firm, "activity:read")
    # Nothing since the baseline, as far as this firm is concerned — the two AML rows are not merely
    # unreadable, they are UNCOUNTABLE.
    assert (await list_firm_audit_actions(ctx=ctx, after=baseline, limit=200))["items"] == []
    # Nor by asking for the family directly.
    denied = await list_firm_audit_actions(ctx=ctx, after=baseline, event_type="aml.", limit=200)
    assert denied["items"] == []


# ---- scope: both sides of the firm, nothing else -------------------------------------------------


@pytest.mark.asyncio
async def test_scope_is_own_actions_plus_proxied_actions_on_the_firm(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """The firm sees what its staff did AND what ops did TO it — and never another firm's rows,
    whether that firm acted or was acted upon. The envelope is `{items, has_more, facets}` (no
    `tenants` lookup: there is no firm dropdown to feed)."""
    s, sub = str(seed["s"]), str(seed["sub"])
    firm, other, ops = int(str(seed["firm"])), int(str(seed["other"])), int(str(seed["ops"]))
    own = await _event(
        service_session, event_type=f"action.fw{s}-own", actor_sub=sub, tenant_id=firm
    )
    proxied_on_us = await _event(
        service_session,
        event_type=f"action.fw{s}-proxied",
        actor_sub=sub,
        tenant_id=ops,
        target_tenant_id=firm,
    )
    await _event(  # another firm's own action — invisible
        service_session, event_type=f"action.fw{s}-elsewhere", actor_sub=sub, tenant_id=other
    )
    await _event(  # ops acting on ANOTHER firm — also invisible
        service_session,
        event_type=f"action.fw{s}-proxied-elsewhere",
        actor_sub=sub,
        tenant_id=ops,
        target_tenant_id=other,
    )

    page = await list_firm_audit_actions(
        ctx=await _firm_ctx(service_session, firm, "activity:read"), limit=200
    )
    assert set(page.keys()) == {"items", "has_more", "facets"}
    items = page["items"]
    assert isinstance(items, list)
    assert {r["id"] for r in items} == {own, proxied_on_us}
    # Proxied rows keep the actor + target names joined, so the UI can attribute the operator.
    by_id = {r["id"]: r for r in items}
    assert by_id[proxied_on_us]["target_tenant_uid"] == seed["firm_uid"]
    # The branding icon columns ride the shared query here too (the platform portal's proxied
    # badge shows the ACTING — Graphene — tenant's icon; the semantics are pinned in the admin
    # file, this only proves the firm envelope didn't drop them).
    assert "tenant_icon_url" in by_id[proxied_on_us]
    assert "target_tenant_icon_url" in by_id[proxied_on_us]
    # The read-time entity labels ride along exactly as on the admin surface.
    assert all("entities" in r for r in items)


@pytest.mark.asyncio
async def test_write_implies_read_and_missing_permission_is_403(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    firm = int(str(seed["firm"]))
    with pytest.raises(HTTPException) as exc:
        await list_firm_audit_actions(ctx=await _firm_ctx(service_session, firm, "members:read"))
    assert exc.value.status_code == 403
    # `system` bypasses, like every registry gate.
    page = await list_firm_audit_actions(ctx=await _firm_ctx(service_session, firm, "system"))
    assert set(page.keys()) == {"items", "has_more", "facets"}
    # The key is always present and `null` unless asked for — the facets are opt-in
    # (`?facets=true`), so a caller that did not ask pays for no extra scan (facet section below).
    assert page["facets"] is None


# ---- THE SAFETY RAIL: the AML family never reaches the firm --------------------------------------


@pytest.mark.asyncio
async def test_aml_events_reach_operations_but_never_the_firm(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """An AML-typed event about the firm (a screen run against it, a hit adjudicated) is evidence
    for Graphene operations — the ADMIN endpoint returns it — and must NEVER appear in the firm's
    own view, even when the firm filters for it explicitly. Do not weaken: a firm sees screening
    outcomes via `screening:read`, never review activity (docs/aml.md), and the audit stream must
    not become a side-channel around that."""
    sub = str(seed["sub"])
    firm, ops = int(str(seed["firm"])), int(str(seed["ops"]))
    # The realistic shape: ops screens the firm's client via ?tenant= (target side)…
    screened = await _event(
        service_session,
        event_type="aml.screen_ran",
        actor_sub=sub,
        tenant_id=ops,
        target_tenant_id=firm,
        route="/aml/screen",
    )
    # …and the paranoid shape: an aml-typed row attributed to the firm as the ACTING tenant
    # (cannot happen today — no firm principal passes the aml gates — but the rail must hold
    # even if that ever changes).
    acting = await _event(
        service_session,
        event_type="aml.hit_decided",
        actor_sub=sub,
        tenant_id=firm,
        route="/aml/hits/{hit_uid}/decision",
    )

    ctx = await _firm_ctx(service_session, firm, "activity:read")
    ids = {r["id"] for r in (await list_firm_audit_actions(ctx=ctx, limit=200))["items"]}
    assert screened not in ids and acting not in ids

    # No filter combination un-hides the family — the exclusion is applied AFTER the filters.
    filtered = await list_firm_audit_actions(ctx=ctx, event_type="action.aml-", limit=200)
    assert filtered["items"] == []

    # Nor the CONTACT filter: an aml screen that NAMES a client (subject.contact_id) is STILL
    # withheld — the deny list ANDs after the contact filter too, so it is no side-channel either.
    named = await _event(
        service_session,
        event_type="aml.screen_ran",
        actor_sub=sub,
        tenant_id=firm,
        route="/aml/screen",
        metadata={"subject": {"contact_id": 7788}},
    )
    # `?contact=` takes the client's UID (a filter value in a URL names the record the way every
    # other URL does), so this needs a REAL contact. Passing an unresolvable uid used to make this
    # assertion vacuous twice over: the filter matched nothing, so `named` was absent whatever the
    # deny list did -- deleting FIRM_EXCLUDED_EVENT_PREFIXES entirely still passed.
    contact_uid = f"fwcon_{uuid.uuid4().hex[:8]}"
    contact_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, surname, service_type) "
                    "VALUES (:t, :u, 'individual', 'Filtered', 'advised') RETURNING id"
                ),
                {"t": firm, "u": contact_uid},
            )
        ).scalar_one()
    )
    aml_named = await _event(
        service_session,
        event_type="aml.screen_ran",
        actor_sub=sub,
        tenant_id=firm,
        route="/aml/screen",
        metadata={"subject": {"contact_id": contact_id}},
    )
    ordinary = await _event(
        service_session,
        event_type="action.update-contact",
        actor_sub=sub,
        tenant_id=firm,
        route="/firm/contacts/{contact_uid}",
        metadata={"subject": {"contact_id": contact_id}},
    )
    by_contact = {
        r["id"]
        for r in (await list_firm_audit_actions(ctx=ctx, contact=contact_uid, limit=200))["items"]
    }
    # The filter MATCHED -- without this the assertion below proves nothing, which is exactly how
    # the previous version passed while testing neither the filter nor the rail.
    assert ordinary in by_contact, (
        "the contact filter matched nothing, so the next assert is vacuous"
    )
    assert aml_named not in by_contact, (
        "an AML event naming this client leaked into the firm's view"
    )
    assert named not in by_contact

    # The same rows ARE visible to Graphene operations at the admin endpoint (the firm filter
    # there matches both sides), so the exclusion is a firm-view control, not lost evidence.
    admin_page = await list_audit_actions(
        session=service_session, _p=SYSTEM, tenant=str(seed["firm_uid"]), limit=200
    )
    admin_ids = {r["id"] for r in admin_page["items"]}  # type: ignore[union-attr]
    assert {screened, acting} <= admin_ids

    # A RETIRED screening op — an event type from a route that no longer exists, so it is in no
    # rename map and keeps its pre-rename name for ever. It must STILL be denied, and it is, because
    # the deny list matches a PREFIX rather than a set of known names. This is the case that decided
    # the migration's shape: it REPORTS leftovers instead of raising, so old history cannot fail a
    # deploy — which is only acceptable because a leftover is still safe.
    retired = await _event(
        service_session,
        event_type="action.aml-some-retired-screening-op",
        actor_sub=str(seed["sub"]),
        tenant_id=int(str(seed["firm"])),
    )
    firm_after = await list_firm_audit_actions(
        ctx=await _firm_ctx(service_session, int(str(seed["firm"])), "activity:read"), limit=200
    )
    assert retired not in {r["id"] for r in firm_after["items"]}, (
        "a retired aml.* event must stay denied to the firm — the deny list is a prefix, not a "
        "list of names, precisely so an unmappable old name cannot become a disclosure"
    )

    # And the deny list itself stays prefix-shaped, covering BOTH spellings of the AML family.
    #
    # The second entry is not redundant tidiness, it is the control holding over history:
    # `audit.actions` is append-only, so every screening event recorded before the `resource.verb`
    # rename still reads `action.aml-…`. A deny list carrying only the current spelling would keep
    # working perfectly on new rows while disclosing every old one — the failure mode a tipping-off
    # control cannot have, and one no test of TODAY's events would catch.
    assert FIRM_EXCLUDED_EVENT_PREFIXES == ("aml.", "action.aml-")
    assert all(
        any(t.startswith(p) for p in FIRM_EXCLUDED_EVENT_PREFIXES)
        for t in ("aml.screen_ran", "aml.hit_decided", "action.aml-run-aml-screen")
    )


@pytest.mark.asyncio
async def test_facets_withhold_the_aml_family_the_rows_withhold(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """THE SAFETY RAIL, IN FACET FORM — the assertion this whole facet change turns on.

    The filter dropdowns are fed by the API (`?facets=true`) so their options describe the STREAM
    rather than the loaded page. **An option is a disclosure.** A firm whose event-type dropdown
    offered `aml.screen_ran` would learn from the dropdown ALONE that its client had
    been screened — the exact knowledge `FIRM_EXCLUDED_EVENT_PREFIXES` withholds from the rows
    (POCA 2002 s.333A tipping-off, docs/aml.md). A deny list applied to the rows and not to the
    vocabulary is not a weaker control, it is no control: the reader learns the same fact.

    So this fails if the deny list is ever dropped from `action_facets`' call in
    `routers/firm.py` — and it checks the other side too, because "absent everywhere" would pass
    trivially: the same type IS offered to a Graphene operations caller at the admin endpoint, so
    what is proven is a firm-view control and not a lost facet. Do not weaken for symmetry."""
    s, sub = str(seed["s"]), str(seed["sub"])
    firm, ops = int(str(seed["firm"])), int(str(seed["ops"]))
    # A screen ops ran on the firm's client (target side — the realistic shape) and a paranoid
    # firm-as-actor row: both are in the firm's SCOPE and must be out of its VOCABULARY.
    await _event(
        service_session,
        event_type="aml.screen_ran",
        actor_sub=sub,
        tenant_id=ops,
        target_tenant_id=firm,
        route="/aml/screen",
    )
    await _event(
        service_session,
        event_type="aml.hit_decided",
        actor_sub=f"{sub}_adjudicator",
        tenant_id=firm,
        route="/aml/hits/{hit_uid}/decision",
    )
    # An ordinary event, so a passing test cannot be a facet query that returned nothing at all.
    await _event(
        service_session, event_type=f"action.fw{s}-ordinary", actor_sub=sub, tenant_id=firm
    )

    firm_facets = (
        await list_firm_audit_actions(
            ctx=await _firm_ctx(service_session, firm, "activity:read"), limit=200, facets=True
        )
    )["facets"]
    assert isinstance(firm_facets, dict)
    types = list(firm_facets["event_types"])
    assert f"action.fw{s}-ordinary" in types, "the facet query returned nothing at all"
    assert not [t for t in types if t.startswith("action.aml-")], types
    # The adjudicator is only ever named by an AML row, so appearing in the ACTOR list would
    # disclose the same fact by a different column — the deny list runs before both aggregates.
    assert f"{sub}_adjudicator" not in firm_facets["actors"]

    # Graphene operations, same rows, same window: the family IS offered. A firm-view control.
    ops_facets = (
        await list_audit_actions(
            session=service_session,
            _p=SYSTEM,
            tenant=str(seed["firm_uid"]),
            limit=200,
            facets=True,
        )
    )["facets"]
    assert isinstance(ops_facets, dict)
    assert "aml.screen_ran" in ops_facets["event_types"]
    assert f"{sub}_adjudicator" in ops_facets["actors"]


@pytest.mark.asyncio
async def test_facets_are_the_firms_own_vocabulary_and_ignore_every_filter(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """Scope-aware and filter-independent, on the firm side too.

    Scope: the SAME predicate the page uses (hoisted into one local and handed to both), so another
    firm's types and actors can never reach this bar — including the case that motivates the
    both-sides scope, an ops action taken ON this firm, whose actor the firm SHOULD be able to
    filter by.

    Filter-independent: applying the event-type and actor filters narrows the ROWS and leaves the
    CHOICES exactly as they were. That is the regression the facets exist for — options derived
    from the loaded page meant picking an actor emptied the event-type dropdown, and the only way
    back was to clear a filter whose effect you could no longer see."""
    s, sub = str(seed["s"]), str(seed["sub"])
    firm, other, ops = int(str(seed["firm"])), int(str(seed["other"])), int(str(seed["ops"]))
    operator = f"wos_fwops_{s}"
    await _event(service_session, event_type=f"action.fw{s}-mine", actor_sub=sub, tenant_id=firm)
    await _event(  # ops acting ON the firm: in scope on the target side, so in the vocabulary
        service_session,
        event_type=f"action.fw{s}-onus",
        actor_sub=operator,
        tenant_id=ops,
        target_tenant_id=firm,
    )
    await _event(  # another firm's row — out of scope, so out of the bar
        service_session,
        event_type=f"action.fw{s}-elsewhere",
        actor_sub=f"wos_fwelse_{s}",
        tenant_id=other,
    )

    ctx = await _firm_ctx(service_session, firm, "activity:read")

    async def facets(**kw: object) -> dict[str, list[str]]:
        page = await list_firm_audit_actions(ctx=ctx, limit=200, facets=True, **kw)
        got = page["facets"]
        assert isinstance(got, dict)
        assert set(got.keys()) == {"event_types", "actors"}
        return {k: [str(v) for v in got[k]] for k in ("event_types", "actors")}

    wide = await facets()
    assert {f"action.fw{s}-mine", f"action.fw{s}-onus"} <= set(wide["event_types"])
    assert f"action.fw{s}-elsewhere" not in wide["event_types"]
    assert {sub, operator} <= set(wide["actors"])
    assert f"wos_fwelse_{s}" not in wide["actors"]
    # Sorted, so the dropdown order is stable across requests.
    assert wide["event_types"] == sorted(wide["event_types"])
    assert wide["actors"] == sorted(wide["actors"])

    # Each filter alone, then the combination that used to leave one option in each dropdown.
    assert await facets(event_type=f"action.fw{s}-mine") == wide
    assert await facets(actor=operator) == wide
    assert await facets(event_type=f"action.fw{s}-mine", actor=sub, method="POST") == wide
    # A date window that excludes everything is the sharpest form of the same question: an empty
    # page must still offer the full vocabulary, or a mis-set range strands the reader.
    assert await facets(since="2099-01-01") == wide
    # The ROWS still narrow — the filters work; it is only the vocabulary that holds.
    narrowed = await list_firm_audit_actions(ctx=ctx, event_type=f"action.fw{s}-mine", limit=200)
    assert {r["event_type"] for r in narrowed["items"]} == {f"action.fw{s}-mine"}


@pytest.mark.asyncio
async def test_facets_are_opt_in_and_never_inferred_from_the_cursor(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """Not asked for ⇒ not computed, so the extra windowed scan is never a cost the common request
    pays — and the cursor is deliberately not the signal, because the toolbar applies a filter in
    place and so re-reads page ONE without one."""
    s, sub = str(seed["s"]), str(seed["sub"])
    firm = int(str(seed["firm"]))
    ids = [
        await _event(
            service_session, event_type=f"action.fw{s}-optin", actor_sub=sub, tenant_id=firm
        )
        for _ in range(2)
    ]
    ctx = await _firm_ctx(service_session, firm, "activity:read")

    assert (await list_firm_audit_actions(ctx=ctx, limit=1))["facets"] is None
    assert (await list_firm_audit_actions(ctx=ctx, limit=1, facets=False))["facets"] is None

    asked = await list_firm_audit_actions(
        ctx=ctx, event_type=f"action.fw{s}-optin", limit=1, facets=True
    )
    assert asked["facets"] == {"event_types": [f"action.fw{s}-optin"], "actors": [sub]}
    # Asked for WITH a cursor: still computed, and the same answer — the facets describe the
    # caller's recent scope, which the page position does not change.
    deep = await list_firm_audit_actions(
        ctx=ctx, event_type=f"action.fw{s}-optin", limit=1, before=ids[1], facets=True
    )
    assert deep["facets"] == asked["facets"]


@pytest.mark.asyncio
async def test_event_id_deep_link_respects_scope_and_the_aml_deny_list(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """The master-detail deep-link (`event=<id>`) is a single-row fetch, but it is ANDed with the
    firm scope AND the AML deny list — so a firm cannot reach a foreign OR a withheld event by
    guessing its id. The same denied id IS fetchable by Graphene operations at the admin endpoint,
    proving it is a firm-view control, not lost evidence. The safety rail, in deep-link form."""
    s, sub = str(seed["s"]), str(seed["sub"])
    firm, other, ops = int(str(seed["firm"])), int(str(seed["other"])), int(str(seed["ops"]))
    own = await _event(
        service_session, event_type=f"action.fw{s}-own", actor_sub=sub, tenant_id=firm
    )
    elsewhere = await _event(
        service_session, event_type=f"action.fw{s}-elsewhere", actor_sub=sub, tenant_id=other
    )
    aml_on_us = await _event(
        service_session,
        event_type="aml.screen_ran",
        actor_sub=sub,
        tenant_id=ops,
        target_tenant_id=firm,
        route="/aml/screen",
    )
    ctx = await _firm_ctx(service_session, firm, "activity:read")

    # In scope, not denied → exactly that row.
    got = await list_firm_audit_actions(ctx=ctx, event=own, limit=200)
    got_items = got["items"]
    assert isinstance(got_items, list)
    assert [r["id"] for r in got_items] == [own]

    # Another firm's event, by id → nothing (the scope ANDs).
    assert (await list_firm_audit_actions(ctx=ctx, event=elsewhere, limit=200))["items"] == []

    # An AML event about THIS firm, by id → nothing (the deny list ANDs), though it is in scope on
    # the target side.
    assert (await list_firm_audit_actions(ctx=ctx, event=aml_on_us, limit=200))["items"] == []

    # Graphene operations CAN fetch that same AML event by id at the admin endpoint.
    admin = await list_audit_actions(session=service_session, _p=SYSTEM, event=aml_on_us, limit=200)
    admin_items = admin["items"]
    assert isinstance(admin_items, list)
    assert [r["id"] for r in admin_items] == [aml_on_us]


# ---- filters + cursor (the shared query, exercised through THIS endpoint's scope) ---------------


@pytest.mark.asyncio
async def test_filters_and_cursor(service_session: AsyncSession, seed: dict[str, object]) -> None:
    s, sub = str(seed["s"]), str(seed["sub"])
    firm = int(str(seed["firm"]))
    ids = [
        await _event(
            service_session, event_type=f"action.fw{s}-walk", actor_sub=sub, tenant_id=firm
        )
        for _ in range(3)
    ]
    await _event(
        service_session,
        event_type=f"action.fw{s}-patch",
        actor_sub=sub,
        tenant_id=firm,
        method="PATCH",
    )
    ctx = await _firm_ctx(service_session, firm, "activity:read")

    # event_type is a prefix filter; method is normalised.
    walk = await list_firm_audit_actions(ctx=ctx, event_type=f"action.fw{s}-walk", limit=200)
    assert {r["event_type"] for r in walk["items"]} == {f"action.fw{s}-walk"}
    patched = await list_firm_audit_actions(ctx=ctx, method="patch", limit=200)
    assert {r["event_type"] for r in patched["items"]} == {f"action.fw{s}-patch"}

    # Keyset cursor, newest first, has_more from the one-row over-fetch.
    first = await list_firm_audit_actions(ctx=ctx, event_type=f"action.fw{s}-walk", limit=2)
    assert [r["id"] for r in first["items"]] == [ids[2], ids[1]]
    assert first["has_more"] is True
    second = await list_firm_audit_actions(
        ctx=ctx, event_type=f"action.fw{s}-walk", limit=2, before=ids[1]
    )
    assert [r["id"] for r in second["items"]] == [ids[0]]
    assert second["has_more"] is False

    # Unparseable dates are the caller's fault, not a 500.
    for field in ("since", "until"):
        with pytest.raises(HTTPException) as exc:
            await list_firm_audit_actions(ctx=ctx, **{field: "yesterday"})
        assert exc.value.status_code == 422


# ---- the gate, over real HTTP --------------------------------------------------------------------
# firm_session resolves the tenant from the caller's LOGIN (auth.users.tenant_id via
# resolve_claims), so the matrix seeds real logins and overrides only the verified principal.


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
def gate_world(super_engine: Engine) -> Iterator[dict[str, object]]:
    """Two firms and an ops-ish tenant, one login each (staff, a CLIENT login in the firm, and
    an ops login), plus one seeded event per interesting slice — committed via the sync
    superuser engine so the HTTP app's own sessions can see them. Yields the world + `act`."""
    s = uuid.uuid4().hex[:8]
    world: dict[str, object] = {"s": s}
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

        def mk_login(who: str, tenant_id: int) -> str:
            sub = f"wos_fwgate_{who}_{s}"
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, "
                    "  first_name, last_name, enabled) "
                    "VALUES (:uid, :t, :e, :w, :f, 'Gate', true)"
                ),
                {
                    "uid": f"usr_fwgate_{who}_{s}",
                    "t": tenant_id,
                    "e": f"{who}_{s}@fwgate.test",
                    "w": sub,
                    "f": who.title(),
                },
            )
            return sub

        firm = mk_tenant(f"fwgatefirm_{s}", f"FW Gate Firm {s}")
        other = mk_tenant(f"fwgateother_{s}", f"FW Gate Other {s}")
        ops = mk_tenant(f"fwgateops_{s}", f"FW Gate Ops {s}")
        world.update(firm=firm, other=other, ops=ops, other_uid=f"fwgateother_{s}")
        # App-only portal (0107): each tenant declares its portal applications, and a login holds
        # a portal only via an ACTIVE membership row. The overridden principal carries the app id.
        for tenant_id, prefix in ((firm, "firm"), (ops, "ops")):
            c.execute(
                text(
                    "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                    "  client_portal_client_id, platform_client_id, operations_client_id) "
                    "VALUES (:t, 'dev', :env, :capp, :papp, :oapp)"
                ),
                {
                    "t": tenant_id,
                    "env": f"env_fwgate_{prefix}_{s}",
                    "capp": f"app_fwgate_client_{prefix}_{s}",
                    "papp": f"app_fwgate_platform_{prefix}_{s}",
                    "oapp": f"app_fwgate_ops_{prefix}_{s}",
                },
            )
        world["app_staff"] = f"app_fwgate_platform_firm_{s}"
        world["app_client"] = f"app_fwgate_client_firm_{s}"
        world["app_ops"] = f"app_fwgate_platform_ops_{s}"
        for who, tenant_id, portal in (
            ("staff", firm, "platform"),
            ("client", firm, "client"),
            ("ops", ops, "platform"),
        ):
            world[f"sub_{who}"] = mk_login(who, tenant_id)
            uid = c.execute(
                text("SELECT id FROM auth.users WHERE workos_user_id = :w"),
                {"w": world[f"sub_{who}"]},
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships "
                    "  (user_id, tenant_id, portal, status) VALUES (:u, :t, :p, 'active')"
                ),
                {"u": uid, "t": tenant_id, "p": portal},
            )

        def mk_event(marker: str, tenant_id: int, target: int | None = None) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO audit.actions (event_type, tenant_id, target_tenant_id, "
                        "  actor_sub, method, route, status) "
                        "VALUES (:et, :t, :tt, 'wos_seed', 'POST', '/x', 200) RETURNING id"
                    ),
                    {"et": f"action.fwgate{s}-{marker}", "t": tenant_id, "tt": target},
                ).scalar_one()
            )

        world["ev_firm"] = mk_event("firm-own", firm)
        world["ev_other"] = mk_event("other-own", other)
        world["ev_ops"] = mk_event("ops-own", ops)
        c.commit()

    def act(who: str, perms: list[str]) -> None:
        def override() -> Principal:
            return Principal(
                user_id=str(world[f"sub_{who}"]),
                organisation_id=f"org_fwgate_{s}",
                permissions=list(perms),
                # The application the session signed in through (0107) — what classifies the portal.
                client_id=str(world[f"app_{who}"]),
            )

        app.dependency_overrides[get_current_principal] = override

    world["act"] = act
    yield world
    app.dependency_overrides.pop(get_current_principal, None)


@pytest.mark.asyncio
async def test_gate_matrix(http: AsyncClient, gate_world: dict[str, object]) -> None:
    act = gate_world["act"]
    assert callable(act)

    # Firm staff holding activity:read: 200, and ONLY their firm's slice.
    act("staff", ["activity:read"])
    r = await http.get("/firm/audit/actions", params={"limit": 200})
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["items"]}
    assert gate_world["ev_firm"] in ids
    assert gate_world["ev_other"] not in ids and gate_world["ev_ops"] not in ids

    # The target parameter is an operations-only workspace door. Firm staff cannot name even
    # their own tenant through it, so it cannot become an escape hatch around the fixed firm scope.
    r = await http.get(
        "/firm/audit/actions",
        params={"limit": 200, "tenant": str(gate_world["other_uid"])},
    )
    assert r.status_code == 403

    # Without the permission: 403 from the granular gate.
    act("staff", ["members:read", "contacts:read"])
    assert (await http.get("/firm/audit/actions")).status_code == 403

    # A client-portal login: 403 — clients hold no permissions (and rbac refuses to attach any
    # to a client-portal role), so the firm's trail is staff-only.
    act("client", [])
    assert (await http.get("/firm/audit/actions")).status_code == 403

    # With no target, an ops/system principal still gets the OPERATIONS tenant's own slice.
    act("ops", ["system"])
    r = await http.get("/firm/audit/actions", params={"limit": 200})
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["items"]}
    assert gate_world["ev_ops"] in ids
    assert gate_world["ev_firm"] not in ids and gate_world["ev_other"] not in ids


async def test_an_unresolvable_contact_filter_narrows_to_nothing_never_to_the_firm(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """`?contact=` that resolves to no client returns NO rows — it must never drop the predicate.

    The two outcomes are a whole firm apart. `_contact_filter_id` returned None for an unknown uid,
    and both streams read None as "no contact predicate at all", so the filter did not narrow — it
    WIDENED. The client record's Events tab sent the serial (which matches no uid), so its first
    page answered with every client's events while its own "Load more", which sent the uid, answered
    with that client's. A feed asked about one person must not answer about everyone.
    """
    s, sub = str(seed["s"]), str(seed["sub"])
    firm = int(str(seed["firm"]))
    ctx = await _firm_ctx(service_session, firm, "activity:read")

    mine = await _event(
        service_session, event_type=f"action.cf{s}-own", actor_sub=sub, tenant_id=firm
    )
    # Readable without a contact filter — the row exists and the caller can see it.
    assert mine in {r["id"] for r in (await list_firm_audit_actions(ctx=ctx, limit=200))["items"]}

    # ...and invisible the moment the feed is asked about a client that does not exist.
    unresolvable = await list_firm_audit_actions(ctx=ctx, contact=f"con_no_such_{s}", limit=200)
    assert unresolvable["items"] == []

    # A BLANK value is the absence of a filter, not an unresolvable one: nothing was asked, so
    # nothing is narrowed. The distinction is the whole reason the helper returns None separately.
    assert mine in {
        r["id"] for r in (await list_firm_audit_actions(ctx=ctx, contact="", limit=200))["items"]
    }


# --------------------------------------------------------------------------------------------
# the contact gate: a scoped member sees only the clients they hold
# --------------------------------------------------------------------------------------------


async def _contact(session: AsyncSession, tenant_id: int, surname: str) -> tuple[int, str]:
    uid = f"fwcon_{uuid.uuid4().hex[:8]}"
    cid = int(
        (
            await session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, surname, service_type) "
                    "VALUES (:t, :u, 'individual', :s, 'advised') RETURNING id"
                ),
                {"t": tenant_id, "u": uid, "s": surname},
            )
        ).scalar_one()
    )
    return cid, uid


@pytest.mark.asyncio
async def test_a_scoped_member_never_sees_a_client_they_do_not_hold(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """The trail is NOT a way around contact scope.

    An adviser who cannot open a client must not learn from the trail that the client's bank
    details changed — "when a field I may not read changed, on a client I may not read" is the
    read, not a lesser one. `auth.can_read_all()` is the whole exemption; everyone else is held to
    `auth.contact_scope()`, and a row naming NO contact (a firm-level setting) stays visible to
    all, because there is no client in it to protect.
    """
    firm = int(str(seed["firm"]))
    sub = str(seed["sub"])
    mine, mine_uid = await _contact(service_session, firm, "Mine")
    theirs, theirs_uid = await _contact(service_session, firm, "Theirs")

    held = await _event(
        service_session,
        event_type="client.bank_account_updated",
        actor_sub=sub,
        tenant_id=firm,
        metadata={"subject": {"contact_id": str(mine)}},
    )
    not_held = await _event(
        service_session,
        event_type="client.bank_account_updated",
        actor_sub=sub,
        tenant_id=firm,
        metadata={"subject": {"contact_id": str(theirs)}},
    )
    # By UID as well as by id: a row names a client either way, and a gate that reads only one of
    # them is a gate with a documented hole in it.
    by_uid = await _event(
        service_session,
        event_type="client.bank_account_updated",
        actor_sub=sub,
        tenant_id=firm,
        metadata={"subject": {"contact_uid": theirs_uid}},
    )
    firm_wide = await _event(
        service_session,
        event_type="firm.branding_updated",
        actor_sub=sub,
        tenant_id=firm,
        route="/firm/branding",
    )

    scoped = await _firm_ctx(service_session, firm, "activity:read", contacts=[mine])
    ids = {r["id"] for r in (await list_firm_audit_actions(ctx=scoped, limit=200))["items"]}
    assert held in ids and firm_wide in ids
    assert not_held not in ids and by_uid not in ids

    # Read-all is the exemption, and it is the ONLY one.
    everything = await _firm_ctx(service_session, firm, "activity:read")
    all_ids = {r["id"] for r in (await list_firm_audit_actions(ctx=everything, limit=200))["items"]}
    assert {held, not_held, by_uid, firm_wide} <= all_ids
    assert mine_uid  # the held client's uid is seeded for symmetry with `theirs_uid`


@pytest.mark.asyncio
async def test_an_account_event_is_gated_by_the_account_holder(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """An account event names only the account, and the account names its holder.

    `account.bank_accounts_updated` carries the account's bank details and no contact id. Read as
    naming nobody, it would be firm business and visible to every adviser with `activity:read`.
    """
    firm = int(str(seed["firm"]))
    sub = str(seed["sub"])
    mine, _ = await _contact(service_session, firm, "Mine")
    theirs, _ = await _contact(service_session, firm, "Theirs")

    async def account_event(holder: int, *, by_serial: bool) -> int:
        account_id, account_uid = (
            await service_session.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, contact_id, uid) "
                    "VALUES (:t, :c, :u) RETURNING id, uid"
                ),
                {"t": firm, "c": holder, "u": f"fwacc_{uuid.uuid4().hex[:8]}"},
            )
        ).one()
        params = {"account_id": str(account_id)} if by_serial else {"account_uid": account_uid}
        return int(
            (
                await service_session.execute(
                    text(
                        "INSERT INTO audit.actions (event_type, tenant_id, actor_sub, method, "
                        "  route, status, path_params, metadata) "
                        "VALUES ('account.bank_accounts_updated', :t, :sub, 'PUT', "
                        "  '/crm/accounts/{account_uid}/bank-accounts', 200, "
                        "  CAST(:pp AS jsonb), '{}'::jsonb) RETURNING id"
                    ),
                    {"t": firm, "sub": sub, "pp": json.dumps(params)},
                )
            ).scalar_one()
        )

    held = await account_event(mine, by_serial=False)
    not_held = await account_event(theirs, by_serial=False)
    not_held_by_serial = await account_event(theirs, by_serial=True)

    scoped = await _firm_ctx(service_session, firm, "activity:read", contacts=[mine])
    ids = {r["id"] for r in (await list_firm_audit_actions(ctx=scoped, limit=200))["items"]}
    assert held in ids
    assert not_held not in ids and not_held_by_serial not in ids

    everything = await _firm_ctx(service_session, firm, "activity:read")
    all_ids = {r["id"] for r in (await list_firm_audit_actions(ctx=everything, limit=200))["items"]}
    assert {held, not_held, not_held_by_serial} <= all_ids


@pytest.mark.asyncio
async def test_being_the_actor_does_not_widen_a_members_scope(
    service_session: AsyncSession, seed: dict[str, object]
) -> None:
    """Doing the edit is not the same as being allowed to see it afterwards.

    A member may act on a client through a surface that is open to them (a shared inbox, an
    admin stand-in) without holding that client. The trail must still judge them by their scope,
    or "who touched it" becomes a standing exemption from "what they may read" — and every
    sensitive change they ever made stays legible to them for ever.
    """
    firm = int(str(seed["firm"]))
    sub = str(seed["sub"])
    mine, _ = await _contact(service_session, firm, "Mine")
    theirs, _ = await _contact(service_session, firm, "Theirs")

    own_edit = await _event(
        service_session,
        event_type="client.bank_account_updated",
        actor_sub=sub,
        tenant_id=firm,
        metadata={"subject": {"contact_id": str(theirs)}},
    )

    scoped = await _firm_ctx(service_session, firm, "activity:read", contacts=[mine])
    page = await list_firm_audit_actions(ctx=scoped, limit=200)
    assert own_edit not in {r["id"] for r in page["items"]}

    # Nor through the contact filter, which is the obvious way to probe for one row at a time.
    probed = await list_firm_audit_actions(ctx=scoped, contact=str(theirs), limit=200)
    assert probed["items"] == []
