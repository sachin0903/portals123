"""The statement view of ``platform.transactions`` — direction, family, running total, totals.

A book listing of transactions is a *ledger*, and a ledger has to answer three things the fact
table does not: which way did the money go, what kind of movement was it, and what was the
balance after it. This module owns those three answers so the router stays a query.

**Direction cannot be read off ``amount``.** The conformed column is the provider's own gross
figure and the providers disagree about its sign: SEI publishes a signed amount (its
``DEBIT_CREDIT_FLAG`` columns become a ``-1``/``+1`` multiplier in
``services/sei/models/platform/transactions.sql``), while Seccl publishes a positive magnitude
and expresses direction as ``movement_type`` In/Out, which the conform step folds into the
canonical ``type`` and then discards. So a listing that rendered ``amount`` as signed money
showed every Seccl withdrawal as a credit — and a running total built on it would walk the wrong
way. The canonical ``type`` IS provider-agnostic (that is the point of the conformed layer), so
direction is derived from it and applied to ``abs(amount)``: correct for both sign conventions.

``corporate_action`` and ``adjustment`` are deliberately **directionless**. A share reorganisation
or a correcting entry can go either way and nothing in the conformed row says which, so their
published sign is passed through untouched rather than guessed at — the same rule the merge
policy applies to an unmapped transaction type (a counted reject, never a guess).

**The running total is a cumulative cash movement, not a balance.** We hold no opening balance:
the feeds start at the day the custodian was connected, not at the day the account was opened.
Anchoring the newest row to ``platform.accounts.value``'s cash figure would produce a column that
looks like a bank balance and is wrong by whatever the feed does not carry. So it accumulates
from the earliest row we hold, and the surface labels it "Running total" and says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

#: Canonical ``platform.transaction_type`` members that put money INTO the account's cash.
#: ``sell`` is a credit because the ledger tracks CASH: selling an instrument settles as cash in.
CREDIT_TYPES: tuple[str, ...] = ("deposit", "transfer_in", "sell", "dividend", "interest")

#: …and the ones that take cash out. ``buy`` is a debit for the same reason ``sell`` is a credit.
DEBIT_TYPES: tuple[str, ...] = ("withdrawal", "transfer_out", "buy", "fee", "tax")

#: Types whose direction the conformed row does not state. Their published sign is kept as-is.
DIRECTIONLESS: tuple[str, ...] = ("corporate_action", "adjustment")

#: The coarse grouping a reader filters and totals by. Finer than "in or out" and much coarser
#: than the twelve canonical types: an adviser scanning a statement wants "show me the charges",
#: not "show me the rows typed `fee` and separately the rows typed `tax`". Dividends and interest
#: are one family for the same reason — both are income the account earned rather than money the
#: client paid in, which is the distinction a contribution report turns on.
FAMILIES: dict[str, str] = {
    "deposit": "money_in",
    "transfer_in": "money_in",
    "withdrawal": "money_out",
    "transfer_out": "money_out",
    "buy": "trade",
    "sell": "trade",
    "dividend": "income",
    "interest": "income",
    "fee": "charge",
    "tax": "charge",
    "corporate_action": "other",
    "adjustment": "other",
}

#: Every family, in the order a filter should offer them.
FAMILY_ORDER: tuple[str, ...] = ("money_in", "money_out", "trade", "income", "charge", "other")

#: Statuses that moved no money, so they must not move the running total. Compared lowercased
#: against ``platform.transactions.status``, which is the provider's own word (Seccl lowercases
#: its own vocabulary in its conform step and expresses a reversal as ``deleted``; SEI publishes
#: none at all, because a row in its report has settled). A status we do not recognise COUNTS —
#: the alternative is a ledger that silently drops a movement because a custodian invented a word.
VOID_STATUSES: tuple[str, ...] = ("failed", "cancelled", "canceled", "rejected", "deleted")


def family(transaction_type: str | None) -> str:
    """The family a canonical type belongs to; ``other`` for anything unrecognised."""
    return FAMILIES.get(transaction_type or "", "other")


def types_for(families: Sequence[str] | None, selected: Sequence[str] | None) -> list[str] | None:
    """Canonical types a family filter allows, intersected with an explicit type filter.

    No family filter leaves ``selected`` alone. A family and a type that do not overlap
    is an empty list: the listing matches nothing. It must not drop the type and show
    the whole family.
    """
    if not families:
        return None if selected is None else list(selected)
    allowed = [kind for kind, group in FAMILIES.items() if group in families]
    if not selected:
        return allowed
    wanted = set(allowed)
    return [kind for kind in selected if kind in wanted]


def direction(transaction_type: str | None) -> int | None:
    """``1`` for cash in, ``-1`` for cash out, ``None`` where the type does not say."""
    if transaction_type in CREDIT_TYPES:
        return 1
    if transaction_type in DEBIT_TYPES:
        return -1
    return None


def signed_amount(transaction_type: str | None, amount: float | None) -> float | None:
    """``amount`` with the sign the canonical type implies — see the module docstring."""
    if amount is None:
        return None
    sign = direction(transaction_type)
    if sign is None:
        return float(amount)
    return sign * abs(float(amount))


def counts_towards_balance(status: str | None) -> bool:
    """False for a movement that never happened (a reversal, a rejection, a cancellation)."""
    return (status or "").strip().lower() not in VOID_STATUSES


#: The signed amount, as SQL. Bound parameters rather than an interpolated CASE, so the type
#: vocabulary travels as data and there is no assembled string to reason about — the same shape
#: the router's nullable ``:account_id`` filter uses.
SIGNED_SQL = (
    "CASE WHEN t.type::text = ANY (:credit_types) THEN abs(t.amount)::float8 "
    "     WHEN t.type::text = ANY (:debit_types) THEN -abs(t.amount)::float8 "
    "     ELSE t.amount::float8 END"
)

#: What a row contributes to the running total: its signed amount, or nothing when the movement
#: was voided. Written against the OUTER query's alias, because the running total is a window over
#: the projected rows.
BALANCE_EFFECT_SQL = (
    "CASE WHEN lower(COALESCE(src.status, '')) = ANY (:void_statuses) "
    "     THEN 0::float8 ELSE COALESCE(src.signed_amount, 0)::float8 END"
)


def sql_params() -> dict[str, Any]:
    """The bound parameters ``SIGNED_SQL``/``BALANCE_EFFECT_SQL`` read."""
    return {
        "credit_types": list(CREDIT_TYPES),
        "debit_types": list(DEBIT_TYPES),
        "void_statuses": list(VOID_STATUSES),
    }


def totals(rows: Sequence[dict[str, Any]]) -> dict[str, float | int]:
    """In/out/net/fees/income over a set of rows — the summary strip above a statement.

    Computed over the MATCH set (everything the search and filters left), not the visible page:
    "£12,400 in" that only counts the fifty rows you happen to be looking at is a figure nobody
    can use. Voided movements are excluded on the same rule the running total applies, so a
    cancelled withdrawal does not appear in "out".

    ``fees`` and ``income`` are magnitudes (a charge reads as "£412 in charges", not "-£412"),
    and both are already counted in ``out``/``in`` — they are a breakdown of the flows, not two
    more of them.
    """
    money_in = money_out = fees = income = 0.0
    for row in rows:
        if not counts_towards_balance(row.get("status")):
            continue
        amount = row.get("signed_amount")
        if amount is None:
            continue
        value = float(amount)
        if value >= 0:
            money_in += value
        else:
            money_out += -value
        group = row.get("family") or family(row.get("type"))
        if group == "charge":
            fees += abs(value)
        elif group == "income":
            income += abs(value)
    return {
        "in": round(money_in, 2),
        "out": round(money_out, 2),
        "net": round(money_in - money_out, 2),
        "fees": round(fees, 2),
        "income": round(income, 2),
        "count": len(rows),
    }


# ── The headline breakdown ───────────────────────────────────────────────────────────────────────
#
# The ten figures a statement is READ for, and the ones a client's own statement from the custodian
# shows: what went in, what came out, what it earned, what it cost, what moved between the client's
# own accounts, what the disposals realised, what the holdings have gained, and the balances either
# side of all of it.
#
# **This is a GROUP BY over the ledger, not a second read of a vendor report.** Seccl publishes its
# own bucketing as reference data (`/static/transactiontype`'s `accountReportGroup`, the grouping
# its own `/account/report` endpoint reports against) and the provider layer conforms it onto
# `platform.transactions.report_group_kind`. Deriving it here rather than reading the report back is
# what makes a figure CLICKABLE: the rows behind £3.36 of charges are the rows whose group is
# Charges, so the same value that produced the figure filters the table to it. A report could only
# ever be a number on a page.
#
# The identity holds: `closing = opening + every movement + the gains`. Checked against a saved
# `/account/report` response, where it reproduces the custodian's own closing value — and where the
# custodian's own STATED totals miss it by exactly its equalisation double-count — which is the
# reason this derives the figures rather than trusting them.

#: The custodian's own group names, mapped onto the buckets a statement shows. Several vendor names
#: collapse: a product transfer and a cash transfer are both "money from another scheme" as far as a
#: reader is concerned, and the distinction is on the row rather than in the headline.
#:
#: `Transactions Realised` / `Investments Realised in Period` are deliberately NOT here — see
#: `TRADE_FAMILY` below, which is what actually excludes them, and why.
BREAKDOWN_GROUPS: dict[str, str] = {
    "Payments In": "payments_in",
    "Payments Out": "payments_out",
    "Transfers In": "transfers_in",
    "Transfers Out": "transfers_out",
    "Product Transfers In": "transfers_in",
    "Product Transfers Out": "transfers_out",
    "Internal Transfers In": "internal_in",
    "Internal Transfers Out": "internal_out",
    "Charges": "charges",
    "Income": "income",
}

#: The buckets a movement can land in, in the order the statement lists them — the order the
#: custodian's own report uses, and therefore the order a client will have seen elsewhere.
BREAKDOWN_ORDER: tuple[str, ...] = (
    "payments_in",
    "payments_out",
    "income",
    "transfers_in",
    "transfers_out",
    "charges",
    "internal_in",
    "internal_out",
)


#: A TRADE is not a statement movement, and this is the correction that makes the closing balance
#: right. Buying and selling moves value between an account's cash and its investments; the account
#: is worth the same either side of it. So a disposal's PROCEEDS must not be added to the balance —
#: only the GAIN it realised, which is `realised` and comes from the cost layer. Counted as a
#: movement too, a £700 sale of units that cost £500 adds £900 to the closing balance, not £200.
#:
#: Keyed on the CANONICAL family rather than on the vendor's group, deliberately. The canonical
#: `type` is ours and provider-agnostic, so this holds for a custodian that publishes no grouping at
#: all, and it is robust to the vendor filing a trade under a name we have not seen. Excluding the
#: two `… Realised` group names instead would leave a BUY counted as an unclassified movement,
#: subtracting the whole purchase: Seccl groups a buy as `N/A`, which the conform step makes NULL.
TRADE_FAMILY = "trade"


def breakdown_bucket(report_group_kind: str | None) -> str | None:
    """The statement bucket a movement belongs to, or ``None`` where the custodian said nothing.

    A NULL group is not an error and not zero: SEI and Morgan Lloyd publish no such grouping at all,
    and Seccl leaves it NULL where its own reference data is ambiguous or silent (see
    `services/seccl/models/platform/transactions.sql`). Those rows are counted in `unclassified`
    rather than silently absorbed into a bucket, because a breakdown that quietly omits movements
    reads as a complete picture and is not one.
    """
    if not report_group_kind:
        return None
    return BREAKDOWN_GROUPS.get(report_group_kind)


def breakdown(
    rows: Sequence[dict[str, Any]],
    *,
    opening: float = 0.0,
    retained: float | None = None,
) -> dict[str, Any]:
    """The ten-figure statement breakdown over a set of rows.

    ``opening`` is the cumulative position before the first row. It is 0 for an unbounded read,
    which is the honest answer: the feed starts the day the custodian was connected, so with no date
    filter there IS no earlier position to carry in. A date-scoped read passes the prior cumulative.

    ``retained`` is unrealised gain on the holdings STILL HELD, which is not a transaction fact at
    all — it comes from the positions (the same source the custodian's own report sums). ``None``
    means "not supplied", and it is then absent from the figures rather than reported as zero: a
    client with £180 of unrealised gain and a caller that did not fetch it must not be told nil.

    Each bucket carries its `filter` — the value that narrows the listing to the rows behind it — so
    the surface never has to know the mapping. `realised`, the two balances and `retained` carry
    none: a balance has no row set, and a realised gain is an attribute of rows already counted
    elsewhere, so filtering on it would double-count the disposals it came from.
    """
    buckets: dict[str, float] = dict.fromkeys(BREAKDOWN_ORDER, 0.0)
    realised = 0.0
    unclassified = 0.0
    unclassified_count = 0

    for row in rows:
        if not counts_towards_balance(row.get("status")):
            continue
        amount = row.get("signed_amount")
        value = 0.0 if amount is None else float(amount)

        # A realised gain is an attribute of the disposal, not a movement of its own. Summed
        # separately, and it is the ONLY thing a disposal contributes (see `TRADE_FAMILY`).
        gain = row.get("realised_gain")
        if gain is not None:
            realised += float(gain)

        # A TRADE moves value between cash and investments and changes the account's worth by
        # nothing. Its gain has just been counted; its proceeds must not be.
        if (row.get("family") or family(row.get("type"))) == TRADE_FAMILY:
            continue

        bucket = breakdown_bucket(row.get("report_group_kind"))
        if bucket is None:
            # Counted and NAMED. A movement nobody can bucket still moved money, and hiding it would
            # make the figures add up to a closing balance the account does not have.
            unclassified += value
            unclassified_count += 1
            continue
        buckets[bucket] += value

    # An accumulation distribution is TWO facts in one row — the equalisation half is a return of
    # capital and the rest is income — but the ROW is counted once, under whichever group the
    # custodian assigned it. Splitting it here would need the row in two buckets and would double
    # the amount; the drawer states the split per movement instead, where it can be read.

    figures = {name: round(buckets[name], 2) for name in BREAKDOWN_ORDER}
    figures["realised"] = round(realised, 2)
    figures["unclassified"] = round(unclassified, 2)
    figures["unclassified_count"] = unclassified_count
    figures["opening"] = round(opening, 2)

    closing = opening + sum(buckets.values()) + realised + unclassified
    if retained is not None:
        figures["retained"] = round(retained, 2)
        closing += retained
    figures["closing"] = round(closing, 2)
    return figures
