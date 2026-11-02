"""Illustrations: a CTC pension quotation, or a Graphene GIA/ISA projection.

Pension wrappers go to CTC. A GIA or ISA is calculated here and filed on the same
``platform.illustrations`` row. The client's own data (name, date of birth) is read from the
record rather than retyped.

The generated PDF is filed as a ``documents.documents`` row so it inherits sharing, audit and
client-portal visibility. The illustration itself is a ``platform.illustrations`` row — the listing
and the assumptions — linked to that document.

The calculation engine is a third party. Where it is switched off or unreachable, a clearly-marked
placeholder is filed in the same shape (``app/integrations/ctc/placeholder.py``) and the response
says so, because a half-finished illustration on a pension record is worse than an obvious stand-in.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage, tenant_storage
from app.addressing import tenant_contact_id
from app.auth import Principal, get_current_principal, require_permission
from app.config import settings
from app.contacts import MARITAL_STATUSES, RETIREMENT_AGE_RANGE, update_contact
from app.db import target_tenant_session as tenant_session
from app.documents import service as docs_service
from app.ids import new_id
from app.integrations import ctc
from app.permissions import CASES_READ, DOCUMENTS_SEND, ILLUSTRATIONS_WRITE
from app.projections import IllustrationBlocked
from app.wrapper_illustrations import (
    InvestmentIllustrationInput,
    InvestmentIllustrationOut,
    create_wrapper_illustration,
)

router = APIRouter(prefix="/crm", tags=["crm"])

# Mirrors _PENSION_PRODUCTS in routers/crm.py and PENSION_PRODUCTS in the platform portal.
_PENSION_PRODUCTS = frozenset({"SIPP", "PENSION", "SSAS"})

# CTC needs a product reference from its own standing data. Morgan Lloyd's SIPP is the only
# product we can illustrate today; the mapping lives here so adding one is a single line.
_PRODUCT_REFS = {
    "SIPP": ("SIPPEN", "Self Invested Personal Pension"),
    "PENSION": ("SIPPEN", "Self Invested Personal Pension"),
    "SSAS": ("SIPPEN", "Self Invested Personal Pension"),
}


class IllustrationInput(BaseModel):
    """The assumptions an adviser supplies. Identity comes from the client record.

    An illustration normally precedes the account -- it is what the client is shown *before*
    deciding to open one. So ``account_id`` is optional: give it to illustrate an existing
    pension (product and fund value are then read from the account), or give ``product_type``
    and ``existing_fund_value`` to illustrate a prospective one.
    """

    account_id: int | None = None
    """Required only when illustrating an existing account."""
    product_type: str | None = None
    """Required when there is no account. A pension wrapper: SIPP | PENSION | SSAS."""
    existing_fund_value: Decimal = Decimal(0)
    """Uncrystallised fund to project from. Read off the account when one is given; otherwise
    the sum of the client's other SIPP accounts, which the form does not ask the adviser to type."""
    crystallised_fund_value: Decimal | None = None
    """A second existing-fund stream flagged crystallised (the spec allows only fund-value
    streams to be). The form no longer sends this: crystallised is a flag on the transfer."""
    initial_payment: Decimal | None = None
    """A one-off member payment. Its own Single stream, so the PDF lists it under payments into
    the plan rather than as money already held."""

    model_id: int | None = None
    """The investment model the projection is based on. Its `target_return` becomes the growth
    rate, and its name is what the client sees as the fund on the illustration."""

    quotation_type: str = Field(default="Accumulation")
    effective_date: str | None = None  # yyyy-mm-dd, defaults to today
    exit_age: int | None = None
    """Retirement age, sent only when the client record does not hold one. A value already on the
    record is what gets illustrated; this never overwrites it. A drawdown quote for someone
    already past that age runs further forward — see ``resolved_exit_age``."""
    marital_status: str | None = None
    """Same rule as ``exit_age``: a fill for a record that has none, ignored when it has one."""
    inflation_adjustment: bool | None = None

    # Contributions on top of the existing fund value.
    # Frequencies are nullable so a caller can switch contributions off wholesale by sending
    # nulls, rather than having to know a default for a field it is not using. They only matter
    # when the matching amount is present, and the stream builder falls back below.
    member_contribution: Decimal | None = None
    member_contribution_frequency: str | None = None
    member_contribution_net_gross: str | None = None
    employer_contribution: Decimal | None = None
    employer_contribution_frequency: str | None = None
    transfer_value: Decimal | None = None
    """A transfer in. Always a Single stream per the spec."""
    transfer_crystallised_amount: Decimal | None = None
    """How much of that transfer is crystallised. The spec allows the flag only on a transfer or
    an existing fund, and the form asks it only on a drawdown basis. Blank leaves the transfer
    uncrystallised; a part of it is a second stream; the whole of it crystallises the one stream."""

    # Drawdown assumptions (ignored unless quotation_type is a drawdown).
    drawdown_type: str | None = None
    withdrawal_amount: Decimal | None = None
    withdrawal_frequency: str | None = None
    withdrawal_start_date: str | None = None

    # Charges to project. Percentages are annual rates.
    ongoing_adviser_fee_percent: Decimal | None = None
    initial_adviser_fee_amount: Decimal | None = None

    # Annuity basis. A HOUSE assumption, not a per-client one, so the portal stopped asking (09 Aug
    # 2026) and ``house_annuity_basis()`` fills it in for any caller that supplies none. Supplying
    # any one of frequency/timing/escalation overrides the house basis wholesale -- which is the
    # seam a firm-level setting would use.
    annuity_frequency: str | None = None
    annuity_timing: str | None = None
    escalation: str | None = None
    escalation_percent: Decimal | None = None
    """Only meaningful with escalation 'Fixed'; LPI/RPI rates come from the index."""
    guaranteed_period: int | None = None
    spouse_percentage: Decimal | None = None

    include_annuity_basis: bool = True
    """Set false to omit the group entirely.

    Spec §i marks it mandatory for a pension product; the engine does not enforce that, and
    omitting it is a request CTC answers -- with the projected-income column reading zero on an
    accumulation quote and no difference at all on a drawdown one
    (docs/morgan-lloyd/illustrations-report.md §10). Kept as an explicit opt-out rather than as
    "send nothing and hope", because those two intentions used to be indistinguishable.
    """


