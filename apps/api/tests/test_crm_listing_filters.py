"""The CRM listings' filter rules — pinned where they are duplicated across languages.

Client status derives a value rather than reading a column and is duplicated in the portal because
that is where the badge is drawn. A derived filter that disagrees with the badge beside it is the
worst kind of wrong: the screen shows a lead, the "Lead" filter does not return
them, and nothing on the page says which of the two is lying.

- ``_contact_status`` mirrors ``pkgs/ui/.../book/clients/status.ts::clientStatus``
- the accounts model filter uses the stable ``platform.models.id`` rendered by the model column
- the contributions status filter speaks STAGES, which is what the workbench renders
"""

from typing import Any

import pytest

from app.routers import contributions as contributions_router
from app.routers import crm


class TestClientStatus:
    """`clientStatus`: closed (deleted_at) wins; then the custodian's Suspended; no accounts
    is a Lead; else Active.

    ``enabled`` is deliberately not consulted: Deactivate is gone, and a disabled flag that
    still painted Inactive would resurrect the status the product no longer offers.
    """

    @pytest.mark.parametrize(
        ("row", "expected"),
        [
            ({"account_count": 3}, "active"),
            ({"account_count": 0}, "lead"),
            ({"account_count": 3, "provider_status": "suspended"}, "suspended"),
            ({"account_count": 3, "provider_status": "Suspended"}, "suspended"),
            ({"account_count": 0, "provider_status": "inactive"}, "suspended"),
            ({"account_count": 3, "deleted_at": "2026-09-11T00:00:00Z"}, "closed"),
            # Closed wins over accounts and over the custodian's word.
            (
                {
                    "account_count": 2,
                    "provider_status": "suspended",
                    "deleted_at": "2026-09-11T00:00:00Z",
                },
                "closed",
            ),
            # enabled is NOT a status: Deactivate is gone.
            ({"enabled": False, "account_count": 3}, "active"),
            # The adult does not own the child's JISA, but representing one is an active client
            # relationship. The separate count prevents the child's AUM being attributed twice.
            ({"account_count": 0, "represented_account_count": 1}, "active"),
        ],
    )
    def test_the_derived_states(self, row: dict[str, Any], expected: str) -> None:
        assert crm._contact_status(row) == expected

    def test_a_missing_account_count_reads_as_a_lead(self) -> None:
        """The count comes from a LATERAL that COALESCEs to 0, so absent means none — and a
        client with no accounts is a lead, not active."""
        assert crm._contact_status({}) == "lead"

    def test_the_filter_uses_the_same_rule_as_the_facet(self) -> None:
        """If the predicate and the option list derived status differently, a filter could offer
        a value that returns nothing."""
        row = {"account_count": 0}
        status_filter = crm._CONTACT_FILTERS["status"]
        read = dict(crm._CONTACT_FACETS)["status"]
        assert status_filter(row, read(row)) is True
        assert status_filter(row, "active") is False


class TestClientFilters:
    def test_advisers_match_on_any(self) -> None:
        """A client can be serviced by several advisers; filtering by one must find them."""
        row = {"advisers": ["Priya Shah", "Dan Reeve"], "enabled": True, "account_count": 1}
        assert crm._CONTACT_FILTERS["adviser"](row, "Dan Reeve") is True
        assert crm._CONTACT_FILTERS["adviser"](row, "Someone Else") is False

    def test_a_client_with_no_adviser_is_not_returned_for_one(self) -> None:
        row = {"advisers": [], "enabled": True, "account_count": 1}
        assert crm._CONTACT_FILTERS["adviser"](row, "Dan Reeve") is False


