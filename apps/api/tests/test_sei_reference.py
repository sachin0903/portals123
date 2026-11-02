"""The SEI reference codes behind the contribution rail.

``docs/contributions.md`` §4 carried ``descriptionTypeCode`` / ``treatmentTypeCode`` as the
standing blocker — "no lookup endpoint exists in the published spec set". True of the API,
false of the file drop: the nightly ``Reference`` report publishes both sets in full, and the
values in ``app/integrations/sei/reference.py`` are transcribed from the p04 production drop.

There is no vendor package to pin them against the way ``morgan_lloyd.enums`` pins the Dynamics
option sets, so what these tests protect is the *reasoning*: the read-side and write-side codes
pair up one-to-one, relief is distinguishable from the contribution that earned it, and a code
we do not recognise produces no suggestion rather than a wrong one.

The pairing, verbatim from the drop:

    treatment 4958 Member Net Contribution              <-> description 21121
    treatment 4959 Member Basic Rate Tax Relief         <-> description 21122
    treatment 4960 Employee Gross Contribution          <-> description 21123
    treatment 4961 Employer Gross Contribution          <-> description 21124
    treatment 4962 Employee Net Contribution            <-> description 21125
    treatment 4963 Employee Basic Rate Tax Relief       <-> description 21126
"""

import pytest

from app.integrations.sei import cash_receipts, reference


class TestTheVerifiedCodes:
    def test_the_ras_pairs_are_offset_by_a_fixed_block(self) -> None:
        """4958-4963 and 21121-21126 are the same six concepts in the same order. Stated as a
        structural check because that ordering is what makes the transcription auditable."""
        treatments = [
            reference.TREATMENT_MEMBER_NET,
            reference.TREATMENT_MEMBER_BASIC_RATE_RELIEF,
            reference.TREATMENT_EMPLOYEE_GROSS,
            reference.TREATMENT_EMPLOYER_GROSS,
            reference.TREATMENT_EMPLOYEE_NET,
            reference.TREATMENT_EMPLOYEE_BASIC_RATE_RELIEF,
        ]
        descriptions = [
            reference.DESCRIPTION_MEMBER_NET,
            reference.DESCRIPTION_MEMBER_BASIC_RATE_RELIEF,
            reference.DESCRIPTION_EMPLOYEE_GROSS,
            reference.DESCRIPTION_EMPLOYER_GROSS,
            reference.DESCRIPTION_EMPLOYEE_NET,
            reference.DESCRIPTION_EMPLOYEE_BASIC_RATE_RELIEF,
        ]
        assert treatments == [str(4958 + n) for n in range(6)]
        assert descriptions == [str(21121 + n) for n in range(6)]

    def test_cash_receipt_is_transaction_type_six(self) -> None:
        """Every contribution and every relief payment arrives on this one type."""
        assert reference.TRANSACTION_TYPE_CASH_RECEIPT == "6"

    def test_every_code_has_a_label(self) -> None:
        """The labels are the Reference report's own DESCRIPTION column, and the workbench
        shows them as provenance — a code with no label renders as a bare number."""
        for code in reference.CONTRIBUTION_DESCRIPTION_CODES | reference.RELIEF_DESCRIPTION_CODES:
            assert reference.DESCRIPTION_LABELS.get(code)

    def test_relief_and_contribution_codes_do_not_overlap(self) -> None:
        """The whole point of the split. An overlap would make the reconciliation count a
        relief credit as a second contribution."""
        assert not (reference.RELIEF_DESCRIPTION_CODES & reference.CONTRIBUTION_DESCRIPTION_CODES)


