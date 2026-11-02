"""Money-movement instructions (demo-grade).

Adviser-initiated expected-money-in, cash withdrawal, ad hoc fee, and model top-up /
withdrawal. Runs on ``firm_session`` (service_role), so every statement is EXPLICITLY scoped to
the caller's own ``tenant_id`` (never a client-supplied one) — the same discipline as
``app.contacts``. A pending outbound row in ``platform.transactions`` (``source_system =
graphene``) is written alongside the instruction and synced best-effort to the account's
custodian; see ``app.outbound`` and ``docs/transaction-status.md``. The 0001 DML audit trigger
captures every write into ``audit.log``.

**The withdrawal control lives here, not in the portal.** A cash withdrawal is checked against the
custodian's latest stated cash (``platform.valuations.cash_value`` — the figure the accounts
listing shows), and a model withdrawal against what is actually INVESTED in the provider's
positions. Neither is ``platform.accounts.value``, the account's *total*, which would authorise
paying away a whole portfolio as if it were cash. Cash is not read from positions: that relation
keeps only the latest file, and a later file that omits an account would show £0 and refuse a
withdrawal against a balance the custodian has already stated.
The check is deliberately at this layer because ``POST /firm/contacts/{id}/instructions``
(routers/firm.py) reaches it with nothing but a ``contacts:write`` check: a control that lived only
in the SvelteKit action was one HTTP call away from being bypassed.

**The gate is cumulative, not per-request.** Nothing settles an instruction yet, so every *pending*
row is still a claim on the same money. Checking one request at a time meant the limit was simply
exceeded by repetition (three £2,000 withdrawals against a £2,000 cash leg were all accepted), and
by swapping kinds (a ``model_topup`` spends cash too). ``available = leg − outstanding claims on
that leg``, and the account row is locked for the duration so two concurrent requests can't both
read the same headroom. Nothing is ever *credited*: an ``expected_in`` is unsettled money and does
not raise what can be paid out.

Fail-closed: if the provider holds no position for the account (unlinked account, absent lake) the
request is REFUSED. For a cash-out, "we can't tell" must not mean "go ahead".

**The account's MANDATE is written here too** (``set_account_model``).
``platform.accounts.model_id`` is what makes an account discretionary, and therefore what makes
``model_topup`` / ``model_withdrawal`` possible at all — so the two belong to the same control,
with the same tenant-scoping and the same refusal to strand a pending claim.
"""

import datetime as dt
import logging
import math
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import bank_accounts, outbound, providers
from app.config import settings
from app.ids import new_id
from app.instruction_kinds import FEE_WITH_MOVEMENT
from app.lake import holdings as lake_holdings

KINDS = frozenset({"expected_in", "withdrawal", "adhoc_fee", "model_topup", "model_withdrawal"})
_MODEL = frozenset({"model_topup", "model_withdrawal"})

# How far back a payment in may be backdated. Mirrored by `instruction.ts::BACKDATE_LIMIT_DAYS`.
BACKDATE_LIMIT_DAYS = 365

logger = logging.getLogger(__name__)

# An instruction can only be raised against an account that is open — 'draft' isn't open yet and
# 'closed' has been paid away (platform.account_status). The portal's picker already excludes both;
# the API must not depend on the portal for that.
_INSTRUCTABLE = frozenset({"active"})

# Which leg of the position each kind spends, and which *pending* kinds are already claiming that
# same leg. A `model_topup` buys into the model out of the account's cash, so it both spends cash
# and has to be funded from it — leaving it out made the cash gate defeatable by swapping kinds
# (top up the whole cash leg, then withdraw the whole cash leg). An `adhoc_fee` spends the same
# cash. `expected_in` spends nothing and is never gated: money coming in is unconstrained by
# what is held.
_CASH_DRAWS = ("withdrawal", "model_topup", "adhoc_fee")
_LEG: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "withdrawal": ("cash", "cash", _CASH_DRAWS),
    "adhoc_fee": ("cash", "cash", _CASH_DRAWS),
    "model_topup": ("cash", "cash", _CASH_DRAWS),
    "model_withdrawal": ("invested", "invested in the model", ("model_withdrawal",)),
}

# What counts as the cash leg of a provider position. The lake's `asset_type` is coarse ("Stock"),
# so the money-market/liquidity funds that ARE cash have to be recognised by name too. This is the
# single definition — routers/crm.py's allocation bucket imports it, so the "Cash" the screen shows
# and the cash a withdrawal is checked against cannot drift apart.
#
# The predicate itself lives in ``lake.holdings.is_cash_position`` — word boundaries, plus
# SEI's currency code — and the holdings summary SQL splices the same constants. Keeping a
# second copy here is how "Cashel Capital …" counted as withdrawable cash on one path and
# not the other.


