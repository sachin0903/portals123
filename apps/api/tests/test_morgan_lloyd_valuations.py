"""Filing one account's valuation at Morgan Lloyd.

The vendor gives no read-back (`cam_policyvaluations` is 403 for our service principal) and no
update — one operation, and a wrong figure cannot be corrected. That is what makes the arithmetic
worth testing this hard: every guard here exists because its failure mode is a permanently wrong
number on a client's pension record with nothing to notice it.

Four decisions carry the weight:

* the value is DERIVED from the splits, never taken from `platform.accounts.value` — the vendor
  requires the two to be equal and does not say what it does otherwise;
* cash is EXCLUDED, because a vendor split needs a price and an ISIN and cash has neither;
* an empty holdings read is a REFUSAL, not a zero valuation;
* the reference is the POLICY number, and a scheme reference in its place is refused as
  "not found" — the `cam_schemerefs` mistake in another costume.
"""

import datetime as dt
import logging
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.integrations.morgan_lloyd import valuations as adapter
from app.integrations.morgan_lloyd.benefits import MorganLloydNotConfigured

_PRESENT = "configured"

FUND = {
    "asset_id": "GB00B3X7QG63",
    "asset_name": "Vanguard LifeStrategy 60% Equity",
    "isin": "GB00B3X7QG63",
    "asset_type": "Stock",
    "quantity": 1200.0,
    "price": 2.4515,
    "value": 2941.80,
    "as_of": "2026-09-03",
}
SECOND_FUND = {
    **FUND,
    "asset_id": "GB00BPN5P238",
    "asset_name": "Fidelity Index World",
    "isin": "GB00BPN5P238",
    "quantity": 500.0,
    "price": 3.10,
    "value": 1550.00,
}
CASH = {
    "asset_id": "GBP",
    "asset_name": "Sterling",
    "isin": None,
    "asset_type": "Cash",
    "quantity": None,
    "price": None,
    "value": 812.44,
    "as_of": "2026-09-03",
}

POLICY = "acc000000001"


class _FakeApi:
    def __init__(self, answer: Any = None) -> None:
        self.answer = answer if answer is not None else {"responseKey": 0, "message": "OK"}
        self.pushed: list[tuple[str, list[Any]]] = []

    async def __aenter__(self) -> "_FakeApi":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def push_policy_valuations(self, provider: str, valuations: list[Any]) -> Any:
        self.pushed.append((provider, valuations))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _install(
    monkeypatch: pytest.MonkeyPatch, stub: _FakeApi, *, provider: str = "firm-guid"
) -> None:
    class _Settings:
        tenant_id = client_id = client_secret = _PRESENT
        adviser_firm_id = provider
        investment_provider_id = ""

        @property
        def investment_provider(self) -> str:
            return self.investment_provider_id or self.adviser_firm_id

    monkeypatch.setattr(adapter, "configured", lambda: True)
    monkeypatch.setattr(adapter, "_settings", lambda: _Settings())
    import morgan_lloyd.client as vendor

    monkeypatch.setattr(vendor, "MorganLloydClient", lambda settings=None: stub)