class TestReliefDetection:
    def test_a_relief_credit_is_recognised_by_its_code(self) -> None:
        assert reference.is_relief_payment({"source_subtype": "21122"}) is True
        assert reference.is_relief_payment({"source_subtype": "21126"}) is True

    def test_a_contribution_credit_is_not(self) -> None:
        assert reference.is_relief_payment({"source_subtype": "21121"}) is False
        assert reference.is_relief_payment({"source_subtype": "20009"}) is False

    def test_a_numeric_code_from_the_lake_still_matches(self) -> None:
        """DuckDB may hand back a float for a column the profiler typed numerically. `21122.0`
        must not read as "not relief" — that would silently reclassify it as a contribution."""
        assert reference.is_relief_payment({"source_subtype": 21122.0}) is True

    def test_a_firm_on_the_unsplit_code_set_falls_back_to_the_narrative(self) -> None:
        """A firm using 20009 for everything has no code to go on. Treating relief as a fresh
        contribution double-counts the member's annual allowance, so the words are worth
        reading — but only when there is no code, never in preference to one."""
        assert reference.is_relief_payment(
            {"source_subtype": None, "narrative": "Basic Rate Tax Relief on Contribution"}
        )

    def test_the_code_wins_over_the_narrative(self) -> None:
        assert (
            reference.is_relief_payment(
                {"source_subtype": "21121", "narrative": "tax relief something"}
            )
            is False
        )


class TestCategorySuggestions:
    @pytest.mark.parametrize(
        ("code", "category"),
        [
            ("21121", "personal_contribution"),
            ("21125", "personal_contribution"),
            ("21124", "employer_contribution"),
            ("20012", "transfer_in"),
        ],
    )
    def test_a_known_code_suggests_its_category(self, code: str, category: str) -> None:
        assert reference.suggested_category({"source_subtype": code}) == category

    def test_an_unknown_code_suggests_nothing(self) -> None:
        """No suggestion is right; a wrong pre-selection an adviser accepts is not."""
        assert reference.suggested_category({"source_subtype": "99999"}) is None
        assert reference.suggested_category({}) is None

    def test_relief_is_never_suggested_as_a_contribution(self) -> None:
        for code in reference.RELIEF_DESCRIPTION_CODES:
            assert reference.suggested_category({"source_subtype": code}) is None


class TestTheRefusalNamesTheCodes:
    """The blocker was never that the call failed — it was that nobody could find out what to
    configure. The 422 now says."""

    def test_a_missing_treatment_code_is_named_with_its_reference_value(self) -> None:
        detail = cash_receipts.explain_missing(
            {
                "sei_account_number": "A1",
                "sei_portfolio_id": "P1",
                "firm_id": "3102",
                "description_code": "20009",
                "treatment_code": None,
                "category": "personal_contribution",
            }
        )
        assert detail is not None
        assert "4958" in detail
        assert "21121" in detail
        # Still a suggestion, not a substitution: SEI validates the pair against the caller's
        # role, and a wrong pair is a rejection on a client's pension.
        assert "confirm them with SEI" in detail

    def test_a_complete_context_explains_nothing(self) -> None:
        assert (
            cash_receipts.explain_missing(
                {
                    "sei_account_number": "A1",
                    "sei_portfolio_id": "P1",
                    "firm_id": "3102",
                    "description_code": "20009",
                    "treatment_code": "4958",
                    "category": "personal_contribution",
                }
            )
            is None
        )

    def test_an_unmappable_category_gets_no_invented_suggestion(self) -> None:
        detail = cash_receipts.explain_missing(
            {
                "sei_account_number": "A1",
                "sei_portfolio_id": "P1",
                "firm_id": "3102",
                "description_code": "20009",
                "treatment_code": None,
                "category": "not_a_contribution",
            }
        )
        assert detail is not None
        assert "4958" not in detail


def test_a_global_portfolio_id_is_the_trailing_entity_id() -> None:
    assert reference.portfolio_entity_id("3108-SWP-I31-59103919") == "59103919"
    assert reference.portfolio_entity_id("59098813") == "59098813"
    assert reference.portfolio_entity_id("90040-1") is None
    assert reference.portfolio_entity_id(None) is None
