"""Flows — the config-driven process engine's authoring HTTP surface.

Staff author flow **definitions** here (``tenant_session``, gated ``flows:*``): CRUD + version +
publish, with strict validation (unknown step types/config keys rejected) and DB reference checks at
publish (the form/document/template/platform a step points at must exist + be published). RLS scopes
every read/write to the tenant.

Running a flow is the orchestration engine's job, not this router: an *execution* over an
application is driven by ``app/routers/applications.py`` (the v2 tiered engine); the client fulfils
steps through the document-request endpoints, which trip the orchestrator.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import listing, providers
from app.addressing import dead_letter_row_id
from app.auth import Principal, get_current_principal, require_permission
from app.db import (
    FirmContext,
    book_session,
)
from app.db import (
    target_firm_session as firm_session,
)
from app.db import (
    target_tenant_session as tenant_session,
)
from app.flows import definitions, handlers, portability, triggers
from app.flows import graph as graph_mod
from app.flows.graph import Issue
from app.ids import new_id
from app.listing import DEFAULT_LIMIT
from app.permissions import FLOWS_READ, FLOWS_WRITE

router = APIRouter(tags=["flows"])

# The crm.account_role enum values (0002 + 0109). A setup_account step's role must be one of
# these, else the CAST(:role AS crm.account_role) would 500 — checked at publish validation.
_ACCOUNT_ROLES = (
    "owner",
    "decision_maker",
    "trustee",
    "beneficiary",
    "attorney",
    "other",
    "registered_contact",
)

Slug = Field(pattern="^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")


# ============================================================================
# Authoring — flow definitions (staff, tenant_session, flows:*)
# ============================================================================
class CreateFlow(BaseModel):
    code: str = Slug
    name: str
    description: str | None = None
    entity: str | None = None


class UpdateFlow(BaseModel):
    name: str | None = None
    description: str | None = None
    entity: str | None = None
    # 'archived' retires the flow (it disappears from the "New journey" picker); 'published' or
    # 'draft' brings it back. Running executions are untouched — they run on a pinned version.
    status: str | None = None


_FLOW_STATUSES = ("draft", "published", "archived")


class SaveVersion(BaseModel):
    definition: dict[str, Any]


class FlowOut(BaseModel):
    uid: str
    code: str
    name: str
    description: str | None
    entity: str | None
    status: str
    version_count: int
    published_version: int | None
    # The published version's intake block (what "New journey" collects) — drives the kickoff.
    intake: dict[str, Any] | None = None


class FlowVersionOut(BaseModel):
    uid: str
    version: int
    status: str
    definition: dict[str, Any]
    published_at: datetime | None


_FLOW_SELECT = (
    "SELECT f.uid, f.code, f.name, f.description, f.entity, f.status, "
    "       (SELECT count(*) FROM flows.flow_versions v WHERE v.flow_id = f.id) AS version_count, "
    "       (SELECT v.version FROM flows.flow_versions v "
    "          WHERE v.id = f.current_published_version_id) AS published_version, "
    "       (SELECT v.definition -> 'intake' FROM flows.flow_versions v "
    "          WHERE v.id = f.current_published_version_id) AS intake "
    "FROM flows.flows f "
)


async def _flow_row(session: AsyncSession, uid: str) -> dict[str, Any] | None:
    row = (
        (await session.execute(text(_FLOW_SELECT + "WHERE f.uid = :u"), {"u": uid}))
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


@router.get("/flows", name="list-flows")
async def list_flows(
    # Searched in SQL rather than in the browser: the page filters `name` and `code`, and doing it
    # here is what makes its question reach past the rows already sent.
    q: str | None = None,
    # The New-journey picker and the listing's start menu only want published flows. Fetching the
    # whole catalogue (drafts included) and filtering in the portal was how those pages paid for
    # authoring data they never drew. Aliased so this argument cannot shadow FastAPI's `status`.
    flow_status: Annotated[str | None, Query(alias="status")] = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> list[FlowOut]:
    """The firm's flow definitions (RLS-scoped). ``q`` matches the way the search bar does
    (``listing.search_sql`` over ``_search``); ``status`` is one of draft/published/archived."""
    require_permission(principal, FLOWS_READ)
    if flow_status is not None and flow_status not in _FLOW_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown flow status."
        )
    clauses: list[str] = []
    params: dict[str, object] = {}
    if q and q.strip():
        # The Spotlight bar's match over `_search` (uid, code, name, description).
        await session.execute(text(listing.FUZZY_THRESHOLD_SQL))
        clauses.append(listing.search_sql("f._search"))
        params.update(listing.search_params(q))
    if flow_status:
        clauses.append("f.status = :status")
        params["status"] = flow_status
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    rows = (
        (
            await session.execute(
                # `where` is fixed text plus bound parameters — no caller string reaches the SQL.
                text(_FLOW_SELECT + where + "ORDER BY f.name"),  # noqa: S608
                params,
            )
        )
        .mappings()
        .all()
    )
    return [FlowOut(**dict(r)) for r in rows]


@router.post("/flows", name="create-flow", status_code=status.HTTP_201_CREATED)
async def create_flow(
    body: CreateFlow,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FlowOut:
    """Create a flow with an initial empty draft version (mirrors templates/forms)."""
    require_permission(principal, FLOWS_WRITE)
    dup = (
        await session.execute(
            text("SELECT 1 FROM flows.flows WHERE tenant_id = auth.tenant_id() AND code = :c"),
            {"c": body.code},
        )
    ).first()
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT, "A flow with that code already exists.")
    flow_id = (
        await session.execute(
            text(
                "INSERT INTO flows.flows (uid, tenant_id, code, name, description, entity, "
                "  created_by) VALUES (:uid, auth.tenant_id(), :code, :name, :desc, :entity, "
                "  auth.uid()) RETURNING id"
            ),
            {
                "uid": new_id(),
                "code": body.code,
                "name": body.name,
                "desc": body.description,
                "entity": body.entity,
            },
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, created_by) "
            "VALUES (:uid, auth.tenant_id(), :fid, 1, auth.uid())"
        ),
        {"uid": new_id(), "fid": flow_id},
    )
    row = (
        (await session.execute(text(_FLOW_SELECT + "WHERE f.id = :id"), {"id": flow_id}))
        .mappings()
        .one()
    )
    return FlowOut(**dict(row))


# ============================================================================
# Engine hygiene — dead letters (staff, firm_session, flows:write)
#
# ``flows.outbox`` is deny-all for `authenticated` (delivery is service_role's job), and a
# dead-lettered row used to be visible only in the worker log — so a mis-authored raise-a-task spec
# silently lost the compliance follow-up and recovery meant psql. These two endpoints give the firm
# that authored the spec an operator view and a replay: fix the role slug, requeue, done. The same
# treatment for a render job the DocumentWorker gave up on.
#
# Elevated (firm_session) BECAUSE the queue tables are service_role-only; every query is forced to
# the caller's own resolved tenant_id, and `flows:write` gates it (whoever authors the side-effects
# owns their failures).
#
# DECLARED BEFORE ``/flows/{uid}`` ON PURPOSE. Starlette dispatches on the FIRST full match, so a
# literal path that looks like a path parameter must be registered ahead of it — declared after,
# every request for /flows/dead-letters landed in ``get_flow`` with uid='dead-letters' and 404'd.
# ``tests/test_flows_routes.py`` locks the order.
# ============================================================================
class DeadLetterOut(BaseModel):
    id: int
    kind: str  # 'outbox' | 'render'
    event_type: str
    attempts: int
    max_attempts: int
    last_error: str | None
    last_attempt: datetime | None
    created_at: datetime
    # What the row was trying to do, in the operator's terms (the task title / document name).
    summary: str | None = None
    payload: dict[str, Any] | None = None


@router.get("/flows/dead-letters", name="list-dead-letters")
async def list_dead_letters(
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
    limit: int = DEFAULT_LIMIT,
) -> list[DeadLetterOut]:
    """Side-effects the engine gave up on: outbox rows past ``max_attempts`` (or permanently
    rejected), plus render jobs that failed. Newest first."""
    require_permission(principal, FLOWS_WRITE)
    limit = max(1, min(limit, 200))
    outbox = (
        (
            await ctx.session.execute(
                text(
                    "SELECT id, uid, event_type, attempts, max_attempts, last_error, last_attempt, "
                    "       created_at, payload FROM flows.outbox "
                    "WHERE tenant_id = :t AND status = 'dead_letter' "
                    "ORDER BY id DESC LIMIT :lim"
                ),
                {"t": ctx.tenant_id, "lim": limit},
            )
        )
        .mappings()
        .all()
    )
    renders = (
        (
            await ctx.session.execute(
                text(
                    "SELECT j.id, j.uid, j.attempts, j.max_attempts, j.last_error, j.claimed_at, "
                    "       j.created_at, d.name AS document_name FROM documents.render_jobs j "
                    "LEFT JOIN documents.documents d ON d.id = j.document_id "
                    "WHERE j.tenant_id = :t AND j.status = 'failed' "
                    "ORDER BY j.id DESC LIMIT :lim"
                ),
                {"t": ctx.tenant_id, "lim": limit},
            )
        )
        .mappings()
        .all()
    )
    out: list[DeadLetterOut] = []
    for r in outbox:
        payload = r["payload"] if isinstance(r["payload"], dict) else None
        spec = (payload or {}).get("spec") or {}
        out.append(
            DeadLetterOut(
                id=int(r["id"]),
                kind="outbox",
                event_type=str(r["event_type"]),
                attempts=int(r["attempts"]),
                max_attempts=int(r["max_attempts"]),
                last_error=r["last_error"],
                last_attempt=r["last_attempt"],
                created_at=r["created_at"],
                summary=spec.get("title") if isinstance(spec, dict) else None,
                payload=payload,
            )
        )
    for r in renders:
        out.append(
            DeadLetterOut(
                id=int(r["id"]),
                kind="render",
                event_type="document.render",
                attempts=int(r["attempts"]),
                max_attempts=int(r["max_attempts"]),
                last_error=r["last_error"],
                last_attempt=r["claimed_at"],
                created_at=r["created_at"],
                summary=r["document_name"],
            )
        )
    out.sort(key=lambda d: d.created_at, reverse=True)
    return out[:limit]


@router.post("/flows/dead-letters/{kind}/{row_uid}/replay", name="replay-dead-letter")
async def replay_dead_letter(
    kind: str,
    row_id: Annotated[int, Depends(dead_letter_row_id)],
    principal: Principal = Depends(get_current_principal),
    ctx: FirmContext = Depends(firm_session),
) -> dict[str, object]:
    """Requeue a dead letter so the worker retries it — the recovery path after fixing whatever the
    error named (a role that now exists, a template that now renders). Attempts reset to 0 so the
    row gets its full budget again."""
    require_permission(principal, FLOWS_WRITE)
    if kind not in ("outbox", "render"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown dead-letter kind.")
    if kind == "outbox":
        requeued = (
            await ctx.session.execute(
                text(
                    "UPDATE flows.outbox SET status = 'queued', attempts = 0, "
                    "  next_retry = now(), last_error = NULL "
                    "WHERE id = :i AND tenant_id = :t AND status = 'dead_letter' RETURNING id"
                ),
                {"i": row_id, "t": ctx.tenant_id},
            )
        ).scalar_one_or_none()
    else:
        requeued = (
            await ctx.session.execute(
                text(
                    "UPDATE documents.render_jobs SET status = 'queued', attempts = 0, "
                    "  run_after = now(), last_error = NULL, claimed_at = NULL "
                    "WHERE id = :i AND tenant_id = :t AND status = 'failed' RETURNING id"
                ),
                {"i": row_id, "t": ctx.tenant_id},
            )
        ).scalar_one_or_none()
    if requeued is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No dead letter with that id.")
    return {"ok": True, "id": int(requeued)}


# ============================================================================
# Portability — a flow as a file, and the file back into a firm
#
# Declared before ``/flows/{uid}`` for the same reason the dead-letter routes are: ``POST
# /flows/import`` is a literal path. The export sits under the uid and needs no such care.
# ============================================================================
class ImportFlow(BaseModel):
    """The file, as parsed JSON, plus an optional code to import it under."""

    bundle: dict[str, Any]
    code: str | None = Field(default=None, pattern="^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")


class ImportedRefs(BaseModel):
    created: list[str]
    reused: list[str]


class FlowImportOut(BaseModel):
    flow: FlowOut
    created: bool
    version_uid: str
    forms: ImportedRefs
    templates: ImportedRefs
    warnings: list[str]


@router.post("/flows/import", name="import-flow", status_code=status.HTTP_201_CREATED)
async def import_flow(
    body: ImportFlow,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FlowImportOut:
    """Bring an exported flow into this firm as a **draft**: its forms and templates are matched
    by code and created when missing, the definition is rewritten to this firm's uids, and the
    flow is created (or, when the code is taken, given its next draft version). Nothing is
    published — the author publishes, and publish runs every reference check."""
    require_permission(principal, FLOWS_WRITE)
    try:
        result = await portability.import_bundle(session, body.bundle, code=body.code)
    except portability.BundleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except definitions.DefinitionError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"The exported definition is not valid: {exc}"
        ) from exc
    row = await _flow_row(session, str(result["flow_uid"]))
    if row is None:  # pragma: no cover
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unexpected error")
    return FlowImportOut(
        flow=FlowOut(**row),
        created=bool(result["created"]),
        version_uid=str(result["version_uid"]),
        forms=ImportedRefs(**result["forms"]),
        templates=ImportedRefs(**result["templates"]),
        warnings=list(result["warnings"]),
    )


@router.get("/flows/{uid}/export", name="export-flow")
async def export_flow(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> dict[str, Any]:
    """The flow as a file another firm can import: its published definition (else its latest
    draft) with every form and template it names, each by code. ``portability`` says what does
    not travel."""
    require_permission(principal, FLOWS_READ)
    bundle = await portability.export_bundle(session, uid)
    if bundle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flow not found")
    return bundle


@router.get("/flows/{uid}", name="get-flow")
async def get_flow(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> FlowOut:
    require_permission(principal, FLOWS_READ)
    row = await _flow_row(session, uid)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flow not found")
    return FlowOut(**row)


@router.patch("/flows/{uid}", name="update-flow")
async def update_flow(
    uid: str,
    body: UpdateFlow,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FlowOut:
    """Rename/describe a flow, or archive it (``status``). Archiving only changes what new journeys
    may start from — executions already running keep their pinned version."""
    require_permission(principal, FLOWS_WRITE)
    if body.status is not None and body.status not in _FLOW_STATUSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"status must be one of {', '.join(_FLOW_STATUSES)}.",
        )
    if body.status == "published":
        published = (
            await session.execute(
                text("SELECT current_published_version_id FROM flows.flows WHERE uid = :u"),
                {"u": uid},
            )
        ).scalar_one_or_none()
        if published is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "This flow has no published version to restore — publish a version instead.",
            )
    row = (
        await session.execute(
            text(
                "UPDATE flows.flows SET name = COALESCE(:name, name), "
                "  description = COALESCE(:desc, description), entity = COALESCE(:entity, entity), "
                "  status = COALESCE(:st, status) "
                "WHERE uid = :u RETURNING id"
            ),
            {
                "name": body.name,
                "desc": body.description,
                "entity": body.entity,
                "st": body.status,
                "u": uid,
            },
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flow not found")
    result = await _flow_row(session, uid)
    if result is None:  # pragma: no cover
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unexpected error")
    return FlowOut(**result)


async def _version_row(session: AsyncSession, flow_uid: str, vid: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT v.uid, v.version, v.status, v.definition, v.published_at "
                    "FROM flows.flow_versions v JOIN flows.flows f ON f.id = v.flow_id "
                    "WHERE f.uid = :f AND v.uid = :v"
                ),
                {"f": flow_uid, "v": vid},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


@router.get("/flows/{uid}/versions", name="list-flow-versions")
async def list_flow_versions(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> list[FlowVersionOut]:
    """The flow's versions (newest first). The editor loads the latest to seed the builder."""
    require_permission(principal, FLOWS_READ)
    flow = (
        await session.execute(text("SELECT id FROM flows.flows WHERE uid = :u"), {"u": uid})
    ).scalar_one_or_none()
    if flow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flow not found")
    rows = (
        (
            await session.execute(
                text(
                    "SELECT uid, version, status, definition, published_at "
                    "FROM flows.flow_versions WHERE flow_id = :f ORDER BY version DESC"
                ),
                {"f": flow},
            )
        )
        .mappings()
        .all()
    )
    return [FlowVersionOut(**dict(r)) for r in rows]