class TestBuildSplits:
    def test_a_fund_becomes_a_split(self) -> None:
        splits, _ = adapter.build_splits([FUND], as_of="2026-09-04")
        assert len(splits) == 1
        assert splits[0].name == FUND["asset_name"]
        assert splits[0].isin == FUND["isin"]
        assert splits[0].valuation_holding == 1200.0
        assert splits[0].price == 2.4515
        assert splits[0].market_value == 2941.80

    def test_cash_is_excluded_rather_than_priced_at_one(self) -> None:
        """A vendor split needs a quantity, a unit price and an ISIN, and cash has none of them.
        Including it at a notional price of 1.0 would be inventing an instrument to make the
        PolicyValue-equals-splits identity hold."""
        splits, _ = adapter.build_splits([FUND, CASH], as_of="2026-09-04")
        assert len(splits) == 1
        assert splits[0].isin == FUND["isin"]

    def test_cash_by_name_is_excluded_too(self) -> None:
        """The estate has ONE definition of cash (`instructions.is_cash_holding`) and it catches
        the money-market funds that are cash by name rather than by type. Re-deriving it here
        would let the cash a screen shows and the cash a custodian is told about drift."""
        mmf = {**FUND, "asset_type": "Stock", "asset_name": "Royal London Cash Plus"}
        assert adapter.build_splits([mmf], as_of="2026-09-04") == ([], [])

    def test_a_holding_with_no_price_is_skipped_and_NAMED(self) -> None:
        """A guessed price changes the total, and the total is the number the client sees — but so
        does an omitted holding. The comment here used to say the skip was "COUNTED by the caller"
        and no caller counted anything, so a £200,000 account with one £20,000 unpriced position
        filed as a £180,000 policy and reported a clean success."""
        unpriced = {**SECOND_FUND, "price": None, "asset_name": "Suspended Property Fund"}
        splits, skipped = adapter.build_splits([FUND, unpriced], as_of="2026-09-04")
        assert len(splits) == 1
        assert skipped == ["Suspended Property Fund"]

    def test_the_price_date_is_the_holdings_own_where_it_has_one(self) -> None:
        """The vendor types `PriceDate` as a `double` and it is a DATE taking YYYY-MM-DD — their
        field table is wrong about the type."""
        splits, _ = adapter.build_splits([FUND], as_of="2026-09-04")
        assert splits[0].price_date == "2026-09-03"

    def test_the_valuation_date_is_the_fallback_price_date(self) -> None:
        splits, _ = adapter.build_splits([{**FUND, "as_of": None}], as_of="2026-09-04")
        assert splits[0].price_date == "2026-09-04"

    def test_an_isin_less_fund_still_becomes_a_split(self) -> None:
        """`Isin` is the vendor's only optional split member."""
        splits, _ = adapter.build_splits([{**FUND, "isin": None}], as_of="2026-09-04")
        assert len(splits) == 1
        assert splits[0].isin is None

    def test_a_nameless_holding_falls_back_to_its_id(self) -> None:
        splits, _ = adapter.build_splits([{**FUND, "asset_name": None}], as_of="2026-09-04")
        assert splits[0].name == FUND["asset_id"]


class TestBuildValuation:
    def test_the_value_is_the_sum_of_the_splits(self) -> None:
        """DERIVED, never handed in: the vendor requires the equality and does not say what it
        does otherwise, so the two must not be able to disagree."""
        valuation = adapter.build_valuation(
            [FUND, SECOND_FUND], policy_reference=POLICY, as_of="2026-09-04"
        )
        assert valuation.policy_value == pytest.approx(2941.80 + 1550.00)
        assert len(valuation.fund_splits) == 2

    def test_the_cash_balance_is_not_in_the_total(self) -> None:
        """The consequence of excluding cash, stated: what we send is the INVESTED value, and
        `cam_getscheme` reports the scheme's bank balance separately."""
        valuation = adapter.build_valuation(
            [FUND, CASH], policy_reference=POLICY, as_of="2026-09-04"
        )
        assert valuation.policy_value == pytest.approx(2941.80)

    def test_it_is_filed_against_the_policy_reference(self) -> None:
        valuation = adapter.build_valuation([FUND], policy_reference=POLICY, as_of="2026-09-04")
        assert valuation.policy_reference == POLICY

    def test_decimal_values_survive_as_json_numbers(self) -> None:
        valuation = adapter.build_valuation(
            [{**FUND, "quantity": Decimal("1200"), "value": Decimal("2941.80")}],
            policy_reference=POLICY,
            as_of="2026-09-04",
        )
        assert isinstance(valuation.policy_value, float)
        assert isinstance(valuation.fund_splits[0].valuation_holding, float)

    def test_nothing_priceable_is_refused_not_valued_at_zero(self) -> None:
        """A policy valued at nothing states the client's pension is empty, and an empty holdings
        read is more often a provider chain that has not run."""
        with pytest.raises(adapter.NothingToValue):
            adapter.build_valuation([], policy_reference=POLICY, as_of="2026-09-04")

    def test_a_cash_only_account_is_refused_too(self) -> None:
        with pytest.raises(adapter.NothingToValue):
            adapter.build_valuation([CASH], policy_reference=POLICY, as_of="2026-09-04")

    def test_a_date_object_is_accepted_and_truncated(self) -> None:
        valuation = adapter.build_valuation(
            [FUND], policy_reference=POLICY, as_of=dt.date(2026, 9, 4)
        )
        assert valuation.valuation_date == "2026-09-04"

    def test_no_date_defaults_to_today(self) -> None:
        valuation = adapter.build_valuation([FUND], policy_reference=POLICY)
        assert valuation.valuation_date == dt.date.today().isoformat()


