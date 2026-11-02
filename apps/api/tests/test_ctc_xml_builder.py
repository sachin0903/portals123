"""CTC Bespoke XML v1.6 request building + response parsing.

Assertions are written against the vendor spec's own worked examples
(``docs/morgan-lloyd/XML_v1.6_Bespoke.pdf``) so a future edit that drifts from the schema fails
here rather than at the engine, whose only diagnostic is "Error reading XML message".
"""

from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from app.integrations.ctc.xml_builder import (
    DRAWDOWN_HORIZON_AGE,
    AnnuityBasis,
    CtcValidationError,
    Fee,
    FundSplit,
    IllustrationRequest,
    Investor,
    PlanStream,
    PremiumSplit,
    Quotation,
    Withdrawals,
    build_request,
    parse_response,
    projection_exit_age,
)

CREATED = "2026-07-28 09:30:00"


def _minimal(**overrides: object) -> IllustrationRequest:
    defaults: dict[str, object] = {
        "effective_date": "2026-07-28",
        "created_date": CREATED,
        "investors": [Investor(surname="Example", date_of_birth="1955-06-13", exit_age=73)],
        "quotations": [
            Quotation(
                product_ref="SIPPEN",
                quotation_type="Accumulation",
                plan_streams=[
                    PlanStream(
                        key="STREAM1",
                        contribution_type="Existing Fund Value",
                        amount=Decimal("543210.98"),
                        frequency="Single",
                    )
                ],
            )
        ],
    }
    defaults.update(overrides)
    return IllustrationRequest(**defaults)  # type: ignore[arg-type]


def _tree(request: IllustrationRequest) -> ET.Element:
    # noqa S314: the input is XML this test just built, not untrusted data.
    return ET.fromstring(build_request(request))  # noqa: S314


class TestHeader:
    def test_declares_the_ctc1_message_version(self) -> None:
        root = _tree(_minimal())
        assert root.findtext("./header/message_version") == "CTC1"
        assert root.findtext("./header/created_date") == CREATED

    def test_emits_a_well_formed_mode_element(self) -> None:
        # The spec's example opens <mode> and closes </state>; that is not parseable XML.
        assert _tree(_minimal()).findtext("./header/mode") == "Request"

    def test_output_is_parseable_and_declares_an_encoding(self) -> None:
        xml = build_request(_minimal())
        assert xml.startswith("<?xml")
        ET.fromstring(xml)  # noqa: S314 - our own output; would raise if malformed


class TestQuotations:
    def test_carries_the_output_reference_and_agent(self) -> None:
        root = _tree(_minimal(output_reference="ABCD1234", agent_name="Graphene Platforms"))
        assert root.findtext(".//quotations/output_required") == "Yes"
        assert root.findtext(".//quotations/output_reference") == "ABCD1234"
        assert root.findtext(".//quotations/agent_name") == "Graphene Platforms"

    def test_omits_optional_elements_rather_than_emitting_them_empty(self) -> None:
        # The engine treats an empty tag as a bad value, so absent must mean absent.
        root = _tree(_minimal())
        assert root.find(".//quotations/output_reference") is None
        assert root.find(".//quotations/agent_name") is None
        assert root.find(".//quotations/client_type") is None

    def test_output_required_no(self) -> None:
        assert _tree(_minimal(output_required=False)).findtext(".//output_required") == "No"

    def test_company_client_requires_a_name(self) -> None:
        with pytest.raises(CtcValidationError, match="needs a <name>"):
            build_request(_minimal(client_type="Company"))

    def test_company_client_with_a_name_emits_both(self) -> None:
        root = _tree(_minimal(client_type="Trust", client_name="Example Trust"))
        assert root.findtext(".//client_type") == "Trust"
        assert root.findtext(".//quotations/name") == "Example Trust"

    def test_rejects_an_unknown_client_type(self) -> None:
        with pytest.raises(CtcValidationError, match="client_type"):
            build_request(_minimal(client_type="Partnership"))

    def test_caps_the_output_reference_at_45_characters(self) -> None:
        with pytest.raises(CtcValidationError, match="45 characters"):
            build_request(_minimal(output_reference="x" * 46))


