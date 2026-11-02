"""Client-raised money movement — the direct / execution-only "Payment" and "Withdraw" journeys.

The adviser platform raises instructions through ``POST /firm/contacts/{id}/instructions``
(routers/firm.py) with ``instructions:write``. A client has no such permission and must not: a
client may move money on an account they SERVE THEMSELVES on (``investment_capacity =
execution_only``) or in a book whose classification carries ``capabilities.money_movement``
(docs/client-classifications.md). This module is the client-facing door onto the SAME control —
``app.instructions.create_instruction`` with its cumulative funding gate, its account/client
status checks and its outbound row — reached through the client's own RLS session for everything
that decides *what they may see* and a ``system_transaction`` for the one write the firm session
would have made.

**Two kinds reach a client.** A payment in is an ``expected_in`` (money the firm should expect,
not money that has moved). A withdrawal is a cash ``withdrawal`` when the account holds the cash,
and a ``model_withdrawal`` (a disinvestment) when it does not and the account is run to a model;
an account with neither is refused with the reason, because selling named holdings is an
instruction the client would have to give in words, not a number.

**The safety check.** A withdrawal over an absolute amount, over a share of the account, or over
a share of the whole portfolio is still RAISED — pending, like every other instruction — but is
flagged for a member of staff to speak to the client before it goes anywhere: it is NOT dispatched
to the custodian, the instruction's note names the flags, and (when the client has a lead
adviser) a task is raised for them. The thresholds below are the platform's defaults.

TODO(firm-level config): the thresholds, the expected-in window, the "lands by" working days and
the firm's PAY-IN bank details (account name / sort code / account number the client is told to
send a payment to) all belong on the tenant — a firm's own policy, set in the platform portal.
Nothing holds them yet, so the defaults stand for every firm and a deposit's "done" screen gives
the client a reference and tells them the firm will confirm where to send the money. Lift them
to ``auth.tenants`` (or a ``platform.tenant_policy`` row) when that surface is built; every reader
goes through ``policy()`` so the swap is one function.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import bank_accounts
from app import instructions as instructions_mod
from app.db import system_transaction

logger = logging.getLogger(__name__)

#: What a client may ask for. The route segment the portal uses (``deposit`` / ``withdraw``) is
#: the portal's; these are the API's words.
KINDS = frozenset({"deposit", "withdrawal"})

#: Products whose contributions are capped by a tax-year allowance the account row tracks
#: (`platform.accounts.allowance_*`). Matched on the product TYPE code, upper-cased.
_ALLOWANCE_PRODUCTS = frozenset({"ISA", "JISA", "LISA"})


@dataclass(frozen=True)
class Policy:
    """The firm's money-movement policy. Platform defaults until the firm-level surface exists
    (module note)."""

    #: A withdrawal at or above this is flagged for a safety check.
    review_absolute_gbp: float = 10_000.0
    #: ...or taking more than this share of the account's value.
    review_account_share: float = 0.5
    #: ...or more than this share of everything the client holds with the firm.
    review_portfolio_share: float = 0.1
    #: How long a payment in is expected within (the ``expected_in`` due date).
    expected_in_days: int = 5
    #: Working days until a withdrawal is in the client's bank — BACS after settlement.
    withdrawal_working_days: int = 7
    #: Working days until a received payment is invested.
    invest_within_working_days: int = 3
    #: Where a client sends a payment. ``None`` ⇒ not configured — the portal says so rather
    #: than printing an account number nobody set.
    pay_in: dict[str, str] | None = None


_DEFAULT_POLICY = Policy()


def policy(tenant_id: int | None = None) -> Policy:  # noqa: ARG001 — the firm-level seam
    """The policy that applies to one firm. One reader, so lifting the values to the tenant is a
    change here and nowhere else."""
    return _DEFAULT_POLICY


@dataclass(frozen=True)
class Flag:
    code: str
    label: str


@dataclass(frozen=True)
class Decision:
    """Everything the review step shows and the raise re-checks — computed once, from the same
    inputs, so the client is never shown one answer and given another."""

    kind: str
    amount: float
    instruction_kind: str | None
    refusal: str | None
    flags: list[Flag] = field(default_factory=list)
    from_cash: float = 0.0
    from_investments: float = 0.0

    @property
    def review_required(self) -> bool:
        return bool(self.flags)


def working_days_from(start: dt.date, days: int) -> dt.date:
    """``start`` plus ``days`` Monday–Friday days. Bank holidays are not known here (the
    custodian's calendar is), so this is the earliest the money could land, stated as "by"."""
    day = start
    remaining = max(0, int(days))
    while remaining > 0:
        day += dt.timedelta(days=1)
        if day.weekday() < 5:
            remaining -= 1
    return day


def money(n: float) -> str:
    return f"£{n:,.0f}" if float(n).is_integer() else f"£{n:,.2f}"


def _iso(value: object) -> str | None:
    """A date or timestamp from a row, as ISO text. ``create_instruction`` types its row as
    ``dict[str, object]``, so the date columns have to be narrowed before ``isoformat``."""
    if isinstance(value, dt.date):
        return value.isoformat()
    return value if isinstance(value, str) else None


def review_flags(
    amount: float,
    *,
    account_value: float | None,
    portfolio_value: float | None,
    pol: Policy = _DEFAULT_POLICY,
) -> list[Flag]:
    """Why a withdrawal is flagged, in the order the client reads them. Unknown values (an
    unvalued account, an unvalued book) cannot trip a share rule — a missing denominator is not
    a hundred-percent withdrawal — but the absolute rule always runs."""
    flags: list[Flag] = []
    if amount >= pol.review_absolute_gbp:
        flags.append(Flag("absolute", f"Over {money(pol.review_absolute_gbp)} threshold"))
    if account_value and account_value > 0 and amount / account_value > pol.review_account_share:
        flags.append(Flag("account_share", f"Over {pol.review_account_share:.0%} of this account"))
    if (
        portfolio_value
        and portfolio_value > 0
        and amount / portfolio_value > pol.review_portfolio_share
    ):
        flags.append(
            Flag("portfolio_share", f"Over {pol.review_portfolio_share:.0%} of total portfolio")
        )
    return flags


def withdrawal_plan(
    amount: float,
    *,
    cash_available: float | None,
    invested_available: float | None,
    has_model: bool,
) -> tuple[str | None, float, float, str | None]:
    """``(instruction_kind, from_cash, from_investments, refusal)`` for one withdrawal.

    Cash first: an account holding the money pays it as a cash ``withdrawal`` and sells nothing.
    Short of cash, an account run to a model raises a ``model_withdrawal`` for the WHOLE amount
    (the model is sold down pro rata and the custodian pays from the proceeds; the funding gate
    checks it against what is invested), and the split is reported so the client is told what
    will be sold. An account with neither is refused with the figure that IS available."""
    if cash_available is not None and amount <= cash_available:
        return "withdrawal", round(amount, 2), 0.0, None
    cash = max(0.0, cash_available or 0.0)
    unconfirmed = (
        "The money available on this account can't be confirmed just now, so nothing was "
        "recorded. Please try again shortly."
    )
    if has_model:
        # FAIL CLOSED here too. The raise's funding gate refuses a sell-down it cannot measure, so
        # a review step built on an unknown would only be refused at the last click.
        if invested_available is None:
            return None, 0.0, 0.0, unconfirmed
        if amount > invested_available + cash:
            return (
                None,
                0.0,
                0.0,
                f"Only {money(invested_available + cash)} can be withdrawn from this account "
                "just now.",
            )
        return "model_withdrawal", round(cash, 2), round(amount - cash, 2), None
    if cash_available is None:
        return None, 0.0, 0.0, unconfirmed
    return (
        None,
        0.0,
        0.0,
        f"Only {money(cash)} is held as cash on this account. Taking out more means selling "
        "investments, which your firm will need to arrange with you.",
    )


def deposit_refusal(
    amount: float, *, product_type: str | None, allowance_remaining: float | None
) -> str | None:
    """A payment into an allowance-capped wrapper may not exceed what is left of this year's
    allowance. Unknown allowance ⇒ no refusal here; the custodian applies its own cap."""
    if (product_type or "").strip().upper() not in _ALLOWANCE_PRODUCTS:
        return None
    if allowance_remaining is None:
        return None
    if amount > allowance_remaining + 1e-9:
        return (
            f"You can put in {money(max(allowance_remaining, 0.0))} more before you reach this "
            "year's allowance."
        )
    return None


def decide(
    *,
    kind: str,
    amount: float,
    account_value: float | None,
    portfolio_value: float | None,
    cash_available: float | None,
    invested_available: float | None,
    has_model: bool,
    product_type: str | None,
    allowance_remaining: float | None,
    pol: Policy = _DEFAULT_POLICY,
) -> Decision:
    """The one decision both ``preview`` and ``raise_instruction`` make."""
    if kind not in KINDS:
        return Decision(kind, amount, None, "Choose a payment or a withdrawal.")
    if amount is None or not math.isfinite(amount) or amount <= 0:
        return Decision(kind, amount or 0.0, None, "Enter an amount greater than zero.")
    if kind == "deposit":
        refusal = deposit_refusal(
            amount, product_type=product_type, allowance_remaining=allowance_remaining
        )
        return Decision("deposit", amount, None if refusal else "expected_in", refusal)
    instruction_kind, from_cash, from_inv, refusal = withdrawal_plan(
        amount,
        cash_available=cash_available,
        invested_available=invested_available,
        has_model=has_model,
    )
    flags = (
        []
        if refusal
        else review_flags(
            amount, account_value=account_value, portfolio_value=portfolio_value, pol=pol
        )
    )
    return Decision("withdrawal", amount, instruction_kind, refusal, flags, from_cash, from_inv)


# ── reads, through the client's own session ────────────────────────────────────────────────────


async def account_for_client(session: AsyncSession, account_uid: str) -> dict[str, Any] | None:
    """The account as the CLIENT may see it (RLS), with the facts the journey needs. ``None`` when
    it is not theirs — the same answer as an account that does not exist."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.uid, a.tenant_id, a.contact_id, a.name, "
                    "       a.status::text AS status, "
                    "       a.value::float8 AS value, a.currency, a.model_id, "
                    "       a.allowance_remaining::float8 AS allowance_remaining, "
                    "       p.product_type, p.name AS product_name, "
                    "       NULLIF(a.investment_capacity, '') AS service_type "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "WHERE a.uid = :uid"
                ),
                {"uid": account_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def portfolio_value(session: AsyncSession, contact_id: int | None) -> float | None:
    """What the client holds across their open accounts with this firm (RLS), for the portfolio
    share rule. ``None`` for an unvalued book."""
    if contact_id is None:
        return None
    total = (
        await session.execute(
            text(
                "SELECT SUM(value)::float8 FROM platform.accounts "
                "WHERE contact_id = :c AND status NOT IN ('draft','closed')"
            ),
            {"c": contact_id},
        )
    ).scalar_one_or_none()
    return None if total is None else round(float(total), 2)


def bank_label(entry: dict[str, Any] | None) -> str | None:
    """ "Barclays •••• 2241" — enough for the client to recognise their own account, never the
    whole number."""
    if not entry:
        return None
    number = str(entry.get("account_number") or entry.get("iban") or "").replace(" ", "")
    name = str(entry.get("bank_name") or entry.get("name") or "Bank account").strip()
    return f"{name} •••• {number[-4:]}" if len(number) >= 4 else name


async def _availability(
    *, tenant_id: int, account_id: int, external_id: Any, provider: Any
) -> tuple[float | None, float | None]:
    """``(cash_available, invested_available)`` net of pending claims, from the SAME gate that
    decides the raise (``instructions._fundable``). ``None`` where the gate cannot tell."""
    cash: float | None = None
    invested: float | None = None
    async with system_transaction() as sysx:
        for kind in ("withdrawal", "model_withdrawal"):
            try:
                available, *_rest = await instructions_mod._fundable(  # noqa: SLF001
                    sysx,
                    tenant_id=tenant_id,
                    account_id=account_id,
                    external_id=external_id,
                    provider=provider,
                    kind=kind,
                )
            except ValueError as exc:
                logger.info(
                    "client money: availability unknown for account %s: %s", account_id, exc
                )
                continue
            if kind == "withdrawal":
                cash = available
            else:
                invested = available
    return cash, invested


async def _provider_link(tenant_id: int, account_id: int) -> tuple[Any, Any]:
    """The account's primary custodian link, the way ``create_instruction`` resolves it."""
    from app import providers

    async with system_transaction() as sysx:
        row = (
            (
                await sysx.execute(
                    text(
                        "SELECT l.external_id, l.provider FROM platform.links l "
                        "JOIN platform.accounts a ON a.id = l.entity_id "
                        "WHERE l.entity_type = 'account' AND l.entity_id = :a "
                        "  AND l.tenant_id = :t AND l.link_type = 'primary' "
                        "  AND l.provider = ANY(:custodians) "
                        "  AND (a.custodian IS NULL OR l.provider = a.custodian) "
                        "ORDER BY (l.provider = a.custodian) DESC NULLS LAST, l.provider LIMIT 1"
                    ),
                    {"a": account_id, "t": tenant_id, "custodians": list(providers.custodians())},
                )
            )
            .mappings()
            .one_or_none()
        )
    return (row["external_id"], row["provider"]) if row else (None, None)


async def preview(
    session: AsyncSession, *, kind: str, account_uid: str, amount: float
) -> dict[str, Any]:
    """What the review step shows. Raises ``LookupError`` for an account the client cannot see
    and ``ValueError`` for an account they may see but not move money on."""
    acct = await account_for_client(session, account_uid)
    if acct is None:
        raise LookupError("Account not found")
    if acct["status"] != "active":
        raise ValueError("No money can be moved on this account yet.")
    pol = policy(int(acct["tenant_id"]))
    book_value = await portfolio_value(session, acct["contact_id"])
    cash_available: float | None = None
    invested_available: float | None = None
    if kind == "withdrawal":
        external_id, provider = await _provider_link(int(acct["tenant_id"]), int(acct["id"]))
        cash_available, invested_available = await _availability(
            tenant_id=int(acct["tenant_id"]),
            account_id=int(acct["id"]),
            external_id=external_id,
            provider=provider,
        )
    decision = decide(
        kind=kind,
        amount=amount,
        account_value=acct["value"],
        portfolio_value=book_value,
        cash_available=cash_available,
        invested_available=invested_available,
        has_model=acct["model_id"] is not None,
        product_type=acct["product_type"],
        allowance_remaining=acct["allowance_remaining"],
        pol=pol,
    )
    bank_entry, _source = await bank_accounts.resolve_for_account(
        session, account_id=int(acct["id"]), direction="pay_to"
    )
    today = dt.date.today()
    value = acct["value"]
    is_allowance = (acct["product_type"] or "").strip().upper() in _ALLOWANCE_PRODUCTS
    return {
        "kind": decision.kind,
        "amount": round(float(amount), 2),
        "instruction_kind": decision.instruction_kind,
        "refusal": decision.refusal,
        "review_required": decision.review_required,
        "flags": [{"code": f.code, "label": f.label} for f in decision.flags],
        "account": {
            "uid": acct["uid"],
            "name": acct["name"] or acct["product_name"] or "Account",
            "product_type": acct["product_type"],
            "value": value,
            "has_model": acct["model_id"] is not None,
            "service_type": acct["service_type"],
        },
        "portfolio_value": book_value,
        "value_after": None
        if value is None
        else round(value + amount if kind == "deposit" else value - amount, 2),
        "share_of_account": None
        if not value or value <= 0 or kind == "deposit"
        else round(amount / value, 4),
        "share_of_portfolio": None
        if not book_value or book_value <= 0 or kind == "deposit"
        else round(amount / book_value, 4),
        "cash_available": cash_available,
        "invested_available": invested_available,
        "from_cash": decision.from_cash,
        "from_investments": decision.from_investments,
        "allowance_remaining": acct["allowance_remaining"] if is_allowance else None,
        "allowance_after": None
        if not is_allowance or acct["allowance_remaining"] is None or kind != "deposit"
        else round(float(acct["allowance_remaining"]) - amount, 2),
        "bank": bank_label(bank_entry),
        "lands_by": working_days_from(today, pol.withdrawal_working_days).isoformat()
        if kind == "withdrawal"
        else None,
        "invest_within_working_days": pol.invest_within_working_days if kind == "deposit" else None,
        "expected_by": (today + dt.timedelta(days=pol.expected_in_days)).isoformat()
        if kind == "deposit"
        else None,
        "pay_in": pol.pay_in,
    }


def _note(decision: Decision, *, from_cash: float, from_investments: float) -> str:
    """What the firm reads on the instruction row — who raised it and why it is flagged."""
    parts = ["Raised by the client in the portal."]
    if decision.instruction_kind == "model_withdrawal":
        parts.append(
            f"{money(from_cash)} from cash and {money(from_investments)} from investments."
        )
    if decision.flags:
        parts.append("Safety check: " + "; ".join(f.label for f in decision.flags) + ".")
        parts.append("Not sent to the custodian — speak to the client before releasing it.")
    return " ".join(parts)


async def _adviser(sysx: AsyncSession, *, tenant_id: int, contact_id: int) -> str | None:
    """The contact's adviser. `role = 'adviser'` is unique per contact (0245), so no tie-break
    ordering is needed."""
    row = (
        await sysx.execute(
            text(
                "SELECT ca.adviser_id FROM crm.client_advisers ca "
                "WHERE ca.tenant_id = :t AND ca.contact_id = :c AND ca.role = 'adviser'"
            ),
            {"t": tenant_id, "c": contact_id},
        )
    ).scalar_one_or_none()
    return str(row) if row else None


async def raise_instruction(
    session: AsyncSession,
    *,
    principal_user_id: str,
    kind: str,
    account_uid: str,
    amount: float,
) -> dict[str, Any]:
    """Raise the instruction the preview described. The preview is recomputed here — the client's
    screen is not trusted to say what the account can afford — and the write goes through
    ``instructions.create_instruction`` so every gate the adviser platform applies applies."""
    shown = await preview(session, kind=kind, account_uid=account_uid, amount=amount)
    if shown["refusal"]:
        raise ValueError(shown["refusal"])
    acct = await account_for_client(session, account_uid)
    if acct is None:  # preview would have raised; a race with a re-scope is a 404 too
        raise LookupError("Account not found")
    tenant_id = int(acct["tenant_id"])
    pol = policy(tenant_id)
    decision = Decision(
        kind=shown["kind"],
        amount=shown["amount"],
        instruction_kind=shown["instruction_kind"],
        refusal=None,
        flags=[Flag(f["code"], f["label"]) for f in shown["flags"]],
        from_cash=shown["from_cash"],
        from_investments=shown["from_investments"],
    )
    note = _note(decision, from_cash=decision.from_cash, from_investments=decision.from_investments)
    from app.actions.money import lodge_instruction, switched_off
    from app.actions.runs import ActionError, Held

    async with system_transaction() as sysx:
        if await switched_off(sysx, tenant_id=tenant_id, kind=str(decision.instruction_kind)):
            raise ActionError("This action is switched off.", status=409)
        created = await instructions_mod.create_instruction(
            sysx,
            tenant_id=tenant_id,
            created_by_workos=principal_user_id,
            kind=str(decision.instruction_kind),
            account_id=int(acct["id"]),
            amount=float(decision.amount),
            due_date=shown["expected_by"] if kind == "deposit" else None,
            note=note,
        )
        if decision.review_required:
            # Best-effort: the instruction IS the record; the task is the nudge.
            try:
                adviser = await _adviser(
                    sysx, tenant_id=tenant_id, contact_id=int(acct["contact_id"])
                )
                if adviser and acct["contact_id"] is not None:
                    from app.tasks import service as tasks_svc

                    await tasks_svc.create_task(
                        sysx,
                        tenant_id=tenant_id,
                        assignee_kind="user",
                        assignee_user_id=adviser,
                        title=f"Safety check: {money(decision.amount)} withdrawal",
                        category="generic",
                        description=note,
                        subject_type="contact",
                        subject_id=str(acct["contact_id"]),
                        # `system` is the CHECK's word for "raised by the platform, not a
                        # person" (0044); the instruction uid makes it idempotent.
                        source_kind="system",
                        source_ref=f"client_money:{created['uid']}",
                        priority="high",
                    )
            except Exception:  # noqa: BLE001 — never unwind a raised instruction over a nudge
                logger.exception("client money: could not raise the safety-check task")
            sent = None
        else:
            maker = (
                await sysx.execute(
                    text(
                        "SELECT id::text FROM auth.users "
                        "WHERE workos_user_id = :by AND tenant_id = :t LIMIT 1"
                    ),
                    {"by": principal_user_id, "t": tenant_id},
                )
            ).scalar_one_or_none()
            sent = await lodge_instruction(
                sysx,
                tenant_id=tenant_id,
                kind=str(created["kind"]),
                instruction_uid=str(created["uid"]),
                account_id=int(acct["id"]),
                contact_id=int(acct["contact_id"]) if acct.get("contact_id") is not None else None,
                requested_by=str(maker) if maker else None,
            )
    held = isinstance(sent, Held)
    return {
        "instruction": {
            "uid": created["uid"],
            "kind": created["kind"],
            "amount": created["amount"],
            "status": created["status"],
            "due_date": _iso(created.get("due_date")),
            "created_at": _iso(created.get("created_at")),
        },
        "review_required": decision.review_required,
        "held": held,
        "flags": shown["flags"],
        "preview": shown,
        "pay_in": pol.pay_in,
    }


async def list_pending(
    session: AsyncSession, contact_id: int | None = None
) -> list[dict[str, Any]]:
    """The client's own pending requests (RLS: ``account_scope``), newest first — what they can
    still cancel. ``contact_id`` narrows a multi-client login to the client on screen."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT i.uid, i.kind, i.amount::float8 AS amount, i.status, i.due_date, "
                    "       i.note, i.created_at, a.uid AS account_uid, "
                    "       COALESCE(a.name, p.name, 'Account') AS account_name "
                    "FROM platform.instructions i "
                    "JOIN platform.accounts a ON a.id = i.account_id "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "WHERE i.status = 'pending' "
                    "  AND i.kind IN ('expected_in', 'withdrawal', 'model_withdrawal') "
                    "  AND (CAST(:c AS int) IS NULL OR a.contact_id = CAST(:c AS int)) "
                    "ORDER BY i.created_at DESC, i.id DESC"
                ),
                {"c": contact_id},
            )
        )
        .mappings()
        .all()
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        note = str(r["note"] or "")
        out.append(
            {
                "uid": r["uid"],
                "kind": r["kind"],
                "amount": r["amount"],
                "status": r["status"],
                "due_date": r["due_date"].isoformat() if r["due_date"] else None,
                "created_at": r["created_at"].isoformat()
                if hasattr(r["created_at"], "isoformat")
                else r["created_at"],
                "account_uid": r["account_uid"],
                "account_name": r["account_name"],
                "review_required": "Safety check:" in note,
                "raised_by_client": note.startswith("Raised by the client"),
            }
        )
    return out


async def cancel(session: AsyncSession, *, uid: str) -> bool:
    """Cancel one of the client's own pending requests. Visibility is decided by the client's
    session (RLS) BEFORE the tenant-scoped write; an instruction they cannot see is not theirs
    to cancel, and reads as absent."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT i.tenant_id FROM platform.instructions i "
                    "WHERE i.uid = :u AND i.status = 'pending' "
                    "  AND i.note LIKE 'Raised by the client%'"
                ),
                {"u": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return False
    async with system_transaction() as sysx:
        return await instructions_mod.cancel_instruction(
            sysx, tenant_id=int(row["tenant_id"]), uid=uid
        )