class IllustrationOut(BaseModel):
    illustration_uid: str | None = None
    document_uid: str | None
    document_location: str | None
    errors: list[str]
    stored: bool
    placeholder: bool = False
    """True when the filed PDF is a Graphene stand-in, not a quotation from the engine."""
    placeholder_reason: str | None = None
    """Why there is no real illustration. Shown to the adviser verbatim."""
    request_xml: str | None = None
    """The CTC1 message that was built. Returned either way — it is what an adviser (or a
    presenter) needs to see to know what the integration sends."""
    response_xml: str | None = None
    """What the engine replied, verbatim. Returned on the same terms as `request_xml`, and for the
    same reason: the parsed view only carries the fields the parser knows about, so a reply that
    succeeds with figures nobody can explain — a growth rate of 999,996%, a fund of £0 — is
    undiagnosable without the engine's own words. None when no call was made."""


def _investor(client: dict[str, object], exit_age: int | None) -> ctc.Investor:
    """Map the client record onto a CTC investor. Names are already length-checked by the
    builder, so a 60-character surname fails here rather than at the engine."""
    return ctc.Investor(
        title=str(client["title"]) if client.get("title") else None,
        forename=str(client["first_name"]) if client.get("first_name") else None,
        surname=str(client["surname"]) if client.get("surname") else None,
        life_type="None",
        date_of_birth=str(client["dob"]) if client.get("dob") else None,
        # `xml_builder` validates both of these and the endpoint used to leave them unset, so
        # every illustration went out with no sex and no marital status. They matter: an
        # annuity projection is priced off them.
        sex=_CTC_SEX.get(str(client.get("gender") or "").lower()),
        marital_status=_CTC_MARITAL.get(str(client.get("marital_status") or "").lower()),
        exit_age=exit_age,
    )


# CTC's own vocabularies (xml_builder.SEXES / MARITAL_STATUSES), which are a third spelling
# again -- neither ours nor Dynamics'. Unmapped values stay None: both fields are optional in
# the schema, and "Unknown" is a claim, not an absence.
_CTC_SEX = {"male": "Male", "female": "Female"}
_CTC_MARITAL = {
    "single": "Single",
    "married": "Married",
    "civil_partner": "Civil Partnership",
    "divorced": "Divorced",
    "cohabiting": "Partnered",
    "widowed": "Widowed",
}


def _streams(fund_value: float, body: IllustrationInput) -> list[ctc.PlanStream]:
    """Existing fund first, then any new contributions. Stream keys are stable and readable so
    an adviser fee can be attached to the contribution it applies to."""
    streams = [
        ctc.PlanStream(
            key="EXISTING",
            contribution_type="Existing Fund Value",
            amount=Decimal(str(fund_value or 0)),
            frequency="Single",
        )
    ]
    if body.crystallised_fund_value:
        streams.append(
            ctc.PlanStream(
                key="CRYSTALLISED",
                contribution_type="Existing Fund Value",
                amount=body.crystallised_fund_value,
                frequency="Single",
                crystallised=True,
            )
        )
    if body.transfer_value:
        streams.extend(_transfer_streams(body.transfer_value, body.transfer_crystallised_amount))
    if body.initial_payment:
        streams.append(
            ctc.PlanStream(
                key="INITIAL",
                contribution_type="Member",
                amount=body.initial_payment,
                frequency="Single",
                # Relief at source is on the member's own money, and a one-off payment is gross
                # unless the adviser says otherwise. The form does not ask, matching a personal
                # contribution's default.
                net_gross="Gross",
            )
        )
    if body.member_contribution:
        streams.append(
            ctc.PlanStream(
                key="MEMBER",
                contribution_type="Member",
                amount=body.member_contribution,
                # A frequency only matters once there is an amount, so the caller may leave it
                # out entirely; monthly is what an adviser means by an unqualified contribution.
                frequency=body.member_contribution_frequency or "Monthly",
                net_gross=body.member_contribution_net_gross or "Gross",
            )
        )
    if body.employer_contribution:
        streams.append(
            ctc.PlanStream(
                key="EMPLOYER",
                contribution_type="Employer",
                amount=body.employer_contribution,
                frequency=body.employer_contribution_frequency or "Monthly",
            )
        )
    return streams


def _transfer_streams(
    transfer_value: Decimal, crystallised_amount: Decimal | None
) -> list[ctc.PlanStream]:
    """One transfer, or the uncrystallised remainder plus the crystallised part.

    Crystallised is a flag on a stream, not a second amount on top of the transfer. Saying
    £10,000 of a £40,000 transfer is crystallised must still illustrate £40,000.
    """
    crystallised = crystallised_amount or Decimal(0)
    if crystallised < 0 or crystallised > transfer_value:
        raise ValueError("The crystallised amount cannot be more than the transfer.")
    if crystallised == transfer_value:
        return [
            ctc.PlanStream(
                key="TRANSFER",
                contribution_type="Transfer Value",
                amount=transfer_value,
                frequency="Single",
                crystallised=True,
            )
        ]
    streams = [
        ctc.PlanStream(
            key="TRANSFER",
            contribution_type="Transfer Value",
            amount=transfer_value - crystallised,
            frequency="Single",  # the spec allows nothing else for a transfer
        )
    ]
    if crystallised > 0:
        streams.append(
            ctc.PlanStream(
                key="TRANSFER_CRYST",
                contribution_type="Transfer Value",
                amount=crystallised,
                frequency="Single",
                crystallised=True,
            )
        )
    return streams