class TestInvestor:
    def test_matches_the_spec_example(self) -> None:
        root = _tree(
            _minimal(
                investors=[
                    Investor(
                        title="Ms",
                        forename="Main Life",
                        surname="Example",
                        sex="Female",
                        life_type="None",
                        marital_status="Unknown",
                        date_of_birth="1955-06-13",
                        exit_age=73,
                    )
                ]
            )
        )
        investor = root.find(".//investor")
        assert investor is not None
        assert [(e.tag, e.text) for e in investor] == [
            ("title", "Ms"),
            ("forename", "Main Life"),
            ("surname", "Example"),
            ("sex", "Female"),
            ("life_type", "None"),
            ("marital_status", "Unknown"),
            ("date_of_birth", "1955-06-13"),
            ("exit_age", "73"),
        ]

    def test_supports_multiple_lives(self) -> None:
        root = _tree(
            _minimal(
                investors=[
                    Investor(surname="One", life_type="Life Assured"),
                    Investor(surname="Two", life_type="Both"),
                ]
            )
        )
        assert len(root.findall(".//investor")) == 2

    def test_rejects_an_unknown_sex_or_life_type(self) -> None:
        with pytest.raises(CtcValidationError, match="sex"):
            build_request(_minimal(investors=[Investor(sex="Other")]))
        with pytest.raises(CtcValidationError, match="life_type"):
            build_request(_minimal(investors=[Investor(life_type="Spouse")]))

    def test_requires_at_least_one_investor(self) -> None:
        with pytest.raises(CtcValidationError, match="at least one investor"):
            build_request(_minimal(investors=[]))


class TestPlanStream:
    def test_matches_the_spec_example(self) -> None:
        root = _tree(
            _minimal(
                quotations=[
                    Quotation(
                        product_ref="SIPPEN",
                        quotation_type="Accumulation",
                        plan_streams=[
                            PlanStream(
                                key="100000",
                                contribution_type="Existing Fund Value",
                                amount=Decimal("543210.98"),
                                frequency="Single",
                            ),
                            PlanStream(
                                key="100001",
                                contribution_type="Existing Fund Value",
                                amount=Decimal("234561.66"),
                                frequency="Single",
                                crystallised=True,
                            ),
                        ],
                    )
                ]
            )
        )
        streams = root.findall(".//plan_stream")
        assert [s.get("plan_stream_key") for s in streams] == ["100000", "100001"]
        assert streams[0].findtext("amount") == "543210.98"
        assert streams[0].findtext("crystallised") == "No"
        assert streams[1].findtext("crystallised") == "Yes"

    def test_member_contribution_carries_net_gross(self) -> None:
        root = _tree(
            _minimal(
                quotations=[
                    Quotation(
                        product_ref="SIPPEN",
                        quotation_type="Accumulation",
                        plan_streams=[
                            PlanStream(
                                key="CONT1",
                                contribution_type="Member",
                                amount=650.52,
                                frequency="Quarterly",
                                net_gross="Gross",
                            )
                        ],
                    )
                ]
            )
        )
        assert root.findtext(".//plan_stream/net_gross") == "Gross"

    def test_member_contribution_without_net_gross_is_rejected(self) -> None:
        with pytest.raises(CtcValidationError, match="net_gross is required"):
            build_request(
                _minimal(
                    quotations=[
                        Quotation(
                            product_ref="SIPPEN",
                            quotation_type="Accumulation",
                            plan_streams=[
                                PlanStream(
                                    key="CONT1",
                                    contribution_type="Member",
                                    amount=100,
                                    frequency="Monthly",
                                )
                            ],
                        )
                    ]
                )
            )

    def test_transfer_value_must_be_single(self) -> None:
        with pytest.raises(CtcValidationError, match="must have frequency 'Single'"):
            build_request(
                _minimal(
                    quotations=[
                        Quotation(
                            product_ref="SIPPEN",
                            quotation_type="Accumulation",
                            plan_streams=[
                                PlanStream(
                                    key="TV1",
                                    contribution_type="Transfer Value",
                                    amount=1000,
                                    frequency="Monthly",
                                )
                            ],
                        )
                    ]
                )
            )

    def test_only_fund_value_streams_may_be_crystallised(self) -> None:
        with pytest.raises(CtcValidationError, match="may be crystallised"):
            build_request(
                _minimal(
                    quotations=[
                        Quotation(
                            product_ref="SIPPEN",
                            quotation_type="Accumulation",
                            plan_streams=[
                                PlanStream(
                                    key="E1",
                                    contribution_type="Employer",
                                    amount=100,
                                    frequency="Monthly",
                                    crystallised=True,
                                )
                            ],
                        )
                    ]
                )
            )

    def test_duplicate_stream_keys_are_rejected(self) -> None:
        with pytest.raises(CtcValidationError, match="unique"):
            build_request(
                _minimal(
                    quotations=[
                        Quotation(
                            product_ref="SIPPEN",
                            quotation_type="Accumulation",
                            plan_streams=[
                                PlanStream("S", "Employer", 1, "Monthly"),
                                PlanStream("S", "Employer", 2, "Monthly"),
                            ],
                        )
                    ]
                )
            )

    def test_amounts_are_plain_two_place_decimals(self) -> None:
        root = _tree(
            _minimal(
                quotations=[
                    Quotation(
                        product_ref="SIPPEN",
                        quotation_type="Accumulation",
                        plan_streams=[
                            PlanStream("S1", "Employer", 1234567.5, "Monthly"),
                        ],
                    )
                ]
            )
        )
        # No thousands separator, no currency symbol — the engine parses this as a number.
        assert root.findtext(".//plan_stream/amount") == "1234567.50"


