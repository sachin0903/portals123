"""The catalogue's parameter model: what a firm may set, and what it may not.

This is the whole of what replaced free-text SQL authoring, so the tests that matter are the
refusals. A parameter that is silently ignored is the failure mode this design exists to remove —
a firm sets a date range, the document renders, and the range did nothing.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.render import catalogue as cat


def _entry(**kwargs: object) -> cat.Entry:
    base: dict[str, object] = {
        "key": "book_test_thing",
        "name": "Thing",
        "description": "A thing.",
        "scope": "global",
        "sql": "SELECT 1 AS n",
        "columns": (cat.Column("n", "N", "number"),),
    }
    base.update(kwargs)
    return cat.Entry(**base)  # type: ignore[arg-type]


ANCHOR = date(2026, 3, 31)


class TestRelativeDates:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("today", date(2026, 3, 31)),
            ("-1d", date(2026, 3, 30)),
            ("+1d", date(2026, 4, 1)),
            ("-1y", date(2025, 3, 31)),
            ("-12m", date(2025, 3, 31)),
            ("2020-02-29", date(2020, 2, 29)),
            ("", None),
            (None, None),
        ],
    )
    def test_tokens_resolve(self, token: object, expected: date | None) -> None:
        assert cat.resolve_date(token, today=ANCHOR) == expected

    def test_a_month_shift_clamps_the_day_rather_than_overflowing(self) -> None:
        # 31 March minus one month is 28 February on a non-leap year. Rolling into 3 March would
        # make a monthly range repeat two days and skip none, which no reader would ever notice.
        assert cat.resolve_date("-1m", today=date(2026, 3, 31)) == date(2026, 2, 28)
        assert cat.resolve_date("-1m", today=date(2024, 3, 31)) == date(2024, 2, 29)

    def test_a_relative_token_is_resolved_per_render_not_once(self) -> None:
        # The reason relative tokens exist: the same stored setting must mean a different, current
        # range on each render, or a quarterly statement reports one fixed quarter for ever.
        stored = "-3m"
        assert cat.resolve_date(stored, today=date(2026, 1, 15)) == date(2025, 10, 15)
        assert cat.resolve_date(stored, today=date(2026, 7, 15)) == date(2026, 4, 15)

    @pytest.mark.parametrize("token", ["-1w", "last month", "31/03/2026", "-0x1d", "1m", 12])
    def test_nonsense_is_refused_and_the_message_says_what_to_type(self, token: object) -> None:
        with pytest.raises(cat.ParamError) as exc:
            cat.resolve_date(token, today=ANCHOR)
        assert "date" in str(exc.value).lower()


class TestCompile:
    def test_absent_parameters_are_bound_as_their_defaults(self) -> None:
        entry = _entry(
            sql="SELECT 1 AS n WHERE CAST(:from_date AS date) IS NULL OR true",
            params=(cat.Param("from_date", "date", "From", default="-12m"),),
        )
        _sql, binds = cat.compile_entry(entry, None, today=ANCHOR)
        # Present in the binds, not merely defaulted in the SQL: a bind the query names and the
        # caller omits is a driver error, not a NULL.
        assert binds == {"from_date": date(2025, 3, 31)}

    def test_an_explicitly_cleared_value_means_no_bound_not_the_default(self) -> None:
        entry = _entry(
            sql="SELECT 1 AS n WHERE CAST(:from_date AS date) IS NULL OR true",
            params=(cat.Param("from_date", "date", "From", default="-12m"),),
        )
        _sql, binds = cat.compile_entry(entry, {"from_date": ""}, today=ANCHOR)
        assert binds == {"from_date": None}

    def test_an_undeclared_parameter_is_refused_and_named(self) -> None:
        # The Dynamics lesson in CLAUDE.md, in another costume: a vendor that ignores what it does
        # not recognise turns a typo into a silent no-op that reads as success.
        entry = _entry(params=(cat.Param("from_date", "date", "From"),))
        with pytest.raises(cat.ParamError) as exc:
            cat.compile_entry(entry, {"form_date": "today"}, today=ANCHOR)
        assert "form_date" in str(exc.value)
        assert "from_date" in str(exc.value)

    def test_an_entry_with_no_parameters_refuses_any(self) -> None:
        with pytest.raises(cat.ParamError):
            cat.compile_entry(_entry(), {"anything": 1}, today=ANCHOR)

    @pytest.mark.parametrize(
        ("kind", "value", "expected"),
        [
            ("bool", "true", True),
            ("bool", "on", True),
            ("bool", "false", False),
            ("bool", True, True),
            ("int", "25", 25),
            ("int", 25, 25),
        ],
    )
    def test_values_are_coerced_on_the_declared_kind(
        self, kind: str, value: object, expected: object
    ) -> None:
        # A form posts strings and Postgres is typed. Keying on the DECLARED kind rather than
        # sniffing the value is deliberate — "0" and 0 are both legitimate answers to different
        # parameters, and inferring would swap them.
        entry = _entry(params=(cat.Param("p", kind, "P"),))  # type: ignore[arg-type]
        _sql, binds = cat.compile_entry(entry, {"p": value}, today=ANCHOR)
        assert binds["p"] == expected
        assert type(binds["p"]) is type(expected)

    def test_an_int_outside_its_range_is_refused(self) -> None:
        entry = _entry(params=(cat.Param("top", "int", "Top", minimum=1, maximum=50),))
        with pytest.raises(cat.ParamError, match="50 or less"):
            cat.compile_entry(entry, {"top": 500}, today=ANCHOR)
        with pytest.raises(cat.ParamError, match="1 or more"):
            cat.compile_entry(entry, {"top": 0}, today=ANCHOR)

    def test_an_undocumented_enum_value_is_refused_naming_the_accepted_ones(self) -> None:
        entry = _entry(
            params=(
                cat.Param("basis", "enum", "Basis", options=(("net", "Net"), ("gross", "Gross"))),
            )
        )
        with pytest.raises(cat.ParamError) as exc:
            cat.compile_entry(entry, {"basis": "NET"}, today=ANCHOR)
        assert "gross" in str(exc.value) and "net" in str(exc.value)

    def test_validate_params_stores_the_token_verbatim(self) -> None:
        entry = _entry(params=(cat.Param("from_date", "date", "From"),))
        assert cat.validate_params(entry, {"from_date": "-6m"}) == {"from_date": "-6m"}

    def test_validate_params_refuses_at_configure_time(self) -> None:
        entry = _entry(params=(cat.Param("from_date", "date", "From"),))
        with pytest.raises(cat.ParamError):
            cat.validate_params(entry, {"from_date": "last week"})


class TestEntryDeclarations:
    def test_a_duplicate_output_column_is_refused(self) -> None:
        # The resolver reads rows into dicts, so two columns of one name collapse into one and the
        # declared schema promises a column the rows do not carry.
        with pytest.raises(ValueError, match="duplicate output column"):
            _entry(columns=(cat.Column("n", "N"), cat.Column("n", "Again")))

    def test_a_cast_shorthand_beside_a_bind_is_refused(self) -> None:
        # SQLAlchemy reads `:from_date::date` as a bind named `from_dat`; Postgres then raises a
        # bare syntax error that the resolver reports as "check the query" — on our own query.
        with pytest.raises(ValueError, match="CAST"):
            _entry(sql="SELECT 1 WHERE d >= :from_date::date")

    def test_an_entry_may_not_declare_the_subject_bind_as_a_parameter(self) -> None:
        with pytest.raises(ValueError, match="subject_id"):
            _entry(scope="subject", params=(cat.Param("subject_id", "int", "Client"),))


class TestShippedEntries:
    """Every shipped entry, checked as data rather than one test per key."""

    def test_there_is_at_least_one(self) -> None:
        assert cat.all_entries(), "the catalogue is empty — no firm can add a dataset"

    def test_every_key_is_a_bare_identifier_a_template_can_bind(self) -> None:
        """The bug this replaced: a key must be spellable in `{% for row in <code> %}`.

        Keys were namespaced (`book.valuations`), which reads better and does not work.
        `components.loop_sources` deliberately drops a DOTTED source, because
        `{% for a in answers.items %}` is a path into the context rather than a dataset name — so
        the dataset was never resolved, Jinja saw `book` as an undeclared root, and every cell of
        the table rendered blank. The only symptom was an authoring warning naming a variable the
        author had never written.
        """
        from app.render.components import loop_sources

        for entry in cat.all_entries():
            assert entry.key.startswith(cat.RESERVED_KEY_PREFIX), entry.key
            assert entry.key == entry.key.lower()
            assert "." not in entry.key, f"{entry.key} would be dropped by loop_sources"
            # Not merely "no dot": the extractor itself must return it, which is the thing that
            # actually has to be true for a template to resolve the dataset.
            markup = "{% for row in " + entry.key + " %}{{ row.x }}{% endfor %}"
            assert loop_sources(markup) == [entry.key], (
                f"a template binding {entry.key} would not resolve it"
            )

    def test_a_key_that_is_not_a_bare_identifier_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no dots"):
            _entry(key="book.dotted")

    def test_every_entry_compiles_with_its_own_defaults(self) -> None:
        for entry in cat.all_entries():
            sql, binds = cat.compile_entry(entry, None, today=ANCHOR)
            assert sql.strip()
            assert set(binds) == {p.name for p in entry.params}

    def test_the_bind_names_in_the_sql_are_exactly_the_declared_parameters(self) -> None:
        # The assertion the executor makes at run time, made here at import time as well: a
        # mismatch is the SQLAlchemy truncation trap and surfaces only as a Postgres syntax error.
        from sqlalchemy import text as sa_text

        for entry in cat.all_entries():
            declared = {p.name for p in entry.params}
            found = set(sa_text(entry.sql)._bindparams)
            # Nothing may be bound that is neither declared nor supplied by the resolver.
            stray = found - declared - cat.RESOLVER_BINDS
            assert not stray, f"{entry.key}: binds {stray}, which nothing declares or supplies"
            # And every declared parameter must actually be USED. A parameter the SQL never binds
            # is a control on the form that changes nothing — the silent no-op this whole design
            # exists to remove, and it would fail at run time as a bind mismatch anyway.
            unused = declared - found
            assert not unused, f"{entry.key}: declares {unused} but its SQL never binds them"

    def test_every_entry_scopes_itself_to_the_firm(self) -> None:
        # RLS is a backstop here, never the mechanism. Measured on the live schema:
        # `platform.contributions`' read policy is `tenant_lineage OR account_scope` with NO
        # `can_read_all()` gate, so under minted subject claims for one client it returns every
        # contribution in the firm — proved by execution, another client's money.
        for entry in cat.all_entries():
            assert f":{cat.TENANT_BIND}" in entry.sql, f"{entry.key} does not filter by firm"

    def test_a_subject_entry_binds_the_subject_and_a_global_one_does_not(self) -> None:
        from sqlalchemy import text as sa_text

        for entry in cat.all_entries():
            binds = set(sa_text(entry.sql)._bindparams)
            if entry.scope == "subject":
                assert cat.SUBJECT_BIND in binds, f"{entry.key} is per-client but binds no client"
            else:
                assert cat.SUBJECT_BIND not in binds, f"{entry.key} is firm-wide but binds a client"

    def test_every_entry_is_a_select(self) -> None:
        for entry in cat.all_entries():
            head = entry.sql.lstrip().lower()
            assert head.startswith(("select", "with")), entry.key
            assert ";" not in entry.sql.rstrip().rstrip(";"), f"{entry.key}: one statement only"