@router.get("/flows/{uid}/versions/{vid}", name="get-flow-version")
async def get_flow_version(
    uid: str,
    vid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> FlowVersionOut:
    require_permission(principal, FLOWS_READ)
    row = await _version_row(session, uid, vid)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    return FlowVersionOut(**row)


@router.post("/flows/{uid}/versions", name="save-flow-version")
async def save_flow_version(
    uid: str,
    body: SaveVersion,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FlowVersionOut:
    """Save the flow's draft definition (validated). Updates the existing draft, or forks a new
    draft version if the latest is published (mirrors the templates editor)."""
    require_permission(principal, FLOWS_WRITE)
    try:
        definition = definitions.validate_definition(body.definition)
    except definitions.DefinitionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    flow = (
        await session.execute(text("SELECT id FROM flows.flows WHERE uid = :u"), {"u": uid})
    ).scalar_one_or_none()
    if flow is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flow not found")
    draft = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, version FROM flows.flow_versions "
                    "WHERE flow_id = :f ORDER BY version DESC LIMIT 1"
                ),
                {"f": flow},
            )
        )
        .mappings()
        .one_or_none()
    )
    import json as _json

    if draft is not None and (
        (
            await session.execute(
                text("SELECT status FROM flows.flow_versions WHERE id = :i"), {"i": draft["id"]}
            )
        ).scalar_one()
        == "draft"
    ):
        vid = str(draft["uid"])
        await session.execute(
            text("UPDATE flows.flow_versions SET definition = CAST(:d AS jsonb) WHERE id = :i"),
            {"d": _json.dumps(definition), "i": draft["id"]},
        )
    else:
        next_version = int(draft["version"]) + 1 if draft else 1
        vid = new_id()
        await session.execute(
            text(
                "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, definition, "
                "  created_by) VALUES (:uid, auth.tenant_id(), :f, :v, CAST(:d AS jsonb), "
                "  auth.uid())"
            ),
            {"uid": vid, "f": flow, "v": next_version, "d": _json.dumps(definition)},
        )
    result = await _version_row(session, uid, vid)
    if result is None:  # pragma: no cover
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unexpected error")
    return FlowVersionOut(**result)