class TestPremiumSplit:
    def _quote(self, funds: list[FundSplit]) -> Quotation:
        return Quotation(
            product_ref="SIPPEN",
            quotation_type="Accumulation",
            plan_streams=[PlanStream("A100", "Employer", 100, "Monthly")],
            premium_splits=[PremiumSplit(stream_keys=["A100"], funds=funds)],
        )

    def test_matches_the_spec_example(self) -> None:
        root = _tree(
            _minimal(
                quotations=[
                    self._quote(
                        [
                            FundSplit(fund_ref="LU0651906728", split_per=60),
                            FundSplit(fund_ref="LU0231455709", split_per=40),
                        ]
                    )
                ]
            )
        )
        assert [e.get("plan_stream_key") for e in root.findall(".//stream_applicable")] == ["A100"]
        assert [e.findtext("fund_ref") for e in root.findall(".//fund_split")] == [
            "LU0651906728",
            "LU0231455709",
        ]
        assert [e.findtext("split_per") for e in root.findall(".//fund_split")] == ["60", "40"]

    def test_percentage_splits_must_total_100(self) -> None:
        with pytest.raises(CtcValidationError, match="total 100"):
            build_request(
                _minimal(
                    quotations=[
                        self._quote(
                            [
                                FundSplit(fund_ref="A", split_per=60),
                                FundSplit(fund_ref="B", split_per=30),
                            ]
                        )
                    ]
                )
            )

    def test_amount_splits_are_not_percentage_checked(self) -> None:
        root = _tree(
            _minimal(
                quotations=[
                    self._quote(
                        [
                            FundSplit(fund_ref="A", split_amount=6000),
                            FundSplit(fund_ref="B", split_amount=4000),
                        ]
                    )
                ]
            )
        )
        assert [e.findtext("split_amount") for e in root.findall(".//fund_split")] == [
            "6000.00",
            "4000.00",
        ]

    def test_a_fund_needs_exactly_one_of_percent_or_amount(self) -> None:
        with pytest.raises(CtcValidationError, match="exactly one"):
            build_request(_minimal(quotations=[self._quote([FundSplit(fund_ref="A")])]))
        with pytest.raises(CtcValidationError, match="exactly one"):
            build_request(
                _minimal(
                    quotations=[
                        self._quote([FundSplit(fund_ref="A", split_per=100, split_amount=1)])
                    ]
                )
            )

    def test_generic_fund_is_flagged_when_assumptions_are_supplied(self) -> None:
        root = _tree(
            _minimal(
                quotations=[
                    self._quote(
                        [
                            FundSplit(
                                fund_ref="A",
                                split_per=100,
                                rate_of_return=5,
                                management_charge=0.75,
                            )
                        ]
                    )
                ]
            )
        )
        assert root.findtext(".//fund_split/generic_fund") == "Yes"
        assert root.findtext(".//fund_split/rate_of_return") == "5"

    def test_commercial_property_type_attribute(self) -> None:
        root = _tree(
            _minimal(
                quotations=[self._quote([FundSplit(fund_ref="A", split_per=100, type="COMMPROP")])]
            )
        )
        assert root.find(".//fund_split").get("type") == "COMMPROP"  # type: ignore[union-attr]

    def test_split_referencing_an_unknown_stream_is_rejected(self) -> None:
        quote = Quotation(
            product_ref="SIPPEN",
            quotation_type="Accumulation",
            plan_streams=[PlanStream("A100", "Employer", 100, "Monthly")],
            premium_splits=[
                PremiumSplit(stream_keys=["NOPE"], funds=[FundSplit(fund_ref="A", split_per=100)])
            ],
        )
        with pytest.raises(CtcValidationError, match="unknown stream keys"):
            build_request(_minimal(quotations=[quote]))


