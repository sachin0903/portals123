"""Cost basis and realised gains, computed sequentially over an account's holding history.

**Why this cannot be a SQL model.** A holding's book cost is a running average: each acquisition
adds its consideration to the pool, each disposal removes units at the pool's average price, and
the average is therefore a function of every movement before it. That is a recurrence, not an
aggregation. A disposal's realised gain is `proceeds - the cost of the units that left`, so it
needs the pool as it stood at that moment — which no window function can give, because the value
being accumulated depends on its own previous output.

**And it cannot be read off the transaction either**, which is what the first attempt at this did.
Seccl publishes a `bookValue` per movement and the conformed layer was reporting `abs(proceeds) -
abs(bookValue)` as the realised gain. That agrees with the vendor's own `profitLoss` on the rows
where its `bookValue` is already the pooled figure, and disagrees everywhere else: on a GIA it
ignores share matching entirely (below), it has nothing to say when `bookValue` is absent, and it
cannot subtract an equalisation payment that was never income in the first place. A realised gain
is a CGT figure, so "close on the rows we checked" is not a standard it can be held to.

**SHARE MATCHING (the 30-day rule).** For a taxable account, units sold are not necessarily the
units in the pool: TCGA 1992 s.106A identifies a disposal first against acquisitions of the same
share in the following 30 days, and only then against the pool. It exists to stop
bed-and-breakfasting — sell to crystallise a loss, buy back the next morning — and it changes the
gain, sometimes to zero. So a disposal is matched against later acquisitions before it touches the
pool, an acquisition can be matched by more than one disposal (hence the partial-match carry), and
only the unmatched remainder comes out of the pool at the pooled price.

Matching applies to a **GIA only**. An ISA, a JISA and a pension are not chargeable to CGT, so
matching them would spend the effort to compute a figure nobody reports. Seccl's own book-cost
report draws the same line.

**Money is `Decimal` throughout, and every conversion goes through `str`.** A pooled average is
divided and re-multiplied on every row, so a float's error compounds down the history rather than
staying in the last penny — and `Decimal(0.1)` is not `Decimal("0.1")`.

Ported from the v1 pipeline's `book_costs()` (`apps/dataflow/seccl/transform.py`), which
established the shape. Two deliberate divergences from it are marked DIVERGENCE below, both about
what happens to an acquisition a disposal was matched against: the original dropped its unmatched
surplus (so later disposals fell back to the pooled price) and still added its whole cost to the
pool (so that cost was counted twice). Neither is bug-compatible here, because the output is a tax
figure.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

#: Seccl's movement types that ADD units to a holding. Everything else removes them.
ACQUISITION_MOVEMENTS = frozenset({"In", "Invest"})

#: The movement type a REALISED GAIN is reported against. Deliberately narrower than "not an
#: acquisition": a `Raise` reduces the holding and so consumes pool cost, but it is Seccl's word
#: for the sell leg the platform raises to fund something else, and the v1 pipeline reported a gain
#: on `Sell` alone. A transfer out reduces the holding too and realises nothing at all — the units
#: move, they are not disposed of.
DISPOSAL_MOVEMENTS = frozenset({"Sell"})

#: Wrappers whose disposals are chargeable to CGT, and therefore the only ones share matching runs
#: for. An ISA/JISA/pension disposal is not a chargeable event.
MATCHED_PRODUCTS = frozenset({"GIA"})

#: TCGA 1992 s.106A — a disposal is matched against acquisitions of the same share made within the
#: 30 days FOLLOWING it, before the pool is touched.
MATCH_WINDOW = timedelta(days=30)

#: Seccl's sub-type for a cash distribution and for an accumulation (reinvested) one. They are
#: handled differently: see `_cost_effect`.
CASH_DIVIDEND = "Dividend"
ACCUMULATION_DIVIDEND = "Accumulation Dividend"

ZERO = Decimal(0)


def dec(value: Any) -> Decimal:
    """Any numeric-ish value as a Decimal, via `str`. `None`/blank is zero."""
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass
class Movement:
    """One transaction, in the terms a cost-basis run needs.

    `signed_quantity` and `cost_effect` are already directional: an acquisition is positive, a
    disposal negative. Doing that at the boundary keeps the recurrence below arithmetic rather than
    a run of `if buy` branches.
    """

    transaction_id: str
    account_id: str
    isin: str
    #: What orders the history. Seccl's execution date when it has one, else the transaction date.
    on: date | None
    movement_type: str | None
    transaction_type: str | None
    sub_type: str | None
    product_type: str | None
    #: Units added (+) or removed (-).
    signed_quantity: Decimal
    #: What the movement does to the cost pool, signed the same way. For an acquisition this is the
    #: consideration paid; for a disposal it is what the units that left had cost, which the pool
    #: overrides.
    consideration: Decimal
    #: The money that actually changed hands. SEPARATE from `consideration`, and the separation is
    #: a bug fix: `consideration` prefers Seccl's `bookCostAmount`/`bookValue`, which on a disposal
    #: is the COST of the units sold — so measuring a gain against it compared a cost with itself
    #: and reported every disposal as realising exactly nothing. Only this field is the proceeds.
    proceeds: Decimal = ZERO
    #: The capital half of a distribution — a return of the capital embedded in the price paid, so
    #: it REDUCES the pool. Never income.
    equalisation: Decimal = ZERO

    @property
    def is_acquisition(self) -> bool:
        return (self.movement_type or "") in ACQUISITION_MOVEMENTS

    @property
    def is_disposal(self) -> bool:
        return (self.movement_type or "") in DISPOSAL_MOVEMENTS

    @property
    def is_matchable(self) -> bool:
        """A disposal share matching applies to: chargeable wrapper, and an actual order."""
        return (
            self.is_disposal
            and (self.product_type or "") in MATCHED_PRODUCTS
            and (self.transaction_type or "") == "Order"
        )


@dataclass
class BookCost:
    """What one movement did to the pool, and what it realised."""

    transaction_id: str
    account_id: str
    isin: str
    #: The cost attributed to THIS movement: what an acquisition added, or what the units a
    #: disposal sold had cost — matched units at their own price plus pooled units at the pooled
    #: price.
    #:
    #: Deliberately NOT the pool delta, which is a different number and the less useful one: a
    #: fully share-matched disposal takes nothing out of the pool (those units were never in it),
    #: so the delta is zero while the units plainly cost something. `total_book_cost` is therefore
    #: not a running sum of this column, and that is the point — one answers "what did this cost",
    #: the other "what is the holding's basis now".
    book_cost: Decimal
    #: The section 104 pool after this movement.
    total_book_cost: Decimal
    #: Units held after it.
    total_quantity: Decimal
    #: The pool's average price after it — what the NEXT disposal comes out at.
    book_price: Decimal
    #: Proceeds less the cost of the units that left. None on anything that is not a disposal: an
    #: acquisition realises nothing, which is not the same as realising zero.
    realised_gain: Decimal | None
    #: How much of the disposal was identified against later acquisitions rather than the pool.
    matched_quantity: Decimal
    #: True on the last movement of the holding — i.e. the row describing the CURRENT position.
    current: bool


@dataclass
class _Unmatched:
    """An acquisition with units still available to match a disposal against."""

    transaction_id: str
    quantity: Decimal
    price: Decimal


def _cost_effect(movement: Movement) -> Decimal:
    """What the movement contributes to the pool, before any disposal pricing.

    A CASH dividend contributes `-equalisation` and nothing else: the income is income, but the
    equalisation part is a return of capital and has to come out of the pool or every subsequent
    gain is overstated. An ACCUMULATION dividend is reinvested, so its consideration joins the pool
    net of the same equalisation. Everything else contributes its consideration as stated.
    """
    if (movement.sub_type or "") == CASH_DIVIDEND:
        return -movement.equalisation
    if (movement.sub_type or "") == ACCUMULATION_DIVIDEND:
        return movement.consideration - movement.equalisation
    return movement.consideration


def _sort_key(movement: Movement) -> tuple[date, int, str]:
    """History order: by date, acquisitions before disposals on the same day, then by id.

    Acquisitions first is not cosmetic — a same-day buy and sell would otherwise price the sell off
    a pool the buy has not joined yet. `date.min` for an undated movement puts it at the start,
    where it can only affect the pool it opens.
    """
    return (movement.on or date.min, 0 if movement.is_acquisition else 1, movement.transaction_id)


def _match_against_later_acquisitions(
    disposal: Movement,
    later: Sequence[Movement],
    unmatched: dict[str, _Unmatched],
) -> tuple[Decimal, Decimal]:
    """Identify a disposal against acquisitions in the following 30 days.

    Returns `(cost_matched, quantity_matched)`, both positive magnitudes. `unmatched` is the carry
    across disposals and is MUTATED: an acquisition of 100 units can be matched 60 by one disposal
    and 40 by the next, so what is left of it has to survive this call.

    DIVERGENCE 1 from v1: the original recorded a partial only when `-matched_quantity >
    remaining_quantity`, which is false in exactly the case that needs it — an acquisition LARGER
    than the disposal, where the surplus is what a later disposal would match against. Its surplus
    was dropped, so the units silently fell back to the pool at the pooled price rather than at
    their own. Here every acquisition's remaining units are carried, and the ledger is simply "how
    many units of this acquisition are still unmatched".
    """
    remaining = abs(disposal.signed_quantity)
    if remaining <= 0 or not disposal.on:
        return ZERO, ZERO

    window_end = disposal.on + MATCH_WINDOW
    cost = ZERO
    matched = ZERO
    for acquisition in later:
        if remaining <= 0:
            break
        if not acquisition.on or acquisition.on > window_end:
            break  # `later` is date-ordered, so the first one past the window ends it
        if not acquisition.is_acquisition or acquisition.transaction_type != "Order":
            continue
        available = unmatched.get(acquisition.transaction_id)
        if available is None or available.quantity <= 0:
            continue
        take = min(remaining, available.quantity)
        cost += available.price * take
        matched += take
        remaining -= take
        available.quantity -= take
    return cost, matched


def compute(movements: Iterable[Movement]) -> list[BookCost]:
    """The cost basis of every movement of one (account, ISIN), in history order.

    One holding at a time: the pool is per account per instrument, so movements for different
    holdings never interact and the caller groups before calling.
    """
    ordered = sorted(movements, key=_sort_key)
    if not ordered:
        return []

    # Every acquisition's units, available to be matched by a later disposal. Seeded up front
    # because matching looks FORWARD: a disposal on day 1 matches a purchase on day 10, so the
    # purchase has to be known before the disposal is priced.
    unmatched: dict[str, _Unmatched] = {}
    for index, movement in enumerate(ordered):
        if not movement.is_acquisition or movement.transaction_type != "Order":
            continue
        quantity = abs(movement.signed_quantity)
        if quantity <= 0:
            continue
        unmatched[movement.transaction_id] = _Unmatched(
            transaction_id=movement.transaction_id,
            quantity=quantity,
            # The acquisition's OWN price, which is the whole point of matching: the matched units
            # cost what they cost, not what the pool averages to.
            price=abs(_cost_effect(movement)) / quantity,
        )
        del index

    pool = ZERO  # total_book_cost — the section 104 pool
    held = ZERO  # total_quantity — the units the pool holds
    price = ZERO  # book_price — the pooled average the next disposal comes out at
    out: list[BookCost] = []

    for position, movement in enumerate(ordered):
        effect = _cost_effect(movement)
        matched_quantity = ZERO
        realised: Decimal | None = None
        available = unmatched.get(movement.transaction_id)

        if movement.is_acquisition and available is not None:
            # DIVERGENCE 2 from v1, and the more consequential one. A MATCHED acquisition's units
            # never enter the pool. The original added every acquisition's full amount to the pool
            # regardless of whether an earlier disposal had been identified against it — so that
            # cost was counted twice, once pricing the disposal and once in the pool, and the pool
            # then stated a basis for units the client no longer held. HMRC removes matched
            # acquisitions from the section 104 holding entirely. `available.quantity` is what is
            # LEFT of this one after every earlier disposal took its share; matching only looks
            # forward, so by the time this row is reached all of them have been processed.
            contributed = available.quantity
            book_cost = pool_delta = available.price * contributed
            held += contributed
        elif movement.is_acquisition or price == ZERO:
            # An acquisition joins the pool at what it cost. So does anything else before the pool
            # has a price — with no basis to price a disposal against, its own consideration is the
            # only figure there is.
            book_cost = pool_delta = effect
            held += movement.signed_quantity
        elif movement.is_matchable:
            matched_cost, matched_quantity = _match_against_later_acquisitions(
                movement, ordered[position + 1 :], unmatched
            )
            from_pool = abs(movement.signed_quantity) - matched_quantity
            pooled_cost = price * from_pool
            # THE TWO FIGURES DIVERGE HERE, and only here. What the units COST is the matched ones
            # at their own price plus the pooled ones at the pooled price — that is the disposal's
            # basis and what its gain is measured against. What leaves the POOL is the pooled part
            # alone: the matched units were never in it, so taking their cost out would remove what
            # was never put in and walk the pool negative (it did, before this split existed).
            book_cost = -(matched_cost + pooled_cost)
            pool_delta = -pooled_cost
            held -= from_pool
            # PROCEEDS less that basis. `effect` is the COST input, so measuring against it
            # compared a cost with itself and reported every disposal as realising exactly nothing.
            realised = (abs(movement.proceeds) - (matched_cost + pooled_cost)).quantize(
                Decimal("0.000001")
            )
        else:
            book_cost = pool_delta = price * movement.signed_quantity
            held += movement.signed_quantity

        pool += pool_delta
        # A holding sold to nothing has no average price. Re-seeding from zero rather than keeping
        # the last one is deliberate: a repurchase after a full exit starts a new pool.
        price = ZERO if held == 0 else pool / held

        if movement.is_disposal and realised is None:
            # An unmatched disposal (a wrapper with no CGT, or one taken before the pool had a
            # price). Magnitudes on both sides: Seccl signs a disposal's proceeds negative and
            # `book_cost` is negative by construction here. PROCEEDS, never `effect` — see the
            # field note on `Movement.proceeds`.
            realised = (abs(movement.proceeds) - abs(book_cost)).quantize(Decimal("0.000001"))

        out.append(
            BookCost(
                transaction_id=movement.transaction_id,
                account_id=movement.account_id,
                isin=movement.isin,
                book_cost=book_cost,
                total_book_cost=pool,
                total_quantity=held,
                book_price=price,
                realised_gain=realised,
                matched_quantity=matched_quantity,
                current=position == len(ordered) - 1,
            )
        )
    return out


def compute_all(movements: Iterable[Movement]) -> list[BookCost]:
    """Every holding's cost basis, grouped by (account, ISIN)."""
    holdings: dict[tuple[str, str], list[Movement]] = {}
    for movement in movements:
        holdings.setdefault((movement.account_id, movement.isin), []).append(movement)
    return [row for holding in holdings.values() for row in compute(holding)]


