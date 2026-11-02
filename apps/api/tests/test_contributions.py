"""Contribution arithmetic and the pipeline vocabularies.

The relief maths is deliberately duplicated: `app/contributions.py` is authoritative (a client
cannot be trusted with the number that determines an HMRC reclaim) and
`apps/platform/src/lib/contributions/relief.ts` drives the live preview. The cases here are the
same cases as `relief.test.ts`, so a change to one without the other shows up as a failure.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import contributions


class TestGrossUp:
    """Personal and third-party contributions are paid NET of basic-rate relief, which the
    scheme reclaims. Employer contributions are paid GROSS — nothing to reclaim."""

    @pytest.mark.parametrize(
        ("category", "amount", "gross", "relief"),
        [
            # 800 net grosses to 1000, of which 200 is reclaimed.
            ("personal_contribution", "800", "1000.00", "200.00"),
            ("third_party_contribution", "800", "1000.00", "200.00"),
            # Employer pays gross: what arrives is what counts, and no relief is due.
            ("employer_contribution", "1000", "1000.00", "0.00"),
            # Neither of these is a contribution in the tax sense.
            ("transfer_in", "1000", "1000.00", "0.00"),
            ("not_a_contribution", "1000", "1000.00", "0.00"),
        ],
    )
    def test_gross_and_relief(self, category: str, amount: str, gross: str, relief: str) -> None:
        assert contributions.gross_of(Decimal(amount), category) == Decimal(gross)
        assert contributions.relief_of(Decimal(amount), category) == Decimal(relief)

    def test_rounds_to_pennies_half_up(self) -> None:
        # 1000.01 / 0.8 = 1250.0125 -> 1250.01
        assert contributions.gross_of(Decimal("1000.01"), "personal_contribution") == Decimal(
            "1250.01"
        )
        # 100.005 / 0.8 = 125.00625 -> 125.01
        assert contributions.gross_of(Decimal("100.005"), "personal_contribution") == Decimal(
            "125.01"
        )

    def test_relief_is_always_gross_minus_net(self) -> None:
        for value in ("1", "33.33", "800", "12345.67"):
            amount = Decimal(value)
            gross = contributions.gross_of(amount, "personal_contribution")
            assert contributions.relief_of(amount, "personal_contribution") == gross - amount

    def test_accepts_floats_and_strings_without_binary_drift(self) -> None:
        assert contributions.gross_of(800.0, "personal_contribution") == Decimal("1000.00")
        assert contributions.gross_of("800", "personal_contribution") == Decimal("1000.00")


class TestTaxYear:
    """A UK tax year starts on 6 April, so 5 April belongs to the previous one."""

    @pytest.mark.parametrize(
        ("on", "expected"),
        [
            (date(2026, 4, 6), "2026/27"),
            (date(2026, 4, 5), "2025/26"),
            (date(2026, 12, 31), "2026/27"),
            (date(2027, 1, 1), "2026/27"),
            (date(2027, 4, 5), "2026/27"),
            (date(2027, 4, 6), "2027/28"),
            # The century roll: 2099/00, not 2099/100.
            (date(2099, 6, 1), "2099/00"),
        ],
    )
    def test_tax_year_of(self, on: date, expected: str) -> None:
        assert contributions.tax_year_of(on) == expected


class TestVocabularies:
    """The service's enums must match the migration's CHECK constraints — a mismatch is a
    500 from Postgres rather than a validation error."""

    def test_categories_match_the_portal(self) -> None:
        assert contributions.CATEGORIES == {
            "personal_contribution",
            "employer_contribution",
            "third_party_contribution",
            "transfer_in",
            "not_a_contribution",
        }

    def test_frequencies_cover_every_morgan_lloyd_period(self) -> None:
        from app.integrations.morgan_lloyd import contributions as ml

        assert contributions.FREQUENCIES == set(ml.FREQUENCY_TO_ML)

    def test_net_paid_is_exactly_the_relievable_categories(self) -> None:
        assert contributions.NET_PAID == {"personal_contribution", "third_party_contribution"}

    def test_classifiable_is_a_prefix_of_the_pipeline(self) -> None:
        assert contributions.CLASSIFIABLE == set(contributions.STATUSES[:3])


class TestStages:
    """The stage vocabulary — the words the workbench actually shows.

    `status` is the engine's word for where a contribution has got to; a STAGE is the word an
    adviser reads, and there are fewer of them because several statuses answer the same question.
    It is applied HERE (the `status` filter and its facet, `routers/contributions.py`) and rendered
    THERE (`pkgs/ui/.../book/contributions/status.ts`), so a divergence would offer a filter option
    that matches nothing and report it as an empty book.
    """

    def test_every_status_has_a_stage(self) -> None:
        # TOTAL, including the terminals. A status with no stage is counted by no metric, badged by
        # nothing and unreachable by any filter option — three silent absences from one omission.
        for status in [*contributions.STATUSES, "manual_intervention", "cancelled"]:
            assert contributions.stage_of(status) is not None, status

    def test_no_status_belongs_to_two_stages(self) -> None:
        # The portal SUMS a stage's amounts, so a status in two stages is the same money reported
        # twice across the strip.
        seen: set[str] = set()
        for members in contributions.STAGES.values():
            for status in members:
                assert status not in seen, status
                seen.add(status)

    def test_an_unknown_status_has_no_stage_rather_than_a_default(self) -> None:
        # Not a fallback bucket: an unmapped status must show up as a gap here rather than be
        # quietly filed under whichever stage looked closest.
        assert contributions.stage_of("something_new") is None
        assert contributions.stage_of(None) is None

    @pytest.mark.pkgs_ui_pin
    def test_the_stages_match_the_portal_exactly(self) -> None:
        """One vocabulary, two languages — the same discipline the relief maths gets.

        Parsed out of the TypeScript rather than kept as a second hand-written list here: a
        hand-written copy is a third place to forget, and the point of the test is that there are
        only two.
        """
        import re
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[3]
            / "pkgs/ui/src/lib/components/book/contributions/status.ts"
        ).read_text(encoding="utf-8")
        block = source[source.index("export const STAGES") : source.index("/** The six the KPI")]
        portal = {
            key: tuple(re.findall(r"'([a-z_]+)'", statuses))
            for key, statuses in re.findall(
                r"key: '([a-z_]+)',[\s\S]*?statuses: \[([^\]]*)\]", block
            )
        }
        assert portal == contributions.STAGES

    def test_the_metric_stages_are_the_six_that_are_work(self) -> None:
        # Completed and Cancelled are filter answers, not metrics: counting a finished contribution
        # makes a tidy book look busy, which is why `OFF_PIPELINE` exists on the portal side too.
        assert list(contributions.STAGES)[:6] == [
            "awaiting_classification",
            "with_administrator",
            "relief_claimed",
            "relief_in_transit",
            "relief_credited",
            "not_lodged",
        ]


class TestStageTotals:
    """The workbench's six figures, computed over the whole book.

    Server-side because the portal is handed one PAGE: "£38,400 is with the scheme administrator"
    derived from twenty-five of four hundred rows is a statement about the scroll position wearing
    the clothes of a book total — and it is exactly the kind of figure somebody quotes.
    """

    def test_it_sums_the_amount_paid_per_stage(self) -> None:
        totals = contributions.stage_totals(
            [
                {"status": "identified", "amount": 100.0},
                {"status": "awaiting_confirmation", "amount": 250.0},
                {"status": "ml_recorded", "amount": 1000.0},
            ]
        )
        # Two statuses, one stage — the point of having stages at all.
        assert totals["awaiting_classification"] == {"amount": 350.0, "count": 2}
        assert totals["with_administrator"] == {"amount": 1000.0, "count": 1}

    def test_it_sums_the_amount_paid_and_never_the_gross(self) -> None:
        # The gross includes relief HMRC has not paid yet, so totalling it states money the scheme
        # does not hold.
        totals = contributions.stage_totals(
            [{"status": "ml_recorded", "amount": 800.0, "gross_amount": 1000.0}]
        )
        assert totals["with_administrator"]["amount"] == 800.0

    def test_every_stage_is_present_even_at_zero(self) -> None:
        # An absent key renders as a missing card; a stage with no money is a real answer, and an
        # empty "Not lodged" is good news that should still be shown.
        totals = contributions.stage_totals([])
        assert set(totals) == set(contributions.STAGES)
        assert all(t == {"amount": 0.0, "count": 0} for t in totals.values())

    def test_a_status_with_no_stage_is_counted_nowhere(self) -> None:
        # Rather than folded into whichever stage looked closest, which would attribute money to a
        # bucket nobody put it in.
        totals = contributions.stage_totals([{"status": "something_new", "amount": 500.0}])
        assert all(t["count"] == 0 for t in totals.values())