class TestAccountFilters:
    def test_model_matches_the_stable_catalogue_id(self) -> None:
        """Names are editable and need not be unique; the dropdown posts the model's integer id."""
        row = {"model_id": 42, "model_name": "Growth 60"}
        assert crm._ACCOUNT_FILTERS["model"](row, "42") is True
        assert crm._ACCOUNT_FILTERS["model"](row, "Growth 60") is False

    def test_unmodelled_account_is_excluded_by_a_model_filter(self) -> None:
        assert crm._ACCOUNT_FILTERS["model"]({"model_id": None}, "42") is False

    def test_every_declared_filter_has_a_facet_and_vice_versa(self) -> None:
        """A filter with no facet cannot be offered in the UI; a facet with no filter offers a
        choice that does nothing. Both are silent.

        A RANGE is the one exception, and it is declared rather than inferred so that a filter
        which simply FORGOT its facet still fails here.
        """
        # The ranges are named HERE because the app expresses "this filter is a range" in its
        # filter SQL now (`listing.range_sql`), not as a set. Add one and forget to list it and
        # this fails, which is the protection the constant used to give.
        for filters, facets, ranges in (
            (crm._CONTACT_FILTERS, crm._CONTACT_FACETS, {"value"}),
            (crm._ACCOUNT_FILTERS, crm._ACCOUNT_FACETS, {"value"}),
            (crm._TRANSACTION_FILTERS, crm._TRANSACTION_FACETS, {"amount", "quantity"}),
        ):
            assert ranges <= set(filters), "a range is declared that is not a filter"
            assert set(filters) - ranges == {key for key, _ in facets}


class TestContributionStatusFilter:
    """The workbench's status filter narrows by STAGE, not by the raw status.

    Six stages and two terminals over ten statuses, because several of them answer the same
    question — and the filter has to speak the words on the screen. Filtering on the raw status
    would offer "confirmed" and "identified" as options while the badges beside them read "Not
    lodged" and "To classify", so picking one would return nothing and read as an
    empty book rather than as a mismatched vocabulary.
    """

    def test_it_matches_on_the_stage(self) -> None:
        predicate = contributions_router._CONTRIBUTION_FILTERS["status"]
        # Two different statuses, one stage — this is the whole point.
        assert predicate({"status": "identified"}, "awaiting_classification") is True
        assert predicate({"status": "awaiting_confirmation"}, "awaiting_classification") is True
        # And the raw status is not an option any more.
        assert predicate({"status": "identified"}, "identified") is False

    def test_the_facet_offers_exactly_what_the_filter_matches(self) -> None:
        """Same rule both sides, for `_contact_status`'s reason: an option the predicate cannot
        match is a filter that returns nothing and explains nothing."""
        row = {"status": "ml_recorded"}
        predicate = contributions_router._CONTRIBUTION_FILTERS["status"]
        read = dict(contributions_router._CONTRIBUTION_FACETS)["status"]
        assert read(row) == "with_administrator"
        assert predicate(row, read(row)) is True

    def test_a_status_with_no_stage_offers_no_option(self) -> None:
        # `listing._values` drops a None, so an unmapped status simply contributes no option —
        # which is honest: it is not reachable by anything the UI offers.
        read = dict(contributions_router._CONTRIBUTION_FACETS)["status"]
        assert read({"status": "something_new"}) is None


class TestValueRanges:
    """The listings' value/amount ranges — continuous columns, so no facet could list them."""

    def test_an_unvalued_row_answers_no_value_question(self) -> None:
        # `portfolio_value` is `COALESCE(..., 0)`, so an unvalued client arrives as a real 0 and
        # reading that as "worth nothing" sweeps the unpriced tail into "under 10,000".
        match = crm._CONTACT_FILTERS["value"]
        assert match({"portfolio_value": 0, "valued_account_count": 0}, ["0", "10000"]) is False
        assert match({"portfolio_value": 0, "valued_account_count": 0}, None) is True
        assert match({"portfolio_value": 0, "valued_account_count": 2}, ["0", "10000"]) is True
        # A cash movement carries no asset and so no quantity at all — not a quantity of zero.
        assert crm._TRANSACTION_FILTERS["quantity"]({"quantity": None}, ["1", ""]) is False
        # A slider's track and its predicate must read the column the same way.
        assert crm._ACCOUNT_BOUNDS["value"]({"value": 250_000}) == 250_000.0
        assert crm._ACCOUNT_FILTERS["value"]({"value": 250_000}, ["250000", "250000"]) is True
