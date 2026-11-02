"""Contributions + transfers — the platform portal's workbench surface (docs/contributions.md).

Runs on ``firm_session`` (the caller's own tenant, resolved from their verified identity) and
is gated by ``contributions:*`` / ``transfers:*``. The service layer (``app.contributions``,
``app.transfers``) owns validation and the provider calls; this module is the HTTP shape and
the status-code discipline.

**Status codes**, matching ``crm.py::account_drawdown_session`` so the portals can treat every
provider-backed action the same way:

- **400** — a bad request (unknown category, non-positive amount).
- **404** — the account/contribution is not the caller's firm's. RLS-equivalent: never 403,
  which would confirm the row exists.
- **409** — the record is in a state that forbids the action (already recorded, past
  classification).
- **422** — we hold everything except what the *provider* needs, and the body names exactly
  which details are missing. Never a substituted placeholder: these calls write to a real
  back-office record on a client's pension.
- **502** — the vendor refused. The request was fine, the upstream wasn't.
- **503** — credentials are not configured. A deployment state, not a client error.

Booking a contribution is the exception to 422/502: the local records are Graphene's truth and
persist regardless, with the SEI outcome reported in the response's ``sei`` block.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import contributions as contributions_mod
from app import listing
from app import matching as matching_mod
from app import providers as providers_mod
from app import transfers as transfers_mod
from app.actions import runs as action_runs
from app.actions import service as action_service
from app.auth import require_permission
from app.db import FirmContext, target_firm_session
from app.integrations import morgan_lloyd
from app.integrations.seccl import transfers as seccl_transfers
from app.integrations.seccl.connection import SecclNotConfigured
from app.integrations.seccl.transfers import SecclUnavailable, TransferNotCancellable
from app.integrations.sei import transfers as sei_transfers
from app.integrations.sei.cash_receipts import SeiNotConfigured, SeiUnavailable
from app.permissions import (
    CONTRIBUTIONS_READ,
    CONTRIBUTIONS_WRITE,
    TRANSFERS_READ,
    TRANSFERS_WRITE,
)
from app.routers.crm import adviser_directory

logger = logging.getLogger("contributions")

router = APIRouter(prefix="/contributions", tags=["contributions"])
transfers_router = APIRouter(prefix="/transfers", tags=["transfers"])

Category = Annotated[
    str,
    Field(
        pattern="^(personal_contribution|employer_contribution|third_party_contribution"
        "|transfer_in|not_a_contribution)$"
    ),
]
Frequency = Annotated[str, Field(pattern="^(single|monthly|quarterly|half_yearly|annually)$")]
PaidBy = Annotated[str, Field(pattern="^(client|third_party|employer)$")]


class Employer(BaseModel):
    """The contributor block Morgan Lloyd requires for anything that is not a personal
    contribution (the docs' ``*`` rule). Held on the contribution, not the client, because the
    payer of an employer contribution is not the member."""

    employer_name: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    address_line3: str | None = None
    city: str | None = None
    county: str | None = None
    postcode: str | None = None
    phone: str | None = None
    email: str | None = None


class CreateContribution(BaseModel):
    account_id: int
    category: Category
    amount: float = Field(gt=0)
    frequency: Frequency = "single"
    paid_by: PaidBy = "client"
    tax_year: str | None = None
    contribution_date: date | None = None
    #: When SEI should expect the money. Defaults to the contribution date.
    due_date: date | None = None
    prefunded_ras_amount: float | None = Field(default=None, ge=0)
    employer: Employer | None = None
    note: str | None = None
    #: Set false to record a contribution without telling SEI (e.g. one already received).
    lodge_expectation: bool = True


class Categorise(BaseModel):
    category: Category | None = None
    tax_year: str | None = None
    frequency: Frequency | None = None
    paid_by: PaidBy | None = None
    employer: Employer | None = None
    note: str | None = None


class SetStatus(BaseModel):
    status: str


class ClassifyTransaction(BaseModel):
    """Classify a credit that has already landed — path A of the flow.

    Deliberately no ``amount``: the contribution is worth what arrived. Letting a caller
    restate it is how a £450 credit becomes a £500 basic-rate reclaim.
    """

    transaction_id: int
    category: Category
    frequency: Frequency = "single"
    paid_by: PaidBy = "client"
    tax_year: str | None = None
    prefunded_ras_amount: float | None = Field(default=None, ge=0)
    employer: Employer | None = None
    note: str | None = None


class Cancel(BaseModel):
    #: Required by the DB CHECK as well as by good sense — a cancelled HMRC-reportable record
    #: with no reason is not an audit trail.
    reason: str = Field(min_length=1)
    #: Also stop the Morgan Lloyd record. Best-effort and reported, never fatal: the local
    #: cancellation is Graphene's own decision and must not be lost to an unreachable vendor.
    stop_at_morgan_lloyd: bool = True


class AttachTransaction(BaseModel):
    transaction_id: int


class UpdateApplication(BaseModel):
    """Amend the Morgan Lloyd Contribution Application. Only valid while it is at
    Application (809130000), which the integration re-reads and enforces."""

    amount: float = Field(gt=0)


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


class TransferAssetIn(BaseModel):
    identifier: str = Field(min_length=1)
    identifier_type: str = Field(min_length=1)
    issue_country: str = Field(min_length=2, max_length=2)
    units: float = Field(gt=0)
    units_type: str | None = None


class TransferDocumentIn(BaseModel):
    document_reference_id: str = Field(min_length=1)


class CreateTransfer(BaseModel):
    account_id: int
    ceding_provider: str = Field(min_length=1)
    #: Optional now: absent derives it from the account's wrapper. The drawer does not ask,
    #: because the account already says whether this is a pension, an ISA or a GIA.
    transfer_type: Annotated[str, Field(pattern="^(pension|isa|gia|cash|in_specie)$")] | None = None
    #: Cash or in-specie. Distinct from ``transfer_type`` (the wrapper). Required; never defaulted.
    basis: Annotated[str, Field(pattern="^(cash|in_specie)$")] | None = None
    #: Full or partial. Hidden and stored as full on a Seccl-only account.
    extent: Annotated[str, Field(pattern="^(full|partial)$")] | None = None
    #: The ceding scheme's reference for the plan being transferred.
    policy_number: str | None = None
    #: Uncrystallised (accumulation) / partial / crystallised. Asked on a pension; the split
    #: amounts are derived from this plus the crystallised amount.
    crystallisation: (
        Annotated[str, Field(pattern="^(uncrystallised|partial|crystallised)$")] | None
    ) = None
    #: Morgan Lloyd ``cam_pensiontype`` (1–20). Asked on a pension.
    pension_type: int | None = Field(default=None, ge=1, le=20)
    #: Pension Schemes Act 2015 s.48(8) safeguarded rights. Asked on a pension; unchecked is No.
    safeguarded_benefits: bool | None = None
    crystallised_value: float | None = Field(default=None, ge=0)
    uncrystallised_value: float | None = Field(default=None, ge=0)
    total_value: float | None = Field(default=None, gt=0)
    expected_date: date | None = None
    note: str | None = None
    provider_address_line1: str | None = None
    provider_address_line2: str | None = None
    provider_city: str | None = None
    provider_postcode: str | None = None
    provider_country: str | None = None
    fca_reference: str | None = None
    #: Select's ceding-account type. Not the Graphene wrapper (pension / isa / gia).
    external_account_type: str | None = None
    assets: list[TransferAssetIn] | None = None
    documents: list[TransferDocumentIn] | None = None


def _employer(model: Employer | None) -> dict[str, Any] | None:
    return model.model_dump(exclude_unset=True) if model else None


# ── contributions ────────────────────────────────────────────────────────────────────────


#: How each contributions filter narrows a row. Declared once so the page cut and the facet
#: options cannot drift apart — the options are built by re-applying every predicate EXCEPT the
#: facet's own (see app/listing.py).
#:
#: **`status` filters on the STAGE, not the raw status** (`contributions_mod.stage_of`). The
#: workbench shows six stages and two terminals where the table holds ten statuses, because
#: several of them answer the same question — and the filter has to speak the words on screen or
#: picking "To classify" would match nothing while the queue plainly shows some. The
#: facet emits stage keys for the same reason: the options ARE the filter's vocabulary, and
#: `pkgs/ui/.../contributions/status.ts::STAGES` renders their labels.
#:
#: An unmapped status filters as the empty string rather than as itself, which is the honest
#: answer: a status with no stage is not reachable by any option the UI offers.
_CONTRIBUTION_FILTERS: listing.Filters = {
    "status": lambda row, value: listing.matches(
        value, contributions_mod.stage_of(row.get("status"))
    ),
    "adviser": lambda row, value: listing.matches(value, row.get("adviser")),
    "category": lambda row, value: listing.matches(value, row.get("category")),
}
_CONTRIBUTION_FACETS: listing.Facets = [
    ("status", lambda row: contributions_mod.stage_of(row.get("status"))),
    ("adviser", lambda row: row.get("adviser")),
    ("category", lambda row: row.get("category")),
]
_CONTRIBUTION_SORTS: listing.Sorts = {
    "client": lambda row: row.get("client_name"),
    "date": lambda row: row.get("contribution_date"),
    "amount": lambda row: row.get("amount"),
    "category": lambda row: row.get("category"),
    "status": lambda row: contributions_mod.stage_of(row.get("status")),
    "account": lambda row: row.get("account_name"),
    "adviser": lambda row: row.get("adviser"),
    "created_at": lambda row: row.get("created_at"),
}


@router.get("", name="list-contributions")
async def list_contributions(
    account_id: int | None = Query(default=None),
    contact_id: int | None = Query(default=None),
    q: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    adviser: str | None = Query(default=None),
    category: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    dir: str | None = Query(default=None),  # noqa: A002 - the wire name the portal sends
    limit: int | None = Query(default=None, ge=1, le=listing.PAGE_LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
    ctx: FirmContext = Depends(target_firm_session),
) -> list[dict[str, Any]] | dict[str, Any]:
    """The firm's contributions — the workbench feed.

    Search and filters are applied **server-side**, before the page cut, so a filtered count is
    the count of matches across the whole book rather than across whatever page happened to be
    fetched. Filtering a page client-side reads as "this firm has no contribution for Halloway"
    when the truth is "not on this page".

    Paged only on request: with no ``limit`` this is the bare list every existing caller parses;
    with one, the ``{items, total, facets}`` envelope.
    """
    require_permission(ctx.principal, CONTRIBUTIONS_READ)
    rows = await contributions_mod.list_contributions(
        ctx.session,
        tenant_id=ctx.tenant_id,
        account_id=account_id,
        contact_id=contact_id,
    )
    active = {
        "status": listing.clean(status_filter),
        "adviser": listing.clean(adviser),
        "category": listing.clean(category),
    }
    query = listing.clean(q)
    visible = [
        row
        for row in rows
        if all(predicate(row, active[key]) for key, predicate in _CONTRIBUTION_FILTERS.items())
        and listing.search(
            query,
            row.get("client_name"),
            row.get("account_name"),
            row.get("adviser"),
            row.get("uid"),
            row.get("employer_name"),
            row.get("transaction_reference"),
        )
    ]
    # Facets over the searched-but-unfiltered set: a search narrows what is on offer (its
    # options should describe the results), a filter must not narrow its own options.
    searchable = [
        row
        for row in rows
        if listing.search(
            query,
            row.get("client_name"),
            row.get("account_name"),
            row.get("adviser"),
            row.get("uid"),
            row.get("employer_name"),
            row.get("transaction_reference"),
        )
    ]
    visible = listing.sort_rows(visible, _CONTRIBUTION_SORTS, sort, dir)
    facet_options, facet_counts = listing.facets_and_counts(
        searchable, _CONTRIBUTION_FACETS, active, _CONTRIBUTION_FILTERS
    )
    if limit is not None:
        listing.union_facet(facet_options, "adviser", await adviser_directory(ctx.session))
    result = listing.page(
        visible,
        limit=limit,
        offset=offset,
        facets=facet_options,
        facet_counts=facet_counts,
    )
    # The workbench's six figures, over the WHOLE BOOK — before the filters and before the page
    # cut. The portal used to derive them from the rows it was handed, which are one page: "£38,400
    # is with the scheme administrator" then meant "on this screen", in the shape of a figure a
    # compliance reviewer would reasonably quote. Before the FILTERS as well, because the strip is
    # how you navigate to a stage and narrowing to one must not empty the other five.
    #
    # Only on the paged envelope: the bare-list callers (the account and client pages) render no
    # strip, and adding a key to a list is not a thing.
    if isinstance(result, dict):
        result["summary"] = contributions_mod.stage_totals(rows)
    return result


@router.post("", name="create-contribution", status_code=status.HTTP_201_CREATED)
async def create_contribution(
    body: CreateContribution, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Book a contribution, raise its payment expectation, and lodge it at SEI.

    The SEI outcome is reported in the ``sei`` block rather than raised: the contribution and
    expectation are Graphene's own records and must not be lost because a custodian is
    unreachable or a firm's SEI reference codes are not configured yet. Retry with
    ``POST /contributions/{uid}/lodge-expectation``.
    """
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    try:
        return await contributions_mod.create_contribution(
            ctx.session,
            tenant_id=ctx.tenant_id,
            created_by=ctx.principal.user_id,
            account_id=body.account_id,
            category=body.category,
            amount=body.amount,
            frequency=body.frequency,
            paid_by=body.paid_by,
            tax_year=body.tax_year,
            contribution_date=body.contribution_date,
            due_date=body.due_date,
            prefunded_ras_amount=body.prefunded_ras_amount,
            employer=_employer(body.employer),
            note=body.note,
            lodge=body.lodge_expectation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/summary", name="contribution-summary")
async def contribution_summary(
    contact_id: int = Query(...),
    account_id: int | None = Query(default=None),
    ctx: FirmContext = Depends(target_firm_session),
) -> dict[str, Any]:
    """Gross contributed this tax year, for the account overview — not an eligibility test.

    Declared above ``/{uid}`` so FastAPI does not treat ``summary`` as a uid."""
    require_permission(ctx.principal, CONTRIBUTIONS_READ)
    rows = await contributions_mod.list_contributions(
        ctx.session,
        tenant_id=ctx.tenant_id,
        contact_id=contact_id,
    )
    tax_year = contributions_mod.tax_year_of(date.today())
    counting = [
        row
        for row in rows
        if row.get("tax_year") == tax_year
        and row.get("status") != "cancelled"
        and row.get("category") not in ("not_a_contribution", "transfer_in")
    ]
    account_gross = sum(
        float(row.get("gross_amount") or 0)
        for row in counting
        if account_id is None or int(row.get("account_id") or 0) == account_id
    )
    return {
        "tax_year": tax_year,
        "account_gross": account_gross,
        "count": sum(
            1
            for row in counting
            if account_id is None or int(row.get("account_id") or 0) == account_id
        ),
    }


@router.get("/unclassified", name="list-unclassified-credits")
async def list_unclassified_credits(
    account_id: int | None = Query(default=None),
    pension_only: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=1000),
    ctx: FirmContext = Depends(target_firm_session),
) -> list[dict[str, Any]]:
    """Money that arrived with no domain record against it — the front of the workbench.

    This is where path A starts: the client sent money, the custodian's feed landed it in
    ``platform.transactions``, and nobody has said what it is yet. Each row carries what SEI's
    own description code implies (``suggested_category``, ``is_relief_payment``) so the common
    case is one click, but nothing is classified without an adviser saying so.

    Declared **above** ``/{uid}`` on purpose: FastAPI matches in declaration order, so a
    literal path registered after a parameterised one is unreachable.
    """
    require_permission(ctx.principal, CONTRIBUTIONS_READ)
    return await matching_mod.unclassified_credits(
        ctx.session,
        tenant_id=ctx.tenant_id,
        account_id=account_id,
        pension_only=pension_only,
        limit=limit,
    )


@router.post("/from-transaction", name="classify-transaction", status_code=201)
async def classify_transaction(
    body: ClassifyTransaction, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Create a contribution from a credit that already landed.

    No SEI lodge happens here — an Anticipated Cash Receipt for money that has already arrived
    would be a forecast of the past. The contribution is written at ``confirmed`` (the
    classification IS the confirmation) and is ready to record to Morgan Lloyd.
    """
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    try:
        return await contributions_mod.classify_transaction(
            ctx.session,
            tenant_id=ctx.tenant_id,
            created_by=ctx.principal.user_id,
            transaction_id=body.transaction_id,
            category=body.category,
            frequency=body.frequency,
            paid_by=body.paid_by,
            tax_year=body.tax_year,
            prefunded_ras_amount=body.prefunded_ras_amount,
            employer=_employer(body.employer),
            note=body.note,
        )
    except contributions_mod.ContributionConflict as exc:
        raise _conflict(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/match", name="run-contribution-matcher")
async def run_matcher(ctx: FirmContext = Depends(target_firm_session)) -> dict[str, Any]:
    """Match open expectations to the credits that fulfilled them — path B.

    Auto-matches only an unambiguous exact-amount credit inside the date window; anything else
    is reported under ``needs_review`` for a human. See ``app/matching.py`` for why a tolerance
    band is the wrong default here.
    """
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    return await matching_mod.run(ctx.session, tenant_id=ctx.tenant_id)


@router.get("/{uid}", name="get-contribution")
async def get_contribution(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    require_permission(ctx.principal, CONTRIBUTIONS_READ)
    record = await contributions_mod.get_contribution(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contribution not found")
    return record


@router.patch("/{uid}", name="categorise-contribution")
async def categorise_contribution(
    uid: str, body: Categorise, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Set or amend the HMRC classification. Recomputes gross and relief server-side — the
    client's preview is a preview, not the number of record."""
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    try:
        return await contributions_mod.categorise(
            ctx.session,
            tenant_id=ctx.tenant_id,
            uid=uid,
            category=body.category,
            tax_year=body.tax_year,
            frequency=body.frequency,
            paid_by=body.paid_by,
            employer=_employer(body.employer),
            note=body.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except contributions_mod.ContributionConflict as exc:
        # A state conflict, not a bad request. This used to be split by matching
        # `"past classification" in str(exc)` — one reworded message away from silently
        # answering 400 for a conflict, so the service raises a distinct type now.
        raise _conflict(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{uid}/status", name="set-contribution-status")
async def set_contribution_status(
    uid: str, body: SetStatus, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Move a contribution along the pipeline (hold, flag for manual, reconcile)."""
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    try:
        return await contributions_mod.set_status(
            ctx.session, tenant_id=ctx.tenant_id, uid=uid, status=body.status
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{uid}/lodge-expectation", name="lodge-contribution-expectation", response_model=None)
async def lodge_expectation(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any] | JSONResponse:
    """Retry the SEI Anticipated Cash Receipt for a contribution whose lodge did not land.

    Unlike creation, this is the explicit ask, so the failure modes surface as status codes:
    422 naming the missing reference data, 503 without credentials, 502 when SEI refuses.
    """
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    record = await contributions_mod.get_contribution(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contribution not found")
    if record["expectation_status"] == "instructed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This expectation is already lodged at SEI.",
        )
    actor, _roles = await action_runs.caller_ids(ctx.session)
    try:
        run = await action_service.lodge_contribution(
            ctx.session, tenant_id=ctx.tenant_id, uid=uid, requested_by=actor
        )
    except action_runs.ActionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    paused = run.state.type.value == "paused"
    # The worker lodges the contribution; the retry button polls the run. A held run waits on its
    # approval gate, the shape this door already returns for a held run.
    return action_runs.pending_response(
        action_runs.Held(
            {
                "status": "awaiting_approval" if paused else "running",
                "run_uid": run.uid,
                "action_key": "transaction.payment",
            }
        )
    )


@router.post(
    "/{uid}/record-to-morgan-lloyd",
    name="record-contribution-to-morgan-lloyd",
    response_model=None,
)
async def record_to_morgan_lloyd(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any] | JSONResponse:
    """Create the ``cam_contribution`` record in Morgan Lloyd's Dynamics and link it.

    Idempotent: a contribution already recorded returns 409 rather than creating a duplicate
    on a client's permanent pension record.
    """
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    record = await contributions_mod.get_contribution(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contribution not found")
    if record.get("custodian") != "sei" or not record.get("is_pension"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Morgan Lloyd administers SEI SIPPs.",
        )
    if record["ml_contribution_id"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This contribution is already recorded in Morgan Lloyd.",
        )

    actor, _roles = await action_runs.caller_ids(ctx.session)
    try:
        run = await action_service.record_at_morgan_lloyd(
            ctx.session,
            tenant_id=ctx.tenant_id,
            action_key="contribution.record",
            account_id=int(record["account_id"]),
            uid=uid,
            requested_by=actor,
        )
    except action_runs.ActionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    # The worker records it at Morgan Lloyd; a held run waits on its approval gate.
    paused = run.state.type.value == "paused"
    return action_runs.pending_response(
        action_runs.Held(
            {
                "status": "awaiting_approval" if paused else "running",
                "run_uid": run.uid,
                "action_key": "contribution.record",
            }
        )
    )


@router.post("/{uid}/cancel", name="cancel-contribution")
async def cancel_contribution(
    uid: str, body: Cancel, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Stop a contribution, recording why, and cancel its open expectation.

    The Morgan Lloyd stop is **best-effort and reported**, matching how booking treats the SEI
    lodge: the local cancellation is Graphene's own decision and losing it because Dynamics is
    unreachable would be worse than the two being briefly out of step. The outcome comes back
    in ``morgan_lloyd``; retry with ``POST …/stop-morgan-lloyd``.
    """
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    try:
        record = await contributions_mod.cancel(
            ctx.session, tenant_id=ctx.tenant_id, uid=uid, reason=body.reason
        )
    except contributions_mod.ContributionConflict as exc:
        raise _conflict(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    report: dict[str, Any] = {"status": "skipped", "detail": "Not stopped at Morgan Lloyd."}
    if body.stop_at_morgan_lloyd and record.get("ml_contribution_id"):
        report = await _stop_at_morgan_lloyd(ctx, record)
        if report["status"] == "stopped":
            record = await contributions_mod.record_ml_status(
                ctx.session,
                tenant_id=ctx.tenant_id,
                uid=uid,
                ml_status=morgan_lloyd.STATUS_STOPPED,
            )
    return {**record, "morgan_lloyd": report}


async def _stop_at_morgan_lloyd(ctx: FirmContext, record: dict[str, Any]) -> dict[str, Any]:
    """Stop the vendor record, reporting rather than raising. Mirrors ``lodge_expectation``'s
    report shape so both best-effort provider calls read the same way in the portal."""
    context = await contributions_mod.ml_context(
        ctx.session, tenant_id=ctx.tenant_id, contribution=record
    )
    try:
        await morgan_lloyd.stop_application(context)
    except ValueError as exc:
        return {"status": "blocked", "detail": str(exc)}
    except morgan_lloyd.MorganLloydNotConfigured as exc:
        return {"status": "unavailable", "detail": str(exc)}
    except morgan_lloyd.MorganLloydUnavailable as exc:
        return {"status": "failed", "detail": str(exc)}
    return {"status": "stopped", "detail": "Stopped at Morgan Lloyd."}


@router.post("/{uid}/attach-transaction", name="attach-contribution-transaction")
async def attach_transaction(
    uid: str, body: AttachTransaction, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Match this contribution's expectation to a credit by hand.

    For everything the automatic matcher declines: a client who paid a different amount, two
    identical credits in the window, a payment that arrived outside it. The match is stamped
    with the acting operator so it stays distinguishable from an automatic one.
    """
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    record = await contributions_mod.get_contribution(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contribution not found")
    if not record.get("expectation_id"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This contribution was created from a credit, so it has no expectation to "
                "match — it is already tied to the money that paid it."
            ),
        )
    credit = await matching_mod.get_transaction(
        ctx.session, tenant_id=ctx.tenant_id, transaction_id=body.transaction_id
    )
    if credit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    if credit["expectation_id"] is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That credit is already matched to another expectation.",
        )
    if credit["account_id"] != record["account_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That credit is on a different account.",
        )
    await matching_mod.link(
        ctx.session,
        tenant_id=ctx.tenant_id,
        expectation_id=int(record["expectation_id"]),
        transaction_id=body.transaction_id,
        matched_by=ctx.principal.user_id,
    )
    updated = await contributions_mod.get_contribution(
        ctx.session, tenant_id=ctx.tenant_id, uid=uid
    )
    assert updated is not None  # noqa: S101 - existed a statement ago, same transaction
    return updated


@router.delete("/{uid}/attach-transaction", name="detach-contribution-transaction")
async def detach_transaction(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Undo a match. The expectation returns to ``instructed`` — it is still lodged at the
    custodian; only our belief about which credit paid it changed."""
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    record = await contributions_mod.get_contribution(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contribution not found")
    if not record.get("expectation_id"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This contribution has no expectation, so there is no match to undo.",
        )
    await matching_mod.unlink(
        ctx.session, tenant_id=ctx.tenant_id, expectation_id=int(record["expectation_id"])
    )
    updated = await contributions_mod.get_contribution(
        ctx.session, tenant_id=ctx.tenant_id, uid=uid
    )
    assert updated is not None  # noqa: S101
    return updated


@router.get("/{uid}/match-candidates", name="list-contribution-match-candidates")
async def list_match_candidates(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> list[dict[str, Any]]:
    """Unmatched credits on this account that could be this contribution's money, best first."""
    require_permission(ctx.principal, CONTRIBUTIONS_READ)
    record = await contributions_mod.get_contribution(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contribution not found")
    if not record.get("expectation_id"):
        return []
    return await matching_mod.candidates_for(
        ctx.session, tenant_id=ctx.tenant_id, expectation_id=int(record["expectation_id"])
    )


@router.patch("/{uid}/morgan-lloyd", name="update-contribution-application")
async def update_contribution_application(
    uid: str, body: UpdateApplication, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Amend the amount on the Morgan Lloyd Contribution Application.

    ``cam_updatecontributionapplication`` is valid **only** while the record is at Application
    (809130000). The integration re-reads the status from Morgan Lloyd before sending rather
    than trusting our mirror — the mirror is as fresh as the last refresh, and the whole point
    of the gate is that someone else may have moved the record on. Past Application the answer
    is 409, not a vendor 502 that reads like an outage.
    """
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    record = await _contribution_for_provider(ctx, uid)
    context = await contributions_mod.ml_context(
        ctx.session, tenant_id=ctx.tenant_id, contribution=record
    )
    try:
        result = await morgan_lloyd.update_application(context, amount=body.amount)
    except morgan_lloyd.NotAnApplication as exc:
        raise _conflict(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # Our own amount follows the vendor's, with the relief arithmetic recomputed: leaving them
    # to disagree is what makes a reclaim wrong.
    await contributions_mod.set_amount(
        ctx.session, tenant_id=ctx.tenant_id, uid=uid, amount=body.amount
    )
    return await contributions_mod.record_ml_status(
        ctx.session, tenant_id=ctx.tenant_id, uid=uid, ml_status=int(result["ml_status"])
    )


@router.post("/{uid}/stop-morgan-lloyd", name="stop-contribution-at-morgan-lloyd")
async def stop_at_morgan_lloyd(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Stop the contribution at Morgan Lloyd (cam_status -> Stopped), and mirror it locally.

    The explicit retry for the best-effort stop in ``…/cancel``, so unlike that one it surfaces
    failures as status codes.
    """
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    record = await _contribution_for_provider(ctx, uid)
    report = await _stop_at_morgan_lloyd(ctx, record)
    if report["status"] != "stopped":
        _raise_for_report({**report, "status": report["status"]})
    return await contributions_mod.record_ml_status(
        ctx.session, tenant_id=ctx.tenant_id, uid=uid, ml_status=morgan_lloyd.STATUS_STOPPED
    )


@router.post("/{uid}/refresh-morgan-lloyd", name="refresh-contribution-from-morgan-lloyd")
async def refresh_from_morgan_lloyd(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Re-read ``cam_status`` from Morgan Lloyd and advance the pipeline to match.

    This is what moves a contribution through Application -> Active -> Received without anyone
    typing a status: those transitions happen at the scheme administrator and at HMRC, not
    here, so the only honest way to learn them is to ask.
    """
    require_permission(ctx.principal, CONTRIBUTIONS_WRITE)
    record = await _contribution_for_provider(ctx, uid)
    context = await contributions_mod.ml_context(
        ctx.session, tenant_id=ctx.tenant_id, contribution=record
    )
    try:
        observed = await morgan_lloyd.fetch_status(context)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if observed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Morgan Lloyd no longer lists this contribution against the scheme.",
        )
    return await contributions_mod.record_ml_status(
        ctx.session, tenant_id=ctx.tenant_id, uid=uid, ml_status=observed
    )


async def _contribution_for_provider(ctx: FirmContext, uid: str) -> dict[str, Any]:
    """The contribution, or the 404/409 that says why a provider call cannot be made on it."""
    record = await contributions_mod.get_contribution(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contribution not found")
    if not record["ml_contribution_id"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This contribution has not been recorded in Morgan Lloyd yet.",
        )
    return record


def _raise_for_report(report: dict[str, Any]) -> None:
    """Turn a lodge report into the matching HTTP failure. ``instructed`` passes through."""
    outcome = report.get("status")
    if outcome == "instructed":
        return
    detail = str(report.get("detail") or "SEI could not be reached.")
    if outcome == "blocked":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)
    if outcome == "unavailable":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)


# ── transfers ────────────────────────────────────────────────────────────────────────────


#: The transfers workbench's filters — status, adviser and the cash/in-specie split. `basis`
#: is a column (0147); unknown rows (raised before the drawer asked) render as an em dash,
#: the same word the UI's `basisLabel` uses. Keeping that rule in one place stops the filter
#: offering a value the table never shows.
def _basis(row: dict[str, Any]) -> str:
    match row.get("basis"):
        case "in_specie":
            return "In-specie"
        case "cash":
            return "Cash"
        case _:
            return "—"


# `status` filters on the STAGE. For a booked transfer the stage key is the status. A detected
# feed row uses the same stage, so the strip and the filter agree about it.
_TRANSFER_FILTERS: listing.Filters = {
    "status": lambda row, value: listing.matches(
        value, transfers_mod.stage_of(row.get("status")) or ""
    ),
    "adviser": lambda row, value: listing.matches(value, row.get("adviser")),
    "basis": lambda row, value: listing.matches(value, _basis(row)),
    "route": lambda row, value: listing.matches(value, row.get("route")),
    "custodian": lambda row, value: listing.matches(value, row.get("custodian")),
}
_TRANSFER_FACETS: listing.Facets = [
    ("status", lambda row: transfers_mod.stage_of(row.get("status"))),
    ("adviser", lambda row: row.get("adviser")),
    ("basis", _basis),
    ("route", lambda row: row.get("route")),
]
# Sortable columns, keyed as the workbench keys them. Closed set — see crm.py::_CONTACT_SORTS.
# `age` reads the created timestamp: the column shows "11 days" and the oldest transfer is the one
# created earliest, so ordering by the age LABEL would sort "9 days" after "11 days" as text.
# `status` sorts on the word the badge shows (`STATUS_LABEL`), not the raw stored key.
_TRANSFER_SORTS: listing.Sorts = {
    "created": lambda row: row.get("created_at"),
    "age": lambda row: row.get("created_at"),
    "client": lambda row: row.get("client_name"),
    "adviser": lambda row: row.get("adviser"),
    "type": _basis,
    "account": lambda row: row.get("account_name"),
    "provider": lambda row: row.get("provider_name") or row.get("ceding_provider"),
    "value": transfers_mod.sortable_amount,
    "status": lambda row: transfers_mod.STATUS_LABEL.get(
        row.get("status") or "", row.get("status")
    ),
}


@transfers_router.get("", name="list-transfers")
async def list_transfers(
    account_id: int | None = Query(default=None),
    include_detected: bool = Query(default=True),
    q: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    adviser: str | None = Query(default=None),
    basis: str | None = Query(default=None),
    route: str | None = Query(default=None),
    custodian: Annotated[list[str] | None, Query()] = None,
    sort: str | None = Query(default=None),
    dir: str | None = Query(default=None),  # noqa: A002 - the wire name the portal sends
    limit: int | None = Query(default=None, ge=1, le=listing.PAGE_LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
    ctx: FirmContext = Depends(target_firm_session),
) -> list[dict[str, Any]] | dict[str, Any]:
    """The firm's transfers — booked, and (by default) detected from the custodian feed.

    ``platform.transfers`` only holds transfers we raised, so a firm that has never used the
    workbench sees an empty page while its custodian is reporting transfer traffic. Seccl
    publishes that traffic on the transaction feed (``is_pension_transfer``,
    ``transfer_status``, ``previous_provider_name``, ``previous_provider_ref``), so the
    detected rows come from
    ``platform.transactions`` and are marked ``detected: true`` — read-only, and clearly not
    part of a saga nobody drove.

    Search, filters and paging are server-side (see ``app/listing.py``); paged only on request.
    """
    require_permission(ctx.principal, TRANSFERS_READ)
    await transfers_mod.apply_synced_progress(ctx.session, tenant_id=ctx.tenant_id)
    booked = await transfers_mod.list_transfers(
        ctx.session, tenant_id=ctx.tenant_id, account_id=account_id
    )
    rows = booked
    if include_detected:
        detected = await matching_mod.detected_transfers(ctx.session, tenant_id=ctx.tenant_id)
        if account_id is not None:
            detected = [row for row in detected if row["account_id"] == account_id]
        rows = [*booked, *detected]

    active = {
        "status": listing.clean(status_filter),
        "adviser": listing.clean(adviser),
        "basis": listing.clean(basis),
        "route": listing.clean(route),
        "custodian": listing.clean_many(custodian),
    }
    query = listing.clean(q)

    def searchable(row: dict[str, Any]) -> bool:
        return listing.search(
            query,
            row.get("client_name"),
            row.get("adviser"),
            row.get("ceding_provider"),
            row.get("policy_number"),
            row.get("account_name"),
            row.get("uid"),
            row.get("transaction_reference"),
        )

    found = [row for row in rows if searchable(row)]
    visible = [
        row
        for row in found
        if all(predicate(row, active[key]) for key, predicate in _TRANSFER_FILTERS.items())
    ]
    visible = listing.sort_rows(visible, _TRANSFER_SORTS, sort, dir)
    facet_options, facet_counts = listing.facets_and_counts(
        found, _TRANSFER_FACETS, active, _TRANSFER_FILTERS
    )
    if limit is not None:
        listing.union_facet(facet_options, "adviser", await adviser_directory(ctx.session))
    result = listing.page(
        visible,
        limit=limit,
        offset=offset,
        facets=facet_options,
        facet_counts=facet_counts,
    )
    # Whole-book stage totals, before the filters and the page cut — the same reason the
    # contributions strip is not derived from the rows on screen.
    if isinstance(result, dict):
        result["summary"] = transfers_mod.stage_totals(rows)
        # Which custodians this firm has switched on. The workbench offers the filter only
        # when there is more than one — a single-custodian book has nothing to choose.
        result["custodians"] = providers_mod.enabled_custodians(
            await providers_mod.list_for_tenant(ctx.session, ctx.tenant_id)
        )
    return result


@transfers_router.get("/providers", name="list-transfer-providers")
async def list_transfer_providers(
    ctx: FirmContext = Depends(target_firm_session),
) -> list[dict[str, Any]]:
    """The firm's ceding-provider register, for the new-transfer combobox.

    Its own endpoint rather than the catalogue's: that one lives under
    ``/admin/tenants/{id}/catalogue`` and is operations-scoped, and a firm raising a transfer
    needs to name a counterparty without being able to edit the register.

    Declared above ``/{uid}`` — FastAPI matches in declaration order, so a literal path
    registered after a parameterised one is unreachable.
    """
    require_permission(ctx.principal, TRANSFERS_READ)
    return await transfers_mod.list_providers(ctx.session, tenant_id=ctx.tenant_id)


@transfers_router.post("", name="create-transfer", status_code=status.HTTP_201_CREATED)
async def create_transfer(
    body: CreateTransfer, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Raise a transfer in from a ceding scheme, and the expectation for its money.

    The local row is written first, then ``transaction.transfer`` sends it. A refusal is stored
    on the transfer (``provider_error``, status stays ``requested``) so the same call can
    be retried; it is not a failed request, because rolling the row back would lose it.
    """
    require_permission(ctx.principal, TRANSFERS_WRITE)
    try:
        record = await transfers_mod.create_transfer(
            ctx.session,
            tenant_id=ctx.tenant_id,
            created_by=ctx.principal.user_id,
            account_id=body.account_id,
            ceding_provider=body.ceding_provider,
            transfer_type=body.transfer_type,
            basis=body.basis,
            extent=body.extent,
            policy_number=body.policy_number,
            crystallisation=body.crystallisation,
            pension_type=body.pension_type,
            safeguarded_benefits=body.safeguarded_benefits,
            crystallised_value=body.crystallised_value,
            uncrystallised_value=body.uncrystallised_value,
            total_value=body.total_value,
            expected_date=body.expected_date,
            note=body.note,
            provider_address_line1=body.provider_address_line1,
            provider_address_line2=body.provider_address_line2,
            provider_city=body.provider_city,
            provider_postcode=body.provider_postcode,
            provider_country=body.provider_country,
            fca_reference=body.fca_reference,
            external_account_type=body.external_account_type,
            assets=[asset.model_dump() for asset in body.assets] if body.assets else None,
            documents=(
                [document.model_dump() for document in body.documents] if body.documents else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await _through_transfer_action(
        ctx.session,
        tenant_id=ctx.tenant_id,
        record=record,
        requested_by=None,
        leg="raise",
    )


async def _through_transfer_action(
    session: Any,
    *,
    tenant_id: int,
    record: dict[str, Any],
    requested_by: str | None,
    leg: str,
) -> dict[str, Any]:
    """Run ``transaction.transfer`` for this row and return the transfer the workbench shows.

    A held run stays on the row as ``action_status`` so the drawer can say it is waiting.
    A structural refusal is written onto the row rather than rolling the raise back.
    """
    from app.actions.attempt import OperationError
    from app.actions.money import lodge_transfer
    from app.actions.runs import ActionError, Held

    try:
        sent = await lodge_transfer(
            session,
            tenant_id=tenant_id,
            record=record,
            requested_by=requested_by,
            leg=leg,
        )
    except ActionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except OperationError as exc:
        await transfers_mod.remember_provider_failure(
            session, tenant_id=tenant_id, uid=str(record["uid"]), message=str(exc)
        )
        sent = None
    except Exception as exc:  # noqa: BLE001 - a Graphene-side bug must not leak as a raw 500
        logger.exception("unexpected error lodging transfer %s", record.get("uid"))
        with contextlib.suppress(Exception):
            await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Couldn't raise the transfer. Nothing was sent — please try again.",
        ) from exc
    if isinstance(sent, Held):
        shown = await transfers_mod.get_transfer(
            session, tenant_id=tenant_id, uid=str(record["uid"])
        )
        if shown is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found.")
        return {
            **shown,
            "action_status": "awaiting_approval",
            "run_uid": sent.body.get("run_uid"),
        }
    shown = await transfers_mod.get_transfer(session, tenant_id=tenant_id, uid=str(record["uid"]))
    return shown or record


@transfers_router.get("/{uid}", name="get-transfer")
async def get_transfer(uid: str, ctx: FirmContext = Depends(target_firm_session)) -> dict[str, Any]:
    require_permission(ctx.principal, TRANSFERS_READ)
    await transfers_mod.apply_synced_progress(ctx.session, tenant_id=ctx.tenant_id)
    record = await transfers_mod.get_transfer(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found")
    return record


@transfers_router.get("/{uid}/match-candidates", name="list-transfer-match-candidates")
async def list_transfer_match_candidates(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> list[dict[str, Any]]:
    """Unmatched credits that could be this transfer's money, best first."""
    require_permission(ctx.principal, TRANSFERS_READ)
    record = await transfers_mod.get_transfer(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found")
    if not record.get("expectation_id"):
        return []
    return await matching_mod.candidates_for(
        ctx.session, tenant_id=ctx.tenant_id, expectation_id=int(record["expectation_id"])
    )


@transfers_router.post("/{uid}/attach-transaction", name="attach-transfer-transaction")
async def attach_transfer_transaction(
    uid: str, body: AttachTransaction, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Match this transfer's expectation to the credit that settled it, by hand.

    A transfer's money almost never arrives at the forecast amount — the ceding scheme deducts
    charges, and an in-specie leg settles in tranches — so unlike a contribution this is the
    normal path rather than the exception, and the automatic matcher will usually decline it.
    """
    require_permission(ctx.principal, TRANSFERS_WRITE)
    record = await transfers_mod.get_transfer(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found")
    if not record.get("expectation_id"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This transfer was raised without a value, so it has no expectation to match. "
                "Set its total value first."
            ),
        )
    credit = await matching_mod.get_transaction(
        ctx.session, tenant_id=ctx.tenant_id, transaction_id=body.transaction_id
    )
    if credit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    if credit["expectation_id"] is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That credit is already matched to another expectation.",
        )
    if credit["account_id"] != record["account_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That credit is on a different account.",
        )
    await matching_mod.link(
        ctx.session,
        tenant_id=ctx.tenant_id,
        expectation_id=int(record["expectation_id"]),
        transaction_id=body.transaction_id,
        matched_by=ctx.principal.user_id,
    )
    updated = await transfers_mod.get_transfer(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    assert updated is not None  # noqa: S101 - existed a statement ago, same transaction
    return updated


@transfers_router.delete("/{uid}/attach-transaction", name="detach-transfer-transaction")
async def detach_transfer_transaction(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Undo a transfer's match."""
    require_permission(ctx.principal, TRANSFERS_WRITE)
    record = await transfers_mod.get_transfer(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found")
    if not record.get("expectation_id"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This transfer has no expectation, so there is no match to undo.",
        )
    await matching_mod.unlink(
        ctx.session, tenant_id=ctx.tenant_id, expectation_id=int(record["expectation_id"])
    )
    updated = await transfers_mod.get_transfer(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    assert updated is not None  # noqa: S101
    return updated


@transfers_router.post("/{uid}/status", name="set-transfer-status")
async def set_transfer_status(
    uid: str, body: SetStatus, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    require_permission(ctx.principal, TRANSFERS_WRITE)
    try:
        return await transfers_mod.set_status(
            ctx.session, tenant_id=ctx.tenant_id, uid=uid, status=body.status
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _transfer_for_provider(ctx: FirmContext, uid: str) -> dict[str, Any]:
    """The transfer, or the 404 saying it is not this firm's."""
    record = await transfers_mod.get_transfer(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found")
    return record


def _require_morgan_lloyd_sipp(record: dict[str, Any]) -> None:
    """Morgan Lloyd's transfer calls apply to an SEI SIPP."""
    if record.get("route") != "ml_sei":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Morgan Lloyd administers SEI SIPPs.",
        )


@transfers_router.post(
    "/{uid}/record-to-morgan-lloyd",
    name="record-transfer-to-morgan-lloyd",
    response_model=None,
)
async def record_transfer_to_morgan_lloyd(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any] | JSONResponse:
    """Create the ``cam_personalpolicy`` (Transfer) record in Morgan Lloyd and link it.

    `platform.transfers`' columns were written to track ``cam_createtransfer``'s required list
    and nothing had ever sent one; this is that call.

    Idempotent by refusal: a transfer already recorded answers 409. Morgan Lloyd has no delete,
    and a duplicate here is a second instruction to a ceding scheme rather than a stray row.

    422 lists what the vendor needs and the record hasn't got — including the two facts no
    column holds (`data.basis`, `data.extent`), which are refused rather than defaulted because
    sending Cash for an in-specie transfer instructs the ceding scheme to sell the portfolio.
    """
    require_permission(ctx.principal, TRANSFERS_WRITE)
    record = await _transfer_for_provider(ctx, uid)
    _require_morgan_lloyd_sipp(record)
    if record["ml_transfer_id"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This transfer is already recorded in Morgan Lloyd.",
        )

    actor, _roles = await action_runs.caller_ids(ctx.session)
    try:
        run = await action_service.record_at_morgan_lloyd(
            ctx.session,
            tenant_id=ctx.tenant_id,
            action_key="transfer.record",
            account_id=int(record["account_id"]),
            uid=uid,
            requested_by=actor,
        )
    except action_runs.ActionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    paused = run.state.type.value == "paused"
    return action_runs.pending_response(
        action_runs.Held(
            {
                "status": "awaiting_approval" if paused else "running",
                "run_uid": run.uid,
                "action_key": "transfer.record",
            }
        )
    )


@transfers_router.post("/{uid}/refresh-morgan-lloyd", name="refresh-transfer-from-morgan-lloyd")
async def refresh_transfer_from_morgan_lloyd(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Re-read Morgan Lloyd's ``transferstatus`` and advance the saga to match.

    The only honest way to learn the six progress values: they are statements about the CEDING
    scheme and about Morgan Lloyd's own settlement, neither of which happens here. The mirror is
    monotonic (0056), so a stale read cannot walk a transfer backwards — except "No longer
    transferring", which cancels from anywhere because the ceding scheme withdrawing is
    authoritative.
    """
    require_permission(ctx.principal, TRANSFERS_WRITE)
    record = await _transfer_for_provider(ctx, uid)
    _require_morgan_lloyd_sipp(record)
    context = await transfers_mod.ml_context(ctx.session, tenant_id=ctx.tenant_id, transfer=record)
    try:
        observed = await morgan_lloyd.fetch_transfer_status(context)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if observed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Morgan Lloyd does not list a transfer for this client under that ceding policy "
                "number."
            ),
        )
    return await transfers_mod.record_ml_status(
        ctx.session, tenant_id=ctx.tenant_id, uid=uid, ml_status=observed
    )


@transfers_router.post("/{uid}/cancel-at-morgan-lloyd", name="cancel-transfer-at-morgan-lloyd")
async def cancel_transfer_at_morgan_lloyd(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Withdraw the transfer at Morgan Lloyd, and mirror it locally.

    409 once the money has moved: past "funds received" the ceding scheme has already paid, and
    withdrawing then is a return arranged with two counterparties rather than a cancellation.
    The gate reads the vendor's own current status, not our mirror — the whole point of it is
    that somebody else may have moved the record on.
    """
    require_permission(ctx.principal, TRANSFERS_WRITE)
    record = await _transfer_for_provider(ctx, uid)
    _require_morgan_lloyd_sipp(record)
    context = await transfers_mod.ml_context(ctx.session, tenant_id=ctx.tenant_id, transfer=record)
    try:
        await morgan_lloyd.cancel_transfer(context)
    except morgan_lloyd.TransferNotCancellable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except morgan_lloyd.MorganLloydUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return await transfers_mod.record_ml_status(
        ctx.session,
        tenant_id=ctx.tenant_id,
        uid=uid,
        ml_status=transfers_mod.ML_NO_LONGER_TRANSFERRING,
    )


def _provider_http(exc: Exception) -> HTTPException:
    """The status-code discipline at the top of this module, for Seccl and SEI."""
    if isinstance(exc, (TransferNotCancellable, transfers_mod.TransferConflict)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, (SecclNotConfigured, SeiNotConfigured)):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, (SecclUnavailable, SeiUnavailable)):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    raise exc


def _require_route(record: dict[str, Any], *routes: str, detail: str) -> None:
    if record.get("route") not in routes:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@transfers_router.post("/{uid}/record-to-seccl", name="record-transfer-to-seccl")
async def record_transfer_to_seccl(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Place a Seccl product transfer through ``transaction.transfer``.

    409 when this account is not Seccl's to instruct, or the transfer is already placed.
    """
    require_permission(ctx.principal, TRANSFERS_WRITE)
    record = await _transfer_for_provider(ctx, uid)
    _require_route(
        record,
        "seccl",
        detail="This transfer is not placed at Seccl — its administrator is not Seccl.",
    )
    if record.get("seccl_transfer_id") or record.get("seccl_placed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This transfer is already placed at Seccl.",
        )
    return await _through_transfer_action(
        ctx.session,
        tenant_id=ctx.tenant_id,
        record=record,
        requested_by=None,
        leg="raise",
    )


@transfers_router.post("/{uid}/refresh-seccl", name="refresh-transfer-from-seccl")
async def refresh_transfer_from_seccl(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Re-read Seccl's ``transferStatus`` and advance the saga. Never writes ``completed``."""
    require_permission(ctx.principal, TRANSFERS_WRITE)
    record = await _transfer_for_provider(ctx, uid)
    _require_route(record, "seccl", detail="This transfer is not held at Seccl.")
    try:
        observed = await seccl_transfers.fetch_transfer(record)
    except (ValueError, SecclNotConfigured, SecclUnavailable) as exc:
        raise _provider_http(exc) from exc
    native = seccl_transfers.transfer_status_of(observed)
    return await transfers_mod.apply_provider_progress(
        ctx.session,
        tenant_id=ctx.tenant_id,
        uid=uid,
        target=transfers_mod.seccl_stage(native),
        ceding_status=native,
    )


async def _cancel_detected_at_seccl(ctx: FirmContext, uid: str) -> dict[str, Any]:
    """Ask Seccl to cancel a product transfer that arrived on the feed."""
    identity = await transfers_mod.detected_identity(ctx.session, tenant_id=ctx.tenant_id, uid=uid)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found")
    if not identity.get("source_transaction_id"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="This transfer has no Seccl id to cancel against.",
        )
    if not transfers_mod.feed_cancel_allowed(identity):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Seccl will only cancel a product transfer that is still with the previous "
                "provider or its documentation checks."
            ),
        )
    handle = {"seccl_transfer_id": identity["source_transaction_id"]}
    try:
        await seccl_transfers.cancel_transfer(
            handle, current_status=identity.get("transfer_status")
        )
        after = await seccl_transfers.fetch_transfer(handle)
    except (ValueError, SecclNotConfigured, SecclUnavailable, TransferNotCancellable) as exc:
        raise _provider_http(exc) from exc
    latest = seccl_transfers.transfer_status_of(after) or identity.get("transfer_status")
    if latest and latest != identity.get("transfer_status"):
        await transfers_mod.stamp_feed_transfer_status(
            ctx.session,
            tenant_id=ctx.tenant_id,
            transaction_id=int(identity["id"]),
            status=str(latest),
        )
    return {"uid": uid, "ceding_status": latest}


@transfers_router.post("/{uid}/cancel-at-seccl", name="cancel-transfer-at-seccl")
async def cancel_transfer_at_seccl(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Request cancellation. Local ``cancelled`` waits until Seccl's own status says so.

    A feed row is not on ``platform.transfers``. Seccl still accepts ``requestCancellation``
    for a product transfer that is with the previous provider or its documentation checks,
    addressed by the transaction id the feed already holds.
    """
    require_permission(ctx.principal, TRANSFERS_WRITE)
    if uid.startswith("detected:"):
        return await _cancel_detected_at_seccl(ctx, uid)
    record = await _transfer_for_provider(ctx, uid)
    _require_route(record, "seccl", detail="This transfer is not held at Seccl.")
    try:
        current = await seccl_transfers.fetch_transfer(record)
        native = seccl_transfers.transfer_status_of(current)
        await seccl_transfers.cancel_transfer(record, current_status=native)
        after = await seccl_transfers.fetch_transfer(record)
    except (ValueError, SecclNotConfigured, SecclUnavailable, TransferNotCancellable) as exc:
        raise _provider_http(exc) from exc
    latest = seccl_transfers.transfer_status_of(after) or native
    return await transfers_mod.apply_provider_progress(
        ctx.session,
        tenant_id=ctx.tenant_id,
        uid=uid,
        target=transfers_mod.seccl_stage(latest),
        ceding_status=latest,
    )


@transfers_router.post("/{uid}/record-to-sei", name="record-transfer-to-sei")
async def record_transfer_to_sei(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Lodge at SEI via ``transaction.transfer``: a cash receipt, a transfer-in, or a free receipt.

    Which call depends on the route and the basis. A Morgan Lloyd transfer does not call
    ``transferIn`` — that would ask the ceding scheme a second time. Cash is an anticipated
    receipt, offered once Morgan Lloyd has completed the transfer. A SIPP uses a second run,
    because the raise already succeeded by leaving SEI until then.
    """
    require_permission(ctx.principal, TRANSFERS_WRITE)
    record = await _transfer_for_provider(ctx, uid)
    route = record.get("route")
    _require_route(
        record,
        "sei",
        "ml_sei",
        detail="This transfer's custodian is not SEI.",
    )
    if record.get("sei_activity_id") or record.get("sei_accepted"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This transfer is already with SEI.",
        )
    if route == "ml_sei" and record.get("status") != "funds_sent":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Generate the custodian receipt once Morgan Lloyd has completed the transfer.",
        )
    if route == "sei" and record.get("status") not in {"requested", "manual_intervention"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This transfer has already been submitted to SEI.",
        )
    return await _through_transfer_action(
        ctx.session,
        tenant_id=ctx.tenant_id,
        record=record,
        requested_by=None,
        leg="sei" if route == "ml_sei" else "raise",
    )


@transfers_router.post("/{uid}/refresh-sei", name="refresh-transfer-from-sei")
async def refresh_transfer_from_sei(
    uid: str, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """Poll SEI's pending-transfer status for a transfer-in.

    A cash receipt and a Morgan Lloyd free receipt are not in that list. Their activity
    ids are followed on the transaction feed. Code 7 (`COMPLETE`) is not Graphene's
    ``completed``.
    """
    require_permission(ctx.principal, TRANSFERS_WRITE)
    record = await _transfer_for_provider(ctx, uid)
    route = record.get("route")
    _require_route(record, "sei", "ml_sei", detail="This transfer's custodian is not SEI.")
    context = await transfers_mod.provider_context(
        ctx.session, tenant_id=ctx.tenant_id, transfer=record
    )
    try:
        sei_transfers.require_transfer_in(record)
        observed = await sei_transfers.poll_pending(context)
    except (ValueError, SeiNotConfigured, SeiUnavailable) as exc:
        raise _provider_http(exc) from exc
    code = observed.get("status_code")
    label = observed.get("status_label") or transfers_mod.sei_label(code)
    return await transfers_mod.apply_provider_progress(
        ctx.session,
        tenant_id=ctx.tenant_id,
        uid=uid,
        target=transfers_mod.sei_stage(code),
        ceding_status=str(label) if label else None,
        sei_activity_id=observed.get("activity_id"),
        advance=route == "sei",
    )


class TransferNote(BaseModel):
    note: str | None = None


@transfers_router.put("/{uid}/note", name="set-transfer-note")
async def set_transfer_note(
    uid: str, body: TransferNote, ctx: FirmContext = Depends(target_firm_session)
) -> dict[str, Any]:
    """An adviser note on the transfer. Stored here.

    Seccl's transfer-history call records a chasing event (``Client action complete``), and
    SEI's transfer calls have no note field, so neither is sent this text.
    """
    require_permission(ctx.principal, TRANSFERS_WRITE)
    try:
        return await transfers_mod.save_note(
            ctx.session, tenant_id=ctx.tenant_id, uid=uid, note=body.note
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


__all__ = ["router", "transfers_router"]
