"""The server-side listing contract: search, filters, paging, and — the subtle one — facets.

`pkgs/ui/.../book/listing/filters.ts` is the client-side twin these replace, and the two are
pinned against the same cases so a change to one without the other shows up here.

The facet rule is what most of this file is about, because getting it wrong is not a visual bug:
if a filter's options are derived from rows already narrowed by that filter, choosing a value
leaves it as the only option and there is no way back to the full list. A filter becomes a
one-way door and the user has to know to reload the page.
"""

from datetime import date, datetime
from typing import Any

import pytest

from app import listing

ROWS: list[dict[str, Any]] = [
    {"name": "Sophie Halloway", "status": "active", "adviser": "Dan Reeve", "type": "SIPP"},
    {"name": "Theo Caldwell", "status": "active", "adviser": "Priya Shah", "type": "ISA"},
    {"name": "Marta Bex", "status": "closed", "adviser": "Dan Reeve", "type": "SIPP"},
    {"name": "Owen Frost", "status": "active", "adviser": None, "type": "GIA"},
]

FILTERS: listing.Filters = {
    "status": lambda row, value: listing.matches(value, row.get("status")),
    "adviser": lambda row, value: listing.matches(value, row.get("adviser")),
    "type": lambda row, value: listing.matches(value, row.get("type")),
}
FACETS: listing.Facets = [
    ("status", lambda row: row.get("status")),
    ("adviser", lambda row: row.get("adviser")),
    ("type", lambda row: row.get("type")),
]


def _visible(active: dict[str, str | None]) -> list[dict[str, Any]]:
    return [
        row
        for row in ROWS
        if all(predicate(row, active.get(key)) for key, predicate in FILTERS.items())
    ]


class TestCleaningAFilterValue:
    @pytest.mark.parametrize("raw", [None, "", "   ", listing.ALL])
    def test_absent_blank_and_the_all_sentinel_all_mean_unfiltered(self, raw: str | None) -> None:
        """`__all__` arrives on the wire when a user re-selects "All statuses". Treating it as a
        value would filter to rows whose status is literally "__all__" — i.e. none."""
        assert listing.clean(raw) is None

    def test_a_real_value_is_trimmed(self) -> None:
        assert listing.clean("  active ") == "active"


class TestMatching:
    def test_an_unset_filter_matches_everything(self) -> None:
        assert listing.matches(None, "anything") is True
        assert listing.matches(None, None) is True

    def test_a_set_filter_is_exact(self) -> None:
        assert listing.matches("active", "active") is True
        assert listing.matches("active", "inactive") is False

    def test_a_null_column_never_matches_a_set_filter(self) -> None:
        # An adviserless account must not appear under "Adviser: Dan Reeve".
        assert listing.matches("Dan Reeve", None) is False

    def test_multi_valued_columns_match_on_any(self) -> None:
        # A client can have several advisers; filtering by one must find them.
        assert listing.matches_any("Dan Reeve", ["Priya Shah", "Dan Reeve"]) is True
        assert listing.matches_any("Dan Reeve", ["Priya Shah"]) is False
        assert listing.matches_any(None, []) is True


class TestMultiSelectFilters:
    """A filter the reader gave SEVERAL values of. The filter bar offers them; the question a book
    is read with is "Active OR Onboarding", and no row could satisfy two values of one column at
    once anyway."""

    def test_a_list_is_an_or(self) -> None:
        assert listing.matches(["active", "onboarding"], "onboarding") is True
        assert listing.matches(["active", "onboarding"], "suspended") is False

    def test_both_sides_may_be_several(self) -> None:
        # A client advised by Cole AND Shaw is a match for a filter of Shaw OR Vale.
        assert listing.matches_any(["Shaw", "Vale"], ["Cole", "Shaw"]) is True
        assert listing.matches_any(["Vale"], ["Cole", "Shaw"]) is False

    def test_one_value_still_means_what_it_did(self) -> None:
        # An older caller's `?status=active` parses to a one-element list and is unchanged.
        assert listing.matches(["active"], "active") is True
        assert listing.matches(["active"], "onboarding") is False

    def test_an_empty_choice_is_no_filter_at_all(self) -> None:
        # A stray `&status=` must not narrow to nothing and read as an empty table.
        assert listing.clean_many([]) is None
        assert listing.clean_many(None) is None
        assert listing.clean_many(["", "  ", listing.ALL]) is None
        assert listing.matches(listing.clean_many(["  "]), "anything") is True

    def test_a_bare_string_is_one_value_not_a_bag_of_letters(self) -> None:
        """The routers are plain async functions. FastAPI hands these params in as a list, but
        every router test — and any internal caller — passes `type="organisation"` directly, and
        iterating that string filtered on "o", "r", "g"… and matched nothing."""
        assert listing.clean_many("organisation") == ["organisation"]
        assert listing.clean_many("") is None
        assert listing.matches(listing.clean_many("organisation"), "organisation") is True
        assert listing.matches(listing.clean_many("organisation"), "individual") is False

    def test_it_keeps_the_readers_order_and_drops_repeats(self) -> None:
        assert listing.clean_many([" onboarding ", "active", "onboarding"]) == [
            "onboarding",
            "active",
        ]


