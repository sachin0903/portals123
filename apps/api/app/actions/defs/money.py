"""Money actions on baton.

Each is a composite: read the account's book, then run the custodian's money leaf. The leaves are
thin adapters over the existing, idempotency-aware handlers (app.integrations.*.actions): those
already check the sent-id link and, for SEI, keep an in-flight flag in a separate transaction, so
baton's roll-back-on-failure matches what the old engine did. A refused send parks the run as
NeedsAttention and persists its attempt; a send that already landed is Skipped and never repeated.

Money is ``manual``: a failure never auto-retries, it waits for a person (``on_failure`` is
needs_attention). When a SIPP is administered by Morgan Lloyd, a payment also records the
contribution there (``morgan-lloyd.record_contribution``, a second child of the same run).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from baton import Permanent, Retry, RunContext, Skip, action

from app.actions.defs.resolve import load_account_book

_MONEY_META = {
    "catalogue": True,
    "category": "money_movement",
    "kind": "composite",
    "providers": ["sei", "seccl", "morgan-lloyd"],
}

Handler = Callable[..., Awaitable[Any]]


async def _adapt(
    ctx: RunContext,
    handler: Handler,
    payload: dict[str, Any],
    *,
    skip_message: str = "Already instructed.",
) -> dict[str, Any]:
    """Call an existing money handler and map its Attempt onto baton's outcomes. The handler owns
    idempotency (sent-id link, SEI's in-flight flag), so a refused send parks and an already-sent
    one is skipped, exactly as the old engine behaved."""
    from app.actions.attempt import Attempt, OperationError

    try:
        attempt: Attempt = await handler(
            ctx.session, payload={"tenant_id": ctx.tenant_id, **payload}, subjects=[]
        )
    except OperationError as exc:
        # Record the call on the attempt even though it was refused, so the run detail shows what
        # was sent and what came back, not only on success. (Permanent carries no evidence, so this
        # goes through ctx.evidence; the engine writes it onto the failed attempt.)
        ctx.evidence(
            method=exc.method,
            url=exc.url,
            request=exc.request,
            response=exc.response,
            status_code=exc.status_code,
        )
        if exc.permanent:
            raise Permanent(str(exc)) from exc
        raise Retry(
            str(exc), status_code=exc.status_code, request=exc.request, response=exc.response
        ) from exc
    ctx.evidence(
        method=attempt.method,
        url=attempt.url,
        request=attempt.request,
        response=attempt.response,
        status_code=attempt.status_code,
    )
    if attempt.skipped:
        raise Skip(skip_message)
    if not attempt.ok:
        raise Permanent(attempt.error or "The custodian refused the instruction.")
    return cast(dict[str, Any], attempt.response)


async def _run_handler(
    ctx: RunContext, handler: Handler, *, account_id: int, instruction_uid: str | None
) -> dict[str, Any]:
    return await _adapt(
        ctx, handler, {"account_id": account_id, "instruction_uid": instruction_uid}
    )


# ---- provider leaves (manual: a failure parks, it does not auto-retry) -------


@action(key="seccl.payment", name="Seccl payment", on_failure="needs_attention")
async def seccl_payment(
    ctx: RunContext, *, account_id: int, instruction_uid: str | None = None
) -> dict[str, Any]:
    from app.integrations.seccl.actions import seccl_payment as handler

    return await _run_handler(ctx, handler, account_id=account_id, instruction_uid=instruction_uid)


@action(key="seccl.withdrawal", name="Seccl withdrawal", on_failure="needs_attention")
async def seccl_withdrawal(
    ctx: RunContext, *, account_id: int, instruction_uid: str | None = None
) -> dict[str, Any]:
    from app.integrations.seccl.actions import seccl_withdrawal as handler

    return await _run_handler(ctx, handler, account_id=account_id, instruction_uid=instruction_uid)


@action(key="seccl.fee", name="Seccl ad-hoc fee", on_failure="needs_attention")
async def seccl_fee(
    ctx: RunContext, *, account_id: int, instruction_uid: str | None = None
) -> dict[str, Any]:
    from app.integrations.seccl.actions import seccl_adhoc_fee as handler

    return await _run_handler(ctx, handler, account_id=account_id, instruction_uid=instruction_uid)


@action(key="seccl.transfer", name="Seccl transfer", on_failure="needs_attention")
async def seccl_transfer(
    ctx: RunContext, *, account_id: int, uid: str | None = None, instruction_uid: str | None = None
) -> dict[str, Any]:
    from app.integrations.seccl.actions import seccl_transfer as handler

    return await _adapt(
        ctx, handler, {"account_id": account_id, "uid": uid, "instruction_uid": instruction_uid}
    )


@action(key="sei.transfer", name="SEI transfer", on_failure="needs_attention", idempotent=True)
async def sei_transfer(
    ctx: RunContext, *, account_id: int, uid: str | None = None, instruction_uid: str | None = None
) -> dict[str, Any]:
    # A SEI SIPP is recorded at Morgan Lloyd first: the handler skips (Attempt skipped → Skip) until
    # the saga is funds_sent, when the later ``:sei`` leg sends the receipt. The handler owns that
    # wait, so the composite does not orchestrate it.
    from app.integrations.sei.actions import sei_transfer as handler

    return await _adapt(
        ctx,
        handler,
        {"account_id": account_id, "uid": uid, "instruction_uid": instruction_uid},
        skip_message="The SEI receipt is raised once Morgan Lloyd has completed the SIPP transfer.",
    )


@action(key="sei.payment", name="SEI payment", on_failure="needs_attention")
async def sei_payment(
    ctx: RunContext, *, account_id: int, instruction_uid: str | None = None
) -> dict[str, Any]:
    # Money in: the booked payment is lodged as a SEI anticipated cash receipt. The handler carries
    # the contribution shape too (ISA/JISA tax year, category, adviser fee) when the instruction has
    # it, so a payment and an ISA/SIPP contribution are the one call. It owns idempotency by the
    # instruction reference, and classifies a SEI refusal: a 4xx parks, a 5xx/lost answer retries.
    from app.integrations.sei.actions import sei_payment as handler

    return await _run_handler(ctx, handler, account_id=account_id, instruction_uid=instruction_uid)


@action(key="sei.withdrawal", name="SEI withdrawal", on_failure="needs_attention")
async def sei_withdrawal(
    ctx: RunContext, *, account_id: int, instruction_uid: str | None = None
) -> dict[str, Any]:
    # Money out: paid to the account's SEI pay-to profile. The handler holds the instruction in
    # flight before the money leaves (migration 0260), so a lost answer never double-pays — every
    # SEI refusal parks for a person rather than auto-retrying.
    from app.integrations.sei.actions import sei_withdrawal as handler

    return await _run_handler(ctx, handler, account_id=account_id, instruction_uid=instruction_uid)


@action(key="sei.fee", name="SEI ad-hoc fee", on_failure="needs_attention")
async def sei_fee(
    ctx: RunContext, *, account_id: int, instruction_uid: str | None = None
) -> dict[str, Any]:
    # Money out: a one-time fee at SEI. Same safety as a withdrawal — a refusal parks, no retry.
    from app.integrations.sei.actions import sei_adhoc_fee as handler

    return await _run_handler(ctx, handler, account_id=account_id, instruction_uid=instruction_uid)


@action(
    key="morgan-lloyd.record_contribution",
    name="Record contribution at Morgan Lloyd",
    on_failure="needs_attention",
)
async def ml_record_contribution(
    ctx: RunContext, *, account_id: int, uid: str | None = None
) -> dict[str, Any]:
    from app.actions.money import record_contribution

    return await _adapt(
        ctx,
        record_contribution,
        {"account_id": account_id, "uid": uid},
        skip_message="Already recorded at Morgan Lloyd.",
    )


@action(
    key="morgan-lloyd.record_transfer",
    name="Record transfer at Morgan Lloyd",
    on_failure="needs_attention",
)
async def ml_record_transfer(
    ctx: RunContext, *, account_id: int, uid: str | None = None
) -> dict[str, Any]:
    from app.actions.money import record_transfer

    return await _adapt(
        ctx,
        record_transfer,
        {"account_id": account_id, "uid": uid},
        skip_message="Already recorded at Morgan Lloyd.",
    )


@action(
    key="contribution.lodge",
    name="Lodge contribution",
    subject_kinds=("contribution",),
    on_failure="needs_attention",
    meta={
        **_MONEY_META,
        # Internal leaf: ``transaction.payment`` is the firm-facing money-in action; it calls this
        # for a contribution-expectation source. Kept in the catalogue's BY_KEY for run history (it
        # is in ``_UNLISTED``), but firms configure the policy on ``transaction.payment``.
        "catalogue": False,
        "category": "money_movement",
        "kind": "leaf",
        "providers": ["sei", "seccl"],
        "event_type": "contribution.lodged",
        "reads": "The contribution and its payment expectation.",
    },
)
async def contribution_lodge(ctx: RunContext, *, uid: str) -> dict[str, Any]:
    """Tell the account's custodian to expect a booked contribution. The handler picks the rail
    (a Seccl portfolio-transaction group, a SEI Anticipated Cash Receipt) and returns a report;
    its status is mapped onto baton's outcomes. ``skipped`` (the custodian has no lodge rail) is
    not a failure; anything short of ``instructed`` parks the run for a person."""
    from app.actions.attempt import OperationError
    from app.actions.money import lodge_contribution

    try:
        attempt = await lodge_contribution(
            ctx.session, payload={"tenant_id": ctx.tenant_id, "uid": uid}, subjects=[]
        )
    except OperationError as exc:
        if exc.permanent:
            raise Permanent(str(exc)) from exc
        raise Retry(str(exc)) from exc
    ctx.evidence(
        method=attempt.method,
        url=attempt.url,
        request=attempt.request,
        response=attempt.response,
        status_code=attempt.status_code,
    )
    report = attempt.response if isinstance(attempt.response, dict) else {}
    status_word = report.get("status")
    if status_word == "instructed":
        return cast(dict[str, Any], report)
    if status_word == "skipped":
        raise Skip(str(report.get("detail") or "No contribution lodge for this custodian."))
    raise Permanent(str(report.get("detail") or status_word or "The custodian refused the lodge."))


# ---- composites -------------------------------------------------------------


async def _send(
    ctx: RunContext, *, account_id: int, instruction_uid: str | None, kind: str, verb: str
) -> dict[str, Any]:
    book = await ctx.run(load_account_book, account_id=account_id)
    custodian = book["custodian"]
    if custodian in ("seccl", "sei"):
        sent = await ctx.run(
            f"{custodian}.{kind}", account_id=account_id, instruction_uid=instruction_uid
        )
        # A Morgan Lloyd-administered SIPP records the money-in with the pension administrator too —
        # the second leg transaction.transfer already runs. Payment only: a withdrawal or fee is not
        # a contribution. The leaf is migrated, so this is no longer deferred.
        if kind == "payment" and book.get("scheme_administrator") == "morgan-lloyd":
            await ctx.run(
                "morgan-lloyd.record_contribution",
                account_id=account_id,
                uid=instruction_uid,
            )
        return cast(dict[str, Any], sent)
    raise Permanent(f"{verb} is not available for {custodian}.")


@action(
    key="transaction.payment",
    name="Payment",
    subject_kinds=("account", "contribution"),
    meta={
        **_MONEY_META,
        # event_type stays the historical word: the engine does not emit it yet (hooks.on_event is a
        # no-op), and the money/contribution audit events are written by the doors, so renaming it
        # would only churn the event registry for no behaviour.
        "event_type": "payment.one_off_instructed",
        "reads": "A payment or contribution booked on the account, or the opening cash.",
    },
)
async def transaction_payment(
    ctx: RunContext,
    *,
    account_id: int | None = None,
    instruction_uid: str | None = None,
    contribution_uid: str | None = None,
) -> dict[str, Any]:
    """The one money-in action, every case. A plain payment and an ISA/SIPP contribution are the
    same custodian call; the contribution just adds tax year and category. Two sources, one face:
    a contribution expectation (the contributions workbench) runs the internal lodge leaf,
    which owns the tax-year/category shape; a booked instruction (the money drawer, the journey's
    opening cash) sends the custodian payment leaf. A SIPP administered by Morgan Lloyd also records
    the contribution there as a second child of the same run."""
    if contribution_uid is not None:
        return cast(dict[str, Any], await ctx.run("contribution.lodge", uid=contribution_uid))
    if account_id is None:
        raise Permanent("A payment needs a booked instruction (account) or a contribution.")
    return await _send(
        ctx,
        account_id=account_id,
        instruction_uid=instruction_uid,
        kind="payment",
        verb="transaction.payment",
    )


@action(
    key="transaction.withdrawal",
    name="Withdrawal",
    subject_kinds=("account",),
    meta={
        **_MONEY_META,
        "providers": ["sei", "seccl"],
        "event_type": "withdrawal.one_off_instructed",
        "reads": "The withdrawal already booked on the account.",
    },
)
async def transaction_withdrawal(
    ctx: RunContext, *, account_id: int, instruction_uid: str | None = None
) -> dict[str, Any]:
    return await _send(
        ctx,
        account_id=account_id,
        instruction_uid=instruction_uid,
        kind="withdrawal",
        verb="transaction.withdrawal",
    )


@action(
    key="transaction.fee",
    name="Ad-hoc fee",
    subject_kinds=("account",),
    meta={
        **_MONEY_META,
        "category": "fees",
        "providers": ["sei", "seccl"],
        "event_type": "fee.charged",
        "reads": "The ad-hoc fee already booked on the account.",
    },
)
async def transaction_fee(
    ctx: RunContext, *, account_id: int, instruction_uid: str | None = None
) -> dict[str, Any]:
    return await _send(
        ctx,
        account_id=account_id,
        instruction_uid=instruction_uid,
        kind="fee",
        verb="transaction.fee",
    )


@action(
    key="transaction.transfer",
    name="Create transfer",
    subject_kinds=("account",),
    meta={
        **_MONEY_META,
        "event_type": "transfer.created",
        "reads": "The transfer already raised on the account.",
    },
)
async def transaction_transfer(
    ctx: RunContext, *, account_id: int, uid: str | None = None, leg: str = "raise"
) -> dict[str, Any]:
    # Two legs. ``raise`` sends the transfer and, for a Morgan Lloyd SIPP, records it there; a SEI
    # SIPP's custodian leaf skips until Morgan Lloyd is done. ``sei`` is the later SEI receipt of
    # that SIPP — custodian only, no second Morgan Lloyd record.
    book = await ctx.run(load_account_book, account_id=account_id)
    custodian = book["custodian"]
    if custodian not in ("seccl", "sei"):
        raise Permanent(f"transaction.transfer is not available for {custodian}.")
    placed = await ctx.run(f"{custodian}.transfer", account_id=account_id, uid=uid)
    if leg != "sei" and book.get("scheme_administrator") == "morgan-lloyd":
        await ctx.run("morgan-lloyd.record_transfer", account_id=account_id, uid=uid)
    return cast(dict[str, Any], placed)


# ---- regular movements (Seccl standing orders) ------------------------------


@action(key="seccl.regular_payment", name="Seccl regular payment", on_failure="needs_attention")
async def seccl_regular_payment(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    from app.integrations.seccl.actions import seccl_regular_payment as handler

    return await _adapt(ctx, handler, {"account_id": account_id})


@action(
    key="seccl.regular_withdrawal",
    name="Seccl regular withdrawal",
    on_failure="needs_attention",
)
async def seccl_regular_withdrawal(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    from app.integrations.seccl.actions import seccl_regular_withdrawal as handler

    return await _adapt(ctx, handler, {"account_id": account_id})


@action(
    key="payment.regular",
    name="Regular payment",
    subject_kinds=("account",),
    meta={
        **_MONEY_META,
        "providers": ["seccl"],
        "event_type": "payment.regular_set",
        "reads": "The account's standing payment-in schedule.",
    },
)
async def payment_regular(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    book = await ctx.run(load_account_book, account_id=account_id)
    if book["custodian"] != "seccl":
        raise Permanent(f"payment.regular is not available for {book['custodian']}.")
    return cast(dict[str, Any], await ctx.run("seccl.regular_payment", account_id=account_id))


@action(
    key="withdrawal.regular",
    name="Regular withdrawal",
    subject_kinds=("account",),
    meta={
        **_MONEY_META,
        "providers": ["seccl"],
        "event_type": "withdrawal.regular_set",
        "reads": "The account's standing withdrawal schedule.",
    },
)
async def withdrawal_regular(ctx: RunContext, *, account_id: int) -> dict[str, Any]:
    book = await ctx.run(load_account_book, account_id=account_id)
    if book["custodian"] != "seccl":
        raise Permanent(f"withdrawal.regular is not available for {book['custodian']}.")
    return cast(dict[str, Any], await ctx.run("seccl.regular_withdrawal", account_id=account_id))


# ---- Morgan Lloyd recording (the record-to-Morgan-Lloyd doors) --------------


@action(
    key="contribution.record",
    name="Record contribution at Morgan Lloyd",
    subject_kinds=("account",),
    meta={
        **_MONEY_META,
        "category": "contributions",
        "providers": ["morgan-lloyd"],
        "event_type": "contribution.recorded",
        "reads": "The contribution, recorded on the client's Morgan Lloyd pension.",
    },
)
async def contribution_record(
    ctx: RunContext, *, account_id: int, uid: str | None = None
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        await ctx.run("morgan-lloyd.record_contribution", account_id=account_id, uid=uid),
    )


@action(
    key="transfer.record",
    name="Record transfer at Morgan Lloyd",
    subject_kinds=("account",),
    meta={
        **_MONEY_META,
        "category": "contributions",
        "providers": ["morgan-lloyd"],
        "event_type": "transfer.recorded",
        "reads": "The transfer, recorded on the client's Morgan Lloyd pension.",
    },
)
async def transfer_record(
    ctx: RunContext, *, account_id: int, uid: str | None = None
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        await ctx.run("morgan-lloyd.record_transfer", account_id=account_id, uid=uid),
    )
