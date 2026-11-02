"""Transfers in from a ceding scheme (docs/contributions.md).

The sibling of ``app.contributions``: same three-record shape (domain record → expectation →
custodian instruction), different domain. A transfer raises an expectation too — the ceding
scheme's money is due on the account — so the matcher treats both the same way.

Column names track ``cam_createtransfer``'s required list so pushing to Morgan Lloyd is a
mapping rather than a translation. One thing is corrected at the boundary rather than carried
inward: the vendor's field is ``cam_uncrystalllisedtransfervalue``, with three Ls. That is a
wire-format quirk, not a domain concept, so the column is spelled properly and the adapter
does the mangling.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any, TypedDict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import outbound
from app.ids import new_id
from app.integrations.sei.reference import portfolio_entity_id


def _administrator_ids() -> list[str]:
    from app import providers

    return list(providers.administrators())


def _provider_params() -> dict[str, list[str]]:
    from app import providers

    return {
        "administrators": list(providers.administrators()),
        "custodians": list(providers.custodians()),
    }


TRANSFER_TYPES = frozenset({"pension", "isa", "gia", "cash", "in_specie"})

#: How the money moves. Distinct from ``TRANSFER_TYPES``, which is the receiving wrapper.
TRANSFER_BASES = frozenset({"cash", "in_specie"})
#: How much of the ceding pot moves.
TRANSFER_EXTENTS = frozenset({"full", "partial"})

#: Whether the transferring pension has taken benefits. Uncrystallised is accumulation.
CRYSTALLISATIONS = frozenset({"uncrystallised", "partial", "crystallised"})

#: Morgan Lloyd ``cam_pensiontype`` (1–20). Labels are the vendor's, used by the raise drawer.
PENSION_TYPES: dict[int, str] = {
    1: "Additional Voluntary Contribution",
    2: "Annuity",
    3: "APPP - Protected Rights",
    4: "Bulk Buy-Out Plan",
    5: "Contracted In Money Purchase",
    6: "Contracted Out Money Purchase",
    7: "Drawdown",
    8: "Executive Personal Pension",
    9: "Final Salary Scheme",
    10: "Free Standing Additional Voluntary Contribution",
    11: "Group Personal Pension",
    12: "Group Stakeholder Pension",
    13: "Other",
    14: "Personal Pension Plan",
    15: "QROPS",
    16: "Retirement Annuity Contract",
    17: "Section 32 Policy Buy Out Bond",
    18: "Self Invested Personal Pension",
    19: "Small Self Administered Scheme",
    20: "Stakeholder",
}

#: `platform.product_type` -> the transfer type it implies. Derived rather than asked: the
#: account already says what wrapper it is, and making an adviser restate it is a question with
#: exactly one correct answer they can nonetheless get wrong. Unmapped products fall back to
#: `cash`, which is the honest reading of "money is coming in and the wrapper does not change
#: what kind of transfer it is".
TRANSFER_TYPE_FOR_PRODUCT: dict[str, str] = {
    "SIPP": "pension",
    "TPPENSION": "pension",
    "PENSION": "pension",
    "SSAS": "pension",
    "ISA": "isa",
    "JISA": "isa",
    "GIA": "gia",
}


def transfer_type_for(product_type: str | None) -> str:
    """The transfer type an account's wrapper implies."""
    return TRANSFER_TYPE_FOR_PRODUCT.get(str(product_type or "").upper(), "cash")


class FormSpec(TypedDict):
    """Which raise-transfer fields this account needs. One vocabulary, gated visibility."""

    ask_extent: bool
    ask_crystallised_split: bool
    #: Crystallisation status and safeguarded rights. Every pension, including a Seccl SIPP.
    ask_pension_facts: bool
    #: Morgan Lloyd ``cam_pensiontype``. An SEI SIPP only — Seccl does not take it.
    ask_pension_type: bool
    default_extent: str | None
    #: False for an SEI-custodied pension: cash would instruct the ceding scheme to liquidate
    #: into an account SEI will not take that way. In-specie only.
    allow_cash: bool
    #: Seccl's product transfer refuses without the ceding scheme's address. Morgan Lloyd sends
    #: the same lines when we hold them, and does not require them.
    ask_ceding_address: bool
    #: Ceding account type and uploaded form reference. Direct SEI only; a Morgan Lloyd SIPP
    #: does not call ``transferIn``.
    ask_sei_instruction: bool


def form_spec_for(
    *,
    custodian: str | None,
    scheme_administrator: str | None,
    is_pension: bool,
) -> FormSpec:
    """The raise drawer's required set, from the receiving account.

    Cash vs in-specie is asked of every account and never defaulted (Cash means the ceding
    scheme sells). A Seccl account, including a Seccl SIPP, is full-only: extent is hidden
    and stored as ``full``, and the ceding scheme's address is required. Seccl administers
    its own SIPP, so a product mapping that still names Morgan Lloyd does not add Morgan
    Lloyd's fields.

    Morgan Lloyd administers an SEI SIPP. That account asks the crystallised split and
    ``cam_pensiontype``. Crystallisation status and safeguarded rights are asked of every
    pension, because Seccl's product transfer takes them too. A direct SEI pension cannot take
    cash — the custodian takes the holdings in-specie. A Morgan Lloyd SIPP can: the ceding
    scheme may sell, and the anticipated receipt is lodged at SEI once the funds have been sent.
    A GIA or an ISA stays on its custodian. An in-specie raise does not list holdings, and
    the transfer request does not send them. The custodian reports the lines on the transfer.
    """
    seccl = custodian == "seccl"
    ml_sipp = morgan_lloyd_sipp(
        custodian=custodian,
        scheme_administrator=scheme_administrator,
        is_pension=is_pension,
    )
    sei_direct_pension = custodian == "sei" and is_pension and not ml_sipp
    return {
        "ask_extent": not seccl,
        "ask_crystallised_split": ml_sipp,
        "ask_pension_facts": is_pension,
        "ask_pension_type": ml_sipp,
        "default_extent": "full" if seccl else None,
        "allow_cash": not sei_direct_pension,
        "ask_ceding_address": seccl,
        # Direct SEI needs the ceding account type Select expects, and the uploaded form's
        # reference. An ML SIPP does not: Morgan Lloyd asks the ceding scheme.
        "ask_sei_instruction": custodian == "sei" and not ml_sipp,
    }