class TestCleanRange:
    """A bound `clean_range` cannot read narrows NOTHING, rather than 422ing the listing."""

    @pytest.mark.parametrize("word", ["nan", "NaN", "inf", "-inf", "Infinity", "1e999", "abc"])
    def test_an_unreadable_bound_is_dropped(self, word: str) -> None:
        # `float()` alone took all of these but "abc": `lo = nan` makes every comparison False
        # (narrows nothing, chip still lit), and `1e999` is `inf`, which empties the book.
        pair = listing.clean_range([word, "1000"])
        assert pair == ["", "1000"]
        assert listing.within_range(pair, 5000) is False
        assert listing.within_range(pair, 500) is True

    def test_a_blank_slot_stays_blank(self) -> None:
        # `clean_many` drops it, turning "up to 3000" into "from 3000".
        assert listing.clean_range(["", "3000"]) == ["", "3000"]
        assert listing.clean_many(["", "3000"]) == ["3000"]
        assert listing.clean_range(["nan", "inf"]) is None

    def test_a_date_range_is_kept_and_compared_by_CALENDAR_DAY(self) -> None:
        # `clean_range` always kept an ISO date; `within_range` parsed its bounds with `float()`
        # alone and returned True for every row, so a date filter did nothing at all. The day,
        # not the moment: filed at 23:58 on the last day is still inside.
        pair = listing.clean_range(["2026-01-01", "2026-01-31"])
        assert pair == ["2026-01-01", "2026-01-31"]
        assert listing.within_range(pair, datetime(2026, 1, 31, 23, 58)) is True
        assert listing.within_range(pair, "2026-01-31T23:58:00+00:00") is True
        assert listing.within_range(pair, date(2026, 2, 1)) is False
        assert listing.within_range(pair, None) is False  # unknown answers no date question
        assert listing.within_range(["2026-01-01", ""], date(2030, 5, 1)) is True
        assert listing.clean_range(["2026-02-31", ""]) is None
        # The axis is chosen by the BOUNDS, so a money column is still read as money.
        assert listing.within_range(["50", "3000"], date(2026, 1, 15)) is False


class TestSearch:
    def test_an_empty_query_matches_everything(self) -> None:
        assert listing.search(None, "anything") is True
        assert listing.search("   ", "anything") is True

    def test_it_is_case_insensitive_and_partial(self) -> None:
        assert listing.search("hallow", "Sophie Halloway") is True
        assert listing.search("HALLOW", "Sophie Halloway") is True

    def test_it_spans_every_field_given(self) -> None:
        assert listing.search("reeve", "Sophie Halloway", None, "Dan Reeve") is True

    def test_a_none_field_is_skipped_not_stringified(self) -> None:
        # `str(None)` is "None", so a search for "none" would match every row with a null field.
        assert listing.search("none", None, None) is False


class TestPaging:
    def test_no_limit_returns_the_bare_list(self) -> None:
        """The house rule: existing callers parse a list, and adding paging must not change the
        shape under them."""
        assert listing.page(ROWS, limit=None) == ROWS

    def test_a_limit_wraps_the_page(self) -> None:
        result = listing.page(ROWS, limit=2)
        assert isinstance(result, dict)
        assert [r["name"] for r in result["items"]] == ["Sophie Halloway", "Theo Caldwell"]

    def test_total_is_the_match_count_not_the_page_size(self) -> None:
        # This is what the pager needs to know there is a next page, and what lets the UI say
        # "2 of 4" rather than "2 of 2".
        assert listing.page(ROWS, limit=2)["total"] == 4

    def test_the_offset_walks_the_list(self) -> None:
        page = listing.page(ROWS, limit=2, offset=2)
        assert [r["name"] for r in page["items"]] == ["Marta Bex", "Owen Frost"]

    def test_an_offset_past_the_end_is_empty_rather_than_an_error(self) -> None:
        page = listing.page(ROWS, limit=2, offset=99)
        assert page["items"] == [] and page["total"] == 4

    def test_junk_is_clamped_rather_than_rejected(self) -> None:
        """A bad page number is a URL someone edited, not an attack. Clamping keeps the page
        usable; erroring makes a shared link break."""
        assert len(listing.page(ROWS, limit=0)["items"]) == 1
        assert listing.page(ROWS, limit=2, offset=-5)["items"][0]["name"] == "Sophie Halloway"

    def test_an_oversized_limit_clamps_to_the_ceiling(self) -> None:
        many = [{"n": i} for i in range(listing.PAGE_LIMIT_MAX + 50)]
        assert len(listing.page(many, limit=10_000)["items"]) == listing.PAGE_LIMIT_MAX