class TestWithdrawals:
    def test_drawdown_type_is_emitted_last(self) -> None:
        quote = Quotation(
            product_ref="SIPPEN",
            quotation_type="Drawdown",
            plan_streams=[PlanStream("S1", "Existing Fund Value", 100000, "Single")],
            withdrawals=Withdrawals(
                drawdown_type="Flexible",
                basis="Fixed",
                amount=1000,
                frequency="Monthly",
                start_date="2026-09-01",
                increase_percent=3,
            ),
        )
        node = _tree(_minimal(quotations=[quote])).find(".//withdrawals")
        assert node is not None
        assert [e.tag for e in node] == [
            "basis",
            "amount",
            "frequency",
            "start_date",
            "increases",
            "drawdown_type",
        ]
        assert node.findtext("increases/percent") == "3"

    def test_percentage_of_max_gad_requires_a_percent(self) -> None:
        quote = Quotation(
            product_ref="SIPPEN",
            quotation_type="Drawdown",
            plan_streams=[PlanStream("S1", "Existing Fund Value", 1000, "Single")],
            withdrawals=Withdrawals(drawdown_type="Capped", basis="Percentage of Maximum GAD"),
        )
        with pytest.raises(CtcValidationError, match="percent is required"):
            build_request(_minimal(quotations=[quote]))

    def test_rejects_an_unknown_drawdown_type(self) -> None:
        quote = Quotation(
            product_ref="SIPPEN",
            quotation_type="Drawdown",
            plan_streams=[PlanStream("S1", "Existing Fund Value", 1000, "Single")],
            withdrawals=Withdrawals(drawdown_type="Phased"),
        )
        with pytest.raises(CtcValidationError, match="drawdown_type"):
            build_request(_minimal(quotations=[quote]))


class TestFees:
    def test_product_fee_matches_the_spec_example(self) -> None:
        quote = Quotation(
            product_ref="SIPPEN",
            quotation_type="Accumulation",
            plan_streams=[PlanStream("S1", "Existing Fund Value", 1000, "Single")],
            product_fees=[
                Fee(
                    fee_name="Bond Charge",
                    fee_basis="Ongoing",
                    fee_frequency="Monthly",
                    fee_timing="In Arrears",
                    fee_value_rate="Per Annum",
                    percentage=Decimal("0.33"),
                    plus_vat=False,
                )
            ],
        )
        fee = _tree(_minimal(quotations=[quote])).find(".//product_fees/product_fee")
        assert fee is not None
        assert fee.findtext("fee_name") == "Bond Charge"
        assert fee.findtext("fee_start/start_type") == "Immediately"
        assert fee.findtext("fee_end/end_type") == "Maturity"
        assert fee.findtext("percentage") == "0.33"
        assert fee.findtext("plus_vat") == "No"

    def test_adviser_fee_attaches_to_a_stream(self) -> None:
        quote = Quotation(
            product_ref="SIPPEN",
            quotation_type="Accumulation",
            plan_streams=[PlanStream("CONT1", "Employer", 1000, "Monthly")],
            adviser_fees=[
                Fee(
                    fee_basis="Initial",
                    fee_frequency="Single",
                    fee_timing="In Arrears",
                    fee_value_rate="Per Annum",
                    amount=500,
                    stream_key="CONT1",
                )
            ],
        )
        fee = _tree(_minimal(quotations=[quote])).find(".//adviser_fees/adviser_fee")
        assert fee is not None
        assert fee.find("stream_applicable").get("plan_stream_key") == "CONT1"  # type: ignore[union-attr]
        assert fee.findtext("amount") == "500.00"

    def test_initial_adviser_fee_without_a_stream_is_rejected(self) -> None:
        quote = Quotation(
            product_ref="SIPPEN",
            quotation_type="Accumulation",
            plan_streams=[PlanStream("CONT1", "Employer", 1000, "Monthly")],
            adviser_fees=[
                Fee(
                    fee_basis="Initial",
                    fee_frequency="Single",
                    fee_timing="In Arrears",
                    fee_value_rate="Per Annum",
                    amount=500,
                )
            ],
        )
        with pytest.raises(CtcValidationError, match="must name the stream"):
            build_request(_minimal(quotations=[quote]))

    def test_a_fee_needs_exactly_one_of_amount_or_percentage(self) -> None:
        quote = Quotation(
            product_ref="SIPPEN",
            quotation_type="Accumulation",
            plan_streams=[PlanStream("S1", "Existing Fund Value", 1000, "Single")],
            product_fees=[
                Fee(
                    fee_basis="Ongoing",
                    fee_frequency="Monthly",
                    fee_timing="In Arrears",
                    fee_value_rate="Per Annum",
                )
            ],
        )
        with pytest.raises(CtcValidationError, match="exactly one"):
            build_request(_minimal(quotations=[quote]))