def instruction_lines(
    *,
    custodian: str | None,
    scheme_administrator: str | None,
    is_pension: bool,
    basis: str,
    external_account_type: str | None,
    assets: list[dict[str, Any]] | None,
    documents: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """The SEI lines a raise must store, or empty when this account does not send them.

    Holdings are not one of those lines. A transfer request does not carry them, and the
    raise does not record them. Select's ceding account type is its own vocabulary: a
    wrapper such as ``gia`` is not that code.
    """
    del assets
    spec = form_spec_for(
        custodian=custodian,
        scheme_administrator=scheme_administrator,
        is_pension=is_pension,
    )
    if basis != "in_specie" or custodian != "sei":
        return {}
    stored: dict[str, Any] = {}
    if spec["ask_sei_instruction"]:
        account_type = (external_account_type or "").strip()
        if not account_type:
            raise ValueError(
                "Give the ceding account type SEI expects (data.external_account_type). "
                "A wrapper such as GIA is not that code."
            )
        references = [
            str(document.get("document_reference_id") or "").strip()
            for document in (documents or [])
            if isinstance(document, dict)
        ]
        references = [reference for reference in references if reference]
        if not references:
            raise ValueError(
                "Give the uploaded transfer form reference "
                "(data.documents[].document_reference_id)."
            )
        stored["external_account_type"] = account_type
        stored["documents"] = [{"document_reference_id": reference} for reference in references]
    return stored


def split_for_crystallisation(
    crystallisation: str,
    total: Decimal,
    crystallised_amount: Decimal | None,
) -> tuple[Decimal, Decimal]:
    """Crystallised and uncrystallised amounts from the status plus, when needed, the amount.

    Uncrystallised (accumulation) is all uncrystallised. Crystallised and partial ask for the
    crystallised slice; the remainder is uncrystallised so the total cannot disagree.
    """
    if crystallisation not in CRYSTALLISATIONS:
        raise ValueError(f"Unknown crystallisation {crystallisation!r}.")
    if total <= 0:
        raise ValueError("Enter a transfer value greater than zero.")
    if crystallisation == "uncrystallised":
        return Decimal("0"), total
    if crystallisation == "crystallised":
        if crystallised_amount is None or crystallised_amount == total:
            return total, Decimal("0")
        if crystallised_amount == 0:
            raise ValueError("A crystallised transfer has a crystallised amount.")
        raise ValueError("A crystallised transfer's crystallised amount is the transfer value.")
    if crystallised_amount is None:
        raise ValueError("Enter the crystallised amount.")
    if crystallised_amount < 0:
        raise ValueError("The crystallised amount cannot be negative.")
    if crystallised_amount > total:
        raise ValueError("The crystallised amount cannot exceed the transfer value.")
    if crystallised_amount == 0 or crystallised_amount == total:
        raise ValueError("A partial crystallisation is some, but not all, of the transfer value.")
    return crystallised_amount, total - crystallised_amount


def _fold_choice(value: str | None) -> str:
    """Whitespace-and-hyphen fold, for a lookup against ``TRANSFER_BASES`` / ``TRANSFER_EXTENTS``.

    ``In Specie``, ``in-specie`` and ``in_specie`` are one answer. An empty string is "not said".
    """
    if value is None:
        return ""
    text_value = str(value).strip()
    if not text_value:
        return ""
    return re.sub(r"[\s-]+", "_", text_value.lower())


#: Graphene's own saga, in order. ``funds_sent`` is new in 0056 and exists because Morgan Lloyd
#: distinguishes it: "Funds received" (the ceding scheme has paid ML) and "Funds sent" (ML has
#: passed it to the custodian) are different weeks of chasing, and collapsing them made a
#: transfer look stalled while the money was already in motion.
STATUSES = (
    "requested",
    "submitted",
    "in_progress",
    "assets_received",
    "funds_sent",
    "completed",
)
ALL_STATUSES = frozenset({*STATUSES, "cancelled", "manual_intervention"})

#: The portal's own words for each status — kept in step with `TRANSFER_STATUS_LABEL` in
#: `pkgs/ui/src/lib/components/book/transfers/api.ts` by hand, the same way `STATUSES` above is
#: kept in step with `ApiTransferStatus`. Used to sort the listing on the word the Status badge
#: shows rather than the raw stored key — see `routers/contributions.py::_TRANSFER_SORTS`.
STATUS_LABEL: dict[str, str] = {
    "requested": "Raised",
    "submitted": "Submitted",
    "in_progress": "At ceding scheme",
    "assets_received": "At administrator",
    "funds_sent": "Completed",
    "completed": "Complete",
    "cancelled": "Cancelled",
    "manual_intervention": "Needs attention",
}

# ---- the Morgan Lloyd status mirror --------------------------------------------------------
#
# `morgan_lloyd.enums.TransferStatus` — the vendor's `transferstatus` / `cam_transferstatus`.
# Six values, documented, and NOT the nine-stage saga the workbench prototype renders: that was
# written against `0003_pensions_ledger.sql`, a schema superseded by 0051 that never shipped.
# Inlined for the same reason the contribution codes are: this module stays importable without
# the vendor package, and a test pins the pair.

ML_AWAITING_FORMS = 0
ML_TO_REQUEST_TRANSFER = 1
ML_AWAITING_CEDING_SCHEME = 2
ML_FUNDS_RECEIVED = 3
ML_FUNDS_SENT = 4
ML_NO_LONGER_TRANSFERRING = 5

ML_STATUS_LABELS: dict[int, str] = {
    ML_AWAITING_FORMS: "Awaiting forms",
    ML_TO_REQUEST_TRANSFER: "ML to request transfer",
    ML_AWAITING_CEDING_SCHEME: "Awaiting action from ceding scheme",
    ML_FUNDS_RECEIVED: "Funds received",
    ML_FUNDS_SENT: "Funds sent",
    ML_NO_LONGER_TRANSFERRING: "No longer transferring",
}

#: Morgan Lloyd's progress -> Graphene's saga stage.
#:
#: ``completed`` is deliberately absent: it is not something Morgan Lloyd can tell us. ML's last
#: forward state is "Funds sent" — the money has left them for the custodian — and a transfer is
#: complete only once that money is matched against the expectation at SEI or Seccl, which is
#: our own reconciliation and nobody else's fact.
ML_STATUS_TO_STAGE: dict[int, str] = {
    ML_AWAITING_FORMS: "submitted",
    ML_TO_REQUEST_TRANSFER: "submitted",
    ML_AWAITING_CEDING_SCHEME: "in_progress",
    ML_FUNDS_RECEIVED: "assets_received",
    ML_FUNDS_SENT: "funds_sent",
    ML_NO_LONGER_TRANSFERRING: "cancelled",
}

# ---- Seccl and SEI, into the same saga ----------------------------------------------------
#
# `completed` is absent from both, for the same reason it is absent from Morgan Lloyd: a vendor
# saying the movement is booked is not our reconciliation. Seccl `Completed` and SEI code 7
# (`COMPLETE`) land on `funds_sent` — the custodian has the assets — and the matcher is what
# finishes the transfer.
#
# Folded to lower case, one space. Seccl writes "With Previous Provider" and "With previous
# provider" for the same state.

SECCL_STATUS_TO_STAGE: dict[str, str] = {
    "with previous provider": "submitted",
    "pending": "submitted",
    "placed": "submitted",
    "onboarding assets": "in_progress",
    "awaiting documentation": "in_progress",
    "reviewing documentation": "in_progress",
    "verifying documentation": "in_progress",
    "awaiting sell down": "in_progress",
    # A cancellation request is not the transfer moving on to the ceding scheme, and it is
    # not cancelled until a later read says Rejected, Deleted or Cancelled. Leaving it on
    # submitted keeps the saga where the request was accepted.
    "pending cancellation": "submitted",
    # Seccl is waiting for the crystallisation split. That is ours to supply, so it is
    # Needs attention rather than "with the ceding scheme".
    "awaiting crystallisation information": "manual_intervention",
    "completed": "funds_sent",
    "rejected": "cancelled",
    "deleted": "cancelled",
    "cancelled": "cancelled",
}

#: Seccl will accept a cancellation request only while the transfer is still with the ceding
#: scheme's paperwork. `requestCancellation` is a request, so we do not write `cancelled` locally
#: until a later read says Rejected, Deleted or Cancelled.
SECCL_CANCELLABLE: frozenset[str] = frozenset(
    {
        "with previous provider",
        "awaiting documentation",
        "reviewing documentation",
        "verifying documentation",
    }
)

#: SEI `transferStatusCode`. The words are what the pending-status service returns beside the code.
SEI_STATUS_LABELS: dict[str, str] = {
    "1": "ACTION REQUIRED",
    "2": "IN PROCESS - SUBMITTED TO COUNTERPARTY",
    "3": "IN PROCESS - RECEIVED AT COUNTERPARTY",
    "4": "SETTLED - AWAITING COST BASIS",
    "5": "COST BASIS NOT RECEIVED",
    "6": "SETTLED",
    "7": "COMPLETE",
    "8": "DUPLICATE RECORD",
    "9": "CANCELLED",
    "10": "VALUATION REQUESTED",
    "18": "DELETE",
}

SEI_CODE_TO_STAGE: dict[str, str] = {
    "1": "manual_intervention",
    "2": "submitted",
    "3": "in_progress",
    "4": "funds_sent",
    "5": "manual_intervention",
    "6": "funds_sent",
    "7": "funds_sent",
    "8": "manual_intervention",
    "9": "cancelled",
    "10": "in_progress",
    "18": "cancelled",
}

#: The stage a status belongs to. One-to-one with the saga. Mirrored by
#: `pkgs/ui/.../book/transfers/stages.ts::STAGES`.
#: The prototype `status.ts` beside it is a different vocabulary and is not this list.
STAGES: dict[str, tuple[str, ...]] = {
    "requested": ("requested",),
    "submitted": ("submitted",),
    "in_progress": ("in_progress",),
    "assets_received": ("assets_received",),
    "funds_sent": ("funds_sent",),
    "manual_intervention": ("manual_intervention",),
    "completed": ("completed",),
    "cancelled": ("cancelled",),
}

#: On the KPI strip. The reconciled `completed` stage and cancelled are filter answers — counting
#: them makes a tidy book look busy.
METRIC_STAGES: tuple[str, ...] = (
    "requested",
    "submitted",
    "in_progress",
    "assets_received",
    "funds_sent",
    "manual_intervention",
)

_STAGE_OF: dict[str, str] = {
    status: stage for stage, members in STAGES.items() for status in members
}

_SEI_LABEL_TO_CODE: dict[str, str] = {
    _fold_choice(label).replace("_", " "): code for code, label in SEI_STATUS_LABELS.items()
}


def fold_provider_status(value: str | None) -> str:
    """Lower case, one space. ``In Specie`` and ``in-specie`` are not this function's job."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def seccl_stage(value: str | None) -> str | None:
    """Seccl ``transferStatus`` → Graphene stage, or None when the words are not in the enum."""
    return SECCL_STATUS_TO_STAGE.get(fold_provider_status(value))


_TXN_COMPLETED = frozenset({"completed", "complete", "settled"})
_TXN_CANCELLED = frozenset({"deleted", "cancelled", "canceled", "rejected"})


def synced_stage(
    source_system: str | None,
    native: str | None,
    transaction_status: str | None = None,
) -> str:
    """A feed row's consolidated stage.

    The provider's transfer status when the feed published one. A stock migration has no
    transfer status — Seccl records it as a Stock Transfer, not a product-transfer workflow —
    so the transaction's own status is the fact. A completed movement is ``funds_sent`` (the
    custodian has the assets; the workbench labels that Completed). An open Seccl line was
    submitted and has not finished.

    SEI's ledger has no lifecycle column. A row in the transaction file or the transaction
    API has already posted — the transactions listing shows that blank as settled — so a
    blank status is ``funds_sent``, not ``submitted``. An explicit pending status still
    means the movement has not finished. ``completed`` stays our own reconciliation.
    """
    source = (source_system or "").lower()
    mapped: str | None = None
    if native:
        if source == "seccl":
            mapped = seccl_stage(native)
        elif source == "sei":
            mapped = sei_stage(native)
        if mapped:
            return mapped
    txn = fold_provider_status(transaction_status)
    if txn in _TXN_COMPLETED:
        return "funds_sent"
    if txn in _TXN_CANCELLED:
        return "cancelled"
    if source == "sei" and not txn:
        return "funds_sent"
    return "submitted"


def seccl_cancellable(value: str | None) -> bool:
    return fold_provider_status(value) in SECCL_CANCELLABLE


def feed_cancel_allowed(identity: dict[str, Any]) -> bool:
    """A feed row Seccl will take a cancellation request for.

    Only a product transfer, and only in the statuses ``requestCancellation`` accepts.
    A stock migration has no transfer status, and SEI has no cancel call.
    """
    if str(identity.get("source_system") or "").lower() != "seccl":
        return False
    if identity.get("source_type") != "Product Transfer":
        return False
    return seccl_cancellable(identity.get("transfer_status"))


def sei_stage(value: str | None) -> str | None:
    """An SEI pending-transfer code or its description → Graphene stage.

    Code 7 (`COMPLETE`) is ``funds_sent``, not ``completed``. SEI has finished its movement;
    we have not reconciled it.
    """
    if value is None:
        return None
    text = str(value).strip()
    if text in SEI_CODE_TO_STAGE:
        return SEI_CODE_TO_STAGE[text]
    # Hyphens and spaces are not part of the code. "IN PROCESS - SUBMITTED…" and the label
    # we store are one status; `_fold_choice` is what makes them one key.
    folded = _fold_choice(text).replace("_", " ")
    code = _SEI_LABEL_TO_CODE.get(folded)
    if code:
        return SEI_CODE_TO_STAGE[code]
    return None


def sei_label(value: str | None) -> str | None:
    """The description beside a code, or the words we were given when they are already words."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return SEI_STATUS_LABELS.get(text, text)