# ── Reading the raw layer ────────────────────────────────────────────────────────────────────────

#: Statuses whose movements have happened. A pending or deleted order has not changed the pool, and
#: including one would price every later disposal off a purchase that never settled.
SETTLED_STATUS = "Completed"


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _text(value: Any) -> str:
    """A value as a non-empty string, or "". Rejects the float nan a pandas read produces for a SQL
    NULL: `str(nan)` is `"nan"`, which is a perfectly good-looking ISIN and a completely wrong one —
    a cash payment with no instrument sailed past a plain truth test and acquired a book-cost row
    for a holding called "nan"."""
    if value is None or isinstance(value, float):
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("", "nan", "none") else text


def _json_field(blob: Any, key: str) -> Any:
    """One key out of Seccl's `information` blob, which lands as a JSON string."""
    if blob is None:
        return None
    if isinstance(blob, dict):
        return blob.get(key)
    import json

    try:
        parsed = json.loads(blob)
    except (TypeError, ValueError):
        return None
    return parsed.get(key) if isinstance(parsed, dict) else None


def movements_from_rows(
    transactions: Iterable[dict[str, Any]], product_by_account: dict[str, str | None]
) -> list[Movement]:
    """The raw transaction rows a cost-basis run reads, as `Movement`s.

    Filters to what actually moves a holding: settled, carrying an ISIN (a cash movement has no
    pool), and not a cash redemption — Seccl books the latter against the instrument while it is
    the fund paying out, not the client disposing.
    """
    out: list[Movement] = []
    for row in transactions:
        isin = _text(row.get("isin")) or _text(_json_field(row.get("information"), "originalIsin"))
        if not isin:
            continue
        if (row.get("status") or "") != SETTLED_STATUS:
            continue
        if (row.get("transaction_type") or "") == "Cash" and (
            row.get("transaction_sub_type") or ""
        ) == "Redemption":
            continue

        account_id = str(row.get("account_id") or "")
        movement_type = row.get("movement_type")
        acquisition = (movement_type or "") in ACQUISITION_MOVEMENTS
        quantity = dec(row.get("executed_quantity") or row.get("quantity"))
        # Seccl's own book-cost figure for the movement when it states one, then its `bookValue`,
        # then the consideration. In that order because `bookCostAmount` is the only one of the
        # three that is explicitly a COST rather than a value that may include charges.
        cost = dec(
            _json_field(row.get("information"), "bookCostAmount")
            or row.get("book_value")
            or row.get("consideration")
            or row.get("executed_amount")
            or row.get("amount")
        )
        # The money that actually moved, which is NEVER a book value: `cost` above prefers Seccl's
        # stated cost basis, and a gain measured against that is a cost compared with itself.
        proceeds = dec(row.get("consideration") or row.get("executed_amount") or row.get("amount"))
        sign = 1 if acquisition else -1
        out.append(
            Movement(
                transaction_id=str(row.get("id") or ""),
                account_id=account_id,
                isin=isin,
                on=_as_date(row.get("execution_date"))
                or _as_date(row.get("transaction_date"))
                or _as_date(row.get("created_date")),
                movement_type=movement_type,
                transaction_type=row.get("transaction_type"),
                sub_type=row.get("transaction_sub_type"),
                product_type=product_by_account.get(account_id),
                signed_quantity=sign * abs(quantity),
                consideration=sign * abs(cost),
                proceeds=abs(proceeds),
                equalisation=dec(_json_field(row.get("information"), "equalisationAmountPayCy")),
            )
        )
    return out


