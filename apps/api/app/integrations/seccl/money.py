"""Seccl portfolio-transaction group for one outbound origin.

The book row lives in ``app.outbound``. This module is only the vendor call: which
``transactions[]`` members a kind becomes, and sending that group once. A payment
with an adviser fee is two members of one group, so they share Seccl's order.
The fee is allowed on the kinds ``instruction_kinds.FEE_WITH_MOVEMENT`` names —
the same set the instructions check constraint allows — and on no others.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import outbound
from app.instruction_kinds import FEE_WITH_MOVEMENT

logger = logging.getLogger(__name__)

#: Seccl ``transactions[]`` member per origin kind. Combinations from
#: ``pkgs/seccl/src/seccl/endpoints/transactions.py``. A transfer is not here — Morgan Lloyd
#: records the scheme-admin half; a Seccl Product Transfer is a different body and is not built.
_LEG: dict[str, dict[str, Any]] = {
    "expected_in": {
        "transactionType": "Payment",
        "transactionSubType": "Deposit",
        "movementType": "In",
        "method": "Bank Transfer",
    },
    "personal_contribution": {
        "transactionType": "Payment",
        "transactionSubType": "Deposit",
        "movementType": "In",
        "method": "Bank Transfer",
    },
    "third_party_contribution": {
        "transactionType": "Payment",
        "transactionSubType": "Deposit",
        "movementType": "In",
        "method": "Bank Transfer",
    },
    "contribution": {
        "transactionType": "Payment",
        "transactionSubType": "Deposit",
        "movementType": "In",
        "method": "Bank Transfer",
    },
    "employer_contribution": {
        "transactionType": "Payment",
        "transactionSubType": "Employer",
        "movementType": "In",
        "method": "Bank Transfer",
    },
    "model_topup": {
        "transactionType": "Payment",
        "transactionSubType": "Deposit",
        "movementType": "In",
        "method": "Bank Transfer",
        "autoInvest": True,
    },
    "withdrawal": {
        "transactionType": "Payment",
        "transactionSubType": "Withdrawal",
        "movementType": "Out",
        "method": "BACS Credit",
    },
    "model_withdrawal": {
        "transactionType": "Payment",
        "transactionSubType": "Withdrawal",
        "movementType": "Out",
        "method": "BACS Credit",
    },
    "adhoc_fee": {
        "transactionType": "Cash",
        "transactionSubType": "Adviser Adhoc Fee",
        "movementType": "Out",
    },
}


#: Our withdrawal rail → Seccl's ``method`` on an Out payment. The vendored collection
#: observes exactly ``BACS Credit``, ``Bank Transfer``, ``Bulk Bank Transfer``, ``Direct Debit``
#: and ``Transfer`` — no Faster Payment, no CHAPS, no SWIFT — so only BACS has a name Seccl is
#: known to accept. A rail with no entry here is not sent as BACS.
_OUT_METHOD: dict[str, str] = {"bacs": "BACS Credit"}

#: Kinds whose ``method`` is the chosen withdrawal rail rather than the table's default.
_RAIL_KINDS = frozenset({"withdrawal", "model_withdrawal"})


class UnsupportedRail(ValueError):
    """The chosen withdrawal rail has no Seccl method name we can stand behind."""


def seccl_leg(
    *,
    origin: str,
    kind: str,
    amount: float,
    currency: str,
    payment_method: str | None = None,
    auto_disinvest: bool = True,
) -> dict[str, Any] | None:
    """The Seccl ``transactions[]`` member for this origin, or None if Seccl has no call.

    ``payment_method`` is the withdrawal's chosen rail. None keeps the table's default. A rail
    Seccl has no method for raises :class:`UnsupportedRail` rather than quietly sending BACS.
    """
    if origin == "transfer" or kind in {"transfer_in", "not_a_contribution"}:
        return None
    spec = _LEG.get(kind)
    if spec is None and origin == "contribution":
        spec = _LEG.get("contribution")
    if spec is None:
        return None
    leg = {"currency": currency or "GBP", "amount": amount, **spec}
    if kind in _RAIL_KINDS and payment_method:
        method = _OUT_METHOD.get(payment_method)
        if method is None:
            raise UnsupportedRail(
                f"Seccl has no payment method for {payment_method!r}; the vendored collection "
                "names BACS Credit only. Not sent as BACS — the client was quoted this rail."
            )
        leg["method"] = method
    # Auto-disinvest (sell assets proportionally to fund the money out) rides the withdrawal
    # only when the instruction asks for it; without it the movement needs settled cash.
    if kind in _RAIL_KINDS and auto_disinvest:
        leg["autoDisinvest"] = "Proportional"
    return leg


def seccl_legs(
    *,
    origin: str,
    kind: str,
    amount: float,
    currency: str,
    adviser_fee: float = 0.0,
    payment_method: str | None = None,
    auto_disinvest: bool = True,
) -> list[dict[str, Any]]:
    """The Seccl ``transactions[]`` for this origin — payment and fee in one group when both.

    Two members in one ``POST /portfoliotransactiongroup`` share Seccl's order / ``linkId``.
    Sending the fee as a second call would give it a different order number, and would debit
    cash that has not landed yet. ``payment_method`` is the withdrawal's chosen rail.
    """
    primary = seccl_leg(
        origin=origin,
        kind=kind,
        amount=amount,
        currency=currency,
        payment_method=payment_method,
        auto_disinvest=auto_disinvest,
    )
    if primary is None:
        return []
    members = [primary]
    fee = float(adviser_fee or 0)
    if fee > 0 and kind in FEE_WITH_MOVEMENT:
        charge = seccl_leg(origin="instruction", kind="adhoc_fee", amount=fee, currency=currency)
        if charge is not None:
            members.append(charge)
    return members


def _vendor_exchange(sent: Any) -> tuple[Any, Any]:
    """Split a group call into the body we posted and the record Seccl returned.

    A test double may return the record itself (``{"id": ...}``). The live call returns
    ``{"request", "response"}`` so the action attempt can show the call, not our book report.
    """
    if (
        isinstance(sent, dict)
        and "request" in sent
        and isinstance(sent.get("response"), dict)
        and not any(sent.get(key) for key in ("id", "_id", "activityId", "activity_id"))
    ):
        return sent.get("request"), sent["response"]
    return None, sent


def _vendor_id(answer: Any) -> str | None:
    if not isinstance(answer, dict):
        return None
    for key in ("id", "_id", "activityId", "activity_id"):
        value = answer.get(key)
        if value:
            return str(value)
    return None


async def _seccl_group(
    account_id: str, transactions: list[dict[str, Any]], *, external_reference: str
) -> dict[str, Any]:
    """One Seccl portfolio-transaction group. Patch-point for tests."""
    from seccl.client import SecclClient

    from app.integrations.seccl.connection import configured, seccl_settings, why_not_configured

    if not configured():
        raise LookupError(why_not_configured())
    async with SecclClient(seccl_settings()) as api:
        body = api.transaction_group_body(
            account_id, transactions, externalReference=external_reference
        )
        answer = await api.create_transaction_group(
            account_id, transactions, externalReference=external_reference
        )
        return {"request": body, "response": answer}


async def dispatch_seccl(
    session: AsyncSession,
    *,
    tenant_id: int,
    origin: str,
    origin_uid: str,
    kind: str,
    account_external_id: str,
    amount: float,
    currency: str,
    payment_method: str | None = None,
) -> dict[str, Any]:
    """Instruct Seccl, or report why not. Never raises — a vendor miss must not unwind the book.

    Idempotent: a row that already carries a Seccl link is not sent again (a retried group is a
    second instruction). Unconfigured / no mapping is ``skipped``, not ``failed``.
    A payment and its adviser-fee sibling take the same outcome: one group, one status.
    """
    current = await outbound.get_by_origin(
        session, tenant_id=tenant_id, origin=origin, origin_uid=origin_uid
    )
    if current is None:
        return {"status": "failed", "detail": "No outbound transaction to dispatch."}
    already = await outbound.sent_id(
        session,
        tenant_id=tenant_id,
        transaction_id=int(current["id"]),
        provider="seccl",
        data=current.get("data") if isinstance(current.get("data"), dict) else None,
    )
    if already:
        return {
            "status": "skipped",
            "detail": "Already instructed at Seccl.",
            "id": already,
        }
    if current["status"] not in outbound.OPEN:
        return {"status": "skipped", "detail": f"Outbound status is {current['status']}."}

    adviser_fee = float((current.get("data") or {}).get("adviser_fee") or 0)
    # A withdrawal auto-disinvests unless the row says otherwise. Absent (older rows) keeps the
    # historical always-on behaviour.
    auto_disinvest = bool((current.get("data") or {}).get("auto_disinvest", True))
    try:
        legs = seccl_legs(
            origin=origin,
            kind=kind,
            amount=amount,
            currency=currency,
            adviser_fee=adviser_fee,
            payment_method=payment_method,
            auto_disinvest=auto_disinvest,
        )
    except UnsupportedRail as exc:
        return {"status": "skipped", "detail": str(exc)}
    if not legs:
        return {"status": "skipped", "detail": "No Seccl mapping for this movement."}
    if not account_external_id:
        return {"status": "skipped", "detail": "This account has no Seccl id."}

    try:
        sent = await _seccl_group(account_external_id, legs, external_reference=origin_uid)
    except LookupError as exc:
        return {"status": "skipped", "detail": str(exc)}
    except Exception as exc:
        logger.warning("Seccl dispatch failed for %s %s: %s", origin, origin_uid, exc)
        await outbound.mark_with_companion(
            session,
            tenant_id=tenant_id,
            origin=origin,
            origin_uid=origin_uid,
            current=current,
            status="failed",
            error=str(exc),
        )
        return {"status": "failed", "detail": str(exc)}

    request, answer = _vendor_exchange(sent)
    vendor_id = _vendor_id(answer)
    await outbound.mark_with_companion(
        session,
        tenant_id=tenant_id,
        origin=origin,
        origin_uid=origin_uid,
        current=current,
        status="submitted",
        provider="seccl" if vendor_id else None,
        external_id=vendor_id,
    )
    if not vendor_id:
        logger.warning(
            "Seccl group for %s %s answered without an id: %s", origin, origin_uid, answer
        )
    report: dict[str, Any] = {"status": "submitted", "id": vendor_id, "response": answer}
    if request is not None:
        report["request"] = request
    return report
