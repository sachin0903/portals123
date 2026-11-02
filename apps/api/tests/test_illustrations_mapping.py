"""Client record -> CTC1 message mapping.

The endpoint itself needs Postgres and object storage, but the mapping is where the bugs live:
which streams get built, what keys they carry, and which stream an initial adviser fee attaches
to. Those are pure functions, tested here directly.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.integrations import ctc
from app.integrations.ctc import build_request
from app.routers import illustrations
from app.routers.illustrations import (
    IllustrationInput,
    _adviser_fees,
    _annuity_basis,
    _document_name,
    _investor,
    _streams,
    illustration_facts,
    recorded_inputs,
    resolved_exit_age,
)

pytestmark = pytest.mark.anyio


class TestInvestor:
    def test_maps_the_client_record(self) -> None:
        investor = _investor(
            {
                "title": "Mr",
                "first_name": "Max",
                "surname": "Oakden",
                "dob": "1960-04-02",
            },
            67,
        )
        assert investor.title == "Mr"
        assert investor.forename == "Max"
        assert investor.surname == "Oakden"
        assert investor.date_of_birth == "1960-04-02"
        assert investor.exit_age == 67
        # We illustrate the member, not a life assured on a bond.
        assert investor.life_type == "None"

    def test_absent_fields_stay_absent_rather_than_becoming_the_string_none(self) -> None:
        investor = _investor(
            {"title": None, "first_name": None, "surname": "Oakden", "dob": None}, None
        )
        assert investor.title is None
        assert investor.forename is None
        assert investor.date_of_birth is None
        assert investor.exit_age is None

    def test_a_date_object_is_stringified(self) -> None:
        from datetime import date

        investor = _investor({"dob": date(1960, 4, 2), "surname": "X"}, None)
        assert investor.date_of_birth == "1960-04-02"


class TestIllustrationFacts:
    def test_a_typed_age_is_illustrated_and_a_stored_one_is_not_overwritten(self) -> None:
        # The form always asks, empty. What they type is this quote. The record's 65 stays.
        facts = illustration_facts(
            recorded_age=65,
            recorded_marital="married",
            requested_age=60,
            requested_marital="single",
        )
        assert facts.exit_age == 60
        assert facts.marital_status == "married"
        assert facts.fill == {}

    def test_a_gap_is_filled_from_the_request_and_only_the_gap_is_written(self) -> None:
        facts = illustration_facts(
            recorded_age=None,
            recorded_marital="",
            requested_age=67,
            requested_marital="widowed",
        )
        assert (facts.exit_age, facts.marital_status) == (67, "widowed")
        assert facts.fill == {"retirement_age": 67, "marital_status": "widowed"}

        one = illustration_facts(
            recorded_age=65,
            recorded_marital=None,
            requested_age=70,
            requested_marital="single",
        )
        assert one.exit_age == 70
        assert one.fill == {"marital_status": "single"}

    def test_a_stored_age_under_55_is_replaced_by_the_request(self) -> None:
        facts = illustration_facts(
            recorded_age=52,
            recorded_marital="married",
            requested_age=60,
            requested_marital="single",
        )
        assert facts.exit_age == 60
        assert facts.fill == {"retirement_age": 60}

    def test_an_age_under_55_is_refused(self) -> None:
        with pytest.raises(HTTPException) as raised:
            illustration_facts(
                recorded_age=None,
                recorded_marital="married",
                requested_age=54,
                requested_marital=None,
            )
        assert raised.value.status_code == 422
        assert "55" in raised.value.detail

    def test_a_gap_the_request_does_not_fill_is_refused(self) -> None:
        with pytest.raises(HTTPException) as raised:
            illustration_facts(
                recorded_age=None,
                recorded_marital=None,
                requested_age=40,
                requested_marital="it's complicated",
            )
        assert raised.value.status_code == 422
        assert "retirement age" in raised.value.detail
        assert "marital status" in raised.value.detail


class TestResolvedExitAge:
    def test_a_drawdown_for_someone_born_in_1940_runs_past_retirement(self) -> None:
        assert (
            resolved_exit_age(
                retirement_age=75,
                dob=date(1940, 6, 1),
                effective_date="2026-10-11",
                quotation_type="Flexi Access Drawdown",
            )
            == 99
        )

    def test_accumulation_for_the_same_client_is_refused(self) -> None:
        with pytest.raises(ctc.CtcValidationError, match="drawdown illustration"):
            resolved_exit_age(
                retirement_age=75,
                dob="1940-06-01",
                effective_date="2026-10-11",
                quotation_type="Accumulation",
            )

    def test_a_missing_date_of_birth_leaves_the_recorded_age(self) -> None:
        # Nothing to measure a term from. The builder skips the same pair.
        assert (
            resolved_exit_age(
                retirement_age=75,
                dob=None,
                effective_date="2026-10-11",
                quotation_type="Flexi Access Drawdown",
            )
            == 75
        )


class TestStreams:
    def test_existing_fund_value_is_always_the_first_stream(self) -> None:
        streams = _streams(125000.0, IllustrationInput(account_id=1))
        assert len(streams) == 1
        assert streams[0].key == "EXISTING"
        assert streams[0].contribution_type == "Existing Fund Value"
        assert streams[0].amount == Decimal("125000.0")
        assert streams[0].frequency == "Single"

    def test_a_zero_fund_still_produces_a_valid_stream(self) -> None:
        # A brand-new pension has no fund yet; the message must still be well-formed.
        streams = _streams(0.0, IllustrationInput(account_id=1))
        assert streams[0].amount == Decimal("0")

    def test_member_contribution_carries_frequency_and_net_gross(self) -> None:
        streams = _streams(
            1000.0,
            IllustrationInput(
                account_id=1,
                member_contribution=Decimal("500"),
                member_contribution_frequency="Quarterly",
                member_contribution_net_gross="Net",
            ),
        )
        member = next(s for s in streams if s.key == "MEMBER")
        assert member.contribution_type == "Member"
        assert member.frequency == "Quarterly"
        assert member.net_gross == "Net"

    def test_employer_contribution_is_added(self) -> None:
        streams = _streams(
            1000.0, IllustrationInput(account_id=1, employer_contribution=Decimal("250"))
        )
        assert [s.key for s in streams] == ["EXISTING", "EMPLOYER"]

    def test_zero_contributions_are_not_emitted_as_streams(self) -> None:
        # A blank form field arrives as 0; a £0 contribution stream would show on the output.
        streams = _streams(
            1000.0,
            IllustrationInput(
                account_id=1,
                member_contribution=Decimal("0"),
                employer_contribution=Decimal("0"),
            ),
        )
        assert [s.key for s in streams] == ["EXISTING"]

    def test_all_three_streams_together_validate_against_the_schema(self) -> None:
        streams = _streams(
            125000.0,
            IllustrationInput(
                account_id=1,
                member_contribution=Decimal("500"),
                employer_contribution=Decimal("250"),
            ),
        )
        assert [s.key for s in streams] == ["EXISTING", "MEMBER", "EMPLOYER"]
        for stream in streams:
            stream.validate()  # would raise on a schema violation

    def test_an_initial_payment_is_its_own_single_member_stream(self) -> None:
        # Separate from the regular contribution, so the PDF lists it under payments into the
        # plan. Gross matches a personal contribution's default; the form does not ask again.
        streams = _streams(
            0.0,
            IllustrationInput(
                account_id=1,
                initial_payment=Decimal("10000"),
                member_contribution=Decimal("500"),
            ),
        )
        assert [s.key for s in streams] == ["EXISTING", "INITIAL", "MEMBER"]
        initial = next(s for s in streams if s.key == "INITIAL")
        assert initial.contribution_type == "Member"
        assert initial.frequency == "Single"
        assert initial.net_gross == "Gross"
        assert initial.amount == Decimal("10000")
        initial.validate()

    def test_a_zero_initial_payment_is_not_emitted(self) -> None:
        streams = _streams(0.0, IllustrationInput(account_id=1, initial_payment=Decimal("0")))
        assert [s.key for s in streams] == ["EXISTING"]

    def test_a_fully_crystallised_transfer_is_one_stream(self) -> None:
        streams = _streams(
            0.0,
            IllustrationInput(
                account_id=1,
                transfer_value=Decimal("40000"),
                transfer_crystallised_amount=Decimal("40000"),
            ),
        )
        transfer = next(s for s in streams if s.key == "TRANSFER")
        assert transfer.crystallised is True
        assert transfer.amount == Decimal("40000")
        assert [s.key for s in streams] == ["EXISTING", "TRANSFER"]
        transfer.validate()

    def test_a_crystallised_part_does_not_add_to_the_transfer(self) -> None:
        # £10,000 of a £40,000 transfer is crystallised. The illustration is still £40,000:
        # the flag splits the stream, it does not sit on top of it.
        streams = _streams(
            0.0,
            IllustrationInput(
                account_id=1,
                transfer_value=Decimal("40000"),
                transfer_crystallised_amount=Decimal("10000"),
            ),
        )
        uncrystallised = next(s for s in streams if s.key == "TRANSFER")
        crystallised = next(s for s in streams if s.key == "TRANSFER_CRYST")
        assert uncrystallised.crystallised is False
        assert uncrystallised.amount == Decimal("30000")
        assert crystallised.crystallised is True
        assert crystallised.amount == Decimal("10000")
        assert crystallised.contribution_type == "Transfer Value"
        assert crystallised.frequency == "Single"
        for stream in (uncrystallised, crystallised):
            stream.validate()

    def test_a_crystallised_amount_above_the_transfer_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be more than the transfer"):
            _streams(
                0.0,
                IllustrationInput(
                    account_id=1,
                    transfer_value=Decimal("40000"),
                    transfer_crystallised_amount=Decimal("40001"),
                ),
            )


class TestAdviserFees:
    def _streams(self, **kwargs: object) -> list[object]:
        return _streams(1000.0, IllustrationInput(account_id=1, **kwargs))  # type: ignore[arg-type]

    def test_no_fees_when_nothing_is_entered(self) -> None:
        assert (
            _adviser_fees(
                IllustrationInput(account_id=1), _streams(1000.0, IllustrationInput(account_id=1))
            )
            == []
        )

    def test_ongoing_fee_is_a_percentage_per_annum(self) -> None:
        streams = _streams(1000.0, IllustrationInput(account_id=1))
        fees = _adviser_fees(
            IllustrationInput(account_id=1, ongoing_adviser_fee_percent=Decimal("0.75")), streams
        )
        assert len(fees) == 1
        assert fees[0].fee_basis == "Ongoing"
        assert fees[0].fee_frequency == "Monthly"
        assert fees[0].fee_timing == "In Arrears"
        assert fees[0].fee_value_rate == "Per Annum"
        assert fees[0].percentage == Decimal("0.75")
        assert fees[0].amount is None
        assert fees[0].stream_key is None  # ongoing fees are not stream-bound

    def test_initial_fee_is_a_single_amount_per_frequency(self) -> None:
        # Per Annum on a pound amount is an annual rate. On a monthly stream the engine spreads
        # it, which is how an initial charge came out of the PDF as a monthly fee.
        body = IllustrationInput(account_id=1, initial_adviser_fee_amount=Decimal("500"))
        fees = _adviser_fees(body, _streams(1000.0, body))
        assert fees[0].fee_basis == "Initial"
        assert fees[0].fee_frequency == "Single"
        assert fees[0].fee_timing == "In Advance"
        assert fees[0].fee_value_rate == "Per Frequency"
        assert fees[0].amount == Decimal("500")

    def test_initial_fee_attaches_to_the_initial_payment_ahead_of_a_monthly_contribution(
        self,
    ) -> None:
        body = IllustrationInput(
            account_id=1,
            initial_payment=Decimal("10000"),
            employer_contribution=Decimal("250"),
            member_contribution=Decimal("100"),
            initial_adviser_fee_amount=Decimal("500"),
        )
        fees = _adviser_fees(body, _streams(1000.0, body))
        assert fees[0].stream_key == "INITIAL"

    def test_initial_fee_does_not_attach_to_a_monthly_contribution(self) -> None:
        # The newest stream used to win. A monthly employer contribution was newest, so the
        # one-off charge was filed against it.
        body = IllustrationInput(
            account_id=1,
            employer_contribution=Decimal("250"),
            member_contribution=Decimal("100"),
            initial_adviser_fee_amount=Decimal("500"),
        )
        streams = _streams(1000.0, body)
        assert streams[-1].key == "EMPLOYER"
        fees = _adviser_fees(body, streams)
        assert fees[0].stream_key == "EXISTING"

    def test_initial_fee_names_the_existing_fund_when_the_list_has_no_single_stream(self) -> None:
        # The spare used to return whatever stream was first. A list with only a monthly
        # contribution would have put the one-off charge back on that contribution.
        fees = _adviser_fees(
            IllustrationInput(account_id=1, initial_adviser_fee_amount=Decimal("500")),
            [
                ctc.PlanStream(
                    key="MEMBER",
                    contribution_type="Member",
                    amount=Decimal("100"),
                    frequency="Monthly",
                    net_gross="Gross",
                )
            ],
        )
        assert fees[0].stream_key == "EXISTING"

    def test_initial_fee_uses_the_transfer_when_there_is_no_initial_payment(self) -> None:
        body = IllustrationInput(
            account_id=1,
            transfer_value=Decimal("40000"),
            member_contribution=Decimal("100"),
            initial_adviser_fee_amount=Decimal("500"),
        )
        fees = _adviser_fees(body, _streams(1000.0, body))
        assert fees[0].stream_key == "TRANSFER"

    def test_initial_fee_falls_back_to_the_existing_fund(self) -> None:
        # With no new single payment the only stream to charge against is the existing fund.
        body = IllustrationInput(account_id=1, initial_adviser_fee_amount=Decimal("500"))
        streams = _streams(1000.0, body)
        fees = _adviser_fees(body, streams)
        assert fees[0].stream_key == "EXISTING"

    def test_every_produced_fee_satisfies_the_schema(self) -> None:
        body = IllustrationInput(
            account_id=1,
            member_contribution=Decimal("100"),
            ongoing_adviser_fee_percent=Decimal("0.75"),
            initial_adviser_fee_amount=Decimal("500"),
        )
        streams = _streams(1000.0, body)
        for fee in _adviser_fees(body, streams):
            fee.validate(adviser=True)  # would raise if the stream binding were missing


class TestDefaults:
    def test_quotation_type_defaults_to_accumulation(self) -> None:
        assert IllustrationInput(account_id=1).quotation_type == "Accumulation"

    def test_member_contributions_default_to_gross_monthly(self) -> None:
        # The default lives in the stream builder, not on the model: the fields are nullable so
        # a caller with contributions switched off can send nulls rather than having to know a
        # default for a field it is not using. The defaulting behaviour itself is unchanged.
        body = IllustrationInput(account_id=1, member_contribution=Decimal("500"))
        member = next(s for s in _streams(0.0, body) if s.contribution_type == "Member")
        assert member.frequency == "Monthly"
        assert member.net_gross == "Gross"

    def test_an_explicit_contribution_frequency_is_honoured(self) -> None:
        body = IllustrationInput(
            account_id=1,
            member_contribution=Decimal("500"),
            member_contribution_frequency="Quarterly",
            member_contribution_net_gross="Net",
        )
        member = next(s for s in _streams(0.0, body) if s.contribution_type == "Member")
        assert (member.frequency, member.net_gross) == ("Quarterly", "Net")


class TestAnnuityBasis:
    def test_a_caller_that_says_nothing_gets_the_house_basis(self) -> None:
        # The portal stopped asking (09 Aug 2026), so "no annuity fields" is now the normal case
        # rather than a request to omit the group. Omitting it is what leaves the projected-income
        # column at zero, and that has to be asked for explicitly.
        basis = _annuity_basis(IllustrationInput(account_id=1))
        assert basis is not None
        assert basis.annuity_frequency == "Monthly"
        assert basis.annuity_timing == "Advance"
        assert basis.escalation == "Level"

    def test_the_house_basis_is_single_life_with_no_guarantee(self) -> None:
        # Both absences are deliberate and both flatter the figure: a guarantee period or a
        # spouse's pension would each show a lower starting income. Pinned so that adding one is a
        # decision rather than a drift.
        basis = _annuity_basis(IllustrationInput(account_id=1))
        assert basis is not None
        assert basis.guaranteed_period is None
        assert basis.spouse_percentage is None
        assert basis.percent is None

    def test_the_group_is_omitted_only_when_asked(self) -> None:
        assert _annuity_basis(IllustrationInput(account_id=1, include_annuity_basis=False)) is None

    def test_an_explicit_basis_is_not_topped_up_from_the_house_one(self) -> None:
        # A caller with a basis of their own gets exactly it. Merging the two would produce a
        # basis nobody chose -- their escalation against our timing.
        basis = _annuity_basis(IllustrationInput(account_id=1, escalation="RPI"))
        assert basis is not None
        assert basis.escalation == "RPI"
        assert basis.annuity_frequency is None
        assert basis.annuity_timing is None

    def test_any_supplied_field_builds_the_group(self) -> None:
        basis = _annuity_basis(IllustrationInput(account_id=1, annuity_frequency="Monthly"))
        assert basis is not None
        assert basis.annuity_frequency == "Monthly"

    def test_a_fixed_escalation_carries_its_percent(self) -> None:
        basis = _annuity_basis(
            IllustrationInput(account_id=1, escalation="Fixed", escalation_percent=Decimal("3"))
        )
        assert basis is not None
        assert basis.percent == Decimal("3")

    def test_a_percent_is_dropped_for_a_non_fixed_escalation(self) -> None:
        # The builder rejects a percent alongside LPI/RPI, so it must not be passed through.
        basis = _annuity_basis(
            IllustrationInput(account_id=1, escalation="RPI", escalation_percent=Decimal("3"))
        )
        assert basis is not None
        assert basis.percent is None


def test_a_gap_fill_is_written_as_service_role() -> None:
    """authenticated may read crm.contacts and may not update it.

    Generating an illustration fills a missing retirement age and marital status. That UPDATE
    used to run as the request's authenticated role and Postgres refused it
    (permission denied for table contacts). Client edits write as service_role; this fill is
    the same write, then the request goes back to authenticated for everything else.
    """
    source = Path(illustrations.__file__).read_text()
    # The loader source is the file. Slice the helper so a later service_role elsewhere
    # cannot satisfy the assertion, and check the request actually calls it.
    fill = source.split("async def _fill_contact_gaps", 1)[1].split("\n@router.post", 1)[0]
    assert "SET LOCAL ROLE service_role" in fill
    assert fill.index("await update_contact") > fill.index("SET LOCAL ROLE service_role")
    assert fill.index("SET LOCAL ROLE authenticated") > fill.index("await update_contact")
    call = source.split("if facts.fill:", 1)[1].split("client_row = dict(row)", 1)[0]
    assert "_fill_contact_gaps" in call


def test_a_mapped_request_serialises_to_valid_ctc1() -> None:
    """End-to-end over the pure layer: record + form in, schema-valid XML out."""
    from app.integrations import ctc

    body = IllustrationInput(
        account_id=1,
        member_contribution=Decimal("500"),
        ongoing_adviser_fee_percent=Decimal("0.75"),
        initial_adviser_fee_amount=Decimal("500"),
    )
    streams = _streams(125000.0, body)
    request = ctc.IllustrationRequest(
        effective_date="2026-07-28",
        created_date="2026-07-28 09:30:00",
        investors=[_investor({"surname": "Oakden", "dob": "1960-04-02"}, 67)],
        quotations=[
            ctc.Quotation(
                product_ref="SIPPEN",
                product_type="Self Invested Personal Pension",
                quotation_type="Accumulation",
                plan_streams=streams,
                adviser_fees=_adviser_fees(body, streams),
            )
        ],
    )
    xml = build_request(request)
    assert "<contribution_type>Existing Fund Value</contribution_type>" in xml
    assert '<plan_stream plan_stream_key="MEMBER">' in xml
    # The member contribution is monthly, so the initial charge stays on the existing fund.
    assert '<stream_applicable plan_stream_key="EXISTING" />' in xml or (
        '<stream_applicable plan_stream_key="EXISTING"/>' in xml
    )


@pytest.mark.parametrize("fund_value", [0.0, 0.005, 1_234_567.89])
def test_fund_values_survive_the_round_trip_without_scientific_notation(fund_value: float) -> None:
    streams = _streams(fund_value, IllustrationInput(account_id=1))
    from app.integrations import ctc

    xml = build_request(
        ctc.IllustrationRequest(
            effective_date="2026-07-28",
            created_date="2026-07-28 09:30:00",
            investors=[_investor({"surname": "X"}, None)],
            quotations=[
                ctc.Quotation(
                    product_ref="SIPPEN", quotation_type="Accumulation", plan_streams=streams
                )
            ],
        )
    )
    assert "E+" not in xml and "e+" not in xml


class TestDocumentName:
    """The one label that stops a stand-in being mistaken for a quotation."""

    def test_a_real_illustration_names_the_wrapper_and_basis(self) -> None:
        assert (
            _document_name("SIPP", "Accumulation", placeholder=False)
            == "SIPP illustration — Accumulation"
        )

    def test_a_placeholder_says_so_first_and_in_capitals(self) -> None:
        # First, because a documents listing truncates from the right.
        name = _document_name("SIPP", "Flexi Access Drawdown", placeholder=True)
        assert name.startswith("PLACEHOLDER — ")
        assert name == "PLACEHOLDER — SIPP illustration — Flexi Access Drawdown"


class TestFormPayload:
    """The shape the platform form actually POSTs.

    Every optional section in the form is switched off by sending nulls rather than by omitting
    keys, because a JSON body built from component state naturally carries the whole object.
    Typing any of these as a bare `str` makes the request 422 with "Input should be a valid
    string" — which is exactly what happened once the sections became toggles.
    """

    def _all_sections_off(self) -> dict[str, object]:
        return {
            "account_id": None,
            "product_type": "SIPP",
            "existing_fund_value": 100000,
            "crystallised_fund_value": None,
            "transfer_value": None,
            "quotation_type": "Accumulation",
            "exit_age": 67,
            "member_contribution": None,
            "member_contribution_frequency": None,
            "member_contribution_net_gross": None,
            "employer_contribution": None,
            "employer_contribution_frequency": None,
            "initial_payment": None,
            "transfer_crystallised_amount": None,
            "ongoing_adviser_fee_percent": None,
            "initial_adviser_fee_amount": None,
            "drawdown_type": None,
            "withdrawal_amount": None,
            "withdrawal_frequency": None,
            "annuity_frequency": None,
            "annuity_timing": None,
            "escalation": None,
            "escalation_percent": None,
            "guaranteed_period": None,
        }

    def test_every_optional_section_switched_off_is_accepted(self) -> None:
        body = IllustrationInput(**self._all_sections_off())  # type: ignore[arg-type]

        assert body.existing_fund_value == Decimal("100000")
        assert [s.contribution_type for s in _streams(0.0, body)] == ["Existing Fund Value"]
        # Explicit nulls for the annuity keys read as "no preference", not as "omit the group" --
        # the portal sends this shape and still wants the house basis. Only
        # `include_annuity_basis=False` omits it.
        assert _annuity_basis(body) is not None

    def test_every_optional_section_switched_on_is_accepted(self) -> None:
        payload = self._all_sections_off()
        payload.update(
            {
                "transfer_value": 60000,
                "member_contribution": 500,
                "member_contribution_frequency": "Monthly",
                "member_contribution_net_gross": "Gross",
                "drawdown_type": "Flexible",
                "withdrawal_amount": 750,
                "withdrawal_frequency": "Monthly",
                "quotation_type": "Drawdown",
                "annuity_frequency": "Monthly",
                "annuity_timing": "Advance",
                "escalation": "Level",
            }
        )
        body = IllustrationInput(**payload)  # type: ignore[arg-type]
        kinds = [s.contribution_type for s in _streams(0.0, body)]

        assert _annuity_basis(body) is not None
        assert "Transfer Value" in kinds
        assert "Member" in kinds


class _Row(dict[str, object]):
    """A `.mappings().first()` result, which is all `_premium_split` reads."""


class _Result:
    def __init__(self, row: _Row | None) -> None:
        self._row = row

    def mappings(self) -> "_Result":
        return self

    def first(self) -> _Row | None:
        return self._row


class _StubSession:
    """Enough of an AsyncSession for one SELECT. `_premium_split` needs a session but nothing
    about it is interesting — the model row is the input under test."""

    def __init__(self, row: _Row | None) -> None:
        self._row = row

    async def execute(self, *_args: object, **_kwargs: object) -> _Result:
        return _Result(self._row)


def _model(**overrides: object) -> _Row:
    row = _Row(
        {
            "name": "Balanced Growth",
            "target_return": Decimal("6.5"),
            "target_return_basis": "gross",
            "model_charge": Decimal("0.35"),
        }
    )
    row.update(overrides)
    return row


class TestPremiumSplit:
    """The growth basis. One generic fund, at the model's target return, NET of the model's own
    charge — the last of which was being dropped, so every projected figure came back higher than
    what the client would actually receive."""

    @staticmethod
    def _streams() -> list[ctc.PlanStream]:
        return [ctc.PlanStream("A100", "Existing Fund Value", 100_000, "Single")]

    async def _split(self, row: _Row | None) -> ctc.PremiumSplit:
        split, _name = await illustrations._premium_split(
            _StubSession(row),  # type: ignore[arg-type]
            1,
            self._streams(),
        )
        return split

    async def test_carries_the_models_charge_as_the_funds_management_charge(self) -> None:
        split = await self._split(_model())
        assert split.funds[0].management_charge == Decimal("0.35")

    async def test_and_the_target_return_as_the_growth_rate(self) -> None:
        split = await self._split(_model())
        assert split.funds[0].rate_of_return == Decimal("6.5")
        assert split.funds[0].fund_name == "Balanced Growth"

    async def test_a_model_with_no_charge_recorded_sends_none(self) -> None:
        """Absent, not zero. "No charge is recorded" and "the charge is nil" are different claims,
        and CTC omits the element entirely when it is None."""
        split = await self._split(_model(model_charge=None))
        assert split.funds[0].management_charge is None

    async def test_a_model_with_no_target_return_is_refused_not_defaulted(self) -> None:
        # A made-up growth rate on a client-facing projection is worse than no illustration.
        with pytest.raises(HTTPException) as raised:
            await self._split(_model(target_return=None))
        assert raised.value.status_code == 422

    async def test_an_unknown_model_is_a_404(self) -> None:
        with pytest.raises(HTTPException) as raised:
            await self._split(None)
        assert raised.value.status_code == 404


class TestRecordedInputs:
    def test_keeps_the_assumptions_onboarding_reads_back(self) -> None:
        body = IllustrationInput(
            product_type="SIPP",
            existing_fund_value=Decimal("50000"),
            model_id=7,
            transfer_value=Decimal("25000"),
            member_contribution=Decimal("400"),
            member_contribution_frequency="Monthly",
            quotation_type="Accumulation",
        )
        recorded = recorded_inputs(
            body,
            product_type="SIPP",
            fund_value=50_000,
            model_name="Balanced Growth",
            effective_date="2026-09-16",
            output_reference="CON1-20260916",
            placeholder=False,
            placeholder_reason=None,
        )
        assert recorded["transfer_value"] == "25000"
        assert recorded["member_contribution"] == "400"
        assert recorded["member_contribution_frequency"] == "Monthly"
        assert recorded["model_id"] == 7
        assert recorded["model"] == {"id": 7, "name": "Balanced Growth"}
        assert recorded["existing_fund_value"] == 50_000.0