def _node_id(s: Mapping[str, Any]) -> str | None:
    """The node id an issue about this step/node should badge, or None when it has no usable id."""
    sid = s.get("id")
    return sid if isinstance(sid, str) and sid else None


async def _handler_issues(
    session: AsyncSession, tenant_id: int, definition: dict[str, Any]
) -> list[Issue]:
    """Each step type's OWN publish-time validation (``StepHandler.preflight``) — the seam that lets
    a module refuse a broken config the generic validator can't see (an invalid ``fill_form`` field
    mapping, a template with no published version). Prefixed with the step's label so the author
    knows which node to fix."""
    issues: list[Issue] = []
    for s in definition.get("steps", []):
        if not isinstance(s, dict):
            continue
        handler = handlers.get_handler(str(s.get("type")))
        if handler is None:
            continue
        raw_cfg = s.get("config")
        cfg: dict[str, Any] = raw_cfg if isinstance(raw_cfg, dict) else {}
        label = s.get("title") or s.get("id") or "a step"
        for issue in await handler.preflight(session, tenant_id, cfg):
            issues.append(
                Issue(
                    _node_id(s),
                    f"Step “{label}”: {issue[0].lower() + issue[1:] if issue else issue}",
                )
            )
    return issues


#: How far the ``start_flow`` cycle check follows a chain of tail calls. Not an invented number:
#: it is the highest chain depth any tenant's runtime can be configured to allow
#: (``triggers.CEILINGS['maxChainDepth']`` bounds the per-tenant override of
#: ``safety.maxChainDepth``), so the walk covers every loop the engine could ever actually go round
#: and stops exactly where the runtime's own cap takes over.
_CYCLE_HOPS: int = triggers.CEILINGS["maxChainDepth"]