def is_cash_holding(h: dict[str, object]) -> bool:
    """Whether one provider holding is part of the account's cash balance.

    Delegates to ``lake.holdings.is_cash_position``, which the summary aggregate uses too.
    """
    asset_type = h.get("asset_type")
    asset_name = h.get("asset_name")
    return lake_holdings.is_cash_position(
        None if asset_type is None else str(asset_type),
        None if asset_name is None else str(asset_name),
    )


def funding_basis(rows: list[dict[str, object]]) -> dict[str, object]:
    """``{cash, invested, as_of}`` for one account, from its provider holdings.

    ``cash`` is what a cash withdrawal can be paid from; ``invested`` is what a model
    disinvestment can come out of. ``as_of`` is the latest snapshot date across the rows, so an
    error message can state how old the position it refused on is.

    A non-finite value in the provider extract counts as nothing rather than poisoning the leg: one
    NaN would make the total NaN, and every `amount > available` comparison against NaN is False —
    which is the whole gate, defeated by one bad row upstream."""
    cash = 0.0
    invested = 0.0
    as_of: str | None = None
    for h in rows:
        value = h.get("value")
        v = float(value) if isinstance(value, int | float) and math.isfinite(value) else 0.0
        if is_cash_holding(h):
            cash += v
        else:
            invested += v
        raw = h.get("as_of")
        if raw:
            d = str(raw)
            if as_of is None or d > as_of:
                as_of = d
    return {"cash": round(cash, 2), "invested": round(invested, 2), "as_of": as_of}


def _money(n: float) -> str:
    return f"£{n:,.2f}"


def _as_at(basis: dict[str, object]) -> str:
    return f" (provider position as at {basis['as_of']})" if basis.get("as_of") else ""


_PAYMENT_METHODS = frozenset({"bacs", "faster_payment", "swift"})
_SEI_TO_OURS = {"FASTERPAYMENT": "faster_payment", "SWIFT": "swift", "BACS": "bacs"}


def _method_of(entry: dict[str, Any] | None) -> str:
    """Our rail name for a resolved pay-to entry. Same rule as ``sei_payment_method``."""
    sei = bank_accounts.sei_payment_method(entry or {})
    return _SEI_TO_OURS.get(sei, "bacs")


def _international(entry: dict[str, Any] | None) -> bool:
    """An IBAN with no sort code is the international rail. SWIFT only, nothing to choose."""
    if not entry:
        return False
    iban = str(entry.get("iban") or "").strip()
    sort_code = str(entry.get("sort_code") or "").strip()
    return bool(iban) and not sort_code


async def _withdrawal_rail(
    session: AsyncSession,
    *,
    account_id: int,
    provider: object,
    kind: str,
    payment_method: str | None,
    pass_rail_fee: bool | None,
) -> tuple[str | None, float, bool]:
    """``(method, tariff, pass_on)`` for one instruction.

    The bank account stores the default. A withdrawal may name another rail; omitting it uses
    that default. The tariff is SEI's, so another custodian is not charged it. ``pass_on`` false
    means the firm pays the tariff and it is not reserved from the client's cash. The tariff is
    still returned, so the row records what the rail cost.
    """
    if kind != "withdrawal":
        if payment_method is not None:
            raise ValueError("A payment method applies to a withdrawal only.")
        return None, 0.0, True
    if payment_method is not None and payment_method not in _PAYMENT_METHODS:
        raise ValueError("Choose BACS, Faster Payment or SWIFT.")
    entry, _source = await bank_accounts.resolve_for_account(
        session, account_id=account_id, direction="pay_to"
    )
    international = _international(entry)
    method = payment_method or _method_of(entry)
    if international and method != "swift":
        raise ValueError("An international bank account is paid by SWIFT.")
    if not international and method not in {"bacs", "faster_payment"}:
        raise ValueError("Choose BACS or Faster Payment for a UK bank account.")
    tariff = 0.0
    if provider == providers.code_named("SEI"):
        tariff = {
            "faster_payment": settings.sei_faster_payment_fee,
            "swift": settings.sei_swift_fee,
        }.get(method, 0.0)
    return method, round(float(tariff), 2), True if pass_rail_fee is None else pass_rail_fee


async def _committed(
    session: AsyncSession, *, tenant_id: int, account_id: int, kinds: tuple[str, ...]
) -> float:
    """What the account's already-*pending* instructions have claimed off one leg.

    Nothing settles an instruction yet, so a pending row is still money promised away. Tenant-scoped
    like every other statement in this module (the session is service_role)."""
    total = (
        await session.execute(
            text(
                # adviser_fee is a withdrawal's extra debit (0 on every other kind), so a pending
                # withdrawal's fee counts against the next instruction's headroom too.
                "SELECT COALESCE(SUM(amount + adviser_fee + CASE WHEN pass_rail_fee "
                "THEN rail_fee ELSE 0 END), 0)::float8 "
                "FROM platform.instructions "
                "WHERE tenant_id = :t AND account_id = :a AND status = 'pending' "
                "  AND kind = ANY(:kinds)"
            ),
            {"t": tenant_id, "a": account_id, "kinds": list(kinds)},
        )
    ).scalar_one()
    return round(float(total), 2)