class TestQuotationShape:
    def test_product_ref_and_type_are_attributes(self) -> None:
        root = _tree(
            _minimal(
                quotations=[
                    Quotation(
                        product_ref="SIPPEN",
                        product_type="Self Invested Personal Pension",
                        quotation_type="Flexi Access Drawdown",
                        plan_streams=[PlanStream("S1", "Existing Fund Value", 1, "Single")],
                    )
                ]
            )
        )
        quotation = root.find(".//quotation")
        assert quotation is not None
        assert quotation.get("product_ref") == "SIPPEN"
        assert quotation.get("product_type") == "Self Invested Personal Pension"
        assert quotation.findtext("quotation_type") == "Flexi Access Drawdown"

    def test_a_quotation_needs_a_plan_stream(self) -> None:
        with pytest.raises(CtcValidationError, match="no plan_stream"):
            build_request(
                _minimal(
                    quotations=[Quotation(product_ref="SIPPEN", quotation_type="Accumulation")]
                )
            )

    def test_inflation_adjustment_indicator(self) -> None:
        root = _tree(
            _minimal(
                quotations=[
                    Quotation(
                        product_ref="SIPPEN",
                        quotation_type="Accumulation",
                        inflation_adjustment=True,
                        plan_streams=[PlanStream("S1", "Existing Fund Value", 1, "Single")],
                    )
                ]
            )
        )
        assert root.findtext(".//inflation_adjustment_indicator") == "Yes"


class TestParseResponse:
    def test_reads_a_success_with_the_document_location(self) -> None:
        result = parse_response(
            """<message><header><response>SUCCESS</response></header>
               <content><results><document_location>ABCD1234.pdf</document_location>
               </results></content></message>"""
        )
        assert result.success is True
        assert result.document_location == "ABCD1234.pdf"
        assert result.errors == []

    def test_collects_error_descriptions(self) -> None:
        result = parse_response(
            """<message><header><response>ERROR</response><errors>
               <error>Unknown Product Code</error>
               <error>Specified fund not recognised. LU0278930234</error>
               </errors></header></message>"""
        )
        assert result.success is False
        assert result.errors == [
            "Unknown Product Code",
            "Specified fund not recognised. LU0278930234",
        ]

    def test_errors_beat_a_success_status(self) -> None:
        result = parse_response(
            """<message><header><response>SUCCESS</response>
               <errors><error>Specified fund not recognised.</error></errors></header></message>"""
        )
        assert result.success is False

    def test_unreadable_body_is_a_failure_not_an_exception(self) -> None:
        result = parse_response("<not xml")
        assert result.success is False
        assert result.document_location is None
        assert "Unreadable response" in result.errors[0]

    def test_round_trips_a_request_we_built(self) -> None:
        # A response echoing our own message shape must not be mistaken for a success.
        assert parse_response(build_request(_minimal())).document_location is None