def _start_flow_targets(definition: Any) -> tuple[str, ...]:
    """Every flow code this definition tail-calls, in document order, de-duplicated."""
    nodes = definition.get("nodes") if isinstance(definition, dict) else None
    if not isinstance(nodes, list):
        return ()
    codes: list[str] = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("kind") != "start_flow":
            continue
        config = node.get("config")
        code = config.get("flow_code") if isinstance(config, dict) else None
        if isinstance(code, str) and code and code not in codes:
            codes.append(code)
    return tuple(codes)


async def _published_targets(
    session: AsyncSession, tenant_id: int, code: str, cache: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """The flow codes ``code``'s **published** definition tail-calls. Empty when there is no such
    flow, when it is not published, or when its definition is unreadable — all three are already
    refused (or simply not our business) elsewhere, and none of them can close a loop.

    Memoised for the whole validation, which is what bounds the walk: one SELECT per distinct flow
    code reached, never one per path.
    """
    hit = cache.get(code)
    if hit is not None:
        return hit
    row = (
        await session.execute(
            text(
                "SELECT v.definition FROM flows.flows f "
                "JOIN flows.flow_versions v ON v.id = f.current_published_version_id "
                "WHERE f.code = :c AND f.tenant_id = :t AND f.status = 'published'"
            ),
            {"c": code, "t": tenant_id},
        )
    ).scalar_one_or_none()
    definition: Any = row
    if isinstance(definition, str):
        import json as _json

        try:
            definition = _json.loads(definition)
        except ValueError:
            definition = None
    targets = _start_flow_targets(definition) if isinstance(definition, dict) else ()
    cache[code] = targets
    return targets


async def _cycle_back_to(
    session: AsyncSession,
    tenant_id: int,
    *,
    origin: str,
    first: str,
    cache: dict[str, tuple[str, ...]],
) -> list[str] | None:
    """The chain of flow codes from ``origin`` through ``first`` that leads back to ``origin``, or
    ``None`` if no such chain exists within ``_CYCLE_HOPS`` hops.

    Breadth-first over DISTINCT codes with a parent map, so each flow is visited (and loaded) at
    most once per node checked and the returned path is the shortest loop — the one an author can
    actually follow on screen.
    """
    if first == origin:
        return [origin, origin]
    parent: dict[str, str] = {}
    frontier = [first]
    seen = {first}
    for _ in range(_CYCLE_HOPS):
        if not frontier:
            break
        nxt: list[str] = []
        for code in frontier:
            for target in await _published_targets(session, tenant_id, code, cache):
                if target == origin:
                    chain = [origin, code]
                    while code in parent:
                        code = parent[code]
                        chain.insert(1, code)
                    return [*chain, origin]
                if target not in seen:
                    seen.add(target)
                    parent[target] = code
                    nxt.append(target)
        frontier = nxt
    return None


async def _automation_issues(
    session: AsyncSession, tenant_id: int, definition: dict[str, Any], flow_code: str | None = None
) -> list[Issue]:
    """The publish checks that need BOTH the graph's node kinds and the database.

    Three things live here and could not live anywhere else:

    **An action node's permission.** ``effects.preflight`` answers "could this action ever run
    in this firm" — is the type implemented, does any role hold the slug a person would need, does
    the template it names have a published version, is the webhook endpoint configured. It is async
    and session-bound, so ``definitions.publish_issues`` (pure and sync) cannot call it; it walks
    ``nodes`` rather than the derived ``steps`` projection because only the node still carries the
    ``kind`` that says this is an action at all. An action whose permission no role holds is not a
    runtime error waiting to happen — it is an automation asserting an authority the firm has not
    granted anyone, and the person who can fix that is looking at the screen right now.

    **A ``start_flow`` node's target.** ``graph_issues`` checks the code is slug-shaped; only the
    database can say whether it names a flow that exists and is PUBLISHED. A tail call into a draft
    is a journey that ends by going nowhere, silently, at whatever hour the client reaches it.

    **A ``start_flow`` chain that comes back round.** ``graph_issues`` refuses a loop drawn inside
    one definition, but a loop drawn ACROSS flows — A tail-calls B, B tail-calls A — is invisible
    to it: ``start_flow`` is a terminal kind, so there is no edge to follow. Following one means
    resolving a code to a published version and parsing that definition, which needs the session,
    which is why the check is here and why the contract used to claim it without anyone writing it.
    The runtime depth cap does bound the damage, but it bounds it into an operations task per hop:
    a published cycle is a slow inbox flood rather than a storm, and it is still wrong.

    **What the cycle check cannot promise**, said out loud so nobody reads it as stronger than it
    is. It judges the definitions AS THEY STAND at this moment. Re-publishing a downstream flow
    afterwards can close a loop this publish could not have seen — nothing short of a check on
    every publish of every flow's dependents could, and the runtime cap is the backstop for that.
    It also only asks whether THIS flow can reach itself: a loop lying entirely downstream (B and C
    calling each other, reached from A) is B's or C's own publish to refuse, and blaming A for it
    would file a refusal against a node whose author cannot fix it from this screen.
    """
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return []  # a legacy definition has neither kind of node
    from app.flows import effects

    issues: list[Issue] = []
    # Shared across every start_flow node in this definition: the walks overlap heavily, and the
    # published definition of a given flow cannot change halfway through one validation.
    target_cache: dict[str, tuple[str, ...]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        kind = node.get("kind") or graph_mod.STEP_KIND
        label = node.get("title") or node.get("id") or "an action"
        nid = _node_id(node)
        raw_config = node.get("config")
        config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
        if kind == graph_mod.ACTION_KIND:
            refusal = await effects.preflight(
                session,
                tenant_id=tenant_id,
                action={"type": node.get("type"), "config": config},
            )
            if refusal:
                issues.append(Issue(nid, f"Action “{label}”: {refusal}"))
        elif kind == "start_flow":
            code = config.get("flow_code")
            if not isinstance(code, str) or not code:
                continue  # graph_issues already says it names no flow
            target = (
                await session.execute(
                    text(
                        "SELECT f.status FROM flows.flows f WHERE f.code = :c AND f.tenant_id = :t"
                    ),
                    {"c": code, "t": tenant_id},
                )
            ).scalar_one_or_none()
            if target is None:
                issues.append(
                    Issue(nid, f"“{label}” starts “{code}”, which is not a flow in this firm.")
                )
                continue
            if str(target) != "published":
                issues.append(
                    Issue(
                        nid,
                        f"“{label}” starts “{code}”, which is not published — publish it first, "
                        "or this journey ends by going nowhere.",
                    )
                )
                continue
            if flow_code is None:
                continue  # nothing to come back TO — the caller could not name this flow
            if code == flow_code:
                issues.append(
                    Issue(
                        nid,
                        f"“{label}” starts “{code}”, which is this same journey. A journey that "
                        "starts itself never ends; it just runs again until the chain cap stops "
                        "it and hands every hop to operations.",
                    )
                )
                continue
            chain = await _cycle_back_to(
                session, tenant_id, origin=flow_code, first=code, cache=target_cache
            )
            if chain is not None:
                issues.append(
                    Issue(
                        nid,
                        f"“{label}” starts “{code}”, which comes back round to this journey "
                        f"({' → '.join(chain)}). The loop never ends; it runs until the chain cap "
                        "stops it and hands every hop to operations.",
                    )
                )
    return issues


async def _reference_issues(session: AsyncSession, definition: dict[str, Any]) -> list[Issue]:
    """DB-backed publish checks: every referenced form/document/platform must exist (and a
    fill_form's form must be published), so a published flow can't hand a client a broken step."""
    issues: list[Issue] = []
    for s in definition.get("steps", []):
        if not isinstance(s, dict):
            continue
        cfg = s.get("config") or {}
        label = s.get("title") or s.get("id") or "a step"
        stype = s.get("type")
        # Collected per step so the node id is stamped in ONE place at the bottom of the loop —
        # ten call sites each remembering to pass it is nine chances to badge the wrong node.
        step_issues: list[str] = []
        if stype == "fill_form" and cfg.get("form_uid"):
            status_ = (
                await session.execute(
                    text("SELECT status FROM documents.forms WHERE uid = :u"),
                    {"u": cfg["form_uid"]},
                )
            ).scalar_one_or_none()
            if status_ is None:
                step_issues.append(f"Step “{label}”: its form was not found.")
            elif status_ != "published":
                step_issues.append(f"Step “{label}”: its form is not published.")
        elif stype == "accept_terms" and cfg.get("document_uid"):
            found = (
                await session.execute(
                    text("SELECT 1 FROM documents.documents WHERE uid = :u"),
                    {"u": cfg["document_uid"]},
                )
            ).first()
            if not found:
                step_issues.append(f"Step “{label}”: its document was not found.")
        elif stype == "setup_account":
            # Every catalogue id referenced by the step must exist AND be visible to this tenant
            # (publish runs on tenant_session, so RLS already scopes models/fee_packages to the
            # firm) — else setup_account would blow up on an FK violation at a client's runtime.
            # The provider is validated against the REGISTRY rather than a table (0131): the
            # vocabulary is closed and lives in code, so an unknown one is a definition mistake
            # rather than a missing row, and publish is the right place to say so.
            if cfg.get("provider") and cfg["provider"] not in providers.PROVIDERS:
                step_issues.append(f"Step “{label}”: its platform was not found.")
            if (
                cfg.get("product_id")
                and not (
                    await session.execute(
                        text("SELECT 1 FROM platform.products WHERE id = :i"),
                        {"i": cfg["product_id"]},
                    )
                ).first()
            ):
                step_issues.append(f"Step “{label}”: its product was not found.")
            if (
                cfg.get("model_id")
                and not (
                    await session.execute(
                        text("SELECT 1 FROM platform.models WHERE id = :i"), {"i": cfg["model_id"]}
                    )
                ).first()
            ):
                step_issues.append(f"Step “{label}”: its investment model was not found.")
            if (
                cfg.get("fee_package_id")
                and not (
                    await session.execute(
                        text("SELECT 1 FROM platform.fee_packages WHERE id = :i"),
                        {"i": cfg["fee_package_id"]},
                    )
                ).first()
            ):
                step_issues.append(f"Step “{label}”: its fee package was not found.")
            if cfg.get("role") and cfg["role"] not in _ACCOUNT_ROLES:
                step_issues.append(f"Step “{label}”: “{cfg['role']}” is not a valid account role.")

        # Raise-a-task side-effects: a role-assigned spec must name a role this tenant actually
        # has (slugs travel between tenants; ids don't), and a user-assigned one an active staff
        # member — else the dispatcher would dead-letter the task at runtime.
        for t in s.get("tasks") or []:
            assign = t.get("assign_to") or {} if isinstance(t, dict) else {}
            if assign.get("kind") == "role":
                found = (
                    await session.execute(
                        text(
                            "SELECT 1 FROM auth.roles "
                            "WHERE tenant_id = auth.tenant_id() AND slug = :s"
                        ),
                        {"s": assign.get("role")},
                    )
                ).first()
                if not found:
                    step_issues.append(
                        f"Step “{label}”: raises a task for role “{assign.get('role')}”, "
                        "which this firm does not have."
                    )
            elif assign.get("kind") == "user":
                found = (
                    await session.execute(
                        text(
                            "SELECT 1 FROM auth.organisation_memberships "
                            "WHERE user_id = CAST(:u AS uuid) AND tenant_id = auth.tenant_id() "
                            "  AND portal = 'platform' AND status = 'active'"
                        ),
                        {"u": assign.get("user_id")},
                    )
                ).first()
                if not found:
                    step_issues.append(
                        f"Step “{label}”: raises a task for a user who is not active "
                        "staff in this firm."
                    )
        issues.extend(Issue(_node_id(s), message) for message in step_issues)
    return issues


async def _all_publish_issues(
    session: AsyncSession, definition: dict[str, Any], flow_code: str | None = None
) -> list[Issue]:
    """Every reason this definition would be refused at publish, node ids attached.

    ONE function so publish and the builder's live validation can never disagree: a badge the
    author cleared and a publish that still refuses is worse than no badge at all. The pure half
    (``definitions.publish_issues_detailed``) plus the three DB-backed halves, in the order publish
    has always run them.

    ``flow_code`` is the code of the flow this definition belongs to — the one thing a definition
    does not carry about itself, and the ``start_flow`` cycle check needs it to know what "back
    round to here" means. Both callers have it; it is optional only so the check degrades to
    silence rather than to a wrong answer if a third one ever forgets.
    """
    tenant_id = int((await session.execute(text("SELECT auth.tenant_id()"))).scalar_one())
    return (
        definitions.publish_issues_detailed(definition)
        + await _reference_issues(session, definition)
        + await _handler_issues(session, tenant_id, definition)
        + await _automation_issues(session, tenant_id, definition, flow_code)
    )


class ValidateDefinition(BaseModel):
    """The DRAFT the author is looking at — not the stored version. Validating anything else is
    worse than not validating, the same reasoning as the automations dry run's overrides."""

    definition: dict[str, Any]


class ValidationIssue(BaseModel):
    """One refusal. ``node_id`` is null for a whole-flow problem (no entry, a breached cap)."""

    node_id: str | None
    message: str


class ValidationOut(BaseModel):
    issues: list[ValidationIssue]


@router.post("/flows/{uid}/validate", name="validate-flow-definition")
async def validate_flow_definition(
    uid: str,
    body: ValidateDefinition,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> ValidationOut:
    """Would this definition publish? Answers WITHOUT writing anything, for a draft that has not
    been saved — so the builder can badge the node at fault while the author is still typing.

    **Why not ``/versions/{vid}/validate``.** Nothing here reads a version: the definition arrives
    in the body precisely because the interesting one is the unsaved draft. Naming a version in the
    path would invite the reader (and the next endpoint) to believe the stored one matters, and
    would 404 the very first validation of a flow whose draft has never been saved. The flow uid
    stays because it is the RLS-checked handle publish uses, and because the DB-backed checks must
    run in the same tenant's view.

    **Side-effect-free by construction.** Every statement it reaches is a SELECT; it never touches
    ``flows.flow_versions``. The session is a normal ``tenant_session`` so the reference and
    permission preflights see exactly what publish sees. It is on
    ``actions.EXEMPT_MUTATING_ROUTES`` — a POST that performs nothing is not an action, and the
    builder calls this on a debounce, so emitting an audit event per keystroke would flood a firm's
    activity log with a question rather than an answer.

    Gated on ``flows:write``: it is an authoring tool, and it reports the same refusals publish
    does, so anyone who can see it can already publish.
    """
    require_permission(principal, FLOWS_WRITE)
    # The CODE, not the id: nothing here needs the id, and the start_flow cycle check needs to know
    # which flow "back round to here" means — a definition does not carry its own code.
    flow_code = (
        await session.execute(text("SELECT code FROM flows.flows WHERE uid = :u"), {"u": uid})
    ).scalar_one_or_none()
    if flow_code is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flow not found")
    try:
        definition = definitions.validate_definition(body.definition)
    except definitions.DefinitionError as exc:
        # Unreadable is a validation ANSWER here, not a 422: the author is mid-edit and the point
        # of the endpoint is to say what is wrong, on the flow as a whole.
        return ValidationOut(issues=[ValidationIssue(node_id=None, message=str(exc))])
    issues = await _all_publish_issues(session, definition, str(flow_code))
    return ValidationOut(
        issues=[ValidationIssue(node_id=i.node_id, message=i.message) for i in issues]
    )


@router.post("/flows/{uid}/versions/{vid}/publish", name="publish-flow-version")
async def publish_flow_version(
    uid: str,
    vid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> FlowOut:
    """Publish a draft version: validate (≥1 step), archive the prior published version, point the
    flow at this one and mark the flow published."""
    require_permission(principal, FLOWS_WRITE)
    row = (
        (
            await session.execute(
                text(
                    "SELECT v.id, v.definition, f.id AS flow_id, f.code AS flow_code "
                    "FROM flows.flow_versions v "
                    "JOIN flows.flows f ON f.id = v.flow_id WHERE f.uid = :f AND v.uid = :v"
                ),
                {"f": uid, "v": vid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    definition = row["definition"]
    if isinstance(definition, str):
        import json as _json

        definition = _json.loads(definition)
    issues = await _all_publish_issues(session, definition, str(row["flow_code"]))
    if issues:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, " ".join(i.message for i in issues)
        )
    await session.execute(
        text(
            "UPDATE flows.flow_versions SET status = 'archived' "
            "WHERE flow_id = :f AND status = 'published'"
        ),
        {"f": row["flow_id"]},
    )
    await session.execute(
        text(
            "UPDATE flows.flow_versions SET status = 'published', published_at = now() "
            "WHERE id = :i"
        ),
        {"i": row["id"]},
    )
    await session.execute(
        text(
            "UPDATE flows.flows SET status = 'published', current_published_version_id = :v "
            "WHERE id = :f"
        ),
        {"v": row["id"], "f": row["flow_id"]},
    )
    result = await _flow_row(session, uid)
    if result is None:  # pragma: no cover
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unexpected error")
    return FlowOut(**result)