def morgan_lloyd_sipp(
    *, custodian: str | None, scheme_administrator: str | None, is_pension: bool
) -> bool:
    """Morgan Lloyd administers an SEI SIPP, and only that.

    A Seccl SIPP is custodied and administered by Seccl. A product-platform row that still
    names Morgan Lloyd on a Seccl account, or on a GIA, does not make it a Morgan Lloyd scheme.
    """
    from app import providers

    return (
        custodian == "sei"
        and bool(is_pension)
        and (scheme_administrator or "") in providers.administrators()
    )


def displayed_scheme_administrator(
    *, custodian: str | None, scheme_administrator: str | None, is_pension: bool
) -> str | None:
    """Who the chain of custody should name as scheme administrator.

    An SEI SIPP keeps its Morgan Lloyd mapping. A Seccl SIPP is Seccl. Any other Morgan Lloyd
    tag is dropped, so a GIA does not grow a scheme administrator it does not have.
    """
    if morgan_lloyd_sipp(
        custodian=custodian,
        scheme_administrator=scheme_administrator,
        is_pension=is_pension,
    ):
        return scheme_administrator
    if custodian == "seccl" and is_pension:
        return "seccl"
    from app import providers

    if (scheme_administrator or "") in providers.administrators():
        return None
    return scheme_administrator


def stamp_route(record: dict[str, Any]) -> None:
    """Set ``route`` and the scheme administrator the panel is allowed to show."""
    pension = bool(record.get("is_pension"))
    tagged = record.get("scheme_administrator")
    custodian = record.get("custodian")
    record["route"] = route_of(custodian=custodian, scheme_administrator=tagged, is_pension=pension)
    record["scheme_administrator"] = displayed_scheme_administrator(
        custodian=custodian, scheme_administrator=tagged, is_pension=pension
    )


def route_of(
    *,
    custodian: str | None,
    scheme_administrator: str | None,
    is_pension: bool = False,
) -> str:
    """Which instruction path this receiving account uses.

    ``ml_sei`` is an SEI SIPP administered by Morgan Lloyd. Every Seccl account, including a
    Seccl SIPP, is ``seccl``. Every other SEI account is ``sei``.
    """
    if morgan_lloyd_sipp(
        custodian=custodian,
        scheme_administrator=scheme_administrator,
        is_pension=is_pension,
    ):
        return "ml_sei"
    if custodian == "seccl":
        return "seccl"
    if custodian == "sei":
        return "sei"
    return "unknown"


def stage_of(status: str | None) -> str | None:
    """The stage a status belongs to, or None for a status with no stage."""
    return _STAGE_OF.get(status or "")


def next_status(current: str, target: str | None) -> str:
    """Where a provider read may move the saga.

    Monotonic along ``STATUSES``. ``cancelled`` and ``manual_intervention`` apply from anywhere
    except a transfer that is already ``completed`` — a stale poll must not reopen a reconciled
    row. A target of ``completed`` is ignored: no vendor is allowed to write that.
    """
    if target is None or target == "completed":
        return current
    if current == "completed" and target != "cancelled":
        return current
    if target in {"cancelled", "manual_intervention"}:
        return target
    if stage_index(target) > stage_index(current):
        return target
    return current


def display_amount(row: dict[str, Any]) -> float:
    """What the strip and the queue show: what landed, else what was expected, else nothing."""
    value = sortable_amount(row)
    return 0.0 if value is None else value


def sortable_amount(row: dict[str, Any]) -> float | None:
    """The Value column, for ordering. None when the transfer has not been advised a figure.

    The shaped row has no ``amount`` key. Reading that sorted every transfer as blank, so
    the column looked unsorted.
    """
    for key in ("transaction_amount", "total_value", "expectation_amount"):
        value = row.get(key)
        if value is not None:
            return float(value)
    return None