async def stated_custodian_cash(
    session: AsyncSession,
    account_ids: Sequence[int],
    *,
    tenant_id: int | None = None,
) -> dict[int, tuple[float, dt.date]]:
    """Latest custodian cash per account, from ``platform.valuations``.

    The latest row of each custodian, summed. A scheme administrator's bank balance is a
    different pot and is not included. An account whose custodian has not stated a cash
    figure is absent: a NULL is "not stated", and a stated zero is zero. ``tenant_id`` is
    required when the session bypasses RLS.
    """
    if not account_ids:
        return {}
    rows = (
        await session.execute(
            text(
                "SELECT account_id, sum(cash_value)::float8 AS cash, max(as_of_date) AS as_of "
                "FROM ("
                "  SELECT DISTINCT ON (account_id, source_system) "
                "         account_id, cash_value, as_of_date "
                "  FROM platform.valuations "
                "  WHERE account_id = ANY(:ids) "
                "    AND (CAST(:tenant AS int) IS NULL OR tenant_id = CAST(:tenant AS int)) "
                "    AND source_system = ANY(:custodians) "
                "  ORDER BY account_id, source_system, as_of_date DESC"
                ") latest "
                "WHERE cash_value IS NOT NULL "
                "GROUP BY account_id"
            ),
            {
                "ids": list(account_ids),
                "tenant": tenant_id,
                "custodians": list(providers.custodians()),
            },
        )
    ).mappings()
    stated: dict[int, tuple[float, dt.date]] = {}
    for row in rows:
        as_of = row["as_of"]
        if not isinstance(as_of, dt.date):
            continue
        stated[int(row["account_id"])] = (float(row["cash"]), as_of)
    return stated