#: The firm's standing annuity assumption: a level, single-life pension paid monthly in advance,
#: with no guarantee period. Approved 09 Aug 2026.
#:
#: It is a HOUSE assumption -- identical for every client -- which is why the portal no longer asks
#: and why the constant lives here rather than in the form: a portal that carries it is a portal
#: that can forget it, and forgetting means the group is omitted and the KFI's projected-income
#: column reads zero.
#:
#: **Say plainly which way it leans.** Level escalation and no spouse's pension together produce the
#: HIGHEST starting income any basis will show; RPI or LPI escalation, a guarantee period, or a 50%
#: spouse's pension would each show less. This is the conventional illustration basis, not a neutral
#: one. The single change to make it err low is ``escalation="RPI"``.
#:
#: The obvious next step is per-firm configuration. Nothing here needs to move for that: a caller
#: that supplies a basis already overrides this one wholesale.
HOUSE_ANNUITY_FREQUENCY = "Monthly"
HOUSE_ANNUITY_TIMING = "Advance"
HOUSE_ANNUITY_ESCALATION = "Level"


def house_annuity_basis() -> ctc.AnnuityBasis:
    """A fresh instance of the house basis.

    A function rather than a module-level constant because ``ctc.AnnuityBasis`` is a mutable
    dataclass: one shared instance could be edited by any caller that holds it, and every later
    illustration would quietly inherit the edit.
    """
    return ctc.AnnuityBasis(
        annuity_frequency=HOUSE_ANNUITY_FREQUENCY,
        annuity_timing=HOUSE_ANNUITY_TIMING,
        escalation=HOUSE_ANNUITY_ESCALATION,
    )


def _annuity_basis(body: IllustrationInput) -> ctc.AnnuityBasis | None:
    """The annuity group: the caller's, the house basis, or none.

    Three cases, in order. ``include_annuity_basis=False`` omits the group -- that is what leaves
    the "yearly income worth" column at zero, and it is now something a caller has to ask for
    rather than something that happens by not filling a form in. Any one of
    frequency/timing/escalation present means the caller has a basis of their own, and it is used
    as given (a partial group is a caller mistake, not something to top up from the house values --
    merging the two would produce a basis nobody chose). Otherwise: the house basis.
    """
    if not body.include_annuity_basis:
        return None
    if not (body.annuity_frequency or body.annuity_timing or body.escalation):
        return house_annuity_basis()
    return ctc.AnnuityBasis(
        annuity_frequency=body.annuity_frequency,
        annuity_timing=body.annuity_timing,
        escalation=body.escalation,
        percent=body.escalation_percent if body.escalation == "Fixed" else None,
        guaranteed_period=body.guaranteed_period,
        spouse_percentage=body.spouse_percentage,
    )


#: Where a one-off adviser charge is taken from, first match wins. A regular contribution is
#: never in this list: binding the charge to a monthly stream, and rating the pound amount Per
#: Annum, is what made CTC print "Initial adviser charge" as a monthly fee.
_INITIAL_FEE_STREAMS = ("INITIAL", "TRANSFER", "TRANSFER_CRYST", "EXISTING")


def _initial_fee_stream_key(streams: list[ctc.PlanStream]) -> str:
    """The Single stream the initial adviser charge comes out of.

    The initial payment, else the transfer, else the existing fund. Every one of those is
    frequency Single. A weekly, monthly, quarterly or annual contribution is skipped even when
    it is the newest stream in the request.
    """
    single = {stream.key for stream in streams if stream.frequency == "Single"}
    for key in _INITIAL_FEE_STREAMS:
        if key in single:
            return key
    # A request built by _streams always carries EXISTING. Naming it, rather than whatever
    # stream happens to be first, is what keeps a monthly contribution from picking the charge
    # up when a caller has handed in a list without the existing fund.
    return "EXISTING"


def _adviser_fees(body: IllustrationInput, streams: list[ctc.PlanStream]) -> list[ctc.Fee]:
    fees: list[ctc.Fee] = []
    if body.ongoing_adviser_fee_percent:
        fees.append(
            ctc.Fee(
                fee_name="Ongoing adviser charge",
                fee_basis="Ongoing",
                fee_frequency="Monthly",
                fee_timing="In Arrears",
                fee_value_rate="Per Annum",
                percentage=body.ongoing_adviser_fee_percent,
            )
        )
    if body.initial_adviser_fee_amount:
        fees.append(
            ctc.Fee(
                fee_name="Initial adviser charge",
                fee_basis="Initial",
                fee_frequency="Single",
                fee_timing="In Advance",
                # A one-off pound amount. Per Annum tells the engine the figure is an annual
                # rate, which it then spreads across a monthly stream.
                fee_value_rate="Per Frequency",
                amount=body.initial_adviser_fee_amount,
                stream_key=_initial_fee_stream_key(streams),
            )
        )
    return fees