def stage_totals(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    """Amount and count per stage, over the whole book — before filters and before the page cut."""
    totals: dict[str, dict[str, float | int]] = {
        stage: {"amount": 0.0, "count": 0} for stage in STAGES
    }
    for row in rows:
        stage = stage_of(row.get("status"))
        if stage is None:
            continue
        bucket = totals[stage]
        bucket["amount"] = float(bucket["amount"]) + display_amount(row)
        bucket["count"] = int(bucket["count"]) + 1
    return totals


def driving_fact(row: dict[str, Any]) -> dict[str, str | None]:
    """The provider words the panel quotes, and the stage they map to.

    Morgan Lloyd drives the saga when it administers the account. Otherwise the custodian's
    own status does. A feed row still carries the custodian's words; it does not have a saga
    to advance, so the mapped stage is informational.
    """
    route = row.get("route") or route_of(
        custodian=row.get("custodian"),
        scheme_administrator=row.get("scheme_administrator"),
        is_pension=bool(row.get("is_pension")),
    )
    ml_status = row.get("ml_status")
    if route == "ml_sei" and ml_status is not None:
        try:
            code = int(ml_status)
        except (TypeError, ValueError):
            code = None
        if code is not None and code in ML_STATUS_LABELS:
            return {
                "party": "Morgan Lloyd",
                "native": ML_STATUS_LABELS[code],
                "stage": ML_STATUS_TO_STAGE.get(code),
            }
    native = row.get("ceding_status")
    if route == "seccl" and native:
        return {"party": "Seccl", "native": str(native), "stage": seccl_stage(str(native))}
    if route == "sei" and native:
        return {"party": "SEI", "native": sei_label(str(native)), "stage": sei_stage(str(native))}
    if native:
        return {"party": "Custodian", "native": str(native), "stage": None}
    return {"party": None, "native": None, "stage": None}


def stage_index(status: str) -> int:
    """Position in ``STATUSES``, or -1 off-saga. Keeps a provider refresh monotonic."""
    try:
        return STATUSES.index(status)
    except ValueError:
        return -1


_SELECT = """
SELECT t.id, t.uid, t.account_id, t.contact_id, t.expectation_id, t.transaction_id,
       t.ceding_provider, t.policy_number, t.transfer_type, t.basis, t.extent,
       t.crystallisation, t.pension_type, t.safeguarded_benefits,
       t.crystallised_value::float8 AS crystallised_value,
       t.uncrystallised_value::float8 AS uncrystallised_value,
       t.total_value::float8 AS total_value, t.currency, t.status,
       NULLIF(t.data ->> 'provider_error', '') AS provider_error,
       COALESCE((t.data ->> 'seccl_placed') = 'true', false) AS seccl_placed,
       COALESCE((t.data ->> 'sei_accepted') = 'true', false) AS sei_accepted,
       t.ml_transfer_id, t.ml_recorded_at, t.expected_date, t.completed_at, t.note,
       t.ml_status, t.ml_status_at, t.ceding_status,
       t.seccl_transfer_id, t.sei_activity_id,
       t.created_at, t.updated_at,
       a.name AS account_name, a.uid AS account_uid,
       COALESCE(a.custodian::text, (
         SELECT l.provider::text FROM platform.links l
          WHERE l.tenant_id = a.tenant_id
            AND l.entity_type = 'account' AND l.entity_id = a.id
            AND l.link_type = 'primary' AND l.provider = ANY(:custodians)
          ORDER BY l.provider
          LIMIT 1)) AS custodian,
       p.product_type, COALESCE(p.is_pension, false) AS is_pension,
       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), c.name)
         AS client_name,
       -- The client's uid travels with the name because `contact_id` cannot take you to the
       -- person: the client record resolves by uid (`/clients/{uid}`), and the serial only says
       -- how many contacts preceded this one. Without this the workbench can show who the
       -- transfer is for but not link to them, which is the same dead end `account_uid` was
       -- already added to avoid.
       c.uid AS contact_uid,
       -- The adviser role is unique per contact (0245), so no tie-break ordering is needed.
       (SELECT u.name FROM crm.client_advisers ca
          JOIN auth.users u ON u.id = ca.adviser_id
         WHERE ca.contact_id = t.contact_id AND ca.role = 'adviser') AS adviser,
       e.uid AS expectation_uid, e.status AS expectation_status,
       e.due_date AS expectation_due_date, e.amount::float8 AS expectation_amount,
       e.matched_at AS expectation_matched_at,
       e.matched_by IS NOT NULL AS matched_by_hand,
       x.amount::float8 AS transaction_amount, x.transaction_date,
       x.source_system AS transaction_source,
       x.source_transaction_id AS transaction_reference,
       (SELECT pp.provider::text FROM platform.product_platforms pp
         WHERE pp.product_id = p.id AND pp.tenant_id = t.tenant_id
           AND pp.provider = ANY(:administrators)
         ORDER BY (pp.role = 'scheme_administrator') DESC NULLS LAST
         LIMIT 1) AS scheme_administrator,
       t.data -> 'assets' AS advised_assets,
       COALESCE((
         SELECT jsonb_agg(jsonb_build_object(
                  'name', NULLIF(s.asset_name, ''),
                  'code', NULLIF(s.isin, ''),
                  'quantity', s.quantity::float8,
                  'status', NULLIF(s.status, '')
                ) ORDER BY s.asset_name NULLS LAST, s.isin)
         FROM platform.transactions s
         WHERE x.id IS NOT NULL
           AND s.tenant_id = t.tenant_id
           AND s.account_id = t.account_id
           AND s.type = 'transfer_in'
           AND s.source_type = 'Stock Transfer'
           AND NULLIF(x.data ->> 'link_id', '') IS NOT NULL
           AND s.data ->> 'link_id' = x.data ->> 'link_id'
       ), '[]'::jsonb) AS linked_instruments
FROM platform.transfers t
  LEFT JOIN platform.accounts a ON a.id = t.account_id
  LEFT JOIN platform.products p ON p.id = a.product_id
  LEFT JOIN crm.contacts c ON c.id = t.contact_id AND c.deleted_at IS NULL
  LEFT JOIN platform.transaction_expectations e ON e.id = t.expectation_id
  LEFT JOIN platform.transactions x ON x.id = t.transaction_id
"""


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _address_payload(**fields: str | None) -> dict[str, str]:
    """Ceding-scheme address lines, verbatim apart from surrounding whitespace.

    An empty string is not an address. Stored on ``platform.transfers.data`` because Morgan
    Lloyd and Seccl both read it and neither has a column of its own.
    """
    payload: dict[str, str] = {}
    for key, value in fields.items():
        if value is None:
            continue
        text_value = str(value).strip()
        if text_value:
            payload[key] = text_value
    return payload


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    """One transfer, with the provider label and the derived reconciliation attached."""
    from app import matching

    record = dict(row)
    ml_status = record.get("ml_status")
    record["ml_status_label"] = (
        ML_STATUS_LABELS.get(int(ml_status)) if ml_status is not None else None
    )
    stamp_route(record)
    fact = driving_fact(record)
    record["provider_party"] = fact["party"]
    record["provider_native"] = fact["native"]
    record["provider_stage"] = fact["stage"]
    linked = record.pop("linked_instruments", None)
    advised = record.pop("advised_assets", None)
    record["instruments"] = (
        matching.normalise_instruments(linked, advised)
        if record.get("basis") == "in_specie"
        else []
    )
    # The three-way compare reads `amount` as the booked leg; a transfer books its total.
    record["reconciliation"] = matching.reconcile({**record, "amount": record.get("total_value")})
    return record


async def list_transfers(
    session: AsyncSession, *, tenant_id: int, account_id: int | None = None
) -> list[dict[str, Any]]:
    clause = " AND t.account_id = :a" if account_id is not None else ""
    rows = (
        await session.execute(
            text(f"{_SELECT} WHERE t.tenant_id = :t{clause} ORDER BY t.created_at DESC"),
            {"t": tenant_id, "a": account_id, **_provider_params()},
        )
    ).mappings()
    return [_shape(dict(r)) for r in rows]


async def list_providers(session: AsyncSession, *, tenant_id: int) -> list[dict[str, Any]]:
    """The firm's ceding-provider register — the new-transfer combobox's options.

    Disabled providers are excluded rather than shown greyed: this list exists to be picked
    from, and a firm that has switched a provider off has said it will not transfer from them.
    The counterparty ids come along because a transfer instruction cannot name the ceding
    scheme without one, and the drawer shows which rails a provider is reachable on.
    """
    rows = (
        await session.execute(
            text(
                "SELECT id, name, masterparty_id, origo_id "
                "FROM platform.transfer_providers "
                "WHERE tenant_id = :t AND enabled "
                "ORDER BY name"
            ),
            {"t": tenant_id},
        )
    ).mappings()
    return [dict(row) for row in rows]


async def get_transfer(session: AsyncSession, *, tenant_id: int, uid: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(f"{_SELECT} WHERE t.tenant_id = :t AND t.uid = :u"),
                {"t": tenant_id, "u": uid, **_provider_params()},
            )
        )
        .mappings()
        .one_or_none()
    )
    return _shape(dict(row)) if row else None