async def _fundable(
    session: AsyncSession,
    *,
    tenant_id: int,
    account_id: int,
    external_id: object,
    provider: object,
    kind: str,
) -> tuple[float, float, float, str, dict[str, object]]:
    """``(available, leg_total, committed, label, basis)`` for one account and one kind.

    Cash (``withdrawal`` / ``adhoc_fee`` / ``model_topup``) is the custodian's latest stated
    balance. Invested (``model_withdrawal``) is the provider's positions. ``committed`` is
    what this account's pending instructions have already claimed off that same leg. Refuses
    (``ValueError``) when cash was never stated and there is no position to check against —
    an account with no provider link, or a lake that isn't there — rather than falling back
    to the account's total value."""
    # Both halves of the link, because the id alone does not say which catalogue to read it from —
    # the LEFT JOIN yields them together or not at all, and a missing provider with an id present
    # would send a real id to `tenant_{uid}.positions` in the wrong warehouse, where it finds
    # nothing and reads as "this account holds nothing".
    if (
        not isinstance(external_id, str)
        or not external_id
        or not isinstance(provider, str)
        or not provider
    ):
        raise ValueError(
            "This account isn't linked to a provider, so the money available to withdraw can't be "
            "confirmed. Nothing was recorded."
        )
    leg, label, draws = _LEG[kind]
    # THE SAME CASH THE LISTING SHOWS. Positions keep only the latest file, so an account
    # that file omits has no row and would read as £0. The valuation is the last day the
    # custodian stated a balance, which is the cash the adviser has already been shown.
    if leg == "cash":
        stated = (await stated_custodian_cash(session, [account_id], tenant_id=tenant_id)).get(
            account_id
        )
        if stated is not None:
            cash, as_of = stated
            committed = await _committed(
                session, tenant_id=tenant_id, account_id=account_id, kinds=draws
            )
            basis = {"cash": cash, "invested": 0.0, "as_of": as_of.isoformat()}
            return round(cash - committed, 2), cash, committed, label, basis
    # THE FIRM'S OWN POSITIONS. This read used to go through `app/lake/seccl.py` to
    # `docs/seccl.duckdb` — an anonymised, downsampled dev extract committed to this repository,
    # the same file for every firm in every deployment. Either the account's provider id was
    # absent from it and the gate refused every withdrawal on that account for ever, or the id was
    # present (the extract keeps real Seccl ids) and the gate decided against someone else's
    # anonymised numbers.
    tenant_uid = (
        await session.execute(text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": tenant_id})
    ).scalar_one_or_none()
    if not tenant_uid:
        raise ValueError(
            "This firm's data source could not be identified, so the money available to withdraw "
            "can't be confirmed. Nothing was recorded."
        )
    try:
        rows = await lake_holdings.firm_holdings(provider, str(tenant_uid), [external_id])
    except lake_holdings.HoldingsUnavailable as exc:
        # REFUSED, and said differently. "We could not read the positions" and "this account holds
        # nothing" both stop the withdrawal, and must: for a cash-out, "we can't tell" never means
        # "go ahead". But only one of them is the account's fault, and only one is worth paging
        # somebody about — the fixture reader returned [] for both and lost the distinction.
        logger.warning(
            "withdrawal gate could not read positions for account %s: %s", account_id, exc
        )
        raise ValueError(
            "This firm's holdings could not be read just now, so the money available to withdraw "
            "can't be confirmed. Nothing was recorded — try again shortly."
        ) from exc
    if not rows:
        raise ValueError(
            "The provider holds no position for this account, so the money available to withdraw "
            "can't be confirmed. Nothing was recorded."
        )
    basis = funding_basis(rows)
    leg_total = float(basis[leg])  # type: ignore[arg-type]
    committed = await _committed(session, tenant_id=tenant_id, account_id=account_id, kinds=draws)
    return round(leg_total - committed, 2), leg_total, committed, label, basis


def _refusal(
    *,
    kind: str,
    available: float,
    leg_total: float,
    committed: float,
    label: str,
    basis: dict[str, object],
) -> str:
    """Tell the adviser what is available, how old that position is, and — when the shortfall is
    caused by instructions already in flight rather than by the position itself — say so. Guessing
    amounts against an unexplained ceiling is how a control gets worked around."""
    head = f"Only {_money(max(available, 0.0))} is {label} on this account{_as_at(basis)}"
    if committed > 0:
        head += (
            f" — {_money(leg_total)} is held but {_money(committed)} is already committed to "
            "pending instructions"
        )
    tail = {
        "withdrawal": "A larger withdrawal needs a disinvestment first.",
        "model_topup": "A top-up can only invest cash the account already holds.",
        "adhoc_fee": "A larger fee needs more cash on the account.",
    }.get(kind, "Reduce the amount.")
    return f"{head}. {tail}"


async def fee_types_for_account(
    session: AsyncSession, *, tenant_id: int, product_id: int | None, model_id: int | None
) -> dict[int, str]:
    """The firm's fee packages an ad hoc fee on this account may be charged under, ``{id: name}``.

    THE SAME FILTER AS THE PICKER, character for character: the drawer is fed by
    ``GET /catalog/fee-packages?product_id=&model_id=``, so any narrowing here the endpoint does
    not do would refuse a fee type the adviser was just offered — a refusal that reads as a bug in
    the form. A package scoped to no product or no model applies to every account, and an account
    that names neither sees the whole enabled register (the endpoint's ``:prod IS NULL`` arm).
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, name FROM platform.fee_packages "
                    "WHERE tenant_id = :t "
                    "  AND enabled IS NOT FALSE "
                    "  AND (CAST(:prod AS int) IS NULL OR product_id IS NULL "
                    "       OR product_id = CAST(:prod AS int)) "
                    "  AND (CAST(:model AS int) IS NULL OR model_id IS NULL "
                    "       OR model_id = CAST(:model AS int)) "
                    "ORDER BY sort_order, name"
                ),
                {"t": tenant_id, "prod": product_id, "model": model_id},
            )
        )
        .mappings()
        .all()
    )
    return {int(r["id"]): str(r["name"]) for r in rows}


def _adviser_fee(kind: str, amount: float, adviser_fee: float | None) -> float:
    """The adviser charge on a money movement, or zero. On a payment in it is taken off the money
    arriving, so it must be less than it; on a withdrawal it is an additional debit (bounded by
    available cash, which the door checks), so it is not. Refused on any other kind."""
    if adviser_fee is None:
        return 0.0
    if not math.isfinite(adviser_fee) or adviser_fee < 0:
        raise ValueError("Enter an adviser fee greater than zero, or leave it off.")
    fee = round(float(adviser_fee), 2)
    if fee == 0:
        return 0.0
    if kind not in FEE_WITH_MOVEMENT:
        raise ValueError("An adviser fee applies to a payment or withdrawal only.")
    if kind == "expected_in" and fee >= amount:
        raise ValueError("The adviser fee must be less than the payment.")
    return fee


async def create_instruction(
    session: AsyncSession,
    *,
    tenant_id: int,
    created_by_workos: str | None,
    kind: str,
    account_id: int,
    amount: float,
    model_id: int | None = None,
    fee_package_id: int | None = None,
    due_date: str | None = None,
    note: str | None = None,
    payment_method: str | None = None,
    pass_rail_fee: bool | None = None,
    category: str | None = None,
    adviser_fee: float | None = None,
    adviser_fee_package_id: int | None = None,
    auto_disinvest: bool = False,
    auto_invest: bool = True,
) -> dict[str, object]:
    """Record one instruction against an account the caller's firm owns. Validates the account
    (open, owned by an active client), the amount, the due date, a linked model (model actions),
    the fee type (ad hoc fee) and the money actually available on the leg being spent, net of what
    this account's pending instructions have already claimed (see the module note). Raises
    ``ValueError`` for a bad request (→ 400) and ``LookupError`` if the account isn't found."""
    if kind not in KINDS:
        raise ValueError("Unknown instruction type.")
    if category is not None and kind != "expected_in":
        raise ValueError("A contribution category applies to a payment only.")
    if category is not None and category not in {
        "personal_contribution",
        "employer_contribution",
        "third_party_contribution",
    }:
        raise ValueError("Choose personal, employer, or third party.")
    # A fee type belongs to a fee. Silently dropping it on another kind would accept a request
    # whose whole point was the field the caller sent, and report success.
    if fee_package_id is not None and kind != "adhoc_fee":
        raise ValueError("A fee type applies to an ad hoc fee only.")
    # The adviser fee that rides a movement names its OWN type (SEI needs a feeTypeId for it), kept
    # apart from `fee_package_id` so the movement itself is not stamped a charge of that type.
    if adviser_fee_package_id is not None and kind not in {
        "expected_in",
        "withdrawal",
        "model_withdrawal",
    }:
        raise ValueError("An adviser fee type applies to a payment or withdrawal only.")
    # Raising cash to fund the movement is a withdrawal concept — a payment in has nothing to sell.
    if auto_disinvest and kind not in {"withdrawal", "model_withdrawal"}:
        raise ValueError("Auto-disinvest applies to a withdrawal only.")
    # `not math.isfinite(...)` before the comparison: every comparison against NaN is False, so
    # `amount <= 0` AND `amount > available` both passed and NaN was written into the amount column.
    if amount is None or not math.isfinite(amount) or amount <= 0:
        raise ValueError("Enter an amount greater than zero.")
    charged = _adviser_fee(kind, amount, adviser_fee)
    # The INSERT casts this straight to a date, so an unvalidated value raised a DB cast error and
    # surfaced as a 500. Validate the shape here and answer 400.
    if due_date:
        try:
            parsed = dt.date.fromisoformat(due_date)
        except ValueError as exc:
            raise ValueError("Enter a valid date (YYYY-MM-DD).") from exc
        today = dt.date.today()
        # A payment in may be BACKDATED — the client paid last week and the record is made now —
        # so the past is allowed on `expected_in`, within a year. A withdrawal or fee is money the
        # custodian has yet to move, so a past date on it is still a mistake.
        if parsed < today and kind != "expected_in":
            raise ValueError("The expected date is in the past.")
        if parsed < today - dt.timedelta(days=BACKDATE_LIMIT_DAYS):
            raise ValueError("The expected date is more than a year ago.")

    acct = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.value::float8 AS value, a.model_id, a.product_id, "
                    "       a.contact_id, a.currency, "
                    "       a.status::text AS status, c.enabled AS client_enabled, "
                    "       c.deleted_at AS client_archived_at, "
                    "       pl.external_id, "
                    # WHICH custodian, not an assumption of one. The gate reads the account's
                    # positions out of that custodian's own slice, and asking Seccl for a
                    # SEI-custodied account resolved no id at all — so every withdrawal on it was
                    # refused for want of a link that was there under another provider's name.
                    "       pl.provider "
                    "FROM platform.accounts a "
                    "LEFT JOIN crm.contacts c "
                    "  ON c.id = a.contact_id AND c.tenant_id = a.tenant_id "
                    # ONE ROW, LATERALLY. `platform.links` has been tenant-keyed since 0066, so
                    # an account may legitimately hold a primary link per custodian — which is the
                    # state widening this predicate from `= 'seccl'` was written for. A plain join
                    # then returns two rows and the `.one_or_none()` below raises
                    # `MultipleResultsFound`, which no handler catches: every withdrawal and model
                    # movement on that account 500s instead of being gated, and the `FOR UPDATE`
                    # lock is taken and released with the funding gate never run.
                    #
                    # The account's OWN `custodian` decides which one, because an instruction
                    # moves money at the custodian and that column names it; anything else is the
                    # fallback, so an account whose column is unset still resolves rather than
                    # refusing for want of a value the pipeline may not have stamped.
                    "LEFT JOIN LATERAL ( "
                    "  SELECT l.external_id, l.provider FROM platform.links l "
                    "   WHERE l.entity_type = 'account' AND l.entity_id = a.id "
                    "     AND l.provider = ANY(:custodians) AND l.link_type = 'primary' "
                    "     AND l.tenant_id = a.tenant_id "
                    "     AND (a.custodian IS NULL OR l.provider = a.custodian) "
                    "   ORDER BY (l.provider = a.custodian) DESC NULLS LAST, l.provider "
                    "   LIMIT 1 "
                    ") pl ON true "
                    "WHERE a.id = :a AND a.tenant_id = :t "
                    # Serialises instruction creation per account: without the lock two concurrent
                    # withdrawals both read the same headroom and both pass the cumulative gate.
                    "FOR UPDATE OF a"
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
    if acct is None:
        raise LookupError("Account not found")
    if acct["status"] not in _INSTRUCTABLE:
        raise ValueError(f"This account is {acct['status']} — no money can be moved on it.")
    # An inactive client is not a client you move money for. Nothing used to stop this: `enabled`
    # was rendered in three places and enforced nowhere.
    if acct["client_enabled"] is False:
        raise ValueError("This client is inactive. Reactivate them before raising an instruction.")
    # ...and neither is an ARCHIVED one. Archiving is a separate axis from `enabled` (`deleted_at`),
    # so checking only `enabled` left archive-then-instruct wide open — a hole introduced by adding
    # archive without extending this gate. Archive itself refuses while instructions are pending, so
    # this is the other half of the same rule.
    if acct["client_archived_at"] is not None:
        raise ValueError("This client is archived. Restore them before raising an instruction.")

    if kind in _MODEL:
        model_id = model_id or acct["model_id"]
        if model_id is None:
            raise ValueError("This account isn't linked to a model.")
        # `model_id` is client input and this session is service_role (RLS bypassed), so the
        # foreign key alone would happily accept ANOTHER firm's model — which the instruction list
        # then joins and displays by name.
        owned = (
            await session.execute(
                text("SELECT 1 FROM platform.models WHERE id = :m AND tenant_id = :t"),
                {"m": model_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        if owned is None:
            raise ValueError("That model isn't one of your firm's.")

    # WHICH fee this is, from the firm's own register. Optional: the drawer records a fee amount
    # without a type. When a type IS sent, it must be one this firm charges on this account —
    # this session is service_role, so the foreign key alone would take another firm's package.
    if kind == "adhoc_fee":
        choices = await fee_types_for_account(
            session,
            tenant_id=tenant_id,
            product_id=acct["product_id"],
            model_id=acct["model_id"],
        )
        if fee_package_id is not None and fee_package_id not in choices:
            raise ValueError("That fee type isn't one your firm charges on this account.")

    # The money gate goes LAST, so "that model isn't yours" wins over "there isn't the cash" and an
    # unauthorised model id can't be probed through a funding message.
    method, rail_fee, pass_on = await _withdrawal_rail(
        session,
        account_id=account_id,
        provider=acct["provider"],
        kind=kind,
        payment_method=payment_method,
        pass_rail_fee=pass_rail_fee,
    )
    # The client is debited the tariff only when the firm passes it on. The row still stores the
    # tariff, so a later read can say what the rail cost when the firm paid it.
    borne = rail_fee if pass_on else 0.0
    # An adviser fee on a withdrawal is an extra debit alongside the money out, funded from the same
    # cash. (`charged` is 0 on a payment in, which is never gated here anyway.)
    debit = round(amount + borne + charged, 2)

    if kind in _LEG:
        available, leg_total, committed, label, basis = await _fundable(
            session,
            tenant_id=tenant_id,
            account_id=account_id,
            external_id=acct["external_id"],
            provider=acct["provider"],
            kind=kind,
        )
        if debit > available:
            raise ValueError(
                _refusal(
                    kind=kind,
                    available=available,
                    leg_total=leg_total,
                    committed=committed,
                    label=label,
                    basis=basis,
                )
            )

    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO platform.instructions "
                    "  (uid, tenant_id, account_id, contact_id, kind, amount, rail_fee, "
                    "   adviser_fee, payment_method, pass_rail_fee, model_id, "
                    "   fee_package_id, adviser_fee_package_id, due_date, note, category, "
                    "   auto_disinvest, created_by) "
                    "VALUES (:uid, :t, :a, :c, :k, :amt, :rail_fee, :adviser_fee, :method, "
                    "        :pass_on, :m, :fee_package, :adviser_fee_package, "
                    "        CAST(:due AS date), :note, :category, :auto_disinvest, "
                    # `created_by` resolves the actor WITHIN this tenant on purpose (the column
                    # answers "which of the firm's people raised this"). A Graphene operator
                    # acting via the book-write proxy has no auth.users row here, so the subquery
                    # yields NULL — their identity is on the audit.log row (auth.uid() from the
                    # published claims), which is the attribution record, not this FK.
                    "        (SELECT id FROM auth.users WHERE workos_user_id = :by "
                    "           AND tenant_id = :t LIMIT 1)) "
                    "RETURNING uid, kind, amount::float8 AS amount, "
                    "          rail_fee::float8 AS rail_fee, "
                    "          adviser_fee::float8 AS adviser_fee, payment_method, pass_rail_fee, "
                    "          status, due_date, category, note, model_id, fee_package_id, "
                    "          adviser_fee_package_id, auto_disinvest, account_id, created_at"
                ),
                {
                    "uid": new_id(),
                    "t": tenant_id,
                    "a": account_id,
                    "c": acct["contact_id"],
                    "k": kind,
                    "amt": amount,
                    "rail_fee": rail_fee,
                    "adviser_fee": charged,
                    "method": method,
                    "pass_on": pass_on,
                    "m": model_id,
                    "fee_package": fee_package_id,
                    "adviser_fee_package": adviser_fee_package_id,
                    "due": due_date or None,
                    "note": note or None,
                    "category": category,
                    "auto_disinvest": auto_disinvest,
                    "by": created_by_workos,
                },
            )
        )
        .mappings()
        .one()
    )
    created = dict(row)
    # Carried on the outbound record (no column on the instruction itself): the inverse of a
    # withdrawal's auto_disinvest, read by the invest-on-landing step. Only a pay-in invests.
    created["auto_invest"] = auto_invest if kind == "expected_in" else None
    await outbound.record_instruction(
        session,
        tenant_id=tenant_id,
        instruction=created,
        account_id=account_id,
        contact_id=acct["contact_id"],
        currency=str(acct["currency"] or "GBP"),
    )
    return created