class TestAnnuityBasis:
    """§i — mandatory for a pension quotation, optional otherwise."""

    def _quote(self, **kwargs: object) -> Quotation:
        base: dict[str, object] = {
            "product_ref": "SIPPEN",
            "quotation_type": "Accumulation",
            "plan_streams": [PlanStream("S1", "Existing Fund Value", 1000, "Single")],
        }
        base.update(kwargs)
        return Quotation(**base)  # type: ignore[arg-type]

    def test_a_pension_quotation_may_omit_it(self) -> None:
        """Regression, reverted twice.

        Spec §i marks the group mandatory for a pension product, so this once raised. The engine
        disagrees: dropping the group from a live request produced an equivalent document, with
        only the projected-income column falling to zero
        (docs/morgan-lloyd/illustrations-report.md §10). Refusing to build it blocked an
        illustration CTC would happily have produced, so the rule is asserted the other way
        round here — restore the check and this fails.
        """
        root = _tree(_minimal(quotations=[self._quote(pension=True)]))

        assert root.find(".//annuity_basis") is None
        assert root.find(".//quotation") is not None

    def test_a_non_pension_quotation_may_omit_it(self) -> None:
        root = _tree(_minimal(quotations=[self._quote()]))
        assert root.find(".//annuity_basis") is None

    def test_matches_the_spec_example(self) -> None:
        root = _tree(
            _minimal(
                quotations=[
                    self._quote(
                        pension=True,
                        annuity_basis=AnnuityBasis(
                            annuity_frequency="Quarterly",
                            annuity_timing="Advance",
                            escalation="Fixed",
                            percent=3,
                            spouse_percentage=50,
                        ),
                    )
                ]
            )
        )
        node = root.find(".//annuity_basis")
        assert node is not None
        assert [(e.tag, e.text) for e in node] == [
            ("annuity_frequency", "Quarterly"),
            ("annuity_timing", "Advance"),
            ("escalation", "Fixed"),
            ("percent", "3"),
            ("spouse_percentage", "50"),
        ]

    def test_lpi_escalation_with_a_guarantee_and_no_spouse(self) -> None:
        root = _tree(
            _minimal(
                quotations=[
                    self._quote(
                        pension=True,
                        annuity_basis=AnnuityBasis(
                            annuity_frequency="Monthly",
                            annuity_timing="Advance",
                            escalation="LPI",
                            guaranteed_period=5,
                        ),
                    )
                ]
            )
        )
        node = root.find(".//annuity_basis")
        assert node is not None
        assert node.findtext("guaranteed_period") == "5"
        assert node.find("percent") is None
        assert node.find("spouse_percentage") is None

    def test_fixed_escalation_requires_a_percent(self) -> None:
        with pytest.raises(CtcValidationError, match="'Fixed' needs a percent"):
            build_request(
                _minimal(
                    quotations=[
                        self._quote(pension=True, annuity_basis=AnnuityBasis(escalation="Fixed"))
                    ]
                )
            )

    def test_a_percent_without_fixed_escalation_is_rejected(self) -> None:
        # LPI/RPI rates come from the index, not from us — supplying one means a mis-built form.
        with pytest.raises(CtcValidationError, match="only to 'Fixed'"):
            build_request(
                _minimal(
                    quotations=[
                        self._quote(
                            pension=True,
                            annuity_basis=AnnuityBasis(escalation="LPI", percent=3),
                        )
                    ]
                )
            )

    def test_rejects_unknown_vocabulary(self) -> None:
        for kwargs, needle in (
            ({"annuity_frequency": "Weekly"}, "annuity_frequency"),
            ({"annuity_timing": "Upfront"}, "annuity_timing"),
            ({"escalation": "CPI"}, "escalation"),
        ):
            with pytest.raises(CtcValidationError, match=needle):
                build_request(
                    _minimal(
                        quotations=[
                            self._quote(pension=True, annuity_basis=AnnuityBasis(**kwargs))  # type: ignore[arg-type]
                        ]
                    )
                )

    def test_annuity_is_emitted_after_the_fee_groups(self) -> None:
        # Element order follows the spec outline: product_fees, adviser_fees, annuity_basis.
        root = _tree(
            _minimal(
                quotations=[
                    self._quote(
                        pension=True,
                        product_fees=[
                            Fee(
                                fee_name="Platform",
                                fee_basis="Ongoing",
                                fee_frequency="Monthly",
                                fee_timing="In Arrears",
                                fee_value_rate="Per Annum",
                                percentage=Decimal("0.3"),
                            )
                        ],
                        annuity_basis=AnnuityBasis(annuity_frequency="Monthly"),
                    )
                ]
            )
        )
        quotation = root.find(".//quotation")
        assert quotation is not None
        tags = [e.tag for e in quotation]
        assert tags.index("product_fees") < tags.index("annuity_basis")
        assert tags[-1] == "annuity_basis"


