"""``POST /flows/{uid}/simulate`` and ``GET /flows/{uid}/insights`` — the two analysis surfaces.

The simulator's own behaviour is ``test_flows_simulate.py``'s job (pure, exhaustive) and the
aggregates are ``test_flows_analytics.py``'s. This file owns what only the HTTP layer can promise:

* **the dry run reaches a real client's real facts** — the whole reason to prefer a real subject
  over a described one is that it exercises the actual field scopes, so this seeds a client with
  accounts and asserts the branch resolves off them;
* **and writes nothing** — asserted three ways: no case, no case_step, no timer, no outbox row and
  no flow version afterwards; the savepoint rollback; and the import guard over in the pure suite;
* **the gates** — ``flows:write`` to dry-run (an authoring affordance), ``flows:read`` to read the
  funnel;
* **the RLS posture** — both endpoints hang off ``book_session``, not a ``service_role`` one, which
  is what makes the tenant boundary RLS's job and gives the admin portal `?tenant=` for free.

Endpoints are called as functions with a hand-built principal + the session fixture (the
``test_automations_api`` pattern): the dependency wiring is FastAPI's business, the gate, the SQL
and the no-writes guarantee are ours.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.db import book_session
from app.flows.definitions import validate_definition
from app.routers import flow_insights as api
from tests.conftest import SECCL_PLATFORM_SQL, api_routes


def _step(node_id: str, **kw: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": "step",
        "type": "task",
        "title": node_id,
        "scope": "participant",
        "assignee": "staff",
        "config": {},
        **kw,
    }


#: intake → offshore? ─┬─ offshore ─→ edd ─→ notify
#: ····················└─ else ──────────→ notify
DEFINITION = validate_definition(
    {
        "version": 3,
        "entry": "intake",
        "nodes": [
            _step("intake", next="risk"),
            {
                "id": "risk",
                "kind": "branch",
                "title": "Offshore?",
                "config": {
                    "branches": [
                        {
                            "id": "b1",
                            "label": "Offshore",
                            "conditions": {
                                "field": "account.custody",
                                "operator": "equals",
                                "value": "offshore",
                            },
                            "next": "edd",
                        },
                        {"id": "b2", "label": "Otherwise", "conditions": None, "next": "notify"},
                    ]
                },
            },
            _step("edd", next="notify"),
            _step("notify"),
        ],
    }
)


class Bed:
    """One firm, one offshore client with an account, and a published journey to dry-run."""

    def __init__(self, session: AsyncSession, **kw: Any) -> None:
        self.session = session
        self.__dict__.update(kw)

    def principal(self, *perms: str) -> Principal:
        return Principal(
            user_id=f"wos_{self.nonce}",
            organisation_id=f"org_{self.nonce}",
            permissions=list(perms),
        )


@pytest_asyncio.fixture
async def bed(service_session: AsyncSession) -> Bed:
    s = uuid.uuid4().hex[:8]

    async def scalar(sql: str, **params: Any) -> Any:
        return (await service_session.execute(text(sql), params)).scalar_one()

    tenant = int(
        await scalar(
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"fi_{s}",
            a=f"fi-{s}",
            n=f"FI {s}",
        )
    )
    contact = int(
        await scalar(
            "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email) "
            "VALUES (:t, :u, 'individual', 'Ada', 'Lovelace', :e) RETURNING id",
            t=tenant,
            u=f"con_{s}",
            e=f"ada_{s}@t.test",
        )
    )
    # The registry row still has to exist — `sync_platforms` seeds it on startup and the
    # merge resolves `platform.platforms.provider` — but the product no longer hangs off it
    # (0130 dropped `products.platform_id`), so the id is not bound to anything.
    await scalar(SECCL_PLATFORM_SQL)
    product = int(
        await scalar(
            "INSERT INTO platform.products (name, product_type) "
            "VALUES ('Offshore Bond', 'TPOFFBOND') RETURNING id",
        )
    )
    account = int(
        await scalar(
            "INSERT INTO platform.accounts (tenant_id, contact_id, product_id, status, uid, "
            "  custody) VALUES (:t, :c, :p, 'draft', :u, 'offshore') RETURNING id",
            t=tenant,
            c=contact,
            p=product,
            u=f"acc_{s}",
        )
    )
    flow = int(
        await scalar(
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, :c, 'Onboarding', 'published') RETURNING id",
            u=f"flw_{s}",
            t=tenant,
            c=f"onb_{s}",
        )
    )
    version = int(
        await scalar(
            "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
            "  definition, published_at) "
            "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb), now()) RETURNING id",
            u=f"fv_{s}",
            t=tenant,
            f=flow,
            d=json.dumps(DEFINITION),
        )
    )
    await service_session.execute(
        text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :f"),
        {"v": version, "f": flow},
    )
    return Bed(
        service_session,
        nonce=s,
        tenant=tenant,
        contact=contact,
        account=account,
        flow_uid=f"flw_{s}",
        version=version,
    )


async def _counts(session: AsyncSession) -> dict[str, int]:
    """Everything a dry run must not have created."""
    out: dict[str, int] = {}
    for name, table in (
        ("cases", "flows.cases"),
        ("steps", "flows.case_steps"),
        ("timers", "flows.timers"),
        ("listeners", "flows.case_listeners"),
        ("outbox", "flows.outbox"),
        ("versions", "flows.flow_versions"),
        ("memory", "flows.case_memory"),
        ("tags", "crm.contact_tags"),
    ):
        out[name] = int(
            (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
        )
    return out


# ---------------------------------------------------------------------------------------------
# The dry run
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_real_subject_drives_the_branch_off_their_own_account(bed: Bed) -> None:
    """The point of preferring a real subject: the offshore arm is taken because THIS client's
    account is offshore, not because the author said so."""
    result = await api.simulate_flow(
        bed.flow_uid,
        api.SimulateRequest(subject=api.SimulateSubject(contact_id=bed.contact)),
        principal=bed.principal("flows:write"),
        session=bed.session,
    )
    assert [h["node_id"] for h in result["path"]] == ["intake", "risk", "edd", "notify"]
    branch = result["path"][1]
    assert branch["taken_arm"] == "b1"
    assert branch["arms"][0]["explanation"]["actual"] == "offshore"
    assert result["subject"]["label"] == "Ada Lovelace"
    assert result["subject"]["accounts"][0]["id"] == bed.account
    assert result["facts"]["contact.type"] == "individual"


@pytest.mark.asyncio
async def test_the_dry_run_writes_absolutely_nothing(bed: Bed) -> None:
    before = await _counts(bed.session)
    await api.simulate_flow(
        bed.flow_uid,
        api.SimulateRequest(
            definition=DEFINITION, subject=api.SimulateSubject(contact_id=bed.contact)
        ),
        principal=bed.principal("flows:write"),
        session=bed.session,
    )
    assert await _counts(bed.session) == before


@pytest.mark.asyncio
async def test_a_hand_set_field_overrides_the_clients_real_one(bed: Bed) -> None:
    """Ask "what if this client were onshore" without editing the client — why `fields` exists."""
    result = await api.simulate_flow(
        bed.flow_uid,
        api.SimulateRequest(
            subject=api.SimulateSubject(
                contact_id=bed.contact, fields={"account.custody": "onshore"}
            )
        ),
        principal=bed.principal("flows:write"),
        session=bed.session,
    )
    assert [h["node_id"] for h in result["path"]] == ["intake", "risk", "notify"]


@pytest.mark.asyncio
async def test_a_described_subject_needs_no_client_at_all(bed: Bed) -> None:
    result = await api.simulate_flow(
        bed.flow_uid,
        api.SimulateRequest(
            subject=api.SimulateSubject(
                kind="synthetic", custody="offshore", products=["TPOFFBOND"]
            )
        ),
        principal=bed.principal("flows:write"),
        session=bed.session,
    )
    assert result["subject"]["kind"] == "synthetic"
    assert [h["node_id"] for h in result["path"]] == ["intake", "risk", "edd", "notify"]


@pytest.mark.asyncio
async def test_the_draft_on_screen_beats_the_last_saved_version(bed: Bed) -> None:
    """Testing something other than what is on screen is worse than not testing at all."""
    edited = validate_definition(
        {"version": 3, "entry": "only", "nodes": [_step("only", title="Only step")]}
    )
    result = await api.simulate_flow(
        bed.flow_uid,
        api.SimulateRequest(definition=edited, subject=api.SimulateSubject(contact_id=bed.contact)),
        principal=bed.principal("flows:write"),
        session=bed.session,
    )
    assert [h["node_id"] for h in result["path"]] == ["only"]
    assert result["version"] is None  # nothing stored was read, and nothing was saved


@pytest.mark.asyncio
async def test_an_unreadable_draft_is_a_422_carrying_the_reason(bed: Bed) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.simulate_flow(
            bed.flow_uid,
            api.SimulateRequest(
                definition={"nodes": [{"id": "a", "kind": "nonsense"}]},
                subject=api.SimulateSubject(contact_id=bed.contact),
            ),
            principal=bed.principal("flows:write"),
            session=bed.session,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["issues"]


@pytest.mark.asyncio
async def test_the_dry_run_needs_the_write_grant(bed: Bed) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.simulate_flow(
            bed.flow_uid,
            api.SimulateRequest(subject=api.SimulateSubject(contact_id=bed.contact)),
            principal=bed.principal("flows:read"),
            session=bed.session,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_a_real_subject_with_no_client_named_is_refused_helpfully(bed: Bed) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.simulate_flow(
            bed.flow_uid,
            api.SimulateRequest(subject=api.SimulateSubject()),
            principal=bed.principal("flows:write"),
            session=bed.session,
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_a_client_that_does_not_exist_is_a_404_not_an_empty_walk(bed: Bed) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.simulate_flow(
            bed.flow_uid,
            api.SimulateRequest(subject=api.SimulateSubject(contact_id=999_999_999)),
            principal=bed.principal("flows:write"),
            session=bed.session,
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------------------------
# The funnel
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_funnel_defaults_to_the_published_version_and_lists_the_rest(bed: Bed) -> None:
    result = await api.flow_insights(
        bed.flow_uid, principal=bed.principal("flows:read"), session=bed.session
    )
    assert result["version"]["is_current"] is True
    assert [v["version"] for v in result["versions"]] == [1]
    assert result["shape"] == "graph"
    assert [n["node_id"] for n in result["nodes"]] == ["intake", "risk", "edd", "notify"]
    assert {(e["from"], e["to"]) for e in result["edges"]} >= {("intake", "risk")}


@pytest.mark.asyncio
async def test_a_journey_nobody_has_run_is_an_empty_funnel_not_an_error(bed: Bed) -> None:
    result = await api.flow_insights(
        bed.flow_uid, principal=bed.principal("flows:read"), session=bed.session
    )
    assert result["cases"]["total"] == 0
    assert all(n["reached"] == 0 for n in result["nodes"])


@pytest.mark.asyncio
async def test_a_legacy_version_gets_counts_and_deliberately_no_edges(bed: Bed) -> None:
    """A legacy definition's edges are presentation order, so drop-off along them would be a funnel
    invented out of the order somebody happened to list the steps in."""
    legacy = validate_definition(
        {
            "steps": [
                {
                    "id": "a",
                    "type": "task",
                    "title": "A",
                    "scope": "participant",
                    "assignee": "staff",
                    "config": {},
                },
                {
                    "id": "b",
                    "type": "task",
                    "title": "B",
                    "scope": "participant",
                    "assignee": "staff",
                    "config": {},
                },
            ]
        }
    )
    uid = f"fvl_{bed.nonce}"
    await bed.session.execute(
        text(
            "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
            "  definition) VALUES (:u, :t, "
            "  (SELECT id FROM flows.flows WHERE uid = :fu), 2, 'draft', CAST(:d AS jsonb))"
        ),
        {"u": uid, "t": bed.tenant, "fu": bed.flow_uid, "d": json.dumps(legacy)},
    )
    result = await api.flow_insights(
        bed.flow_uid, version=uid, principal=bed.principal("flows:read"), session=bed.session
    )
    assert result["shape"] == "legacy"
    assert [n["node_id"] for n in result["nodes"]] == ["a", "b"]
    assert result["edges"] == []


@pytest.mark.asyncio
async def test_the_funnel_needs_the_read_grant(bed: Bed) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.flow_insights(
            bed.flow_uid, principal=bed.principal("cases:read"), session=bed.session
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_an_unknown_journey_is_a_404(bed: Bed) -> None:
    with pytest.raises(HTTPException) as exc:
        await api.flow_insights(
            "flw_nope", principal=bed.principal("flows:read"), session=bed.session
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------------------------
# Posture
# ---------------------------------------------------------------------------------------------


def test_both_endpoints_run_under_rls_on_a_book_session() -> None:
    """The tenant boundary on this surface is RLS, not a ``tenant_id`` the handler carries. Swapping
    either endpoint to a ``service_role`` session would silently remove that boundary AND lose the
    admin portal's read-only `?tenant=` mount, so the dependency is pinned rather than trusted."""
    from fastapi.routing import APIRoute

    from app.main import app

    for path in ("/flows/{uid}/simulate", "/flows/{uid}/insights"):
        route = next(r for r in api_routes(app) if isinstance(r, APIRoute) and r.path == path)
        deps = {d.call for d in route.dependant.dependencies}
        assert book_session in deps, f"{path} does not depend on book_session"


