"""Flow insights — the dry run before publishing, and the step funnel after.

Two endpoints, deliberately in their own module rather than bolted onto ``routers/flows.py``:
that router is the authoring CRUD surface and these are read-only analysis, they share no state
with it, and keeping them apart means the definition endpoints stay the small, heavily-validated
thing they are.

* ``POST /flows/{uid}/simulate`` (``flows:write``) — **a dry run. Nothing is performed.** Walk a
  draft definition for a real client out of the firm's own book (or a subject the author describes
  by hand) and report the path, each branch's verdict with the values it read, what would be
  planned at which tier, where it pauses, and where it ends.
* ``GET /flows/{uid}/insights`` (``flows:read``) — the step funnel for one published version over
  a date range: reach, outcome split, time-to-complete percentiles, and the drop-off along every
  authored edge.

Both run on ``book_session``, which is ``tenant_session`` (RLS ``authenticated``) plus the
operations-only ``?tenant=<uid>`` target, so neither needs a second code path for the admin
portal. **Only the funnel is actually mountable there**, and deliberately: ``db.py``'s
``_BOOK_READ_VISIBILITY`` — the slugs an operations reader borrows for one ``?tenant=`` request —
grants ``flows:read`` and no write slug at all, on purpose (a write slug in a read overlay is a
gap nobody would find again). The dry run gates on ``flows:write``, so with ``?tenant=`` it is
reachable only by a ``system`` operator. That is the intended shape: a dry run is an AUTHORING
affordance (see the endpoint's own docstring), and Graphene does not author a firm's journeys —
it reads them. Widening the overlay to make this sentence true would be the wrong repair.

Why the dry run cannot perform anything
---------------------------------------
Three independent guarantees, none relying on the others:

1. ``app/flows/simulate.py`` imports no module that can write, send or call out — pinned by
   ``tests/test_flows_simulate.py``. There is no code path from here to an executor because the
   executor is not in the import graph.
2. The whole request runs inside a savepoint this handler always rolls back. Even a future read
   that turned out to write could not commit.
3. The definition is validated and simulated in memory; it is never saved. An author dry-running a
   draft does not create a version.

A POST that writes nothing still reads as mutating to the action-event tripwire, and that is
correct rather than something to exempt: it authenticates through ``get_current_principal`` (via
``book_session``), so it lands one ``flow.simulated`` row like every other authored
act. "Who dry-ran this journey against which client" is a reasonable thing for a firm's activity
log to carry — the rule tier's ``POST /automations/{uid}/test`` is on the same footing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, get_current_principal, require_permission
from app.db import book_session
from app.flows import analytics, planning
from app.flows import definitions as definitions_mod
from app.flows import graph as graph_mod
from app.flows import simulate as simulate_mod
from app.permissions import FLOWS_READ, FLOWS_WRITE

router = APIRouter(tags=["flows"])

#: How many of a contact's accounts the tier fan-out is modelled over. A client with forty accounts
#: would fan a per-account step forty ways and turn a dry run into a wall; the point of the preview
#: is the SHAPE of the journey, and ten accounts show every shape twelve would.
MAX_SIMULATED_ACCOUNTS = 10

#: The default insights window. Ninety days is long enough for an onboarding cohort to have
#: finished and short enough that a firm is looking at how the journey behaves now.
DEFAULT_WINDOW_DAYS = 90


# ---------------------------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------------------------


class SimulateSubject(BaseModel):
    """Who the dry run is about.

    ``real`` is the useful one and the default: pointing at an actual client exercises the actual
    field scopes, which is the difference between testing a branch and testing your own idea of
    what the data looks like. ``synthetic`` exists for the case a real subject cannot cover — "what
    happens to an offshore SIPP client" for a firm that has not onboarded one yet.
    """

    kind: Literal["real", "synthetic"] = "real"

    #: real
    contact_id: int | None = None
    #: Which of the client's accounts to fan account-scoped steps over. Absent ⇒ all of them, up to
    #: ``MAX_SIMULATED_ACCOUNTS``.
    account_ids: list[int] | None = None

    #: synthetic
    contact_type: str = "individual"
    roles: list[str] = Field(default_factory=lambda: ["owner"])
    custody: str | None = None
    products: list[str] = Field(default_factory=list)
    has_transfer: bool = False

    #: both — the author's hand-set values. ``memory`` seeds case memory (what an automation or an
    #: adviser's intake would inject); ``fields`` overrides any dotted key in the condition
    #: registry, which is how an author tests "what if this client were a US person" without
    #: editing the client.
    memory: dict[str, Any] | None = None
    fields: dict[str, Any] | None = None


class SimulateRequest(BaseModel):
    """The draft on screen plus a subject.

    ``definition`` is optional and overriding it is the point: an author tests the graph they are
    looking at, not the last thing they saved. Absent ⇒ the version named by ``version_uid``, or
    the flow's newest version.
    """

    definition: dict[str, Any] | None = None
    version_uid: str | None = None
    subject: SimulateSubject = Field(default_factory=SimulateSubject)
    #: Pin the clock. Test-facing, and it also lets an author ask "what if this ran on a Monday".
    now: datetime | None = None


# ---------------------------------------------------------------------------------------------
# Subject resolution (reads only — every statement here is a SELECT)
# ---------------------------------------------------------------------------------------------


async def _real_subject(
    session: AsyncSession, subject: SimulateSubject
) -> tuple[planning.ApplicationGraph, dict[str, Any], dict[str, Any]]:
    """Build the tier shape and the condition context for a client out of the firm's own book.

    The context is the same vocabulary and the same columns ``orchestrator.case_context`` and
    ``automation.build_facts`` read — deliberately, because a dry run whose context differs from
    the runtime's is a dry run that lies. Two known absences are inherited rather than papered
    over: ``account.value`` is lake-derived and the runtime does not fetch it either, and an
    unresolved field reads as ``None`` here exactly as it would there.

    RLS is the boundary. The session is ``authenticated`` and every table below carries a policy,
    so a contact outside the caller's book simply does not come back — no ``tenant_id`` predicate
    from the wire, and no cross-firm dry run.
    """
    if subject.contact_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pick a client to dry-run against, or switch to a described subject.",
        )
    contact = (
        (
            await session.execute(
                text(
                    "SELECT c.id, c.tenant_id, c.type::text AS type, "
                    "       c.service_type::text AS service_type, c.sub_type::text AS sub_type, "
                    "       c.enabled, c.nationality, "
                    "       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), "
                    "                c.name, 'Client') AS display_name, "
                    "       EXISTS (SELECT 1 FROM crm.contact_users cu "
                    "               WHERE cu.contact_id = c.id) AS has_login "
                    "FROM crm.contacts c WHERE c.id = :id"
                ),
                {"id": subject.contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such client in this firm.")

    from app import tags as tags_mod

    tenant_id = int(contact["tenant_id"])
    account_rows = (
        (
            await session.execute(
                text(
                    "SELECT a.id, p.product_type::text AS product, a.custody::text AS custody, "
                    "       a.status::text AS status, (a.transfer IS NOT NULL) AS has_transfer, "
                    "       (a.model_id IS NOT NULL) AS discretionary, "
                    "       COALESCE(ac.role::text, 'owner') AS role "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "LEFT JOIN crm.account_contacts ac "
                    "       ON ac.account_id = a.id AND ac.contact_id = :cid "
                    "WHERE (a.contact_id = :cid OR ac.contact_id = :cid) "
                    "  AND (:filtered = false OR a.id = ANY(:ids)) "
                    "ORDER BY a.id LIMIT :cap"
                ),
                {
                    "cid": subject.contact_id,
                    "filtered": bool(subject.account_ids),
                    "ids": list(subject.account_ids or []),
                    "cap": MAX_SIMULATED_ACCOUNTS,
                },
            )
        )
        .mappings()
        .all()
    )

    roles = frozenset(str(r["role"]) for r in account_rows) or frozenset({"owner"})
    custody = next((r["custody"] for r in account_rows if r["custody"]), None)
    app_graph = planning.ApplicationGraph(
        participants=(
            planning.Participant(
                contact_id=int(contact["id"]),
                type=str(contact["type"] or "individual"),
                roles=roles,
            ),
        ),
        accounts=tuple(
            planning.AccountSpec(
                account_id=int(r["id"]),
                participant_contact_ids=(int(contact["id"]),),
                product=r["product"],
                custody=r["custody"],
                has_transfer=bool(r["has_transfer"]),
            )
            for r in account_rows
        ),
        custody=custody,
    )

    facts: dict[str, Any] = {
        "contact.type": contact["type"],
        "contact.service_type": contact["service_type"],
        "contact.sub_type": contact["sub_type"],
        "contact.enabled": contact["enabled"],
        "contact.nationality": contact["nationality"],
        "contact.has_login": contact["has_login"],
        "contact.tags": await tags_mod.list_for(
            session, target="contact", target_id=int(contact["id"]), tenant_id=tenant_id
        ),
        "application.custody": custody,
        "application.has_pension_product": app_graph.has_pension_product,
        "application.has_sipp_product": app_graph.has_sipp_product,
        "application.has_transfer": app_graph.has_transfer,
        # A dry run is about a case that does not exist yet, so the case scope is what a brand-new
        # one would carry. Reporting a running case's status here would answer a question the
        # author did not ask, and a branch on `case.status` would then read a value the simulated
        # journey never had.
        "case.status": "draft",
        "case.tags": [],
    }
    if account_rows:
        first = account_rows[0]
        facts.update(
            {
                "account.product": first["product"],
                "account.custody": first["custody"],
                "account.status": first["status"],
                "account.has_transfer": bool(first["has_transfer"]),
                "account.discretionary": bool(first["discretionary"]),
            }
        )
    described = {
        "kind": "real",
        "contact_id": int(contact["id"]),
        "label": str(contact["display_name"]),
        "accounts": [
            {"id": int(r["id"]), "product": r["product"], "status": r["status"]}
            for r in account_rows
        ],
        "truncated": len(account_rows) == MAX_SIMULATED_ACCOUNTS,
    }
    return app_graph, facts, described


def _synthetic_subject(
    subject: SimulateSubject, now: datetime
) -> tuple[planning.ApplicationGraph, dict[str, Any], dict[str, Any]]:
    app_graph = simulate_mod.synthetic_graph(
        contact_type=subject.contact_type,
        roles=subject.roles,
        custody=subject.custody,
        products=subject.products,
        has_transfer=subject.has_transfer,
    )
    facts = simulate_mod.synthetic_facts(app_graph, memory=subject.memory, now=now)
    described = {
        "kind": "synthetic",
        "contact_id": None,
        "label": f"A described {subject.contact_type}",
        "accounts": [{"id": None, "product": p, "status": "draft"} for p in subject.products],
        "truncated": False,
    }
    return app_graph, facts, described


# ---------------------------------------------------------------------------------------------
# The dry run
# ---------------------------------------------------------------------------------------------


async def _flow_row(session: AsyncSession, uid: str) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT f.id, f.uid, f.code, f.name, f.current_published_version_id "
                    "FROM flows.flows f WHERE f.uid = :u"
                ),
                {"u": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such journey.")
    return dict(row)


@router.post("/flows/{uid}/simulate", name="simulate-flow")
async def simulate_flow(
    uid: str,
    body: SimulateRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> dict[str, Any]:
    """**A DRY RUN. Nothing is performed, nothing is written, nothing is queued, nothing is
    sent.**

    Walk this definition for one subject and report the path it takes: the nodes entered in order,
    which arm each branch takes and the values each condition read, what would be planned at which
    tier and with what fan-out, which nodes this subject never reaches and why, where the journey
    pauses and until when, and how it ends.

    Gated on ``flows:write``, matching the rule tier's dry run: it is an authoring affordance that
    reads a client's facts to answer a question about a draft, and the read grant's job is viewing
    journeys, not testing them.
    """
    require_permission(principal, FLOWS_WRITE)
    flow = await _flow_row(session, uid)
    now = (body.now or datetime.now(UTC)).astimezone(UTC)

    raw = body.definition
    version_meta: dict[str, Any] | None = None
    if raw is None:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT v.uid, v.version, v.status, v.definition "
                        "FROM flows.flow_versions v "
                        "WHERE v.flow_id = :f "
                        # CAST because psycopg cannot infer a NULL parameter's type here, and
                        # `version_uid` is legitimately absent (⇒ the newest version).
                        "  AND (CAST(:vuid AS text) IS NULL OR v.uid = :vuid) "
                        "ORDER BY v.version DESC LIMIT 1"
                    ),
                    {"f": flow["id"], "vuid": body.version_uid},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="This journey has no version to dry-run yet."
            )
        raw = dict(row["definition"] or {})
        version_meta = {"uid": row["uid"], "version": row["version"], "status": row["status"]}

    # Validate the SAME way a save would. A dry run over a shape the save path would have rewritten
    # is the first way a dry run starts lying — and it also means an author gets the definition's
    # real errors here rather than a plausible-looking walk over a broken graph.
    try:
        definition = definitions_mod.validate_definition(raw)
    except definitions_mod.DefinitionError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "This journey can’t be read.", "issues": [str(exc)]},
        ) from exc

    # The second, independent no-writes guarantee (see the module docstring): everything below runs
    # in a savepoint that is always rolled back, so nothing this request touched can commit even if
    # a future read grew a write.
    savepoint = await session.begin_nested()
    try:
        if body.subject.kind == "synthetic":
            app_graph, facts, described = _synthetic_subject(body.subject, now)
        else:
            app_graph, facts, described = await _real_subject(session, body.subject)
        if body.subject.memory:
            facts["memory"] = dict(body.subject.memory)
        for key, value in (body.subject.fields or {}).items():
            facts[str(key)] = value
        facts.setdefault("time.hour", now.hour)
        facts.setdefault("time.weekday", now.weekday())
        facts["time.now"] = now
        result = simulate_mod.simulate(definition, facts=facts, app_graph=app_graph, now=now)
    finally:
        await savepoint.rollback()

    return {
        **result.as_dict(),
        "flow": {"uid": flow["uid"], "code": flow["code"], "name": flow["name"]},
        "version": version_meta,
        "subject": described,
        "issues": definitions_mod.publish_issues(definition),
        "simulated_at": now,
    }


# ---------------------------------------------------------------------------------------------
# The step funnel
# ---------------------------------------------------------------------------------------------


@router.get("/flows/{uid}/insights", name="flow-insights")
async def flow_insights(
    uid: str,
    version: str | None = Query(default=None, description="Version uid; default the published one"),
    since: datetime | None = None,
    until: datetime | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> dict[str, Any]:
    """The step funnel for one flow VERSION over one date range.

    The version is the cohort and it is never implicit across versions — see
    ``app/flows/analytics.py`` for why comparing a definition against a different definition is how
    a firm reaches a confident wrong conclusion. The default is the currently published version,
    which is the one an author is looking at; ``versions`` in the response feeds the picker.
    """
    require_permission(principal, FLOWS_READ)
    flow = await _flow_row(session, uid)
    version_list = await analytics.versions(session, flow_uid=uid)
    if not version_list:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This journey has no versions yet.")

    chosen = next((v for v in version_list if v["uid"] == version), None)
    if chosen is None:
        chosen = next((v for v in version_list if v["is_current"]), version_list[0])

    end = (until or datetime.now(UTC)).astimezone(UTC)
    start = (since or (end - timedelta(days=DEFAULT_WINDOW_DAYS))).astimezone(UTC)

    row = (
        (
            await session.execute(
                text("SELECT id, definition FROM flows.flow_versions WHERE uid = :u"),
                {"u": chosen["uid"]},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:  # pragma: no cover — `versions` just returned it under the same RLS scope
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such version.")

    aggregates = await analytics.funnel(session, version_id=int(row["id"]), since=start, until=end)
    try:
        parsed = graph_mod.parse(dict(row["definition"] or {}))
    except graph_mod.DefinitionError:
        # A stored version that no longer parses is a real possibility (it predates a rule we
        # added). The counts are still true and still worth showing, so report them with no graph
        # rather than 500 a page whose data is fine.
        return {
            "flow": {"uid": flow["uid"], "code": flow["code"], "name": flow["name"]},
            "version": chosen,
            "versions": version_list,
            "range": {"since": start, "until": end},
            "shape": "unreadable",
            "cases": aggregates["cases"],
            "nodes": [],
            "edges": [],
        }

    nodes = analytics.funnel_nodes(
        parsed,
        steps=aggregates["steps"],
        visits=aggregates["visits"],
        branch_choices=aggregates["branch_choices"],
    )
    return {
        "flow": {"uid": flow["uid"], "code": flow["code"], "name": flow["name"]},
        "version": chosen,
        "versions": version_list,
        "range": {"since": start, "until": end},
        "shape": "legacy" if parsed.is_legacy_linear else "graph",
        "cases": aggregates["cases"],
        "nodes": nodes,
        # A legacy definition's edges are PRESENTATION order, not execution order (graph.py's
        # module docstring). Reporting drop-off along them would invent a funnel out of the order
        # somebody happened to list the steps in, so a legacy version gets counts and no edges.
        "edges": ([] if parsed.is_legacy_linear else analytics.funnel_edges(parsed, nodes)),
    }