async def create_transfer(
    session: AsyncSession,
    *,
    tenant_id: int,
    created_by: str | None,
    account_id: int,
    ceding_provider: str,
    #: None derives it from the account's wrapper — see TRANSFER_TYPE_FOR_PRODUCT. The drawer
    #: does not ask, because the account already answers it.
    transfer_type: str | None = None,
    #: Cash or in-specie. Always required; never defaulted.
    basis: str | None = None,
    #: Full or partial. Required unless the account is Seccl-only, in which case ``full`` is stored.
    extent: str | None = None,
    policy_number: str | None = None,
    crystallisation: str | None = None,
    pension_type: int | None = None,
    safeguarded_benefits: bool | None = None,
    crystallised_value: float | Decimal | None = None,
    uncrystallised_value: float | Decimal | None = None,
    total_value: float | Decimal | None = None,
    expected_date: date | None = None,
    note: str | None = None,
    provider_address_line1: str | None = None,
    provider_address_line2: str | None = None,
    provider_city: str | None = None,
    provider_postcode: str | None = None,
    provider_country: str | None = None,
    fca_reference: str | None = None,
    external_account_type: str | None = None,
    assets: list[dict[str, Any]] | None = None,
    documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Raise a transfer in and the expectation for its money.

    Raises ``ValueError`` for a bad request (→ 400) and ``LookupError`` if the account is not
    the caller's firm's (→ 404).
    """
    from app import providers

    if transfer_type is not None and transfer_type not in TRANSFER_TYPES:
        raise ValueError(f"Unknown transfer type {transfer_type!r}.")
    if not (ceding_provider or "").strip():
        raise ValueError("Name the ceding provider.")

    account = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.contact_id, a.currency, "
                    "       COALESCE(a.custodian::text, ("
                    "         SELECT l.provider::text FROM platform.links l "
                    "          WHERE l.tenant_id = a.tenant_id "
                    "            AND l.entity_type = 'account' AND l.entity_id = a.id "
                    "            AND l.link_type = 'primary' AND l.provider = ANY(:custodians) "
                    "          ORDER BY l.provider LIMIT 1)) AS custodian, "
                    "       p.product_type, COALESCE(p.is_pension, false) AS is_pension, "
                    "       (SELECT pp.provider::text FROM platform.product_platforms pp "
                    "         WHERE pp.product_id = p.id AND pp.tenant_id = a.tenant_id "
                    "           AND pp.provider = ANY(:administrators) "
                    "         ORDER BY (pp.role = 'scheme_administrator') DESC NULLS LAST "
                    "         LIMIT 1) AS scheme_administrator "
                    "FROM platform.accounts a "
                    "  LEFT JOIN platform.products p ON p.id = a.product_id "
                    "WHERE a.id = :a AND a.tenant_id = :t"
                ),
                {
                    "a": account_id,
                    "t": tenant_id,
                    "administrators": list(providers.administrators()),
                    "custodians": list(providers.custodians()),
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if account is None:
        raise LookupError("Account not found")

    spec = form_spec_for(
        custodian=account["custodian"],
        scheme_administrator=account["scheme_administrator"],
        is_pension=bool(account["is_pension"]),
    )
    folded_basis = _fold_choice(basis)
    if not folded_basis:
        raise ValueError(
            "Say whether this is a cash or in-specie transfer — cash instructs the ceding "
            "scheme to sell."
        )
    if folded_basis not in TRANSFER_BASES:
        raise ValueError(f"Unknown transfer basis {basis!r}.")
    if folded_basis == "cash" and not spec["allow_cash"]:
        raise ValueError(
            "A cash transfer is not available on this SEI pension — the ceding scheme would "
            "liquidate, and this custodian takes the holdings in-specie."
        )

    folded_extent = _fold_choice(extent)
    if spec["default_extent"] and not folded_extent:
        folded_extent = spec["default_extent"]
    if spec["ask_extent"]:
        if not folded_extent:
            raise ValueError("Say whether this is a full or partial transfer.")
        if folded_extent not in TRANSFER_EXTENTS:
            raise ValueError(f"Unknown transfer extent {extent!r}.")
    else:
        if folded_extent and folded_extent != "full":
            raise ValueError("Seccl transfers are full only.")
        folded_extent = "full"

    folded_crystallisation = (crystallisation or "").strip().lower() or None
    if folded_crystallisation and folded_crystallisation not in CRYSTALLISATIONS:
        raise ValueError(f"Unknown crystallisation {crystallisation!r}.")

    parsed_pension_type: int | None = None
    if pension_type is not None:
        try:
            parsed_pension_type = int(pension_type)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unknown pension type {pension_type!r}.") from exc
        if parsed_pension_type not in PENSION_TYPES:
            raise ValueError(f"Unknown pension type {pension_type!r}.")

    is_pension = bool(account["is_pension"])
    if spec["ask_pension_type"] and parsed_pension_type is None:
        raise ValueError("Say what type of pension is being transferred.")
    if is_pension:
        if folded_crystallisation is None and (
            crystallised_value is None or uncrystallised_value is None
        ):
            raise ValueError(
                "Say whether the transferring pension is crystallised, partially crystallised, "
                "or uncrystallised."
            )
        if safeguarded_benefits is None:
            safeguarded_benefits = False

    if folded_crystallisation:
        if total_value is None:
            raise ValueError("Enter a transfer value greater than zero.")
        crystallised_value, uncrystallised_value = split_for_crystallisation(
            folded_crystallisation,
            _dec(total_value),
            _dec(crystallised_value) if crystallised_value is not None else None,
        )
    elif spec["ask_crystallised_split"]:
        if crystallised_value is None:
            raise ValueError("Enter the crystallised value (0 if none is crystallised).")
        if uncrystallised_value is None:
            raise ValueError("Enter the uncrystallised value (0 if none is uncrystallised).")

    # If only the split is given, the total is its sum — Morgan Lloyd wants all three and a
    # derived total is better than a blank one an adviser has to compute by hand.
    parts = [v for v in (crystallised_value, uncrystallised_value) if v is not None]
    total = total_value if total_value is not None else (sum(_dec(v) for v in parts) or None)
    if total is not None and _dec(total) <= 0:
        raise ValueError("Enter a transfer value greater than zero.")

    if (
        folded_crystallisation is None
        and crystallised_value is not None
        and uncrystallised_value is not None
    ):
        cry_d, uncry_d = _dec(crystallised_value), _dec(uncrystallised_value)
        if cry_d == 0:
            folded_crystallisation = "uncrystallised"
        elif uncry_d == 0:
            folded_crystallisation = "crystallised"
        elif cry_d > 0:
            folded_crystallisation = "partial"

    address = _address_payload(
        provider_address_line1=provider_address_line1,
        provider_address_line2=provider_address_line2,
        provider_city=provider_city,
        provider_postcode=provider_postcode,
        provider_country=provider_country,
        fca_reference=fca_reference,
    )
    # Before the insert: a missing SEI instruction line must not leave a row the provider
    # will refuse. Holdings are not one of those lines.
    stored = {
        **address,
        **instruction_lines(
            custodian=account["custodian"],
            scheme_administrator=account["scheme_administrator"],
            is_pension=bool(account["is_pension"]),
            basis=folded_basis,
            external_account_type=external_account_type,
            assets=assets,
            documents=documents,
        ),
    }
    if spec["ask_ceding_address"] and (
        not address.get("provider_address_line1")
        or not address.get("provider_postcode")
        or not address.get("provider_country")
    ):
        raise ValueError(
            "Seccl requires the ceding scheme's address — line 1, postcode and country — "
            "before the transfer can be placed."
        )

    currency = account["currency"] or "GBP"
    transfer_type = transfer_type or transfer_type_for(account["product_type"])
    expectation_id: int | None = None
    if total is not None:
        expectation_id = (
            await session.execute(
                text(
                    "INSERT INTO platform.transaction_expectations "
                    "  (uid, tenant_id, account_id, contact_id, kind, direction, amount, "
                    "   currency, due_date, status, created_by) "
                    "VALUES (:uid, :t, :a, :c, 'transfer_in', 'in', :amt, :cur, :due, "
                    "        'expected', (SELECT id FROM auth.users WHERE workos_user_id = :by)) "
                    "RETURNING id"
                ),
                {
                    "uid": new_id(),
                    "t": tenant_id,
                    "a": account_id,
                    "c": account["contact_id"],
                    "amt": _dec(total),
                    "cur": currency,
                    "due": expected_date,
                    "by": created_by,
                },
            )
        ).scalar_one()

    uid = new_id()
    await session.execute(
        text(
            "INSERT INTO platform.transfers "
            "  (uid, tenant_id, account_id, contact_id, expectation_id, ceding_provider, "
            "   policy_number, transfer_type, basis, extent, crystallisation, pension_type, "
            "   safeguarded_benefits, crystallised_value, uncrystallised_value, total_value, "
            "   currency, expected_date, note, created_by) "
            "VALUES (:uid, :t, :a, :c, :exp, :prov, :policy, :type, :basis, :extent, "
            "        :crystallisation, :pension_type, :safeguarded, :cry, :uncry, :total, "
            "        :cur, :due, :note, "
            "        (SELECT id FROM auth.users WHERE workos_user_id = :by))"
        ),
        {
            "uid": uid,
            "t": tenant_id,
            "a": account_id,
            "c": account["contact_id"],
            "exp": expectation_id,
            "prov": ceding_provider.strip(),
            "policy": policy_number,
            "type": transfer_type,
            "basis": folded_basis,
            "extent": folded_extent,
            "crystallisation": folded_crystallisation,
            "pension_type": parsed_pension_type,
            "safeguarded": safeguarded_benefits,
            "cry": _dec(crystallised_value) if crystallised_value is not None else None,
            "uncry": _dec(uncrystallised_value) if uncrystallised_value is not None else None,
            "total": _dec(total) if total is not None else None,
            "cur": currency,
            "due": expected_date,
            "note": note,
            "by": created_by,
        },
    )
    if stored:
        await session.execute(
            text(
                "UPDATE platform.transfers SET data = CAST(:data AS jsonb) "
                "WHERE uid = :u AND tenant_id = :t"
            ),
            {"data": json.dumps(stored), "u": uid, "t": tenant_id},
        )
    record = await get_transfer(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101 - just inserted, same transaction
    await outbound.record_transfer(session, tenant_id=tenant_id, transfer=record)
    return record


def _instruction_failure(exc: BaseException) -> bool:
    """A provider refusal that must not roll the local transfer back."""
    from app.integrations.morgan_lloyd import MorganLloydNotConfigured, MorganLloydUnavailable
    from app.integrations.seccl.connection import SecclNotConfigured
    from app.integrations.seccl.transfers import SecclUnavailable
    from app.integrations.sei.cash_receipts import SeiNotConfigured, SeiUnavailable

    return isinstance(
        exc,
        (
            ValueError,
            TransferConflict,
            SecclNotConfigured,
            SecclUnavailable,
            SeiNotConfigured,
            SeiUnavailable,
            MorganLloydNotConfigured,
            MorganLloydUnavailable,
        ),
    )


async def remember_provider_failure(
    session: AsyncSession, *, tenant_id: int, uid: str, message: str
) -> dict[str, Any]:
    """Keep the transfer at ``requested`` and record why the provider call failed.

    The row is the retry. Raising here would roll the insert back with the request,
    and the adviser would have to type the transfer in again to send it once more.
    """
    detail = " ".join(message.split())[:500]
    await session.execute(
        text(
            "UPDATE platform.transfers SET "
            "  data = jsonb_set(COALESCE(data, '{}'::jsonb), '{provider_error}', "
            "                    to_jsonb(CAST(:msg AS text)), true) "
            "WHERE uid = :u AND tenant_id = :t"
        ),
        {"msg": detail, "u": uid, "t": tenant_id},
    )
    record = await get_transfer(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101
    return record


async def hold_unreadable_seccl(
    session: AsyncSession, *, tenant_id: int, uid: str, message: str
) -> dict[str, Any]:
    """Seccl accepted the instruction and we have no id to address a second one.

    ``requested`` is the retry state. This is not a retry: another place would create
    another product transfer. ``manual_intervention`` is not offered as Place, and
    ``seccl_placed`` refuses the endpoint if something posts it anyway.
    """
    detail = " ".join(message.split())[:500]
    await session.execute(
        text(
            "UPDATE platform.transfers SET status = 'manual_intervention', "
            "  data = jsonb_set(jsonb_set(COALESCE(data, '{}'::jsonb), "
            "    '{provider_error}', to_jsonb(CAST(:msg AS text)), true), "
            "    '{seccl_placed}', 'true'::jsonb, true) "
            "WHERE uid = :u AND tenant_id = :t"
        ),
        {"msg": detail, "u": uid, "t": tenant_id},
    )
    record = await get_transfer(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101
    return record


async def hold_unreadable_sei(
    session: AsyncSession, *, tenant_id: int, uid: str, freeze_status: bool = False
) -> dict[str, Any]:
    """SEI accepted the instruction and the body had no activity id.

    Polling without that id would attach some other pending row, and submitting again would
    send a second instruction. ``sei_accepted`` refuses both. A Morgan Lloyd transfer keeps
    its saga status: SEI does not own that walk.
    """
    detail = (
        "SEI accepted this instruction but did not return an activity id, "
        "so it cannot be refreshed or sent again."
    )
    if freeze_status:
        statement = (
            "UPDATE platform.transfers SET "
            "  data = jsonb_set(jsonb_set(COALESCE(data, '{}'::jsonb), "
            "    '{provider_error}', to_jsonb(CAST(:msg AS text)), true), "
            "    '{sei_accepted}', 'true'::jsonb, true) "
            "WHERE uid = :u AND tenant_id = :t"
        )
    else:
        statement = (
            "UPDATE platform.transfers SET status = 'manual_intervention', "
            "  data = jsonb_set(jsonb_set(COALESCE(data, '{}'::jsonb), "
            "    '{provider_error}', to_jsonb(CAST(:msg AS text)), true), "
            "    '{sei_accepted}', 'true'::jsonb, true) "
            "WHERE uid = :u AND tenant_id = :t"
        )
    await session.execute(text(statement), {"msg": detail, "u": uid, "t": tenant_id})
    record = await get_transfer(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101
    return record


async def _clear_provider_error(session: AsyncSession, *, tenant_id: int, uid: str) -> None:
    await session.execute(
        text(
            "UPDATE platform.transfers SET data = COALESCE(data, '{}'::jsonb) - 'provider_error' "
            "WHERE uid = :u AND tenant_id = :t"
        ),
        {"u": uid, "t": tenant_id},
    )


def already_instructed(record: dict[str, Any]) -> bool:
    """The provider already has this instruction. A second call would create another."""
    route = record.get("route")
    if route == "seccl":
        return bool(record.get("seccl_transfer_id") or record.get("seccl_placed"))
    if route == "sei":
        return bool(record.get("sei_activity_id") or record.get("sei_accepted"))
    if route == "ml_sei":
        return bool(record.get("ml_transfer_id"))
    return False


async def send_sei(
    session: AsyncSession, *, tenant_id: int, record: dict[str, Any]
) -> dict[str, Any]:
    """The SEI call for a transfer this firm has already raised.

    Cash is an anticipated receipt. In-specie that SEI chases is a transfer-in. In-specie
    that Morgan Lloyd has already asked the ceding scheme for is a free receipt, and that
    leaves Morgan Lloyd's saga where it is. A refusal stays on the row.
    """
    uid = record["uid"]
    route = record.get("route")
    try:
        context = await provider_context(session, tenant_id=tenant_id, transfer=record)
        if route == "ml_sei" and record.get("basis") == "in_specie":
            from app.integrations.sei.transfers import register_free_receipt

            result = await register_free_receipt(context)
            await _clear_provider_error(session, tenant_id=tenant_id, uid=uid)
            activity = result.get("activity_id")
            if not activity:
                return await hold_unreadable_sei(
                    session, tenant_id=tenant_id, uid=uid, freeze_status=True
                )
            return await apply_provider_progress(
                session,
                tenant_id=tenant_id,
                uid=uid,
                target=None,
                sei_activity_id=str(activity),
                advance=False,
            )
        if record.get("basis") == "in_specie":
            from app.integrations.sei.transfers import submit_transfer_in

            result = await submit_transfer_in(context)
            await _clear_provider_error(session, tenant_id=tenant_id, uid=uid)
            activity = result.get("activity_id")
            if not activity:
                return await hold_unreadable_sei(session, tenant_id=tenant_id, uid=uid)
            return await mark_submitted(
                session, tenant_id=tenant_id, uid=uid, sei_activity_id=str(activity)
            )
        await _clear_provider_error(session, tenant_id=tenant_id, uid=uid)
        return await lodge_transfer_cash(session, tenant_id=tenant_id, transfer=context)
    except Exception as exc:
        if not _instruction_failure(exc):
            raise
        return await remember_provider_failure(
            session, tenant_id=tenant_id, uid=uid, message=str(exc)
        )


async def instruct_raised(
    session: AsyncSession, *, tenant_id: int, record: dict[str, Any]
) -> dict[str, Any]:
    """Send a transfer that already exists locally to the provider that owns it.

    Seccl is placed at Seccl. An SEI account is a transfer-in or an anticipated receipt.
    An SEI SIPP is recorded at Morgan Lloyd now; the SEI receipt waits until Morgan Lloyd
    has completed the transfer, and is the detail action rather than this call.

    A failure leaves ``requested`` and ``provider_error``. Progress after a successful
    instruction comes from the provider sync (``apply_synced_progress``), not from polling
    inside this call. A Seccl write that comes back without an id is the exception: that
    row is held at ``manual_intervention`` so the same instruction is not posted twice.
    """
    route = record.get("route")
    uid = record["uid"]
    if already_instructed(record):
        return record
    try:
        if route == "seccl":
            from app.integrations.seccl.transfers import place_transfer

            context = await provider_context(session, tenant_id=tenant_id, transfer=record)
            identifier, observed = await place_transfer(context)
            await _clear_provider_error(session, tenant_id=tenant_id, uid=uid)
            if observed:
                await mark_submitted(
                    session,
                    tenant_id=tenant_id,
                    uid=uid,
                    seccl_transfer_id=identifier,
                    ceding_status=observed,
                )
                return await apply_provider_progress(
                    session,
                    tenant_id=tenant_id,
                    uid=uid,
                    target=seccl_stage(observed),
                    ceding_status=observed,
                    seccl_transfer_id=identifier,
                )
            return await mark_submitted(
                session, tenant_id=tenant_id, uid=uid, seccl_transfer_id=identifier
            )
        if route == "sei":
            return await send_sei(session, tenant_id=tenant_id, record=record)
        if route == "ml_sei":
            from app.integrations.morgan_lloyd import create_transfer_at_ml

            context = await ml_context(session, tenant_id=tenant_id, transfer=record)
            ml_id = await create_transfer_at_ml(context)
            await _clear_provider_error(session, tenant_id=tenant_id, uid=uid)
            return await mark_ml_recorded(
                session, tenant_id=tenant_id, uid=uid, ml_transfer_id=ml_id
            )
    except Exception as exc:
        from app.integrations.seccl.transfers import SecclPlacedWithoutId

        if isinstance(exc, SecclPlacedWithoutId):
            # The group was created. Leaving this `requested` would place a second one.
            return await hold_unreadable_seccl(
                session, tenant_id=tenant_id, uid=uid, message=str(exc)
            )
        if not _instruction_failure(exc):
            raise
        return await remember_provider_failure(
            session, tenant_id=tenant_id, uid=uid, message=str(exc)
        )
    return record


def synced_status(*, route: str | None, current: str, target: str | None) -> str:
    """The status a list refresh may write from a synced transaction.

    Morgan Lloyd owns an ``ml_sei`` saga. The SEI feed can say Cancelled about the same
    activity; writing that would cancel a transfer Morgan Lloyd still has in flight. The
    explicit SEI refresh leaves the saga where it is for the same reason.
    """
    if route == "ml_sei":
        return current
    return next_status(current, target)


async def apply_synced_progress(session: AsyncSession, *, tenant_id: int) -> None:
    """Move raised transfers to the stage their synced transaction now reports.

    Seccl and SEI publish transfer progress on ``platform.transactions``. A transfer we
    raised is a different row; this joins them on the provider id we stored when the
    instruction was accepted, and advances with ``next_status`` so a stale feed cannot
    walk a transfer backwards. An ``ml_sei`` row is left on its saga: Morgan Lloyd owns
    that, and an SEI Cancelled must not cancel it. Morgan Lloyd's conformed transfers
    are not merged into this table — that overwrite would ignore the monotonic rule —
    so an SEI SIPP still takes Morgan Lloyd's status from the refresh until the
    custodian's own transaction arrives.
    """
    from app import providers

    rows = (
        await session.execute(
            text(
                "SELECT tr.uid, tr.status, x.source_system, x.status AS transaction_status, "
                "       NULLIF(x.data ->> 'transfer_status', '') AS native, "
                "       COALESCE(a.custodian::text, ("
                "         SELECT l.provider::text FROM platform.links l "
                "          WHERE l.tenant_id = a.tenant_id "
                "            AND l.entity_type = 'account' AND l.entity_id = a.id "
                "            AND l.link_type = 'primary' AND l.provider = ANY(:custodians) "
                "          ORDER BY l.provider LIMIT 1)) AS custodian, "
                "       COALESCE(p.is_pension, false) AS is_pension, "
                "       (SELECT pp.provider::text FROM platform.product_platforms pp "
                "         WHERE pp.product_id = p.id AND pp.tenant_id = tr.tenant_id "
                "           AND pp.provider = ANY(:administrators) "
                "         ORDER BY (pp.role = 'scheme_administrator') DESC NULLS LAST "
                "         LIMIT 1) AS scheme_administrator "
                "FROM platform.transfers tr "
                "JOIN platform.accounts a ON a.id = tr.account_id AND a.tenant_id = tr.tenant_id "
                "LEFT JOIN platform.products p ON p.id = a.product_id "
                "JOIN platform.transactions x "
                "  ON x.tenant_id = tr.tenant_id AND x.account_id = tr.account_id "
                " AND x.type = 'transfer_in' "
                " AND ("
                "   (tr.seccl_transfer_id IS NOT NULL AND x.source_system = 'seccl' "
                "    AND x.source_transaction_id = tr.seccl_transfer_id) "
                "   OR (tr.sei_activity_id IS NOT NULL AND x.source_system = 'sei' AND ("
                "        x.source_transaction_id = tr.sei_activity_id "
                "     OR x.data ->> 'RELATED_ACTIVITY_ID' = tr.sei_activity_id "
                "     OR x.data ->> 'related_activity_id' = tr.sei_activity_id))"
                " ) "
                "WHERE tr.tenant_id = :t AND tr.status NOT IN ('completed', 'cancelled')"
            ),
            {
                "t": tenant_id,
                "custodians": list(providers.custodians()),
                "administrators": list(providers.administrators()),
            },
        )
    ).mappings()
    for row in rows:
        target = synced_stage(row["source_system"], row["native"], row["transaction_status"])
        route = route_of(
            custodian=row["custodian"],
            scheme_administrator=row["scheme_administrator"],
            is_pension=bool(row["is_pension"]),
        )
        new = synced_status(route=route, current=row["status"], target=target)
        native = row["native"]
        if new == row["status"] and not native:
            continue
        await session.execute(
            text(
                "UPDATE platform.transfers SET status = :s, "
                "  ceding_status = COALESCE(:ceding, ceding_status) "
                "WHERE uid = :u AND tenant_id = :t AND status = :current"
            ),
            {
                "s": new,
                "ceding": native,
                "u": row["uid"],
                "t": tenant_id,
                "current": row["status"],
            },
        )


async def record_ml_status(
    session: AsyncSession,
    *,
    tenant_id: int,
    uid: str,
    ml_status: int,
    ceding_status: str | None = None,
) -> dict[str, Any]:
    """Mirror Morgan Lloyd's transfer progress and advance the saga to match.

    Monotonic for the same reason the contribution mirror is: a nightly read that lands after a
    local advance must not walk the transfer backwards. "No longer transferring" is the
    exception and cancels regardless — the ceding scheme withdrawing is authoritative.

    ``ceding_status`` is the custodian's own words, stored verbatim. Seccl and SEI map those
    words through ``seccl_stage`` / ``sei_stage`` when *their* refresh runs; this function does
    not parse them, because a Morgan Lloyd read must not also move the saga on a custodian
    string that arrived in the same call.

    A standing ``provider_error`` stays on the row. An SEI accept that came back without an
    activity id is still that hold, and clearing it here would hide the reason while
    ``sei_accepted`` still blocks a second receipt. This read succeeded; the portals do not
    treat that leftover string as this refresh failing.
    """
    if ml_status not in ML_STATUS_LABELS:
        raise ValueError(f"Unknown Morgan Lloyd transfer status {ml_status!r}.")
    current = await get_transfer(session, tenant_id=tenant_id, uid=uid)
    if current is None:
        raise LookupError("Transfer not found")

    target = ML_STATUS_TO_STAGE.get(ml_status)
    status = current["status"]
    if target == "cancelled":
        status = "cancelled"
    elif target and stage_index(target) > stage_index(status):
        status = target

    await session.execute(
        text(
            "UPDATE platform.transfers SET ml_status = :ml, ml_status_at = now(), status = :s, "
            "  ceding_status = COALESCE(:ceding, ceding_status) "
            "WHERE uid = :u AND tenant_id = :t"
        ),
        {"ml": ml_status, "s": status, "ceding": ceding_status, "u": uid, "t": tenant_id},
    )
    record = await get_transfer(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101
    return record


async def set_status(
    session: AsyncSession, *, tenant_id: int, uid: str, status: str
) -> dict[str, Any]:
    if status not in ALL_STATUSES:
        raise ValueError(f"Unknown status {status!r}.")
    updated = (
        await session.execute(
            text(
                "UPDATE platform.transfers SET status = :s, "
                "  completed_at = CASE WHEN :s = 'completed' THEN now() ELSE completed_at END "
                "WHERE uid = :u AND tenant_id = :t RETURNING id"
            ),
            {"s": status, "u": uid, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if updated is None:
        raise LookupError("Transfer not found")
    record = await get_transfer(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101
    return record


async def ml_context(
    session: AsyncSession, *, tenant_id: int, transfer: dict[str, Any]
) -> dict[str, Any]:
    """The transfer plus the Morgan Lloyd ids it must be addressed by.

    The sibling of ``app.contributions.ml_context``, and it resolves the same two links — the
    receiving account's SCHEME reference (``cam_createtransfer`` files a transfer against a
    scheme) and the client's Dynamics GUID (``cam_getpensiontransfer`` looks one UP by the
    client, since the vendor gives us a transfer id and then offers no lookup for it).

    THE `primary` LINK, NOT THE `policy` ONE. A transfer belongs to the scheme; only a VALUATION
    is filed against the policy. Reading the wrong one here would send a policy number where a
    scheme reference belongs, and the vendor's answer for that reads as "not found".
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT "
                    "  (SELECT external_id FROM platform.links "
                    "    WHERE provider = 'morgan-lloyd' AND entity_type = 'account' "
                    "      AND entity_id = :a AND link_type = 'primary' AND tenant_id = :t"
                    "  ) AS ml_scheme_ref, "
                    "  (SELECT external_id FROM platform.links "
                    "    WHERE provider = 'morgan-lloyd' AND entity_type = 'contact' "
                    "      AND entity_id = :c AND link_type = 'primary' AND tenant_id = :t"
                    "  ) AS ml_contact_id, "
                    "  (SELECT data FROM platform.transfers WHERE uid = :u AND tenant_id = :t"
                    "  ) AS data"
                ),
                {
                    "a": transfer["account_id"],
                    "c": transfer["contact_id"],
                    "u": transfer["uid"],
                    "t": tenant_id,
                },
            )
        )
        .mappings()
        .one()
    )
    # `data` is read HERE rather than in `_SELECT`: `basis`/`extent`/`pension_type` are
    # columns now and the listing projects them, but the ceding address still lives in jsonb
    # and only the Morgan Lloyd push needs it.
    return {
        **transfer,
        "ml_scheme_ref": row["ml_scheme_ref"],
        "ml_contact_id": row["ml_contact_id"],
        "data": row["data"],
    }