def test_only_the_funnel_is_mountable_by_an_operations_book_reader() -> None:
    """The module header used to say the admin portal mounts BOTH surfaces read-only over
    ``?tenant=``. It cannot: the book read overlay grants ``flows:read`` and no write slug, so the
    dry run (``flows:write``) is reachable with ``?tenant=`` only by a ``system`` operator.

    Pinned in the direction the sentence must stay true — the repair is the sentence, never the
    overlay. A write slug in ``_BOOK_READ_VISIBILITY`` would be a hole in the read-only guarantee
    that no reader of this file would ever find.
    """
    from app.db import _BOOK_READ_VISIBILITY
    from app.permissions import FLOWS_READ, FLOWS_WRITE

    assert FLOWS_READ.slug in _BOOK_READ_VISIBILITY
    assert FLOWS_WRITE.slug not in _BOOK_READ_VISIBILITY


def test_the_analytics_indexes_exist() -> None:
    """0047's indexes are the difference between a page a firm leaves open and one that scans the
    table. Pinned here because an index nobody asserts is an index a later migration can drop."""
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parent.parent / "migrations" / "sql" / "0047_flow_analytics.sql"
    ).read_text(encoding="utf-8")
    assert "cases_tenant_version_created_idx" in sql
    assert "case_steps_funnel_idx" in sql