class TestPushValuation:
    async def test_sends_the_valuation_and_reports_what_was_sent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _FakeApi()
        _install(monkeypatch, stub)
        result = await adapter.push_valuation(
            policy_reference=POLICY, holdings=[FUND, SECOND_FUND, CASH], as_of="2026-09-04"
        )

        assert result["policy_reference"] == POLICY
        assert result["policy_value"] == pytest.approx(4491.80)
        assert result["splits"] == 2
        assert result["excluded_cash"] == 1
        provider, sent = stub.pushed[0]
        assert provider == "firm-guid"
        assert sent[0].policy_reference == POLICY

    async def test_the_answer_says_it_cannot_be_verified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`cam_policyvaluations` is 403 for our service principal, so our own answer is the only
        record that the figure was filed. Saying so beats a caller assuming it can re-read it."""
        _install(monkeypatch, _FakeApi())
        result = await adapter.push_valuation(policy_reference=POLICY, holdings=[FUND])
        assert result["verifiable"] is False

    @pytest.mark.parametrize("absent", [None, "", "   "])
    async def test_no_policy_recorded_says_what_to_run(
        self, monkeypatch: pytest.MonkeyPatch, absent: str | None
    ) -> None:
        """Its own error, because the fix is a specific action rather than a missing field."""
        stub = _FakeApi()
        _install(monkeypatch, stub)
        with pytest.raises(adapter.NoPolicyRecorded, match="sync"):
            await adapter.push_valuation(policy_reference=absent, holdings=[FUND])
        assert stub.pushed == []

    async def test_a_scheme_reference_is_not_silently_accepted_as_a_policy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """We cannot tell the vendor's answer apart from "not found", so the only defence is that
        the caller reads the `policy` link and never the `primary` one. This pins that the value
        is passed through verbatim — a mapper that "helpfully" derived one from the other is what
        the `cam_schemerefs` incident was."""
        stub = _FakeApi()
        _install(monkeypatch, stub)
        await adapter.push_valuation(policy_reference="MLSIPP/01417", holdings=[FUND])
        assert stub.pushed[0][1][0].policy_reference == "MLSIPP/01417"

    async def test_nothing_to_value_never_reaches_the_vendor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _FakeApi()
        _install(monkeypatch, stub)
        with pytest.raises(adapter.NothingToValue):
            await adapter.push_valuation(policy_reference=POLICY, holdings=[CASH])
        assert stub.pushed == []

    async def test_no_credentials_is_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(adapter, "configured", lambda: False)
        with pytest.raises(MorganLloydNotConfigured):
            await adapter.push_valuation(policy_reference=POLICY, holdings=[FUND])

    async def test_no_provider_guid_is_refused_rather_than_attempted(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Without it the vendor refuses in a way that reads as a scheme problem.

        The setting reaches the LOG and not the message: whoever sees this cannot set it."""
        stub = _FakeApi()
        _install(monkeypatch, stub, provider="")
        with caplog.at_level(logging.WARNING):
            with pytest.raises(MorganLloydNotConfigured, match="unavailable for this firm"):
                await adapter.push_valuation(policy_reference=POLICY, holdings=[FUND])
        assert stub.pushed == []
        assert "MORGAN_LLOYD_INVESTMENT_PROVIDER_ID" in caplog.text

    async def test_a_braced_provider_guid_is_unbraced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _FakeApi()
        _install(monkeypatch, stub, provider="{firm-guid}")
        await adapter.push_valuation(policy_reference=POLICY, holdings=[FUND])
        assert stub.pushed[0][0] == "firm-guid"

    async def test_a_vendor_failure_is_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _FakeApi(httpx.ConnectError("boom"))
        _install(monkeypatch, stub)
        with pytest.raises(adapter.MorganLloydUnavailable):
            await adapter.push_valuation(policy_reference=POLICY, holdings=[FUND])

    async def test_it_goes_through_the_typed_push_not_the_raw_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`create_policy_valuation` takes raw dicts and skips the value-equals-splits check.
        The stub exposes only the typed method, so a revert to the raw one is an AttributeError
        here rather than an unvalidated figure at the vendor — the posture
        `services/morgan-lloyd/tests/test_valuations_sink.py` already takes."""
        stub = _FakeApi()
        _install(monkeypatch, stub)
        assert not hasattr(stub, "create_policy_valuation")
        await adapter.push_valuation(policy_reference=POLICY, holdings=[FUND])
        assert len(stub.pushed) == 1


class TestTheVendorsOwnEqualityCheck:
    """`PolicyValuation` refuses a value that does not match its splits. Asserted here because
    `build_valuation` deriving the value is what makes it unreachable — and a future change that
    let a caller pass the value in would make it reachable again."""

    def test_a_mismatch_is_refused_by_the_model(self) -> None:
        from morgan_lloyd.endpoints.valuations import FundSplit, PolicyValuation

        split = FundSplit(
            name="Fund A",
            valuation_holding=100.0,
            price=1.0,
            market_value=100.0,
            price_date="2026-09-04",
        )
        with pytest.raises(ValueError, match="does not match"):
            PolicyValuation(
                policy_reference=POLICY,
                policy_value=9999.0,
                valuation_date="2026-09-04",
                fund_splits=[split],
            )

    def test_our_derived_value_always_satisfies_it(self) -> None:
        """Rounding: the parts are rounded independently upstream, so the model allows a penny
        per split — and a derived total cannot exceed that by construction."""
        valuation = adapter.build_valuation(
            [FUND, SECOND_FUND, {**FUND, "value": 0.005}],
            policy_reference=POLICY,
            as_of="2026-09-04",
        )
        assert valuation.policy_value == pytest.approx(
            sum(split.market_value for split in valuation.fund_splits)
        )


class TestAnUnpriceableHoldingRefusesThePush:
    """This module's header states the rule and this is where it applies: the vendor documents no
    update and `cam_policyvaluations` is 403 for our principal, so "a wrong figure must be refused
    here rather than fixed later". `PolicyValue` is derived from the splits, so a holding with no
    price understates the pension by exactly its value — on a figure that can neither be corrected
    nor read back.
    """

    def test_it_refuses_and_names_the_holding(self) -> None:
        unpriced = {**SECOND_FUND, "price": None, "asset_name": "Suspended Property Fund"}
        with pytest.raises(adapter.NothingToValue, match="Suspended Property Fund"):
            adapter.build_valuation(
                holdings=[FUND, unpriced],
                policy_reference="acc000000001",
                as_of="2026-09-04",
            )

    def test_a_fully_priced_account_still_files(self) -> None:
        out = adapter.build_valuation(
            holdings=[FUND, SECOND_FUND], policy_reference="acc000000001", as_of="2026-09-04"
        )
        assert len(out.fund_splits) == 2