async def list_for_contact(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    account_id: int | None = None,
) -> list[dict[str, object]]:
    """Every instruction raised for a client (newest first), with account, model and fee-type names.

    ``account_id`` is returned as well as the account's name: the portal has to be able to work out
    which account each pending row is a claim against, or its own "available" figure states the
    provider's whole cash leg while some of it is already promised away.

    ``fee_package_name`` is what makes the ad hoc fee's type worth capturing — a charge recorded
    under a kind nothing reads back is a field that only looks like a control."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT i.uid, i.kind, i.amount::float8 AS amount, "
                    "       i.rail_fee::float8 AS rail_fee, "
                    "       i.adviser_fee::float8 AS adviser_fee, "
                    "       i.payment_method, i.pass_rail_fee, "
                    "       i.status, i.due_date, "
                    "       i.note, i.created_at, i.account_id, a.uid AS account_uid, "
                    "       a.name AS account_name, m.name AS model_name, "
                    "       i.fee_package_id, f.name AS fee_package_name "
                    "FROM platform.instructions i "
                    "JOIN platform.accounts a ON a.id = i.account_id "
                    "LEFT JOIN platform.models m ON m.id = i.model_id "
                    "LEFT JOIN platform.fee_packages f ON f.id = i.fee_package_id "
                    "WHERE i.tenant_id = :t AND i.contact_id = :c "
                    "  AND (CAST(:a AS int) IS NULL OR i.account_id = CAST(:a AS int)) "
                    "ORDER BY i.created_at DESC, i.id DESC"
                ),
                {"t": tenant_id, "c": contact_id, "a": account_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def set_account_model(
    session: AsyncSession, *, tenant_id: int, account_id: int, model_id: int | None
) -> dict[str, object]:
    """Associate a model with an account, change it, or clear it (``model_id=None``).

    The mandate: an account carrying a ``model_id`` is run to a discretionary model (``crm.py``
    derives ``discretionary``/``model_name`` from exactly this column) and is the only kind of
    account a model top-up or model withdrawal can be raised against. Nothing wrote this column, so
    the firm's models — and ``GET /catalog/models`` — were unreachable from the product.

    Tenant-scoped on both sides: the session is ``service_role``, so a ``model_id`` from another
    firm would otherwise satisfy the foreign key and then be displayed as this firm's mandate.

    Refuses (``ValueError``) while the account has PENDING model instructions. Those rows are claims
    against the model that was in force when they were raised; re-pointing or clearing the mandate
    underneath them would leave a live claim describing a mandate the account no longer has.
    ``LookupError`` if the account isn't the caller's."""
    acct = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.model_id, a.status::text AS status "
                    "FROM platform.accounts a "
                    "WHERE a.id = :a AND a.tenant_id = :t "
                    "FOR UPDATE OF a"
                ),
                {"a": account_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if acct is None:
        raise LookupError("Account not found")

    if model_id is not None:
        model_name = (
            await session.execute(
                text(
                    "SELECT name FROM platform.models "
                    "WHERE id = :m AND tenant_id = :t AND enabled IS NOT FALSE"
                ),
                {"m": model_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        if model_name is None:
            raise ValueError("That model isn't one of your firm's.")
    else:
        model_name = None

    incompatible_fee = (
        await session.execute(
            text(
                "SELECT 1 FROM platform.account_fee_packages afp "
                "JOIN platform.fee_packages fp "
                "  ON fp.id = afp.fee_package_id AND fp.tenant_id = afp.tenant_id "
                "WHERE afp.account_id = :a AND afp.tenant_id = :t "
                "  AND fp.model_id IS NOT NULL "
                "  AND fp.model_id IS DISTINCT FROM CAST(:m AS int) "
                "LIMIT 1"
            ),
            {"a": account_id, "t": tenant_id, "m": model_id},
        )
    ).scalar_one_or_none()
    if incompatible_fee is not None:
        raise ValueError(
            "One or more of this account's fee packages are tied to its current model. Change them "
            "before changing the mandate."
        )

    if model_id != acct["model_id"]:
        pending = (
            await session.execute(
                text(
                    "SELECT count(*) FROM platform.instructions "
                    "WHERE tenant_id = :t AND account_id = :a AND status = 'pending' "
                    "  AND kind = ANY(:kinds)"
                ),
                {"t": tenant_id, "a": account_id, "kinds": list(_MODEL)},
            )
        ).scalar_one()
        if pending:
            raise ValueError(
                f"This account has {pending} pending model instruction(s). Cancel or complete "
                "them before changing its mandate."
            )

    row = (
        (
            await session.execute(
                text(
                    "UPDATE platform.accounts SET model_id = CAST(:m AS int) "
                    "WHERE id = :a AND tenant_id = :t "
                    "RETURNING id AS account_id, model_id, "
                    "          model_id IS NOT NULL AS discretionary"
                ),
                {"m": model_id, "a": account_id, "t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    return {**dict(row), "model_name": model_name}


async def set_account_fee_packages(
    session: AsyncSession, *, tenant_id: int, account_id: int, fee_package_ids: list[int]
) -> dict[str, object]:
    """Atomically replace an account's ordered set of equal fee-package assignments.

    Compatibility is checked here, not trusted to the picker: every package narrowed to a product
    or model must match the account. The account is locked before validation and the old set is
    deleted only after every requested id passes, so a bad id leaves the assignment unchanged.
    Input order is retained for display only; no package is primary.
    """
    if len(fee_package_ids) != len(set(fee_package_ids)):
        raise ValueError("Choose each fee package only once.")

    account = (
        (
            await session.execute(
                text(
                    "SELECT id, product_id, model_id FROM platform.accounts "
                    "WHERE id = :a AND tenant_id = :t FOR UPDATE"
                ),
                {"a": account_id, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if account is None:
        raise LookupError("Account not found")

    packages: list[dict[str, object]] = []
    if fee_package_ids:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, name FROM platform.fee_packages "
                        "WHERE id = ANY(:fees) AND tenant_id = :t AND enabled IS NOT FALSE "
                        "  AND (product_id IS NULL OR product_id = CAST(:p AS int)) "
                        "  AND (model_id IS NULL OR model_id = CAST(:m AS int))"
                    ),
                    {
                        "fees": fee_package_ids,
                        "t": tenant_id,
                        "p": account["product_id"],
                        "m": account["model_id"],
                    },
                )
            )
            .mappings()
            .all()
        )
        by_id = {int(row["id"]): row["name"] for row in rows}
        if len(by_id) != len(fee_package_ids):
            raise ValueError("That fee package isn't available for this account.")
        packages = [
            {"id": fee_id, "name": by_id[fee_id], "sort_order": sort_order}
            for sort_order, fee_id in enumerate(fee_package_ids)
        ]

    await session.execute(
        text("DELETE FROM platform.account_fee_packages WHERE account_id = :a AND tenant_id = :t"),
        {"a": account_id, "t": tenant_id},
    )
    if fee_package_ids:
        await session.execute(
            text(
                "INSERT INTO platform.account_fee_packages "
                "  (tenant_id, account_id, fee_package_id, sort_order) "
                "SELECT :t, :a, f.id, (f.ordinality - 1)::smallint "
                "FROM unnest(CAST(:fees AS int[])) WITH ORDINALITY AS f(id, ordinality)"
            ),
            {"t": tenant_id, "a": account_id, "fees": fee_package_ids},
        )

    # A singular value would falsely designate a primary package. Clear the deprecated compatibility
    # column on every plural write; older rows remain backfilled by migration 0110.
    await session.execute(
        text("UPDATE platform.accounts SET fee_package_id = NULL WHERE id = :a AND tenant_id = :t"),
        {"a": account_id, "t": tenant_id},
    )
    return {"account_id": account_id, "fee_packages": packages}


async def cancel_instruction(session: AsyncSession, *, tenant_id: int, uid: str) -> bool:
    """Cancel a pending instruction (tenant-scoped). Returns True if one was cancelled.

    Refused when the outbound transaction has already been accepted by a custodian — cancelling
    the local row would release the cash gate while the vendor still has the order. That policy
    lives in ``outbound.cancel_origin``.
    """
    await outbound.cancel_origin(session, tenant_id=tenant_id, origin="instruction", origin_uid=uid)
    res = await session.execute(
        text(
            "UPDATE platform.instructions SET status = 'cancelled' "
            "WHERE uid = :u AND tenant_id = :t AND status = 'pending' RETURNING uid"
        ),
        {"u": uid, "t": tenant_id},
    )
    return res.first() is not None