class TestExitAge:
    """The engine accepts a retirement age the investor has already passed and produces a
    document quoting a maturity date in the past (see the module docstring on `_check_exit_age`),
    so this is the only place it can be caught."""

    def _investor(self, **kw: object) -> Investor:
        base: dict[str, object] = {"surname": "Example", "date_of_birth": "1959-02-20"}
        base.update(kw)
        return Investor(**base)  # type: ignore[arg-type]

    def test_an_exit_age_already_passed_is_rejected(self) -> None:
        # 1959 + 65 = 2024, two years before the effective date. This is the exact shape of the
        # illustration that shipped.
        with pytest.raises(CtcValidationError, match="already 67"):
            build_request(_minimal(investors=[self._investor(exit_age=65)]))

    def test_an_exit_age_equal_to_current_age_is_rejected(self) -> None:
        # Equal means the maturity date is today — a zero-length projection, not a plan.
        with pytest.raises(CtcValidationError, match="not in the future"):
            build_request(_minimal(investors=[self._investor(exit_age=67)]))

    def test_a_future_exit_age_is_accepted(self) -> None:
        root = _tree(_minimal(investors=[self._investor(exit_age=68)]))
        assert root.findtext(".//investor/exit_age") == "68"

    def test_age_is_measured_at_the_effective_date_not_today(self) -> None:
        # Same investor, same exit age, different effective dates. Measuring against the clock
        # instead would make this test's outcome depend on when it runs.
        early = _minimal(effective_date="2020-01-01", investors=[self._investor(exit_age=63)])
        assert _tree(early).findtext(".//investor/exit_age") == "63"
        with pytest.raises(CtcValidationError, match="already 6"):
            build_request(
                _minimal(effective_date="2026-07-28", investors=[self._investor(exit_age=63)])
            )

    def test_an_absurd_exit_age_is_rejected(self) -> None:
        with pytest.raises(CtcValidationError, match="above 100"):
            build_request(_minimal(investors=[self._investor(exit_age=650)]))

    def test_the_check_is_skipped_when_either_field_is_absent(self) -> None:
        # Both are optional in the schema; an illustration without them is legitimate (and
        # meaningless, which is the caller's business, not this validator's).
        assert _tree(_minimal(investors=[Investor(surname="NoDates")])) is not None
        assert _tree(_minimal(investors=[Investor(surname="AgeOnly", exit_age=65)])) is not None

    def test_a_malformed_date_of_birth_is_named(self) -> None:
        with pytest.raises(CtcValidationError, match="not yyyy-mm-dd"):
            build_request(_minimal(investors=[Investor(date_of_birth="20/02/1959", exit_age=68)]))


class TestProjectionExitAge:
    """A client already in drawdown is older than the retirement age on the record. That age
    is when they retired. The quote still has to end in the future, or the engine prints a
    maturity date that has already passed."""

    def test_someone_already_drawing_is_projected_forward(self) -> None:
        # Born 1940, retirement age 75, illustrated on 2026-10-11: already 86. 75 would be a
        # term that ended in 2015.
        assert (
            projection_exit_age(
                retirement_age=75,
                born=date(1940, 1, 1),
                effective=date(2026, 10, 11),
                drawdown=True,
            )
            == DRAWDOWN_HORIZON_AGE
        )

    def test_a_retirement_still_ahead_is_the_term_on_either_basis(self) -> None:
        born = date(1960, 1, 1)
        effective = date(2026, 10, 11)
        assert (
            projection_exit_age(retirement_age=75, born=born, effective=effective, drawdown=True)
            == 75
        )
        assert (
            projection_exit_age(retirement_age=75, born=born, effective=effective, drawdown=False)
            == 75
        )

    def test_accumulation_past_retirement_is_refused(self) -> None:
        with pytest.raises(CtcValidationError, match="already 86"):
            projection_exit_age(
                retirement_age=75,
                born=date(1940, 1, 1),
                effective=date(2026, 10, 11),
                drawdown=False,
            )

    def test_a_client_older_than_the_horizon_runs_one_year_ahead(self) -> None:
        assert (
            projection_exit_age(
                retirement_age=75,
                born=date(1927, 1, 1),
                effective=date(2026, 10, 11),
                drawdown=True,
            )
            == 100
        )

    def test_a_client_past_the_maximum_age_is_refused(self) -> None:
        with pytest.raises(CtcValidationError, match="cannot run past age 100"):
            projection_exit_age(
                retirement_age=75,
                born=date(1926, 1, 1),
                effective=date(2026, 10, 11),
                drawdown=True,
            )