#: The Arrow schema `derived.book_costs` publishes. Declared rather than inferred so an empty firm
#: still produces a bindable table — the same reason `raw.fee_structures` declares its whole spine.
COLUMNS: tuple[str, ...] = (
    "transaction_id",
    "account_id",
    "isin",
    "book_cost",
    "total_book_cost",
    "total_quantity",
    "book_price",
    "realised_gain",
    "matched_quantity",
    "current",
)


def as_records(rows: Iterable[BookCost]) -> list[dict[str, Any]]:
    """`BookCost`s as plain records, money as float for the Arrow boundary.

    Float only at the very end: the recurrence above is all `Decimal`, and the warehouse casts
    these back to `decimal(24,8)`. Rounded to eight places because that is what the column holds —
    leaving a 32-digit quotient to be truncated by the cast would put the rounding somewhere nobody
    chose.
    """

    def money(value: Decimal | None) -> float | None:
        return None if value is None else float(round(value, 8))

    return [
        {
            "transaction_id": row.transaction_id,
            "account_id": row.account_id,
            "isin": row.isin,
            "book_cost": money(row.book_cost),
            "total_book_cost": money(row.total_book_cost),
            "total_quantity": money(row.total_quantity),
            "book_price": money(row.book_price),
            "realised_gain": money(row.realised_gain),
            "matched_quantity": money(row.matched_quantity),
            "current": row.current,
        }
        for row in rows
    ]


@dataclass(frozen=True)
class Summary:
    """What a run did, for the log. A book-cost run that silently computed nothing is the failure
    mode worth naming: it reads downstream as "this firm realised no gains"."""

    holdings: int = 0
    movements: int = 0
    disposals: int = 0
    matched_disposals: int = 0
    accounts: set[str] = field(default_factory=set)

    def describe(self) -> str:
        return (
            f"{self.movements} movements across {self.holdings} holdings in "
            f"{len(self.accounts)} accounts; {self.disposals} disposals, "
            f"{self.matched_disposals} of them share-matched"
        )


def summarise(movements: Sequence[Movement], rows: Sequence[BookCost]) -> Summary:
    return Summary(
        holdings=len({(row.account_id, row.isin) for row in rows}),
        movements=len(movements),
        disposals=sum(1 for row in rows if row.realised_gain is not None),
        matched_disposals=sum(1 for row in rows if row.matched_quantity > 0),
        accounts={movement.account_id for movement in movements},
    )