class TestFacets:
    def test_unfiltered_options_are_every_distinct_value(self) -> None:
        options = listing.facets_for(ROWS, FACETS, {}, FILTERS)
        assert options["status"] == ["active", "closed"]
        assert options["type"] == ["GIA", "ISA", "SIPP"]

    def test_nulls_are_not_offered_as_an_option(self) -> None:
        # "Adviser: —" is not a filter anyone can act on.
        assert listing.facets_for(ROWS, FACETS, {}, FILTERS)["adviser"] == [
            "Dan Reeve",
            "Priya Shah",
        ]

    def test_a_facet_does_not_narrow_its_own_options(self) -> None:
        """THE rule. With status=closed applied, the status options must still offer active —
        otherwise there is no way back to the full list."""
        active = {"status": "closed", "adviser": None, "type": None}
        assert listing.facets_for(ROWS, FACETS, active, FILTERS)["status"] == [
            "active",
            "closed",
        ]

    def test_a_facet_is_narrowed_by_the_OTHER_filters(self) -> None:
        """The other half of the rule: with status=closed, the only adviser worth offering is
        one who actually has a closed row. Offering Priya Shah there is offering a combination
        that returns nothing."""
        active = {"status": "closed", "adviser": None, "type": None}
        options = listing.facets_for(ROWS, FACETS, active, FILTERS)
        assert options["adviser"] == ["Dan Reeve"]
        assert options["type"] == ["SIPP"]

    def test_options_survive_selecting_one_of_them(self) -> None:
        """The regression the rule exists for: pick an adviser, and both advisers are still
        listed, so the choice is reversible from the control itself."""
        active = {"status": None, "adviser": "Dan Reeve", "type": None}
        assert listing.facets_for(ROWS, FACETS, active, FILTERS)["adviser"] == [
            "Dan Reeve",
            "Priya Shah",
        ]

    def test_options_are_collated_case_insensitively(self) -> None:
        rows = [{"status": "beta"}, {"status": "Alpha"}]
        facets: listing.Facets = [("status", lambda row: row.get("status"))]
        filters: listing.Filters = {
            "status": lambda row, value: listing.matches(value, row.get("status"))
        }
        assert listing.facets_for(rows, facets, {}, filters)["status"] == ["Alpha", "beta"]


class TestTheWholePipeline:
    def test_filtering_then_paging_reports_the_filtered_total(self) -> None:
        """The bug this whole module exists to prevent: a page count taken before filtering
        tells the pager there are pages that do not exist, and a filtered page whose total is
        the book's size says "1 of 4" when it means "1 of 1"."""
        visible = _visible({"status": "closed"})
        page = listing.page(visible, limit=10)
        assert page["total"] == 1
        assert [r["name"] for r in page["items"]] == ["Marta Bex"]


