"""Outbound pending transactions — the Graphene-originated half of the money ledger.

Decision 2 still holds for inbound credits: ``platform.transactions`` is an immutable synced
mirror of what the custodian already recorded, keyed on
``(tenant_id, source_system, source_transaction_id)``. Two firms syncing one custodian
firm each keep a copy.
The exception is the other direction. Raising a withdrawal, payment, contribution or transfer
is instructing money that has not landed yet, and that instruction has to exist as a transaction
in a pending-style state so it can be synced to SEI / Seccl / Morgan Lloyd, have its status
advanced when the vendor accepts it, and carry the vendor's id on ``platform.links``.

These rows are keyed ``source_system = 'graphene'`` / ``source_transaction_id = {uid}``.
The provider already separates them from the custodian feed, so the reference is the origin's
public id. The vendor id is recorded on ``platform.links``
(``entity_type = 'transaction'``). That link is what a later send checks. A copy stays on
``data.provider_references`` so an older reader still sees it. We do not rewrite the
Graphene key, because the book-sink ``ON CONFLICT`` would then UPDATE this row and could wipe
the origin. Matching still attaches the landed inbound credit to the contribution/transfer;
this row is a sibling, never the credit ``transaction_id``.

Status vocabulary and the mapping onto instructions / contributions / transfers / expectations
is ``docs/transaction-status.md``. Dispatch is best-effort and idempotent: a retried call that
already has a vendor link is a no-op. Seccl's portfolio-transaction group has no vendor
idempotency — we send ``externalReference = origin uid`` and refuse to fire a second group
once a Seccl link exists.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import providers
from app.ids import new_id
from app.instruction_kinds import display_kind

logger = logging.getLogger(__name__)

SOURCE = "graphene"

ORIGINS = frozenset({"instruction", "contribution", "transfer"})
STATUSES = ("pending", "submitted", "posted", "failed", "cancelled")
OPEN = frozenset({"pending", "failed"})
SENT = frozenset({"submitted", "posted"})
CANCELABLE = frozenset({"pending", "failed", "submitted"})
#: Four-eye stage. Null means this send has no approval. Vendor progress stays on status.
REVIEW_STATUSES = frozenset({"awaiting_approval", "approved", "rejected"})

#: Instruction kind / contribution category / transfer type -> ``platform.transaction_type``.
TYPE_FOR: dict[str, str] = {
    "expected_in": "deposit",
    "withdrawal": "withdrawal",
    "adhoc_fee": "fee",
    "model_topup": "deposit",
    "model_withdrawal": "withdrawal",
    "contribution": "deposit",
    "personal_contribution": "deposit",
    "employer_contribution": "deposit",
    "third_party_contribution": "deposit",
    "transfer": "transfer_in",
    "transfer_in": "transfer_in",
}


def source_key(origin: str, origin_uid: str) -> str:
    """The origin's public id. Unique under ``source_system = graphene``.

    The origin itself is ``data.origin``, not a prefix on this id. A custodian feed writes a
    different ``source_system``, so the bare id cannot land on this row.
    """
    if origin not in ORIGINS:
        raise ValueError(f"Unknown outbound origin {origin!r}.")
    if not origin_uid:
        raise ValueError("An outbound transaction needs the origin uid.")
    return origin_uid


def transaction_type(*, origin: str, kind: str | None) -> str:
    """The normalised ``platform.transaction_type`` for one origin kind."""
    if kind and kind in TYPE_FOR:
        return TYPE_FOR[kind]
    if origin in TYPE_FOR:
        return TYPE_FOR[origin]
    raise ValueError(f"No transaction type for origin {origin!r} kind {kind!r}.")


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def _row(mapping: Any) -> dict[str, Any]:
    return dict(mapping)


async def get_by_origin(
    session: AsyncSession, *, tenant_id: int, origin: str, origin_uid: str
) -> dict[str, Any] | None:
    """The Graphene-originated row for this instruction / contribution / transfer, if any."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, status, review_status, source_system, source_transaction_id, "
                    "       type::text AS type, "
                    "       amount::float8 AS amount, currency, fee_amount::float8 AS fee_amount, "
                    "       expectation_id, account_id, contact_id, data, transaction_date "
                    "FROM platform.transactions "
                    "WHERE tenant_id = :t AND source_system = :src "
                    "  AND source_transaction_id = :sid AND data ->> 'origin' = :origin"
                ),
                {
                    "t": tenant_id,
                    "src": SOURCE,
                    "sid": source_key(origin, origin_uid),
                    "origin": origin,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    return _row(row) if row else None


async def set_review_status(
    session: AsyncSession,
    *,
    tenant_id: int,
    origin: str,
    origin_uid: str,
    review: str,
) -> None:
    """Stamp the four-eye stage on this origin's Graphene rows, including a fee sibling.

    The sibling is a second public id grouped on the instruction uid, so the match is the
    group as well as the origin uid. Rejecting a row that has not been sent cancels it:
    a later dispatch must not treat it as an ordinary pending payment.
    """
    if review not in REVIEW_STATUSES:
        raise ValueError(f"Unknown review status {review!r}.")
    if origin not in ORIGINS or not origin_uid:
        return
    await session.execute(
        text(
            "UPDATE platform.transactions SET review_status = :review, updated_at = now(), "
            "  status = CASE WHEN :review = 'rejected' AND status IN ('pending', 'failed') "
            "                THEN 'cancelled' ELSE status END "
            "WHERE tenant_id = :t AND source_system = :src AND ("
            "  report_group = :uid OR source_transaction_id = :uid "
            "  OR (data ->> 'origin' = :origin AND data ->> 'origin_uid' = :uid)"
            ")"
        ),
        {
            "review": review,
            "t": tenant_id,
            "src": SOURCE,
            "uid": origin_uid,
            "origin": origin,
        },
    )


def _review_hold(current: dict[str, Any] | None) -> dict[str, Any] | None:
    """Refuse to send a payment a second person has not released."""
    if current is None:
        return None
    review = current.get("review_status")
    if review == "awaiting_approval":
        return {"status": "skipped", "detail": "Waiting for approval."}
    if review == "rejected":
        return {"status": "skipped", "detail": "This payment was rejected."}
    return None


async def ensure_pending(
    session: AsyncSession,
    *,
    tenant_id: int,
    origin: str,
    origin_uid: str,
    kind: str,
    account_id: int,
    contact_id: int | None,
    amount: float,
    currency: str = "GBP",
    fee_amount: float = 0.0,
    expectation_id: int | None = None,
    transaction_date: date | str | None = None,
    extra: dict[str, Any] | None = None,
    report_group: str | None = None,
) -> dict[str, Any]:
    """Insert the pending outbound row, or return the one already there.

    Idempotent on ``(graphene, origin uid)``. A second call with a posted / cancelled /
    submitted row leaves it alone (the amounts are not restated over a vendor-accepted order).
    A pending or failed row may have its amount and origin payload refreshed — that is how a
    retried lodge after a configuration fix keeps one row.
    """
    key = source_key(origin, origin_uid)
    payload = {
        "origin": origin,
        "origin_uid": origin_uid,
        "origin_kind": kind,
        **(extra or {}),
    }
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO platform.transactions "
                    "  (tenant_id, account_id, contact_id, expectation_id, source_system, "
                    "   source_transaction_id, type, source_type, source_subtype, "
                    "   amount, currency, fee_amount, transaction_date, status, data, "
                    "   report_group) "
                    "VALUES (:t, :a, :c, :e, :src, :sid, CAST(:type AS platform.transaction_type), "
                    "        :origin, :subtype, :amt, :cur, :fee, CAST(:on AS date), 'pending', "
                    "        CAST(:data AS jsonb), :grp) "
                    "ON CONFLICT (tenant_id, source_system, source_transaction_id) DO UPDATE SET "
                    "  updated_at = now(), "
                    "  amount = EXCLUDED.amount, "
                    "  currency = EXCLUDED.currency, "
                    "  fee_amount = EXCLUDED.fee_amount, "
                    "  expectation_id = COALESCE(platform.transactions.expectation_id, "
                    "                           EXCLUDED.expectation_id), "
                    "  data = platform.transactions.data || EXCLUDED.data "
                    "WHERE platform.transactions.status IN ('pending', 'failed') "
                    "RETURNING id, status, source_system, source_transaction_id, "
                    "          type::text AS type, amount::float8 AS amount, currency, "
                    "          fee_amount::float8 AS fee_amount, expectation_id, account_id, "
                    "          contact_id, data, transaction_date"
                ),
                {
                    "t": tenant_id,
                    "a": account_id,
                    "c": contact_id,
                    "e": expectation_id,
                    "src": SOURCE,
                    "sid": key,
                    "type": transaction_type(origin=origin, kind=kind),
                    "origin": origin,
                    "subtype": display_kind(kind),
                    "grp": report_group or origin_uid,
                    "amt": amount,
                    "cur": currency or "GBP",
                    "fee": fee_amount or 0,
                    "on": transaction_date,
                    "data": _json(payload),
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is not None:
        return _row(row)
    existing = await get_by_origin(
        session, tenant_id=tenant_id, origin=origin, origin_uid=origin_uid
    )
    if existing is None:
        raise RuntimeError(f"Outbound transaction {key} neither inserted nor found.")
    return existing


async def record_link(
    session: AsyncSession,
    *,
    tenant_id: int,
    transaction_id: int,
    provider: str,
    external_id: str,
    link_type: str = "primary",
) -> None:
    """Record the vendor's id against this outbound transaction.

    ``platform.links`` is the sent-id (``entity_type = 'transaction'``). A later send looks
    here, not at the row. ``data.provider_references`` is still written so a reader that has
    not moved over keeps seeing the id. Idempotent: a second success with the same provider
    overwrites the id (the vendor's current handle), it does not insert a second link.
    """
    if not external_id:
        return
    await session.execute(
        text(
            "INSERT INTO platform.links "
            "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
            "VALUES (:t, 'transaction', :eid, CAST(:p AS platform.provider_code), :lt, :ext) "
            "ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type) "
            "DO UPDATE SET external_id = EXCLUDED.external_id, updated_at = now()"
        ),
        {
            "t": tenant_id,
            "eid": transaction_id,
            "p": provider,
            "lt": link_type,
            "ext": str(external_id),
        },
    )
    await session.execute(
        text(
            "UPDATE platform.transactions SET updated_at = now(), "
            "  data = jsonb_set(COALESCE(data, '{}'::jsonb), '{provider_references}', "
            "    COALESCE(data -> 'provider_references', '{}'::jsonb) || "
            "    jsonb_build_object(CAST(:p AS text), CAST(:ext AS text))) "
            "WHERE id = :i AND tenant_id = :t"
        ),
        {"t": tenant_id, "i": transaction_id, "p": provider, "ext": str(external_id)},
    )


async def vendor_link(
    session: AsyncSession,
    *,
    tenant_id: int,
    transaction_id: int,
    provider: str,
) -> str | None:
    """The vendor id already stored for this transaction, if a send has landed.

    This is the guard a money retry checks. The account's own custodian link is a different
    row and must not be read as "this payment was sent".
    """
    found = (
        await session.execute(
            text(
                "SELECT external_id FROM platform.links "
                "WHERE tenant_id = :t AND entity_type = 'transaction' AND entity_id = :id "
                "  AND provider::text = :p AND link_type = 'primary' "
                "LIMIT 1"
            ),
            {"t": tenant_id, "id": transaction_id, "p": provider},
        )
    ).scalar_one_or_none()
    return str(found) if found else None


async def sent_id(
    session: AsyncSession,
    *,
    tenant_id: int,
    transaction_id: int,
    provider: str,
    data: dict[str, Any] | None = None,
) -> str | None:
    """The vendor id when this transaction has already been sent to ``provider``.

    The link is the sent-id. ``data.provider_references`` is still read so a row sent before
    the link was the guard is not sent a second time. A link wins when both are present.
    """
    linked = await vendor_link(
        session, tenant_id=tenant_id, transaction_id=transaction_id, provider=provider
    )
    if linked:
        return linked
    legacy = (data or {}).get("provider_references") or {}
    raw = legacy.get(provider) if isinstance(legacy, dict) else None
    return str(raw) if raw else None


async def mark_outcome(
    session: AsyncSession,
    *,
    tenant_id: int,
    origin: str,
    origin_uid: str,
    status: str,
    provider: str | None = None,
    external_id: str | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """Advance (or revert) the outbound row. No-ops if there is no row.

    ``posted`` and ``cancelled`` are terminals against a further submit; ``failed`` may be
    retried (it stays in ``OPEN``). A submitted row is not walked back to pending.
    """
    if status not in STATUSES:
        raise ValueError(f"Unknown outbound status {status!r}.")
    current = await get_by_origin(
        session, tenant_id=tenant_id, origin=origin, origin_uid=origin_uid
    )
    if current is None:
        return None
    if current["status"] == status and not (provider and external_id):
        return current
    # A vendor miss on one rail must not unwind another rail's accept, and a posted/cancelled
    # row is terminal against everything except recording an extra provider id on the same status.
    if current["status"] in SENT and status == "failed":
        return current
    if current["status"] in {"posted", "cancelled"} and status != current["status"]:
        return current

    await session.execute(
        text(
            "UPDATE platform.transactions SET status = :s, updated_at = now(), "
            "  posted_at = CASE WHEN :s = 'posted' THEN COALESCE(posted_at, now()) "
            "                   ELSE posted_at END, "
            "  data = CASE WHEN CAST(:err AS text) IS NULL THEN data "
            "              ELSE jsonb_set(COALESCE(data, '{}'::jsonb), '{error}', "
            "                             to_jsonb(CAST(:err AS text))) END "
            "WHERE id = :i AND tenant_id = :t"
        ),
        {
            "s": status,
            "err": error,
            "i": current["id"],
            "t": tenant_id,
        },
    )
    if provider and external_id:
        await record_link(
            session,
            tenant_id=tenant_id,
            transaction_id=int(current["id"]),
            provider=provider,
            external_id=external_id,
        )
    return await get_by_origin(session, tenant_id=tenant_id, origin=origin, origin_uid=origin_uid)


SENT_INSTRUCTION = (
    "This instruction has already been sent to the custodian, so it cannot be cancelled here."
)


def _companion_uid(row: dict[str, Any] | None) -> str | None:
    """The sibling Graphene row for an adviser fee booked with a payment, if any."""
    if not row:
        return None
    uid = (row.get("data") or {}).get("companion_uid")
    return str(uid) if uid else None


async def mark_with_companion(
    session: AsyncSession,
    *,
    tenant_id: int,
    origin: str,
    origin_uid: str,
    current: dict[str, Any] | None,
    status: str,
    provider: str | None = None,
    external_id: str | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """Apply one outcome to an origin and to its adviser-fee sibling, when it has one.

    The two rows are one instruction. Cancel, submit and fail have to move both, or the
    listing shows a fee that is still pending beside a payment that has already gone.
    """
    marked = await mark_outcome(
        session,
        tenant_id=tenant_id,
        origin=origin,
        origin_uid=origin_uid,
        status=status,
        provider=provider,
        external_id=external_id,
        error=error,
    )
    companion = _companion_uid(current)
    if companion:
        await mark_outcome(
            session,
            tenant_id=tenant_id,
            origin=origin,
            origin_uid=companion,
            status=status,
            provider=provider,
            external_id=external_id,
            error=error,
        )
    return marked


def _refuse_cancel(origin: str, status: str) -> str | None:
    """Why this origin cannot be cancelled in its current outbound status, or None.

    Already-cancelled is not a refusal — the caller still needs to cancel the domain row.
    """
    if status == "cancelled":
        return None
    if status == "posted":
        return "This money has already been matched to a landed credit."
    if origin == "instruction" and status in SENT:
        return SENT_INSTRUCTION
    if status not in CANCELABLE:
        return f"An outbound transaction in status {status!r} cannot be cancelled."
    return None


async def cancel_origin(
    session: AsyncSession, *, tenant_id: int, origin: str, origin_uid: str
) -> dict[str, Any] | None:
    """Cancel the outbound row for this origin, or refuse.

    Instructions that have been accepted by a custodian cannot be cancelled here — that would
    release the cash gate while the vendor still has the order. Contributions and transfers may
    still cancel a submitted (not posted) row: the scheme-admin record is a separate step and
    the pipeline already refuses once relief is in flight.
    """
    current = await get_by_origin(
        session, tenant_id=tenant_id, origin=origin, origin_uid=origin_uid
    )
    if current is None:
        return None
    if current["status"] == "cancelled":
        return current
    reason = _refuse_cancel(origin, str(current["status"]))
    if reason:
        raise ValueError(reason)
    return await mark_with_companion(
        session,
        tenant_id=tenant_id,
        origin=origin,
        origin_uid=origin_uid,
        current=current,
        status="cancelled",
    )


async def restore_cancelled(
    session: AsyncSession, *, tenant_id: int, origin: str, origin_uid: str
) -> dict[str, Any] | None:
    """Undo :func:`cancel_origin` when the origin is reclassified out of a disposal.

    ``mark_outcome`` treats ``cancelled`` as terminal against a further submit, which is right
    for a staff cancel. A disposal is a classification that can be walked back — the Graphene
    row has to come with it, or the deposit stays cancelled with no staff path to clear it.
    Posted money is not restored: that credit has already landed. The fee sibling is in the
    same update. ``mark_with_companion`` cannot do it: ``mark_outcome`` will not leave
    ``cancelled``.
    """
    current = await get_by_origin(
        session, tenant_id=tenant_id, origin=origin, origin_uid=origin_uid
    )
    if current is None:
        return None
    if current["status"] != "cancelled":
        return current
    ids = [int(current["id"])]
    companion = _companion_uid(current)
    if companion:
        other = await get_by_origin(
            session, tenant_id=tenant_id, origin=origin, origin_uid=companion
        )
        if other is not None:
            ids.append(int(other["id"]))
    await session.execute(
        text(
            "UPDATE platform.transactions SET status = 'pending', updated_at = now() "
            "WHERE id = ANY(:ids) AND tenant_id = :t AND status = 'cancelled'"
        ),
        {"ids": ids, "t": tenant_id},
    )
    return await get_by_origin(session, tenant_id=tenant_id, origin=origin, origin_uid=origin_uid)


async def mark_posted_for_expectation(
    session: AsyncSession, *, tenant_id: int, expectation_id: int, inbound_id: int
) -> None:
    """The inbound credit landed: Graphene siblings of this expectation are now posted.

    Does not touch the inbound row (``inbound_id``) — that is the feed credit matching.link
    already pointed at the expectation.
    """
    await session.execute(
        text(
            "UPDATE platform.transactions SET status = 'posted', posted_at = now(), "
            "  updated_at = now() "
            "WHERE tenant_id = :t AND expectation_id = :e AND source_system = :src "
            "  AND id <> :x AND status IN ('pending', 'submitted')"
        ),
        {"t": tenant_id, "e": expectation_id, "src": SOURCE, "x": inbound_id},
    )


async def revert_posted_for_expectation(
    session: AsyncSession, *, tenant_id: int, expectation_id: int
) -> None:
    """Undo :func:`mark_posted_for_expectation` when a match is unlinked.

    Submitted if a vendor id was recorded, otherwise pending — we still believe the order is
    out if the custodian accepted it.
    """
    await session.execute(
        text(
            "UPDATE platform.transactions SET status = CASE "
            "    WHEN EXISTS (SELECT 1 FROM platform.links l "
            "                 WHERE l.tenant_id = platform.transactions.tenant_id "
            "                   AND l.entity_type = 'transaction' "
            "                   AND l.entity_id = platform.transactions.id) "
            "      OR COALESCE(data -> 'provider_references', '{}'::jsonb) <> '{}'::jsonb "
            "      THEN 'submitted' ELSE 'pending' END, "
            "  posted_at = NULL, updated_at = now() "
            "WHERE tenant_id = :t AND expectation_id = :e AND source_system = :src "
            "  AND status = 'posted'"
        ),
        {"t": tenant_id, "e": expectation_id, "src": SOURCE},
    )


# ---- destination + dispatch -----------------------------------------------------------------


async def account_destination(
    session: AsyncSession, *, tenant_id: int, account_id: int
) -> dict[str, Any]:
    """Which custodian this account is instructed at, and that custodian's account id.

    Same LATERAL rule as ``instructions.create_instruction``: the account's own ``custodian``
    column decides, falling back to any primary custodian link so an unstamped column still
    resolves.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.contact_id, a.currency, a.custodian::text AS custodian, "
                    "       pl.external_id, pl.provider "
                    "FROM platform.accounts a "
                    "LEFT JOIN LATERAL ( "
                    "  SELECT l.external_id, l.provider::text AS provider FROM platform.links l "
                    "   WHERE l.entity_type = 'account' AND l.entity_id = a.id "
                    "     AND l.provider = ANY(:custodians) AND l.link_type = 'primary' "
                    "     AND l.tenant_id = a.tenant_id "
                    "     AND (a.custodian IS NULL OR l.provider = a.custodian) "
                    "   ORDER BY (l.provider = a.custodian) DESC NULLS LAST, l.provider "
                    "   LIMIT 1 "
                    ") pl ON true "
                    "WHERE a.id = :a AND a.tenant_id = :t"
                ),
                {
                    "a": account_id,
                    "t": tenant_id,
                    "custodians": list(providers.custodians()),
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Account not found")
    return _row(row)


def sei_skip_reason(*, origin: str, kind: str) -> str | None:
    """Why SEI is not dispatched from this module, or None if a caller should lodge.

    Contributions already lodge an Anticipated Cash Receipt in ``contributions.lodge_expectation``;
    this module only records the outcome. Withdrawals need ``createcashdisbursement`` plus a
    pay-to profile, which is not wrapped. SIPP cash-out is Drawdown (DCube), not a disbursement.
    """
    if origin == "contribution":
        return None
    if kind in {"withdrawal", "model_withdrawal"}:
        return (
            "SEI cash disbursement is not wrapped in the API yet "
            "(createcashdisbursement needs a pay-to profile)."
        )
    if kind == "expected_in":
        return (
            "An SEI anticipated receipt is lodged from a contribution, not a payment instruction."
        )
    if origin == "transfer":
        return (
            "SEI has no transfer-in instruction on this path; the credit is matched when it lands."
        )
    return f"No SEI mapping for {origin}:{kind}."


def money_rail(dest: dict[str, Any]) -> str | None:
    """The custodian this account is instructed at: ``accounts.custodian``, else the primary link.

    Morgan Lloyd is a scheme administrator, not a cash rail — it is not returned here even when
    the account has an ML scheme link. Record-to-Morgan-Lloyd is a separate step.
    """
    raw = dest.get("custodian") or dest.get("provider")
    if not raw:
        return None
    rail = str(raw)
    if rail in providers.administrators():
        return None
    return rail


async def dispatch(
    session: AsyncSession,
    *,
    tenant_id: int,
    origin: str,
    origin_uid: str,
    account_id: int | None = None,
) -> dict[str, Any]:
    """Send this pending row to the account's custodian. One rail, the one on the account.

    Seccl: portfolio-transaction group. SEI contributions return ``status="lodge"`` so the
    caller can raise the anticipated receipt (that call needs the SEI reference data, which
    does not live here); other SEI kinds are skipped until disbursement is wrapped. Morgan
    Lloyd is not a money rail — ``on_ml_recorded`` is the scheme-admin path.

    ``account_id`` is used only when the pending row does not exist yet, so a contribution
    lodged before this ledger row was introduced still resolves a rail.
    """
    current = await get_by_origin(
        session, tenant_id=tenant_id, origin=origin, origin_uid=origin_uid
    )
    held = _review_hold(current)
    if held is not None:
        return held
    resolved_account = int(current["account_id"]) if current is not None else account_id
    if resolved_account is None:
        return {"status": "failed", "detail": "No outbound transaction to dispatch."}
    dest = await account_destination(session, tenant_id=tenant_id, account_id=resolved_account)
    rail = money_rail(dest)
    kind = ""
    amount = 0.0
    currency = str(dest.get("currency") or "GBP")
    payment_method: str | None = None
    if current is not None:
        data = current.get("data") or {}
        kind = str(data.get("origin_kind") or "")
        amount = float(current["amount"])
        currency = str(current.get("currency") or currency)
        payment_method = str(data["payment_method"]) if data.get("payment_method") else None
    if rail == "seccl":
        from app.integrations.seccl.money import dispatch_seccl

        result = await dispatch_seccl(
            session,
            tenant_id=tenant_id,
            origin=origin,
            origin_uid=origin_uid,
            kind=kind,
            account_external_id=str(dest.get("external_id") or ""),
            amount=amount,
            currency=currency,
            payment_method=payment_method,
        )
        return {**result, "rail": "seccl"}
    if rail == "sei":
        reason = sei_skip_reason(origin=origin, kind=kind)
        if reason:
            return {"status": "skipped", "detail": reason, "rail": "sei"}
        # The anticipated receipt lives next to the SEI reference data. The caller lodges it.
        return {"status": "lodge", "rail": "sei"}
    if not rail:
        return {"status": "skipped", "detail": "This account isn't linked to a custodian."}
    return {
        "status": "skipped",
        "detail": f"No outbound mapping for custodian {rail} and kind {kind}.",
    }


async def record_instruction(
    session: AsyncSession,
    *,
    tenant_id: int,
    instruction: dict[str, Any],
    account_id: int,
    contact_id: int | None,
    currency: str,
) -> dict[str, Any]:
    """Pending outbound row for one ``platform.instructions`` row. Does not call a vendor.

    A withdrawal's chosen rail and who bears its fee ride on the row (``data.payment_method``,
    ``data.pass_rail_fee``), so ``dispatch`` instructs the rail the client was quoted — and the
    fee waiver is on the record even where the custodian has no field for it.

    A payment with an adviser fee writes a sibling Graphene row so the listing shows both legs,
    keyed on a second public id and grouped on the instruction uid. Dispatch sends them as two
    members of one Seccl group.
    """
    uid = str(instruction["uid"])
    kind = str(instruction["kind"])
    fee = float(instruction.get("adviser_fee") or 0)
    extra: dict[str, Any] = {"instruction_kind": kind}
    if instruction.get("payment_method"):
        extra["payment_method"] = str(instruction["payment_method"])
    if instruction.get("pass_rail_fee") is not None:
        extra["pass_rail_fee"] = bool(instruction["pass_rail_fee"])
    if kind in {"withdrawal", "model_withdrawal"} and instruction.get("auto_disinvest") is not None:
        extra["auto_disinvest"] = bool(instruction["auto_disinvest"])
    if kind == "expected_in" and instruction.get("auto_invest") is not None:
        extra["auto_invest"] = bool(instruction["auto_invest"])
    existing = await get_by_origin(
        session, tenant_id=tenant_id, origin="instruction", origin_uid=uid
    )
    companion_uid = _companion_uid(existing) if existing else None
    if fee > 0:
        companion_uid = companion_uid or new_id()
        extra["companion_uid"] = companion_uid
        extra["adviser_fee"] = fee
    await ensure_pending(
        session,
        tenant_id=tenant_id,
        origin="instruction",
        origin_uid=uid,
        kind=kind,
        account_id=account_id,
        contact_id=contact_id,
        amount=float(instruction["amount"]),
        currency=currency,
        fee_amount=float(instruction.get("rail_fee") or 0),
        transaction_date=instruction.get("due_date"),
        extra=extra,
        report_group=uid,
    )
    if fee > 0 and companion_uid:
        await ensure_pending(
            session,
            tenant_id=tenant_id,
            origin="instruction",
            origin_uid=companion_uid,
            kind="adhoc_fee",
            account_id=account_id,
            contact_id=contact_id,
            amount=fee,
            currency=currency,
            transaction_date=instruction.get("due_date"),
            extra={"companion_of": uid, "instruction_kind": "adhoc_fee"},
            report_group=uid,
        )
    return {"status": "pending", "detail": "Recorded locally; dispatch is a separate step."}


async def record_contribution(
    session: AsyncSession,
    *,
    tenant_id: int,
    contribution: dict[str, Any],
) -> dict[str, Any]:
    """Pending row for a booked contribution. Dispatch is the caller's lodge path."""
    uid = str(contribution["uid"])
    kind = str(contribution.get("category") or "contribution")
    await ensure_pending(
        session,
        tenant_id=tenant_id,
        origin="contribution",
        origin_uid=uid,
        kind=kind,
        account_id=int(contribution["account_id"]),
        contact_id=contribution.get("contact_id"),
        amount=float(contribution["amount"]),
        currency=str(contribution.get("currency") or "GBP"),
        expectation_id=contribution.get("expectation_id"),
        transaction_date=contribution.get("expectation_due_date")
        or contribution.get("contribution_date"),
        extra={"category": kind},
    )
    return {"status": "pending", "detail": "Recorded locally; dispatch is a separate step."}


async def on_sei_lodged(
    session: AsyncSession,
    *,
    tenant_id: int,
    origin: str,
    origin_uid: str,
    activity_id: str | None,
    failed: bool = False,
    error: str | None = None,
) -> None:
    """Apply the SEI ACR (or equivalent) outcome to the outbound row."""
    if failed:
        await mark_outcome(
            session,
            tenant_id=tenant_id,
            origin=origin,
            origin_uid=origin_uid,
            status="failed",
            error=error,
        )
        return
    if not activity_id:
        return
    await mark_outcome(
        session,
        tenant_id=tenant_id,
        origin=origin,
        origin_uid=origin_uid,
        status="submitted",
        provider="sei",
        external_id=activity_id,
    )


async def record_transfer(
    session: AsyncSession, *, tenant_id: int, transfer: dict[str, Any]
) -> dict[str, Any]:
    """Pending row for a booked transfer in. Dispatch is the Morgan Lloyd record endpoint."""
    total = transfer.get("total_value")
    if total is None:
        return {"status": "skipped", "detail": "No transfer value to instruct."}
    uid = str(transfer["uid"])
    await ensure_pending(
        session,
        tenant_id=tenant_id,
        origin="transfer",
        origin_uid=uid,
        kind="transfer_in",
        account_id=int(transfer["account_id"]),
        contact_id=transfer.get("contact_id"),
        amount=float(total),
        currency=str(transfer.get("currency") or "GBP"),
        expectation_id=transfer.get("expectation_id"),
        transaction_date=transfer.get("expected_date"),
        extra={
            "transfer_type": transfer.get("transfer_type"),
            "basis": transfer.get("basis"),
        },
    )
    return {"status": "pending", "detail": "Recorded locally; provider sync is a separate step."}


async def on_ml_recorded(
    session: AsyncSession,
    *,
    tenant_id: int,
    origin: str,
    origin_uid: str,
    ml_id: str,
) -> None:
    """Morgan Lloyd accepted the scheme-admin record. Link it; submit if still pending."""
    current = await get_by_origin(
        session, tenant_id=tenant_id, origin=origin, origin_uid=origin_uid
    )
    status = "submitted"
    if current and current["status"] in SENT:
        status = str(current["status"])
    await mark_outcome(
        session,
        tenant_id=tenant_id,
        origin=origin,
        origin_uid=origin_uid,
        status=status,
        provider="morgan-lloyd",
        external_id=ml_id,
    )