async def _premium_split(
    session: AsyncSession,
    model_id: int,
    streams: list[ctc.PlanStream],
) -> tuple[ctc.PremiumSplit, str]:
    """The growth basis: one generic fund, named after the model, at the model's target return.

    THIS IS WHAT MAKES A PROJECTION VALID. CTC derives the illustration from
    premium_split/fund_split/rate_of_return; with no split it has no fund and no rate, substitutes
    its unset sentinel 999999 for the mid rate and derives low/high as mid +/- 3 -- which is
    exactly the 999,996% / 999,999% / 1,000,002% an adviser was shown, with every monetary figure
    then collapsing to zero. Verified against the live engine both ways.

    ONE generic fund rather than one per holding, deliberately. The model's allocations name our
    instruments (ISINs, Seccl asset ids); a fund_split names a fund in CTC's own catalogue, and we
    hold no mapping between the two. Projecting the model as a single fund at its target return is
    the honest version of what we can say -- and CTC prints the fund_name on the document, so the
    client sees the model's name rather than "Cash".

    THE MODEL'S OWN CHARGE GOES WITH IT. `platform.models.model_charge` is sent as the fund's
    ``management_charge`` — it was not sent at all before, so the one charge belonging to the
    investment rather than to the firm never reached the engine. Separate from the adviser fees
    (those are ``adviser_fees``, from the form), and optional: a model with no charge recorded sends
    none, which is a real answer rather than a zero. Whether CTC honours it on an accumulation quote
    is unmeasured — see the note at the call below.

    The rate is the MID rate; the engine spreads low and high itself (verified: 6.5 gives
    3.5/6.5/9.5).
    """
    row = (
        (
            await session.execute(
                text(
                    """
                    -- No tenant filter: the session is RLS-bound, so another firm's model is
                    -- not visible here and resolves as "does not exist" — the same answer, from
                    -- the database rather than from a WHERE clause someone could forget.
                    SELECT name, target_return, target_return_basis, model_charge
                      FROM platform.models
                     WHERE id = :id AND enabled
                    """
                ),
                {"id": model_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That investment model does not exist for this firm, or is not enabled.",
        )
    if row["target_return"] is None:
        # Refused rather than defaulted: a made-up growth rate on a client-facing projection is
        # worse than no illustration, and the absurd-rate failure above is what a silent fallback
        # looks like from the adviser's side.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Model '{row['name']}' has no target return, so there is no growth rate to "
                "project. Set one on the model before illustrating against it."
            ),
        )

    total = sum(Decimal(str(stream.amount)) for stream in streams)
    return (
        ctc.PremiumSplit(
            stream_keys=[stream.key for stream in streams],
            funds=[
                ctc.FundSplit(
                    # A generic fund, so fund_ref is our label rather than a CTC catalogue code.
                    fund_ref="MODEL",
                    fund_name=str(row["name"]),
                    split_amount=total,
                    rate_of_return=row["target_return"],
                    # The model's OWN annual charge, which this was not sending at all.
                    # `model_charge` is a real column (0042) — the materialiser fills it from
                    # Seccl's `modelCharge` (a fraction, scaled ×100 at the conformed layer so
                    # 0.002 lands as 0.2, matching `target_return` and CTC's "% management
                    # charge"), the catalogue UI edits it — and it was simply never selected
                    # here, so the one charge that belongs to the investment rather than to the
                    # firm never reached the engine. Adviser fees go separately as
                    # `adviser_fees`; this is not the same money.
                    #
                    # NOT YET VERIFIED AGAINST THE LIVE ENGINE, and §4 of
                    # docs/morgan-lloyd/illustrations-report.md is the reason to say so: CTC
                    # overrides fund-level `rate_of_return` on an accumulation quote with the COBS
                    # standard rates, so a fund-level input being sent is not proof it is honoured.
                    # The charge figures did respond to product and adviser fees (§5), but nothing
                    # has been measured for this element. Sending it is right either way — the spec
                    # provides for it and withholding a charge can only overstate — but do not tell
                    # an adviser the projection is net of it until a real PDF says so.
                    management_charge=row["model_charge"],
                )
            ],
        ),
        str(row["name"]),
    )


def _document_name(product_type: str, quotation_type: str, *, placeholder: bool) -> str:
    """The filed document's name.

    A placeholder says so first and in capitals: this name is the whole label in the documents
    listing, in the client portal, and in the "send to a client" picker, where nothing else about
    the document is visible.
    """
    name = f"{product_type} illustration — {quotation_type}"
    return f"PLACEHOLDER — {name}" if placeholder else name


def recorded_inputs(
    body: IllustrationInput,
    *,
    product_type: str,
    fund_value: float,
    model_name: str | None,
    effective_date: str,
    output_reference: str,
    placeholder: bool,
    placeholder_reason: str | None,
    engine_errors: list[str] | None = None,
    exit_age: int | None = None,
    marital_status: str | None = None,
) -> dict[str, object]:
    """The assumptions the onboarding account form reads back.

    The filed row used to keep only wrapper / basis / fund, so a transfer, contribution or
    withdrawal the adviser had already typed for the quotation never reached Account details.
    The full request is stored; run metadata (placeholder, engine errors, the resolved fund)
    overwrites the same keys so the listing and kickoff filters keep working.
    """
    recorded = body.model_dump(mode="json")
    recorded.update(
        {
            "product_type": product_type,
            "quotation_type": body.quotation_type,
            "existing_fund_value": float(fund_value),
            "account_id": body.account_id,
            "effective_date": effective_date,
            "output_reference": output_reference,
            "exit_age": exit_age if exit_age is not None else body.exit_age,
            "marital_status": (
                marital_status if marital_status is not None else body.marital_status
            ),
            "placeholder": placeholder,
            "placeholder_reason": placeholder_reason,
            "engine_errors": list(engine_errors or []),
        }
    )
    if body.model_id is not None:
        recorded["model"] = {"id": body.model_id, "name": model_name}
    return recorded


class IllustrationSummary(BaseModel):
    """A row of the client's illustrations listing."""

    uid: str
    """The illustration's own uid — not the document's."""
    document_uid: str | None = None
    """The filed PDF, when storage took it. Open/viewer paths use this."""
    name: str | None
    created_at: datetime
    inputs: dict[str, object]
    product_type: str | None = None
    status: str = "ready"
    shared_with_client: bool = False
    """True once the client can read the PDF."""


class OnboardingIllustrationOption(BaseModel):
    uid: str
    contact_id: int
    contact_uid: str
    client_name: str
    name: str | None
    created_at: datetime
    quotation_type: str | None = None
    existing_fund_value: float | None = None
    output_reference: str | None = None
    model_name: str | None = None
    document_uid: str | None = None


@router.get("/illustrations/onboarding", name="list-onboarding-illustrations")
async def list_onboarding_illustrations(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[OnboardingIllustrationOption]:
    """Unused successful SIPP runs offered at journey kickoff."""
    require_permission(principal, CASES_READ)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT i.uid, i.contact_id, c.uid AS contact_uid, "
                    "       COALESCE(NULLIF(concat_ws(' ', c.first_name, c.surname), ''), "
                    "                c.name, c.email, 'Client') AS client_name, "
                    "       i.name, i.created_at, i.quotation_type, "
                    "       i.existing_fund_value::float8 AS existing_fund_value, "
                    "       i.output_reference, i.inputs #>> '{model,name}' AS model_name, "
                    "       d.uid AS document_uid "
                    "FROM platform.illustrations i "
                    "JOIN crm.contacts c ON c.id = i.contact_id AND c.tenant_id = i.tenant_id "
                    "LEFT JOIN documents.documents d ON d.id = i.document_id "
                    "  AND d.status <> 'archived' "
                    "WHERE i.account_id IS NULL AND i.product_type = 'SIPP' "
                    "  AND i.placeholder IS FALSE "
                    "  AND jsonb_array_length(CASE "
                    "        WHEN jsonb_typeof(i.inputs -> 'engine_errors') = 'array' "
                    "        THEN i.inputs -> 'engine_errors' ELSE '[]'::jsonb END) = 0 "
                    "ORDER BY i.created_at DESC"
                )
            )
        )
        .mappings()
        .all()
    )
    return [OnboardingIllustrationOption(**dict(row)) for row in rows]