class TestSortRows:
    """`sort_rows` — ordering the MATCH SET, before the page is cut.

    A server-paged listing that sorts client-side is telling a lie: it reorders the 25 rows in
    hand and calls the result "by value", while the largest row in the book may be three pages
    away and never moves. The whole point of ordering here is that the page is then the top of
    the match set rather than the top of whatever the default order happened to put first.
    """

    SORTS: listing.Sorts = {
        "name": lambda row: row.get("name"),
        "value": lambda row: row.get("value"),
    }

    ROWS: list[dict[str, Any]] = [
        {"name": "Theo", "value": 30},
        {"name": "ada", "value": None},
        {"name": "Sophie", "value": 200},
        {"name": "", "value": 5},
    ]

    def _names(self, key: str | None, direction: str | None) -> list[str]:
        return [r["name"] for r in listing.sort_rows(self.ROWS, self.SORTS, key, direction)]

    def test_numbers_order_numerically_in_both_directions(self) -> None:
        assert self._names("value", "asc")[:3] == ["", "Theo", "Sophie"]
        assert self._names("value", "desc")[:3] == ["Sophie", "Theo", ""]

    def test_text_ignores_case(self) -> None:
        # "ada" before "Sophie" before "Theo" — a case-sensitive sort puts every lower-case name
        # after every capitalised one, which reads as a broken A-Z.
        assert self._names("name", "asc")[:3] == ["ada", "Sophie", "Theo"]

    def test_blanks_sort_LAST_in_both_directions(self) -> None:
        """A client with no valuation is not the poorest, and reversing a column must not fill the
        top of the table with the rows that have nothing in it. `data-table/sort.ts` does the same,
        so a page ordered here and one ordered there read the same way."""
        assert self._names("value", "asc")[-1] == "ada"
        assert self._names("value", "desc")[-1] == "ada"

    def test_an_unknown_or_absent_key_leaves_the_order_alone(self) -> None:
        """`sort` comes off a query string. A stale bookmark naming a column that has since been
        renamed should show the list in its default order, not 422 on a page someone only wanted
        to look at."""
        assert self._names(None, None) == ["Theo", "ada", "Sophie", ""]
        assert self._names("nonesuch", "asc") == ["Theo", "ada", "Sophie", ""]

    def test_it_does_not_mutate_the_caller_s_list(self) -> None:
        before = list(self.ROWS)
        listing.sort_rows(self.ROWS, self.SORTS, "value", "desc")
        assert self.ROWS == before


class TestRangeInSql:
    """`range_sql` / `range_params` — the SQL twin of `within_range`.

    What the SQL then DOES is proved against a real database in `test_crm_scaling.py`; what is
    pinned here is that the pair reaches the binds positionally, because a range whose two ends
    swap or collapse is the filter that silently answers a different question.
    """

    def test_the_predicate_names_the_two_binds_and_the_column(self) -> None:
        sql = listing.range_sql("value", "portfolio_value")
        assert ":value_min" in sql and ":value_max" in sql
        assert sql.count("portfolio_value") == 2  # one end each, both inclusive
        assert ">=" in sql and "<=" in sql

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, (None, None)),
            (["", "3000"], (None, 3000.0)),  # "up to 3000", never "from 3000"
            (["50", ""], (50.0, None)),
            (["50", "50"], (50.0, 50.0)),  # a range of exactly one value
            ("250000", (250000.0, None)),  # one occurrence on the wire is the lower bound
            (["nan", "inf"], (None, None)),  # would match every row / empty the book
            (["abc", "9"], (None, 9.0)),  # a hand-edited URL narrows nothing, not a 422
            (["2026-01-01", ""], (None, None)),  # not a question a number column can answer
        ],
    )
    def test_the_pair_reaches_the_binds_positionally(
        self, raw: Any, expected: tuple[float | None, float | None]
    ) -> None:
        assert listing.range_params("value", raw) == {
            "value_min": expected[0],
            "value_max": expected[1],
        }


def test_union_facet_adds_directory_names_the_match_set_never_produced() -> None:
    options = {"adviser": ["Sam Shaw"], "status": ["active"]}
    listing.union_facet(options, "adviser", ["Dana Cole", "Sam Shaw", "", "  "])
    assert options["adviser"] == ["Dana Cole", "Sam Shaw"]
    assert options["status"] == ["active"]


def test_union_facet_starts_from_nothing() -> None:
    options: dict[str, list[str]] = {}
    listing.union_facet(options, "adviser", ["Dana Cole"])
    assert options["adviser"] == ["Dana Cole"]


def test_search_params_fold_the_term_the_way_search_normalise_folds_the_column() -> None:
    """Lower case, whitespace collapsed, wildcards escaped; typo matching from four plain
    characters only; nothing to search binds a NULL pattern that matches every row."""
    assert listing.search_params("  Ash   WORTH ") == {
        "q_like": "%ash worth%",
        "q_norm": "ash worth",
        "q_fuzzy": True,
    }
    assert listing.search_params("100%")["q_like"] == "%100\%%"
    assert listing.search_params("the")["q_fuzzy"] is False
    assert listing.search_params("c_le")["q_fuzzy"] is False
    assert listing.search_params("   ") == {"q_like": None, "q_norm": None, "q_fuzzy": False}


def test_search_sql_matches_each_column_and_the_extra_fields_by_containment() -> None:
    sql = listing.search_sql("a._search", "c._search", also=("m.name",))
    assert sql.startswith("(CAST(:q_like AS text) IS NULL OR ")
    assert "a._search LIKE :q_like" in sql and "<% a._search" in sql
    assert "c._search LIKE :q_like" in sql and "<% c._search" in sql
    assert "m.name ILIKE :q_like" in sql and "<% m.name" not in sql