async def mark_ml_recorded(
    session: AsyncSession, *, tenant_id: int, uid: str, ml_transfer_id: str
) -> dict[str, Any]:
    """Record the ``cam_personalpolicyid`` GUID and advance the transfer to ``submitted``.

    ``submitted`` rather than a mirrored progress value: what we know at this instant is that WE
    have instructed Morgan Lloyd, which is our own saga's word for it. Their `transferstatus`
    starts at "Awaiting forms" and is a statement about the ceding scheme, so it is learned by
    ``…/refresh-morgan-lloyd`` and not asserted here — and `record_ml_status` is monotonic, so a
    guess now would have to be walked back and could not be.
    """
    await session.execute(
        text(
            "UPDATE platform.transfers SET ml_transfer_id = :ml, ml_recorded_at = now(), "
            "  status = CASE WHEN status = 'requested' THEN 'submitted' ELSE status END "
            "WHERE uid = :u AND tenant_id = :t"
        ),
        {"ml": ml_transfer_id, "u": uid, "t": tenant_id},
    )
    await _clear_provider_error(session, tenant_id=tenant_id, uid=uid)
    record = await get_transfer(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101 - just updated, same transaction
    await outbound.on_ml_recorded(
        session,
        tenant_id=tenant_id,
        origin="transfer",
        origin_uid=uid,
        ml_id=ml_transfer_id,
    )
    return record


async def apply_provider_progress(
    session: AsyncSession,
    *,
    tenant_id: int,
    uid: str,
    target: str | None,
    ceding_status: str | None = None,
    seccl_transfer_id: str | None = None,
    sei_activity_id: str | None = None,
    advance: bool = True,
) -> dict[str, Any]:
    """Store a provider read and, when ``advance`` is set, move the saga with ``next_status``.

    ``advance=False`` stores a provider id and leaves the saga where it is. A free receipt
    on a Morgan Lloyd transfer uses it: Morgan Lloyd owns that saga until reconciliation.
    """
    current = await get_transfer(session, tenant_id=tenant_id, uid=uid)
    if current is None:
        raise LookupError("Transfer not found")
    status = next_status(current["status"], target) if advance else current["status"]
    await session.execute(
        text(
            "UPDATE platform.transfers SET status = :s, "
            "  ceding_status = COALESCE(:ceding, ceding_status), "
            "  seccl_transfer_id = COALESCE(:seccl, seccl_transfer_id), "
            "  sei_activity_id = COALESCE(:sei, sei_activity_id), "
            "  completed_at = CASE WHEN :s = 'completed' THEN COALESCE(completed_at, now()) "
            "                      ELSE completed_at END "
            "WHERE uid = :u AND tenant_id = :t"
        ),
        {
            "s": status,
            "ceding": ceding_status,
            "seccl": seccl_transfer_id,
            "sei": sei_activity_id,
            "u": uid,
            "t": tenant_id,
        },
    )
    await _clear_provider_error(session, tenant_id=tenant_id, uid=uid)
    record = await get_transfer(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101
    return record


async def mark_submitted(
    session: AsyncSession,
    *,
    tenant_id: int,
    uid: str,
    seccl_transfer_id: str | None = None,
    sei_activity_id: str | None = None,
    ceding_status: str | None = None,
) -> dict[str, Any]:
    """We have instructed the provider. That is ``submitted``, not a guess at their progress."""
    await session.execute(
        text(
            "UPDATE platform.transfers SET "
            "  status = CASE WHEN status = 'requested' THEN 'submitted' ELSE status END, "
            "  seccl_transfer_id = COALESCE(:seccl, seccl_transfer_id), "
            "  sei_activity_id = COALESCE(:sei, sei_activity_id), "
            "  ceding_status = COALESCE(:ceding, ceding_status) "
            "WHERE uid = :u AND tenant_id = :t"
        ),
        {
            "seccl": seccl_transfer_id,
            "sei": sei_activity_id,
            "ceding": ceding_status,
            "u": uid,
            "t": tenant_id,
        },
    )
    await _clear_provider_error(session, tenant_id=tenant_id, uid=uid)
    record = await get_transfer(session, tenant_id=tenant_id, uid=uid)
    assert record is not None  # noqa: S101
    return record


def chosen_masterparty(ids: list[str], *, route: str | None) -> str | None:
    """The Seccl counterparty for a ceding-scheme name, or none when the name is ambiguous.

    The same legal name can be two master parties. Sending ``previousProvider`` as whichever
    row ``LIMIT 1`` returned would ask the wrong scheme to move the assets.
    """
    distinct = list(dict.fromkeys(ids))
    if len(distinct) > 1:
        if route == "seccl":
            raise ValueError(
                "More than one ceding scheme is registered under that name. "
                "The instruction was not sent."
            )
        return None
    return distinct[0] if distinct else None


async def provider_context(
    session: AsyncSession, *, tenant_id: int, transfer: dict[str, Any]
) -> dict[str, Any]:
    """The transfer plus the Seccl account id, the SEI links, and the ceding address.

    One read for both adapters. A missing id is left null so the adapter can name it in a 422
    rather than this query inventing one.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT "
                    "  (SELECT external_id FROM platform.links "
                    "    WHERE provider = 'seccl' AND entity_type = 'account' "
                    "      AND entity_id = :a AND link_type = 'primary' AND tenant_id = :t"
                    "  ) AS seccl_account_id, "
                    "  (SELECT external_id FROM platform.links "
                    "    WHERE provider = 'sei' AND entity_type = 'account' "
                    "      AND entity_id = :a AND link_type = 'primary' AND tenant_id = :t"
                    "  ) AS sei_account_number, "
                    "  (SELECT external_id FROM platform.links "
                    "    WHERE provider = 'sei' AND entity_type = 'account' "
                    "      AND entity_id = :a AND link_type = 'portfolio' AND tenant_id = :t"
                    "  ) AS sei_portfolio_id, "
                    "  (SELECT config FROM platform.tenant_platforms "
                    "    WHERE tenant_id = :t AND provider = 'sei') AS sei_config, "
                    "  (SELECT array_agg(DISTINCT masterparty_id) "
                    "     FILTER (WHERE masterparty_id IS NOT NULL) "
                    "     FROM platform.transfer_providers "
                    "    WHERE tenant_id = :t AND enabled "
                    "      AND lower(name) = lower(:name)) AS masterparty_ids, "
                    "  (SELECT data FROM platform.transfers WHERE uid = :u AND tenant_id = :t"
                    "  ) AS data, "
                    "  (SELECT TRIM(CONCAT_WS(' ', first_name, surname)) "
                    "     FROM crm.contacts "
                    "    WHERE id = :c AND tenant_id = :t) AS client_name, "
                    "  (SELECT national_identifier FROM crm.contacts "
                    "    WHERE id = :c AND tenant_id = :t) AS national_identifier, "
                    "  (SELECT national_identifier_type FROM crm.contacts "
                    "    WHERE id = :c AND tenant_id = :t) AS national_identifier_type"
                ),
                {
                    "a": transfer["account_id"],
                    "c": transfer.get("contact_id"),
                    "u": transfer["uid"],
                    "t": tenant_id,
                    "name": transfer.get("ceding_provider") or "",
                },
            )
        )
        .mappings()
        .one()
    )
    return {
        **transfer,
        "seccl_account_id": row["seccl_account_id"],
        "sei_account_number": row["sei_account_number"],
        "sei_portfolio_id": portfolio_entity_id(row["sei_portfolio_id"]) or row["sei_portfolio_id"],
        "sei_config": row["sei_config"],
        "masterparty_id": chosen_masterparty(
            [str(item) for item in (row["masterparty_ids"] or [])],
            route=transfer.get("route"),
        ),
        "data": row["data"] if isinstance(row["data"], dict) else {},
        "client_name": row["client_name"],
        "national_identifier": row["national_identifier"],
        "national_identifier_type": row["national_identifier_type"],
    }


_TERMINAL_EXPECTATIONS = frozenset({"instructed", "matched", "reconciled"})


class TransferConflict(RuntimeError):
    """The record's current state forbids the action — a 409, not a 400."""


def expectation_already_lodged(transfer: dict[str, Any]) -> bool:
    return transfer.get("expectation_status") in _TERMINAL_EXPECTATIONS


async def lodge_transfer_cash(
    session: AsyncSession, *, tenant_id: int, transfer: dict[str, Any]
) -> dict[str, Any]:
    """Tell SEI to expect this transfer's cash, and record the receipt against the expectation.

    Raises ``ValueError`` (422) when the reference data is missing, the SEI configuration and
    availability errors the router already maps, and ``TransferConflict`` when the expectation
    has already been lodged or matched.
    """
    from app.integrations.sei import cash_receipts

    expectation_id = transfer.get("expectation_id")
    if not expectation_id:
        raise ValueError("This transfer has no expectation to lodge.")
    if expectation_already_lodged(transfer):
        raise TransferConflict("This transfer's cash receipt has already been lodged.")

    context = await provider_context(session, tenant_id=tenant_id, transfer=transfer)
    context.update(cash_receipts.resolve_transfer_codes(context.get("sei_config")))
    due = transfer.get("expectation_due_date") or transfer.get("expected_date")
    context.update(
        amount=transfer.get("expectation_amount") or transfer.get("total_value"),
        currency=transfer.get("currency") or "GBP",
        due_date=due,
        reference=transfer["uid"],
    )
    if context.get("amount") is None:
        raise ValueError("This transfer has no value to lodge at SEI.")

    result = await cash_receipts.create_transfer_receipt(context)
    activity = result.get("activity_id")
    await session.execute(
        text(
            "UPDATE platform.transaction_expectations SET status = 'instructed', error = NULL, "
            "  provider = 'sei', provider_reference = COALESCE(:ref, provider_reference), "
            "  response_payload = CAST(:resp AS jsonb), "
            "  instructed_at = now() "
            "WHERE id = :i AND tenant_id = :t"
        ),
        {
            "ref": str(activity) if activity else None,
            "resp": json.dumps(result, default=str),
            "i": expectation_id,
            "t": tenant_id,
        },
    )
    if not activity:
        return await hold_unreadable_sei(
            session,
            tenant_id=tenant_id,
            uid=transfer["uid"],
            freeze_status=transfer.get("route") == "ml_sei",
        )
    # A currency-receipt id. Pending-transfer status does not list it, so it is not polled.
    return await mark_submitted(
        session,
        tenant_id=tenant_id,
        uid=transfer["uid"],
        sei_activity_id=str(activity),
    )


#: An adviser note. Seccl's ``updateNote`` is a chasing event ("Client action complete"), not a
#: standing note, and SEI's transfer calls have no note field. This stays on our row.
NOTE_MAX = 4000


async def detected_identity(
    session: AsyncSession, *, tenant_id: int, uid: str
) -> dict[str, Any] | None:
    """The feed row a ``detected:`` uid names, or None when it is not one of this firm's."""
    raw = uid.removeprefix("detected:") if uid.startswith("detected:") else ""
    if not raw.isdigit():
        return None
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, source_system, source_type, source_transaction_id, "
                    "       NULLIF(data ->> 'transfer_status', '') AS transfer_status "
                    "FROM platform.transactions "
                    "WHERE id = :i AND tenant_id = :t AND type = 'transfer_in'"
                ),
                {"i": int(raw), "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def stamp_feed_transfer_status(
    session: AsyncSession, *, tenant_id: int, transaction_id: int, status: str
) -> None:
    """Write Seccl's transfer status onto the feed row until the next sync replaces ``data``.

    The merge owns ``data``. This is the gap between a cancellation request and that sync,
    so the workbench does not keep offering Cancel on a transfer Seccl has already moved.
    """
    await session.execute(
        text(
            "UPDATE platform.transactions "
            "SET data = jsonb_set(COALESCE(data, '{}'::jsonb), '{transfer_status}', "
            "                     to_jsonb(CAST(:s AS text)), true), "
            "    updated_at = now() "
            "WHERE id = :i AND tenant_id = :t AND type = 'transfer_in'"
        ),
        {"s": status, "i": transaction_id, "t": tenant_id},
    )


async def save_note(
    session: AsyncSession, *, tenant_id: int, uid: str, note: str | None
) -> dict[str, Any]:
    """Store an adviser note. A feed row keeps it on the transaction; a raised transfer on itself.

    Neither Seccl nor SEI has a standing note to write, so this does not call them.
    """
    text_note = (note or "").strip() or None
    if text_note is not None and len(text_note) > NOTE_MAX:
        raise ValueError(f"A note can be at most {NOTE_MAX} characters.")
    if uid.startswith("detected:"):
        raw = uid.removeprefix("detected:")
        if not raw.isdigit():
            raise LookupError("Transfer not found")
        found = (
            await session.execute(
                text(
                    "UPDATE platform.transactions SET adviser_note = :n "
                    "WHERE id = :i AND tenant_id = :t AND type = 'transfer_in' "
                    "RETURNING id"
                ),
                {"n": text_note, "i": int(raw), "t": tenant_id},
            )
        ).first()
        if found is None:
            raise LookupError("Transfer not found")
        return {"uid": uid, "note": text_note}
    found = (
        await session.execute(
            text(
                "UPDATE platform.transfers SET note = :n, updated_at = now() "
                "WHERE uid = :u AND tenant_id = :t RETURNING uid"
            ),
            {"n": text_note, "u": uid, "t": tenant_id},
        )
    ).first()
    if found is None:
        raise LookupError("Transfer not found")
    record = await get_transfer(session, tenant_id=tenant_id, uid=uid)
    if record is None:
        raise LookupError("Transfer not found")
    return record