@router.get("/contacts/{contact_uid}/illustrations", name="list-illustrations")
async def list_illustrations(
    contact_id: Annotated[int, Depends(tenant_contact_id)],
    session: AsyncSession = Depends(tenant_session),
) -> list[IllustrationSummary]:
    """This client's illustrations, newest first.

    A dedicated read rather than filtering ``GET /documents``: the listing's columns come from
    ``platform.illustrations`` (the assumptions each PDF was built on), which the general documents
    projection deliberately doesn't expose. RLS scopes the rows.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT i.uid, d.uid AS document_uid, i.name, i.created_at, i.inputs, "
                    "       i.product_type, "
                    "       CASE "
                    "         WHEN d.status::text IN ('queued', 'generating') THEN 'generating' "
                    "         WHEN d.status::text = 'failed' THEN 'failed' "
                    "         WHEN i.placeholder IS TRUE THEN 'placeholder' "
                    "         ELSE 'ready' "
                    "       END AS status, "
                    "       EXISTS ("
                    "         SELECT 1 FROM documents.document_contacts dc "
                    "         WHERE dc.document_id = d.id AND dc.contact_id = i.contact_id"
                    "       ) AS shared_with_client "
                    "FROM platform.illustrations i "
                    "LEFT JOIN documents.documents d ON d.id = i.document_id "
                    "WHERE i.contact_id = :cid "
                    # An archived PDF drops out of Documents; the illustration row stays but is
                    # hidden here so the two listings cannot disagree about what is live.
                    "  AND (d.id IS NULL OR d.status <> 'archived') "
                    "ORDER BY i.created_at DESC"
                ),
                {"cid": contact_id},
            )
        )
        .mappings()
        .all()
    )
    return [IllustrationSummary(**dict(r)) for r in rows]


class IllustrationShared(BaseModel):
    shared_with_client: bool = True


@router.post(
    "/contacts/{contact_uid}/illustrations/{illustration_uid}/share",
    name="share-illustration",
)
async def share_illustration(
    illustration_uid: str,
    contact_id: Annotated[int, Depends(tenant_contact_id)],
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> IllustrationShared:
    """Show this one illustration in the client's portal.

    Auto-share does this for every new illustration when the firm has opted in. This is the
    adviser's choice for one row: the same ``subject`` link, and not a send — no email, and no
    ``recipient`` row that would record a delivery. ``documents:send`` because the PDF leaves
    the firm.
    """
    require_permission(principal, DOCUMENTS_SEND)
    row = (
        (
            await session.execute(
                text(
                    "SELECT i.tenant_id, i.placeholder, d.id AS document_id, "
                    "       d.status::text AS status "
                    "FROM platform.illustrations i "
                    "LEFT JOIN documents.documents d ON d.id = i.document_id "
                    "WHERE i.uid = :uid AND i.contact_id = :cid"
                ),
                {"uid": illustration_uid, "cid": contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["document_id"] is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Illustration not found.")
    if row["placeholder"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An indicative illustration is not shared with the client.",
        )
    if row["status"] != "generated":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="This illustration is not ready to share.",
        )
    await docs_service.link_subject(
        session,
        tenant_id=int(row["tenant_id"]),
        document_id=row["document_id"],
        contact_id=contact_id,
    )
    return IllustrationShared()


class IllustrationFacts(NamedTuple):
    """The retirement age and marital status an illustration is built from, and what to write
    back onto the client. ``fill`` is empty when the record already held both."""

    exit_age: int
    marital_status: str
    fill: dict[str, object]


def illustration_facts(
    *,
    recorded_age: int | None,
    recorded_marital: str | None,
    requested_age: int | None,
    requested_marital: str | None,
) -> IllustrationFacts:
    """The retirement age typed for this quote is the one illustrated.

    The form always asks, and never copies a stored age into the box, so a request that
    carries an age is that answer. It is written back only when the record has none, or
    when the stored age is under 55 and cannot be illustrated. A stored age of 55 or over
    is left as it is. Marital status is still taken from the record when the record holds
    one; a gap is filled from the request, and a gap the request does not fill is refused.
    """
    fill: dict[str, object] = {}
    problems: list[str] = []
    # The contact column still allows 50. An illustration will not: the minimum pension age is 55.
    low, high = 55, RETIREMENT_AGE_RANGE[1]

    def usable(value: int | None) -> bool:
        return value is not None and low <= value <= high

    age: int | None
    if requested_age is not None:
        if not usable(requested_age):
            problems.append(f"Enter a retirement age between {low} and {high}.")
            age = None
        else:
            age = requested_age
            # A usable stored age is the client's record, not this quote's assumption.
            if recorded_age is None or not usable(recorded_age):
                fill["retirement_age"] = age
    elif usable(recorded_age):
        age = recorded_age
    else:
        problems.append(f"Enter a retirement age between {low} and {high}.")
        age = None

    marital = (recorded_marital or "").strip() or None
    if marital is None:
        requested = (requested_marital or "").strip()
        if requested not in MARITAL_STATUSES:
            problems.append("Choose a marital status.")
        else:
            marital = requested
            fill["marital_status"] = marital

    if problems or age is None or marital is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=" ".join(problems) or "A retirement age and a marital status are required.",
        )
    return IllustrationFacts(age, marital, fill)


def _as_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def resolved_exit_age(
    *,
    retirement_age: int,
    dob: object,
    effective_date: str,
    quotation_type: str,
) -> int:
    """The age the engine projects to.

    The record's retirement age, once it is still in the future. A drawdown illustration for a
    client who has already reached it — the ordinary shape of a pension in payment, and the only
    shape the column allows once someone is over 75 — runs to ``DRAWDOWN_HORIZON_AGE`` instead.
    Sending the recorded age would ask the engine for a maturity date that has already passed.
    """
    born = _as_date(dob)
    if born is None:
        return retirement_age
    try:
        effective = date.fromisoformat(effective_date)
    except ValueError as exc:
        raise ctc.CtcValidationError(
            f"effective_date {effective_date!r} is not yyyy-mm-dd"
        ) from exc
    return ctc.projection_exit_age(
        retirement_age=retirement_age,
        born=born,
        effective=effective,
        drawdown="Drawdown" in quotation_type,
    )


async def _fill_contact_gaps(
    session: AsyncSession, *, contact_id: int, fields: dict[str, object]
) -> None:
    """Write a missing retirement age or marital status, then go back to authenticated.

    This request runs as ``authenticated``, which may SELECT crm.contacts and may not UPDATE
    it (0002: writes go through service_role, the same role PATCH /firm/contacts uses). The
    savepoint keeps a failed UPDATE from aborting the transaction before the role is restored,
    which would hide the original error behind a second one from ``SET ROLE``.
    """
    tenant_id = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one()
    await session.execute(text("SAVEPOINT illustration_contact_fill"))
    await session.execute(text("SET LOCAL ROLE service_role"))
    try:
        await update_contact(
            session,
            tenant_id=int(tenant_id),
            contact_id=contact_id,
            fields=fields,
        )
    except LookupError as exc:
        await session.execute(text("ROLLBACK TO SAVEPOINT illustration_contact_fill"))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        ) from exc
    except Exception:
        await session.execute(text("ROLLBACK TO SAVEPOINT illustration_contact_fill"))
        raise
    else:
        await session.execute(text("RELEASE SAVEPOINT illustration_contact_fill"))
    finally:
        await session.execute(text("SET LOCAL ROLE authenticated"))


@router.post(
    "/contacts/{contact_uid}/illustrations",
    name="create-illustration",
    status_code=status.HTTP_201_CREATED,
)
async def create_illustration(
    contact_id: Annotated[int, Depends(tenant_contact_id)],
    body: IllustrationInput,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> IllustrationOut:
    """Generate a pension illustration and file the PDF against the client.

    RLS decides whether the caller may see the client and account. A CTC rejection is returned
    as a 200-shaped result with ``errors`` rather than an HTTP error: the engine's descriptions
    ("Specified fund not recognised. LU0278930234") are the useful output, and the adviser needs
    to read them to fix the request. In that case — and whenever the engine is off, slow or
    unreachable — a placeholder is filed and ``placeholder`` is true.

    Gated ``illustrations:write`` (split out of ``documents:write``): this calls a paid external
    engine and files client-facing advice output — a different trust from editing documents.
    """
    require_permission(principal, ILLUSTRATIONS_WRITE)

    # Identity always comes from the client record — an illustration is *for* a person, whether
    # or not they hold an account yet.
    row = (
        (
            await session.execute(
                text(
                    "SELECT c.title, c.first_name, c.surname, c.dob, c.type AS client_type, "
                    "       c.name AS client_name, c.gender, c.marital_status, "
                    "       c.retirement_age "
                    "FROM crm.contacts c "
                    "WHERE c.id = :cid AND c.deleted_at IS NULL"
                ),
                {"cid": contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    # Existing account: read the wrapper + fund off it. Prospective: the adviser states both.
    reference = f"CON{contact_id}"
    fund_value = float(body.existing_fund_value)
    if body.account_id is not None:
        account = (
            (
                await session.execute(
                    text(
                        "SELECT a.id, a.value::float8 AS value, p.product_type "
                        "FROM platform.accounts a "
                        "LEFT JOIN platform.products p ON p.id = a.product_id "
                        "WHERE a.id = :aid AND a.contact_id = :cid"
                    ),
                    {"aid": body.account_id, "cid": contact_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if account is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Account not found for this client",
            )
        product_type = str(account["product_type"] or "").upper()
        fund_value = float(account["value"] or 0.0)
        reference = f"ACC{account['id']}"
    else:
        if not body.product_type:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Give an account_id, or a product_type to illustrate a new pension.",
            )
        product_type = body.product_type.upper()

    if product_type not in _PENSION_PRODUCTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Illustrations apply to pension wrappers only "
            f"({', '.join(sorted(_PENSION_PRODUCTS))}).",
        )
    product_ref, product_label = _PRODUCT_REFS[product_type]

    now = datetime.now(UTC)
    effective_date = body.effective_date or now.date().isoformat()
    output_reference = f"{reference}-{now.strftime('%Y%m%d%H%M%S')}"[:45]
    try:
        streams = _streams(fund_value, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    # No model, no projection: see _premium_split. Sending a quotation without a growth basis is
    # what produced the 999,996% illustrations, so it is refused at the door rather than filed.
    if body.model_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "An illustration needs an investment model: its target return is the growth rate "
                "the projection is built on."
            ),
        )
    premium_split, model_name = await _premium_split(session, body.model_id, streams)
    raw_age = row["retirement_age"]
    facts = illustration_facts(
        recorded_age=raw_age if isinstance(raw_age, int) else None,
        recorded_marital=str(row["marital_status"] or ""),
        requested_age=body.exit_age,
        requested_marital=body.marital_status,
    )
    if facts.fill:
        # Written before the engine runs, so a quotation is never filed against facts the record
        # does not hold. Only the gaps — update_contact touches the keys it is given.
        await _fill_contact_gaps(session, contact_id=contact_id, fields=facts.fill)
    try:
        exit_age = resolved_exit_age(
            retirement_age=facts.exit_age,
            dob=row["dob"],
            effective_date=effective_date,
            quotation_type=body.quotation_type,
        )
    except ctc.CtcValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    client_row = dict(row)
    client_row["marital_status"] = facts.marital_status
    withdrawals = None
    if body.drawdown_type:
        withdrawals = ctc.Withdrawals(
            drawdown_type=body.drawdown_type,
            basis="Fixed" if body.withdrawal_amount else None,
            amount=body.withdrawal_amount,
            frequency=body.withdrawal_frequency,
            start_date=body.withdrawal_start_date,
        )

    request = ctc.IllustrationRequest(
        effective_date=effective_date,
        created_date=now.strftime("%Y-%m-%d %H:%M:%S"),
        # output_reference names the PDF the engine builds; keep it traceable to the account when
        # there is one, otherwise to the client.
        output_reference=output_reference,
        agent_name=settings.ctc_agent_name or None,
        client_type="Individual" if row["client_type"] == "individual" else "Company",
        client_name=str(row["client_name"]) if row["client_type"] != "individual" else None,
        # The record wins for a retirement still ahead, and a fill is written back before the
        # engine runs. Someone already past that age is illustrated in drawdown to a later age;
        # that later age is the term of the quote, and it is not written onto the record.
        investors=[_investor(client_row, exit_age)],
        quotations=[
            ctc.Quotation(
                product_ref=product_ref,
                product_type=product_label,
                quotation_type=body.quotation_type,
                inflation_adjustment=body.inflation_adjustment,
                plan_streams=streams,
                premium_splits=[premium_split],
                withdrawals=withdrawals,
                adviser_fees=_adviser_fees(body, streams),
                pension=True,
                annuity_basis=_annuity_basis(body),
            )
        ],
    )

    # An unbuildable request still fails loudly. Everything past that point degrades to a marked
    # placeholder rather than a dead end — see ctc/placeholder.py for why that is safe.
    try:
        outcome = await ctc.generate_or_placeholder(request)
    except ctc.CtcValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    name = _document_name(product_type, body.quotation_type, placeholder=outcome.placeholder)

    inputs = recorded_inputs(
        body,
        product_type=product_type,
        fund_value=float(fund_value),
        model_name=model_name,
        effective_date=request.effective_date,
        output_reference=output_reference,
        placeholder=outcome.placeholder,
        placeholder_reason=outcome.placeholder_reason,
        engine_errors=outcome.engine_errors,
        exit_age=exit_age,
        marital_status=facts.marital_status,
    )

    try:
        doc_uid, document_id = await _store_pdf(
            session,
            contact_id=contact_id,
            pdf=outcome.pdf,
            name=name,
            # Kept so the illustrations listing has real columns (wrapper, basis, fund) and a
            # rejected or superseded illustration can be diagnosed without re-deriving the request.
            inputs=inputs,
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_503_SERVICE_UNAVAILABLE:
            raise
        # Storage is unconfigured. Say so instead of 503-ing: the illustration was produced, and
        # the adviser can still read the request and the reason.
        return IllustrationOut(
            illustration_uid=None,
            document_uid=None,
            document_location=outcome.document_location,
            errors=[*outcome.engine_errors, str(exc.detail)],
            stored=False,
            placeholder=outcome.placeholder,
            placeholder_reason=outcome.placeholder_reason,
            request_xml=outcome.request_xml,
            response_xml=outcome.response_xml,
        )

    illustration_uid = await _record_illustration(
        session,
        contact_id=contact_id,
        account_id=body.account_id,
        document_id=document_id,
        name=name,
        product_type=product_type,
        quotation_type=body.quotation_type,
        existing_fund_value=float(fund_value),
        output_reference=output_reference,
        placeholder=outcome.placeholder,
        placeholder_reason=outcome.placeholder_reason,
        inputs=inputs,
    )

    return IllustrationOut(
        illustration_uid=illustration_uid,
        document_uid=doc_uid,
        document_location=outcome.document_location,
        errors=outcome.engine_errors,
        stored=True,
        placeholder=outcome.placeholder,
        placeholder_reason=outcome.placeholder_reason,
        request_xml=outcome.request_xml,
        response_xml=outcome.response_xml,
    )


async def _store_pdf(
    session: AsyncSession,
    *,
    contact_id: int,
    pdf: bytes,
    name: str,
    inputs: dict[str, object],
) -> tuple[str, object]:
    """File the PDF as a generated document linked to the client. Mirrors the upload path so
    illustrations behave like every other document (download, share, audit).

    Returns ``(document_uid, document_id)`` — the public id the portals address and the uuid the
    illustration row points at.
    """
    tenant_id = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one_or_none()
    if tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenant context")

    doc_uid = new_id()
    bucket = await tenant_storage.document_bucket(session, tenant_id)
    key = storage.tenant_upload_key(doc_uid, "pdf")
    try:
        await storage.put_document(key, pdf, "application/pdf", bucket=bucket)
    except storage.StorageUnavailable as exc:
        # str(exc): the exception distinguishes an unconfigured store from an unreachable one, and
        # sending an operator to check S3_DOCUMENTS_BUCKET when it is set is a wasted afternoon.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    # A store that is configured but refuses the write (missing bucket, wrong credentials) is the
    # same outcome from the adviser's side: the illustration exists, it is not filed. Reported as
    # 503 so the caller states that, rather than a 500 in front of whoever is looking at the screen.
    # Only the upload is inside this — a failed INSERT below must still surface as a real error.
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Document storage refused the upload: {exc}",
        ) from exc

    file_id = (
        await session.execute(
            text(
                "INSERT INTO documents.files "
                "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                "VALUES (auth.tenant_id(), :bucket, :key, 'application/pdf', :size, :sha) "
                "RETURNING id"
            ),
            {
                "bucket": bucket,
                "key": key,
                "size": len(pdf),
                "sha": hashlib.sha256(pdf).hexdigest(),
            },
        )
    ).scalar_one()

    # `intended_contact_id` records who the document is FOR without showing it to them (0021) —
    # it is what the client tab lists by, and what a later share reads.
    document_id = (
        await session.execute(
            text(
                "INSERT INTO documents.documents "
                "  (uid, tenant_id, document_type, source, name, status, "
                "   original_file_id, pdf_file_id, generated_by, intended_contact_id, inputs) "
                "VALUES (:uid, auth.tenant_id(), 'illustration', 'generated', :name, "
                "        'generated', :fid, :fid, auth.uid(), :contact, CAST(:inputs AS jsonb)) "
                "RETURNING id"
            ),
            {
                "uid": doc_uid,
                "name": name,
                "fid": file_id,
                "contact": contact_id,
                "inputs": json.dumps(inputs),
            },
        )
    ).scalar_one()

    # The 'subject' link is the client's read permission. It used to be bound here on every
    # render, so an illustration nobody had reviewed was in the portal the moment it existed
    # (FB-20261006-04). Now only a firm that has opted in (`illustrations_auto_share`) files it at
    # once; otherwise staff share it from the document, like an upload.
    if await docs_service.illustrations_auto_share(session, int(tenant_id)):
        await docs_service.link_subject(
            session,
            tenant_id=int(tenant_id),
            document_id=document_id,
            contact_id=contact_id,
        )
    return doc_uid, document_id


async def _record_illustration(
    session: AsyncSession,
    *,
    contact_id: int,
    account_id: int | None,
    document_id: object,
    name: str,
    product_type: str,
    quotation_type: str,
    existing_fund_value: float,
    output_reference: str,
    placeholder: bool,
    placeholder_reason: str | None,
    inputs: dict[str, object],
) -> str:
    """The domain row. The PDF is the artefact; this is the listing."""
    uid = new_id()
    row = await session.execute(
        text(
            "INSERT INTO platform.illustrations ("
            "  uid, tenant_id, contact_id, account_id, document_id, name, product_type, "
            "  quotation_type, existing_fund_value, output_reference, placeholder, "
            "  placeholder_reason, inputs, created_by"
            ") VALUES ("
            "  :uid, auth.tenant_id(), :cid, :aid, :did, :name, :product_type, "
            "  :quotation_type, :fund, :output_reference, :placeholder, "
            "  :placeholder_reason, CAST(:inputs AS jsonb), auth.uid()"
            ") RETURNING uid"
        ),
        {
            "uid": uid,
            "cid": contact_id,
            "aid": account_id,
            "did": document_id,
            "name": name,
            "product_type": product_type,
            "quotation_type": quotation_type,
            "fund": existing_fund_value,
            "output_reference": output_reference,
            "placeholder": placeholder,
            "placeholder_reason": placeholder_reason,
            "inputs": json.dumps(inputs),
        },
    )
    return str(row.scalar_one())


class PlatformFeeChoice(BaseModel):
    id: int
    name: str
    fee_type: str
    summary: str


class IllustrationCapability(BaseModel):
    enabled: bool
    platform_fees: list[PlatformFeeChoice] = []
    missing_disclosure: list[str] = []


def _fee_summary(fee_type: str, rule: object) -> str:
    if not isinstance(rule, dict):
        return fee_type
    if fee_type == "percentage" and "rate" in rule:
        return f"{rule['rate']}% ongoing"
    if fee_type == "fixed" and "amount" in rule:
        return f"£{rule['amount']} fixed"
    if fee_type == "tiered":
        return "Tiered"
    return fee_type


@router.get("/illustration-capability", name="illustration-capability")
async def illustration_capability(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> IllustrationCapability:
    """Whether this firm issues GIA and ISA illustrations, and the platform fees it can use."""
    require_permission(principal, ILLUSTRATIONS_WRITE)
    firm = (
        (
            await session.execute(
                text(
                    "SELECT gia_isa_illustrations, legal_name, frn, chn, registered_office, "
                    "       illustration_warning "
                    "FROM auth.tenants WHERE id = auth.tenant_id()"
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    missing = [
        label
        for label, value in (
            ("legal entity", None if firm is None else firm["legal_name"]),
            ("FCA number", None if firm is None else firm["frn"]),
            ("company number", None if firm is None else firm["chn"]),
            ("registered office", None if firm is None else firm["registered_office"]),
            ("future-performance warning", None if firm is None else firm["illustration_warning"]),
        )
        if value in (None, "")
    ]
    fees = (
        (
            await session.execute(
                text(
                    "SELECT id, name, fee_type::text AS fee_type, rule "
                    "FROM platform.fee_packages "
                    "WHERE coalesce(enabled, true) AND category ILIKE '%platform%' "
                    "ORDER BY sort_order, name"
                )
            )
        )
        .mappings()
        .all()
    )
    return IllustrationCapability(
        enabled=bool(firm and firm["gia_isa_illustrations"]),
        missing_disclosure=missing,
        platform_fees=[
            PlatformFeeChoice(
                id=int(row["id"]),
                name=str(row["name"]),
                fee_type=str(row["fee_type"]),
                summary=_fee_summary(str(row["fee_type"]), row["rule"]),
            )
            for row in fees
        ],
    )


@router.post(
    "/contacts/{contact_uid}/illustrations/investment",
    name="create-investment-illustration",
    status_code=status.HTTP_201_CREATED,
)
async def create_investment_illustration(
    contact_id: Annotated[int, Depends(tenant_contact_id)],
    body: InvestmentIllustrationInput,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> InvestmentIllustrationOut:
    """Calculate a GIA or ISA illustration and file it against the client.

    The figures are computed here. The PDF is queued for the render worker. A missing reference
    row is a 400 and writes nothing. Gated ``illustrations:write``, same as the pension path.
    """
    require_permission(principal, ILLUSTRATIONS_WRITE)
    try:
        out = await create_wrapper_illustration(session, contact_id, body)
    except IllustrationBlocked as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail) from exc
    if out is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    return out