def test_the_app_dispatches_both_paths_to_this_module() -> None:
    """Starlette dispatches on the FIRST route that fully matches, and this module is registered
    AFTER `routers/flows.py` — so a future `/flows/{uid}/{something}` added there would silently
    swallow both of these. That is exactly how `GET /flows/dead-letters` once shipped dead
    (`test_flows_routes.py`), so it is pinned rather than reasoned about."""
    from starlette.routing import Match

    from app.main import app

    def scope(path: str, method: str) -> dict[str, Any]:
        return {
            "type": "http",
            "path": path,
            "method": method,
            "path_params": {},
            "headers": [],
            "query_string": b"",
            "root_path": "",
        }

    for path, method, name in (
        ("/flows/flw_abc/simulate", "POST", "simulate-flow"),
        ("/flows/flw_abc/insights", "GET", "flow-insights"),
    ):
        # `api_routes` rather than `app.routes`: since FastAPI 0.141 the latter holds one opaque
        # wrapper per included router, which matches (it delegates) but carries no `name` — so a
        # shadowing check against it reports every path as unresolved. The flattened list keeps the
        # app's resolution order, which is what "another route shadows it" is about.
        matched = next(
            (r for r in api_routes(app) if r.matches(scope(path, method))[0] == Match.FULL),
            None,
        )
        assert matched is not None and getattr(matched, "name", None) == name, (
            f"{method} {path} resolves to {getattr(matched, 'name', None)!r}, not {name!r} — "
            "another route shadows it"
        )
