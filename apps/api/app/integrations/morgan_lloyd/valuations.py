"""Push one account's valuation to Morgan Lloyd (``cam_createpolicyvaluation``).

**THIS CLOSES THE GAP `services/morgan-lloyd/models/sink_valuations.py` DOCUMENTS ABOUT ITSELF.**
Its `_resolve_valuation` returns `None` unconditionally, so every scheme's ledger row on every run
reads `skipped / no policy reference or fund splits`, and it names both halves of why:

* the POLICY reference, which nothing recorded — `sync.py` recorded the contact and the scheme.
  It does now (``sync.LINK_POLICY``), which is what makes this module possible;
* the FUND SPLITS, which live in the tenant's lake slice and were not joined to ML policies.
  Here they are: ``app.lake.holdings.firm_holdings`` is the conformed per-firm positions relation
  the withdrawal gate is already checked on, so the figures we send a custodian and the figures a
  money control refuses on cannot drift apart.

THE NIGHTLY SINK IS STILL NOT WIRED, and deliberately not wired here. It runs in the warehouse
process, where `platform.links` (the portals DB) is not reachable — a chain the pipeline would
have to grow, not a mapping. So this is the per-account door, which is what an adviser or an
operator actually asks for; the nightly whole-book push stays a follow-up with its blocker now
half-removed rather than being quietly duplicated.

TWO THINGS THE FIELD LIST DOES NOT TELL YOU, both already recorded in
``morgan_lloyd.endpoints.valuations`` and both load-bearing here:

* **the valuation is against a POLICY, not a scheme.** Sending `MLSIPP/…` is a plausible
  identifier of the wrong kind, refused as "not found";
* **``PolicyValue`` must equal the sum of the fund splits.** ``PolicyValuation.from_splits``
  derives it so the two cannot disagree, and it is derived from the HOLDINGS rather than taken
  from ``platform.accounts.value`` for exactly that reason — those two numbers are computed by
  different paths and a mismatch would be refused with neither of them named.

**THERE IS NO UPDATE, AND NO READ-BACK.** The vendor documents one operation for policy
valuations, and ``cam_policyvaluations`` is one of the 85 `cam_*` tables our service principal
cannot read (403). So a valuation cannot be corrected and cannot be verified through the API:
`audit.api_log` is the only record that one was sent, which is why a wrong figure must be refused
here rather than fixed later.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any

import httpx
from morgan_lloyd import MorganLloydError

from app.integrations.morgan_lloyd.benefits import (
    MorganLloydUnavailable,
    _explain,
    _settings,
    configured,
    unconfigured,
)

logger = logging.getLogger(__name__)


class NoPolicyRecorded(RuntimeError):
    """This account has no Morgan Lloyd policy, so there is nothing to file a valuation against.

    Its own error rather than a ValueError, because the fix is a specific action — run
    `POST /crm/contacts/{uid}/sync-to-morgan-lloyd`, which creates the policy and records it —
    and the caller can say that instead of listing a missing field.
    """


class NothingToValue(RuntimeError):
    """The account holds no priceable position on the valuation date.

    NOT sent as a zero. A policy valued at nothing is a statement that the client's pension is
    empty, and the likelier cause of an empty holdings read is that the provider chain has not
    run — which `app.lake.holdings` raises for separately, but a slice that exists and is empty
    reaches here indistinguishable from a genuinely empty account. Refusing means somebody looks;
    sending 0 puts a wrong figure on a client's permanent record that cannot be corrected.
    """


def _money(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _is_cash(holding: dict[str, Any]) -> bool:
    """Whether this position is cash, by the estate's ONE definition of that.

    `instructions.is_cash_holding` owns the rule (`asset_type` plus the money-market-fund names
    that are cash by name rather than by type) and `app.lake.holdings` maps the vendor's own
    `position_type` into the slot it reads. Re-deriving it here would let the cash a screen shows
    and the cash a custodian is told about drift apart.
    """
    from app.instructions import is_cash_holding

    return bool(is_cash_holding(holding))


def build_splits(holdings: list[dict[str, Any]], *, as_of: str) -> tuple[list[Any], list[str]]:
    """The account's priceable positions as vendor `FundSplit`s.

    CASH IS EXCLUDED, and that is a real decision rather than a filter. The vendor's split takes
    a holding quantity, a unit price and an ISIN: cash has no price and no ISIN, so it cannot be
    expressed as one — and because `PolicyValue` must equal the sum of the splits, including cash
    at a notional price of 1.0 would be inventing an instrument to make an arithmetic identity
    hold. The consequence is stated rather than hidden: what we send is the INVESTED value, and
    `cam_getscheme` already reports the scheme's bank balance separately (it can be negative),
    which is the vendor's own model of the same distinction.
    """
    from morgan_lloyd.endpoints.valuations import FundSplit

    splits: list[Any] = []
    unpriced: list[str] = []
    for holding in holdings:
        if _is_cash(holding):
            continue
        quantity = holding.get("quantity")
        price = holding.get("price")
        value = holding.get("value")
        if quantity is None or price is None or value is None:
            # RECORDED, never defaulted and never silently dropped. A split with a guessed price
            # changes the total, and the total is the number the client sees — but so does an
            # omitted holding, and the comment here used to say "COUNTED by the caller" while no
            # caller counted anything: a £200,000 account holding one £20,000 unpriced position
            # filed as a £180,000 policy, reported as a clean success. `unpriced` is what makes
            # the caller able to refuse.
            unpriced.append(
                str(holding.get("asset_name") or holding.get("asset_id") or "an unnamed holding")
            )
            continue
        splits.append(
            FundSplit(
                name=str(holding.get("asset_name") or holding.get("asset_id") or "Unknown"),
                valuation_holding=float(_money(quantity)),
                price=float(_money(price)),
                market_value=float(_money(value)),
                # The vendor's field table types this `double` and it is a DATE taking
                # YYYY-MM-DD — their table is wrong about the type, so the holding's own as-of
                # date is used where it has one and the valuation date otherwise.
                price_date=str(holding.get("as_of") or as_of)[:10],
                isin=str(holding["isin"]) if holding.get("isin") else None,
            )
        )
    return splits, unpriced


def build_valuation(
    holdings: list[dict[str, Any]], *, policy_reference: str, as_of: str | dt.date | None = None
) -> Any:
    """One `PolicyValuation` for this policy, with its value derived from its splits."""
    from morgan_lloyd.endpoints.valuations import PolicyValuation

    day = str(as_of or dt.date.today())[:10]
    splits, unpriced = build_splits(holdings, as_of=day)
    # REFUSED, not filed short. This module's header states the rule: the vendor documents no
    # update and `cam_policyvaluations` is 403 for our principal, so "a wrong figure must be
    # refused here rather than fixed later" — and a holding with no price makes the total wrong in
    # a way nobody can see, since `PolicyValue` is derived from the splits. Named, so an operator
    # can price the position rather than guess which one it was.
    if unpriced:
        raise NothingToValue(
            "These holdings have no price, so the policy total would understate the pension: "
            + ", ".join(sorted(unpriced))
            + ". Morgan Lloyd accepts no correction to a filed valuation, so this is refused "
            "rather than sent short."
        )
    if not splits:
        raise NothingToValue(
            "This account holds no priceable position, so there is no valuation to send. A "
            "policy valued at nothing states that the client's pension is empty, and an empty "
            "holdings read is more often a provider chain that has not run."
        )
    # from_splits, never a value we were handed: the vendor requires PolicyValue to equal the sum
    # of the splits and does not say what it does otherwise, so the two must not be able to
    # disagree.
    return PolicyValuation.from_splits(policy_reference, day, splits)


async def push_valuation(
    *,
    policy_reference: str | None,
    holdings: list[dict[str, Any]],
    as_of: str | dt.date | None = None,
) -> dict[str, Any]:
    """Send one account's valuation and report what was sent.

    The caller resolves the account through RLS, reads its `policy` link and its holdings, and
    passes both — this module reaches no database, the seam every other adapter here keeps.

    Raises :class:`NoPolicyRecorded` without a policy (409), :class:`NothingToValue` with nothing
    priceable (409), ``ValueError`` for a valuation that does not add up (422),
    ``MorganLloydNotConfigured`` (503) and ``MorganLloydUnavailable`` (502).
    """
    if not policy_reference or not str(policy_reference).strip():
        raise NoPolicyRecorded(
            "This account has no Morgan Lloyd policy number, so a valuation cannot be filed "
            "against it. Run the Morgan Lloyd sync for the client first — it creates the policy "
            "and records the reference."
        )
    valuation = build_valuation(
        holdings, policy_reference=str(policy_reference).strip(), as_of=as_of
    )

    if not configured():
        raise unconfigured("MORGAN_LLOYD_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET")
    settings = _settings()
    provider = settings.investment_provider.strip()
    if not provider:
        # Refused rather than attempted: without it the vendor refuses in a way that reads as a
        # scheme problem (`docs/morgan-lloyd/README.md`).
        raise unconfigured("MORGAN_LLOYD_INVESTMENT_PROVIDER_ID")
    from morgan_lloyd.client import MorganLloydClient

    try:
        async with MorganLloydClient(settings=settings) as api:
            response = await api.push_policy_valuations(provider.strip("{}"), [valuation])
    # THE CLIENT DOES NOT RAISE httpx. `morgan_lloyd._base` wraps every transport and status
    # error into `MorganLloydApiError`, which descends from `MorganLloydError` and NOT from
    # `httpx.HTTPError` — so an `except httpx.HTTPError` here caught nothing and a rejected vendor
    # call, or an Azure Conditional Access block, escaped as an unhandled 500 with a stack trace
    # instead of the 502 this route's docstring promises. httpx stays in the tuple for anything
    # raised outside the client.
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc

    # Reported back rather than merely logged, because the vendor gives us no way to read it: our
    # own answer is the only record that this figure was filed, and `audit.api_log` keeps the
    # request beside it.
    return {
        "policy_reference": valuation.policy_reference,
        "valuation_date": valuation.valuation_date,
        "policy_value": valuation.policy_value,
        "splits": len(valuation.fund_splits),
        "excluded_cash": sum(1 for holding in holdings if _is_cash(holding)),
        "verifiable": False,
        "response": response,
    }
