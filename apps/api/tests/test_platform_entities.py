"""The firm console's entity registry and its query builder.

The registry is data, so the tests that matter are about the RULES it is read under, not about
the particular columns Morgan Lloyd happens to publish:

  * no identifier from a request reaches the SQL;
  * a declared column the relation does not have is dropped and reported, never assumed;
  * "not built", "unavailable" and "empty" stay three distinguishable answers.

The warehouse itself is exercised in `_rows_sync` against a real DuckDB catalogue, which these
do not have, so the connection is stubbed and the SQL is inspected.
"""

from __future__ import annotations

from typing import Any

import pytest

from app import platform_entities as pe


class _Cursor:
    def __init__(self, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
        self.description = [(c,) for c in columns]
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _Conn:
    """Records every statement, and answers plausibly by shape.

    `ml_tenant_id` is added unless a test opts out. Every Morgan Lloyd relation the console reads
    carries it -- `raw_core`/`raw_odata` stamp it on every record -- so a stub without it is not a
    simpler relation, it is an impossible one, and the console now refuses to narrow such a
    relation rather than emitting a WHERE that cannot bind. `slice_column=False` is how the test
    for that refusal asks for the impossible shape on purpose.
    """

    def __init__(
        self,
        columns: list[str],
        rows: list[tuple[Any, ...]] | None = None,
        *,
        slice_column: bool = True,
    ) -> None:
        self.selected = list(columns)
        # What `DESCRIBE`/`LIMIT 0` sees. The narrowing column is part of the relation but is
        # never in the projection -- it is not one of the entity's display columns -- so it is
        # kept apart from `selected`, which is what a data query's cursor describes.
        self.columns = (
            [*columns, "ml_tenant_id"]
            if slice_column and "ml_tenant_id" not in columns
            else list(columns)
        )
        self.rows = rows or []
        self.statements: list[tuple[str, list[Any]]] = []
        self.closed = False

    def execute(self, sql: str, params: list[Any] | None = None) -> _Cursor:
        self.statements.append((sql, list(params or [])))
        if "LIMIT 0" in sql:
            return _Cursor(self.columns, [])
        if "count(*)" in sql:
            return _Cursor(["count"], [(len(self.rows),)])
        return _Cursor(list(self.selected), self.rows)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def contacts() -> pe.EntityDef:
    found = pe.entity_for("morgan-lloyd", "contacts")
    assert found is not None
    return found


class TestRegistry:
    def test_the_user_facing_entities_are_all_declared(self) -> None:
        """The set the console was specified around. Named individually rather than counted: a
        count still passes when one entity is silently swapped for another."""
        keys = {e.key for e in pe.entities_for("morgan-lloyd")}
        assert {
            "contacts",
            "schemes",
            "advisers",
            "beneficiaries",
            "transfers",
            "contributions",
            "investments",
            "valuations",
            "bank_transactions",
            "benefits",
            "documents",
            "illustrations",
        } <= keys

    def test_every_entity_has_a_relation_and_columns(self) -> None:
        for entity in pe.entities_for("morgan-lloyd"):
            assert entity.relation, f"{entity.key} has no relation"
            assert entity.columns, f"{entity.key} declares no columns"

    def test_search_columns_are_declared_columns(self) -> None:
        """A search column absent from the projection would filter on something the operator
        cannot see — results appearing to match nothing on screen."""
        for entity in pe.entities_for("morgan-lloyd"):
            assert set(entity.search) <= set(entity.columns), entity.key

    def test_the_default_sort_is_a_declared_column(self) -> None:
        for entity in pe.entities_for("morgan-lloyd"):
            if entity.order_by:
                assert entity.order_by in entity.columns, entity.key

    def test_an_unknown_provider_has_no_entities(self) -> None:
        assert pe.entities_for("nope") == ()
        assert pe.entity_for("nope", "contacts") is None


class TestSliceNarrowing:
    def test_a_single_key_narrows(self, contacts: pe.EntityDef) -> None:
        where, params = pe._slice_clause(contacts, ["tenant-a"])
        assert "ml_tenant_id" in where and where.count("?") == 1
        assert params == ["tenant-a"]

    def test_several_keys_narrow_with_in(self, contacts: pe.EntityDef) -> None:
        """A firm running more than one Dynamics tenant holds several; `config.tenant_ids` is a
        list, so `=` would silently show only the first."""
        where, params = pe._slice_clause(contacts, ["a", "b", "c"])
        assert " IN (?, ?, ?)" in where
        assert params == ["a", "b", "c"]

    def test_no_keys_on_a_shared_relation_is_refused(self, contacts: pe.EntityDef) -> None:
        """`raw.contacts` is every contact in the Dynamics ORG.

        This used to return no WHERE at all, on the reasoning that an empty console reads as
        "this custodian holds nothing for us". The mitigation was UI copy on one page, while the
        leak was an API property: any `platforms:read` holder could read another firm's clients
        through a firm whose config was left blank.
        """
        with pytest.raises(pe.Unnarrowable):
            pe._slice_clause(contacts, [])
        assert pe.is_narrowable(contacts, []) is False
        assert pe.is_narrowable(contacts, ["tenant-a"]) is True

    def test_a_schema_scoped_entity_needs_no_keys(self) -> None:
        """The other safe shape: Seccl's and SEI's entities read `tenant_{uid}.*`, which the
        provider's own chain computed for one firm. `slice_column=None` says the SCHEMA is the
        narrowing, so there is nothing to filter and nothing shared to leak."""
        conformed = pe.EntityDef(
            key="accounts",
            label="Accounts",
            relation="tenant_abc.accounts",
            columns=("external_id",),
            slice_column=None,
        )
        assert pe.is_narrowable(conformed, []) is True
        assert pe._slice_clause(conformed, []) == ("", [])

    def test_the_slice_value_is_bound_not_interpolated(self, contacts: pe.EntityDef) -> None:
        where, params = pe._slice_clause(contacts, ["'; DROP TABLE x --"])
        assert "DROP" not in where
        assert params == ["'; DROP TABLE x --"]

    def test_a_relation_without_the_slice_column_cannot_be_narrowed(
        self, contacts: pe.EntityDef
    ) -> None:
        """Morgan Lloyd's `valuations` reads `sink.daily_valuations`, which has no `ml_tenant_id`.

        The probe checked only the DISPLAY columns while the narrowing column went into the WHERE
        unconditionally, so this was a DuckDB binder error escaping `asyncio.to_thread` as a 500.
        """
        without = _Conn(["contactid", "firstname"], slice_column=False)
        assert pe._probe(without, contacts).has_slice_column is False
        assert pe._probe(_Conn(["contactid"]), contacts).has_slice_column is True


class TestColumnProbe:
    def test_declared_columns_absent_from_the_relation_are_reported(
        self, contacts: pe.EntityDef
    ) -> None:
        """Dropped AND reported. Silently omitting them makes a vendor rename look like a firm
        that simply has no data in that field."""
        conn = _Conn(["contactid", "firstname"])
        probe = pe._probe(conn, contacts)
        assert probe.columns == ["contactid", "firstname"]
        assert "emailaddress1" in probe.missing
        assert not probe.error

    def test_an_unbuilt_relation_is_an_error_not_an_empty_column_list(
        self, contacts: pe.EntityDef
    ) -> None:
        class _Missing(_Conn):
            def execute(self, sql: str, params: list[Any] | None = None) -> _Cursor:
                raise RuntimeError("Catalog Error: Table with name contacts does not exist!")

        probe = pe._probe(_Missing([]), contacts)
        assert probe.error
        assert "does not exist" in probe.error


class TestQuery:
    def _run(self, monkeypatch: pytest.MonkeyPatch, conn: _Conn, **kwargs: Any) -> dict[str, Any]:
        entity = pe.entity_for("morgan-lloyd", "contacts")
        assert entity is not None
        monkeypatch.setattr("app.platforms._core_connect_sync", lambda _p: conn)
        return pe._rows_sync("morgan-lloyd", entity, **kwargs)

    def test_only_probed_columns_are_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _Conn(["contactid", "firstname"], [("a", "Ada")])
        out = self._run(
            monkeypatch, conn, slice_ids=["t"], q="", sort=None, descending=True, limit=10, offset=0
        )
        assert out["columns"] == ["contactid", "firstname"]
        assert out["items"] == [{"contactid": "a", "firstname": "Ada"}]
        select = [s for s, _ in conn.statements if s.startswith("SELECT t.")][-1]
        assert "emailaddress1" not in select

    def test_an_unknown_sort_column_never_reaches_the_sql(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE INJECTION GUARD. `sort` is the one caller-supplied value that has to name a
        column, so it is validated against the probe rather than quoted and hoped for."""
        conn = _Conn(["contactid", "firstname"], [])
        self._run(
            monkeypatch,
            conn,
            slice_ids=["t"],
            q="",
            sort="x; DROP TABLE y",
            descending=True,
            limit=10,
            offset=0,
        )
        assert not any("DROP" in sql for sql, _ in conn.statements)

    def test_a_known_sort_column_is_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _Conn(["contactid", "firstname"], [])
        self._run(
            monkeypatch,
            conn,
            slice_ids=["t"],
            q="",
            sort="firstname",
            descending=False,
            limit=10,
            offset=0,
        )
        select = [s for s, _ in conn.statements if "ORDER BY" in s][-1]
        assert 'ORDER BY t."firstname" ASC NULLS LAST' in select

    def test_search_is_a_bound_parameter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _Conn(["contactid", "firstname", "lastname"], [])
        self._run(
            monkeypatch,
            conn,
            slice_ids=["t"],
            q="O'Brien",
            sort=None,
            descending=True,
            limit=10,
            offset=0,
        )
        sql, params = next((s, p) for s, p in conn.statements if "ILIKE" in s)
        assert "O'Brien" not in sql
        assert "%O'Brien%" in params

    def test_search_only_uses_columns_the_relation_has(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`cam_ninumber` is a declared search column; a relation without it must not be asked
        to filter on it, which would fail the whole query rather than narrow it."""
        conn = _Conn(["contactid", "firstname"], [])
        self._run(
            monkeypatch,
            conn,
            slice_ids=["t"],
            q="ada",
            sort=None,
            descending=True,
            limit=10,
            offset=0,
        )
        sql, _ = next((s, p) for s, p in conn.statements if "ILIKE" in s)
        assert "cam_ninumber" not in sql

    def test_the_limit_is_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A caller asking for a million rows gets the cap, not the million."""
        conn = _Conn(["contactid"], [])
        self._run(
            monkeypatch,
            conn,
            slice_ids=["t"],
            q="",
            sort=None,
            descending=True,
            limit=1_000_000,
            offset=0,
        )
        _, params = conn.statements[-1]
        assert pe._MAX_LIMIT in params

    def test_an_unbuilt_relation_raises_rather_than_returning_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.platforms import PlatformUnavailable

        class _Missing(_Conn):
            def execute(self, sql: str, params: list[Any] | None = None) -> _Cursor:
                raise RuntimeError("Catalog Error: does not exist")

        with pytest.raises(PlatformUnavailable, match="has not run"):
            self._run(
                monkeypatch,
                _Missing([]),
                slice_ids=["t"],
                q="",
                sort=None,
                descending=True,
                limit=10,
                offset=0,
            )

    def test_the_connection_is_always_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _Conn(["contactid"], [])
        self._run(
            monkeypatch, conn, slice_ids=["t"], q="", sort=None, descending=True, limit=10, offset=0
        )
        assert conn.closed


class TestCellCoercion:
    def test_json_safe_values_pass_through(self) -> None:
        assert pe._cell(None) is None
        assert pe._cell(True) is True
        assert pe._cell(3) == 3
        assert pe._cell("x") == "x"

    def test_anything_else_becomes_a_string(self) -> None:
        from datetime import date
        from decimal import Decimal

        assert pe._cell(Decimal("1.5")) == "1.5"
        assert pe._cell(date(2026, 8, 9)) == "2026-08-09"


class _Result:
    """Enough of a SQLAlchemy result for the outbound reader's three shapes."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def one(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(**self._rows[0])

    def scalar_one(self) -> Any:
        return next(iter(self._rows[0].values()))

    def mappings(self) -> list[dict[str, Any]]:
        return self._rows


class _Session:
    """Records every statement and its bound parameters."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(clause)
        self.statements.append((sql, dict(params or {})))
        if "FILTER" in sql:
            return _Result([{"mine": len(self.rows), "unattributed": 0}])
        if "count(*)" in sql:
            return _Result([{"count": len(self.rows)}])
        return _Result(self.rows)


@pytest.fixture
def investments() -> pe.EntityDef:
    found = pe.entity_for("morgan-lloyd", "investments")
    assert found is not None
    return found


class TestOutboundRegistry:
    def test_the_three_unreadable_entities_are_outbound(self) -> None:
        """Morgan Lloyd publishes none of these back: valuations are 403 for our service
        principal, and documents and illustrations have no collection form. Their rows can only
        ever be our own record of what we sent."""
        by_key = {e.key: e for e in pe.entities_for("morgan-lloyd")}
        for key in ("investments", "documents", "illustrations"):
            assert by_key[key].source == "outbound", key
            assert by_key[key].operations, f"{key} names no vendor operation"

    def test_the_vendor_entities_stay_on_the_lake(self) -> None:
        by_key = {e.key: e for e in pe.entities_for("morgan-lloyd")}
        for key in ("contacts", "schemes", "transfers", "beneficiaries"):
            assert by_key[key].source == "lake", key
            assert by_key[key].operations == ()

    def test_an_outbound_entity_is_not_firm_narrowed_by_a_warehouse_column(self) -> None:
        """`ml_tenant_id` is a warehouse column; `audit.api_log` has `tenant_id`. Leaving the
        default would have added a WHERE on a column the log does not have."""
        by_key = {e.key: e for e in pe.entities_for("morgan-lloyd")}
        for key in ("investments", "documents", "illustrations"):
            assert by_key[key].slice_column is None, key

    def test_illustrations_cover_both_legs(self) -> None:
        """CTC produces the illustration and `cam_illustration` attaches it. Showing only the
        attach would hide every illustration that failed before there was anything to attach."""
        found = pe.entity_for("morgan-lloyd", "illustrations")
        assert found is not None
        assert set(found.operations) == {"cam_illustration", "run_quote"}

    def test_the_search_columns_are_in_the_projection(self) -> None:
        for key in ("investments", "documents", "illustrations"):
            found = pe.entity_for("morgan-lloyd", key)
            assert found is not None
            assert set(found.search) <= set(found.columns), key


class TestOutboundRows:
    async def _run(self, session: _Session, **kwargs: Any) -> dict[str, Any]:
        entity = pe.entity_for("morgan-lloyd", "investments")
        assert entity is not None
        return await pe.rows("morgan-lloyd", entity, slice_ids=[], session=session, **kwargs)

    @pytest.mark.asyncio
    async def test_the_firm_and_the_operations_are_bound_not_interpolated(self) -> None:
        session = _Session()
        await self._run(session, tenant_id=7)
        for sql, params in session.statements:
            assert "cam_createwrapper" not in sql
            assert params["tenant_id"] == 7
            assert params["operations"] == ["cam_createwrapper"]

    @pytest.mark.asyncio
    async def test_the_operations_are_matched_exactly(self) -> None:
        """`= ANY(:operations)`, never a LIKE. A prefix match would silently fold a future
        `cam_createwrapperbulk` into this tab."""
        session = _Session()
        await self._run(session, tenant_id=7)
        sql = session.statements[-1][0]
        assert "operation = ANY(:operations)" in sql

    @pytest.mark.asyncio
    async def test_an_unknown_sort_column_never_reaches_the_sql(self) -> None:
        """THE INJECTION GUARD, the same one the lake path has — except here the validation is
        against the declared column list, since there is no relation to probe."""
        session = _Session()
        await self._run(session, tenant_id=7, sort="x; DROP TABLE y")
        assert not any("DROP" in sql for sql, _ in session.statements)
        assert "ORDER BY l.created_at" in session.statements[-1][0]

    @pytest.mark.asyncio
    async def test_a_declared_sort_column_is_applied(self) -> None:
        session = _Session()
        await self._run(session, tenant_id=7, sort="status_code", descending=False)
        assert "ORDER BY l.status_code ASC NULLS LAST" in session.statements[-1][0]

    @pytest.mark.asyncio
    async def test_the_joined_column_is_not_qualified_with_the_log_alias(self) -> None:
        """`actor` comes from the users join, so `l.actor` would not resolve."""
        session = _Session()
        await self._run(session, tenant_id=7, sort="actor")
        assert "ORDER BY actor" in session.statements[-1][0]

    @pytest.mark.asyncio
    async def test_search_is_a_bound_parameter(self) -> None:
        session = _Session()
        await self._run(session, tenant_id=7, q="O'Brien")
        sql, params = session.statements[-1]
        assert "O'Brien" not in sql
        assert params["q"] == "%O'Brien%"

    @pytest.mark.asyncio
    async def test_search_is_null_safe(self) -> None:
        """`error` is NULL on every successful call, and a bare ILIKE on it would drop the row
        from the results of a search that mentions any other column."""
        session = _Session()
        await self._run(session, tenant_id=7, q="wrapper")
        assert "coalesce(CAST(l.error AS text), '')" in session.statements[-1][0]

    @pytest.mark.asyncio
    async def test_the_limit_is_capped(self) -> None:
        session = _Session()
        await self._run(session, tenant_id=7, limit=1_000_000)
        assert session.statements[-1][1]["limit"] == pe._MAX_LIMIT

    @pytest.mark.asyncio
    async def test_a_negative_offset_is_floored(self) -> None:
        session = _Session()
        await self._run(session, tenant_id=7, offset=-5)
        assert session.statements[-1][1]["offset"] == 0

    @pytest.mark.asyncio
    async def test_without_a_session_it_refuses_rather_than_reading_the_lake(self) -> None:
        """The lake has no `audit.api_log`; falling through would report the tab "not built",
        which is the exact misreading this module exists to prevent."""
        from app.platforms import PlatformUnavailable

        entity = pe.entity_for("morgan-lloyd", "investments")
        assert entity is not None
        with pytest.raises(PlatformUnavailable, match="database session"):
            await pe.rows("morgan-lloyd", entity, slice_ids=[])


class TestOutboundDescribe:
    @pytest.mark.asyncio
    async def test_an_outbound_tab_is_available_even_with_no_rows(self) -> None:
        """ "We have sent none" is a COMPLETE answer, unlike a missing warehouse relation. The
        three tabs used to report "not built", which reads as a gap in the console rather than as
        a true statement about the firm."""
        session = _Session()
        entity = pe.entity_for("morgan-lloyd", "investments")
        assert entity is not None
        record = await pe._describe_outbound(session, "morgan-lloyd", entity, 7)
        assert record["availability"] == "ok"
        assert record["rows"] == 0
        assert "reason" not in record

    @pytest.mark.asyncio
    async def test_unattributed_calls_are_counted_not_shown(self) -> None:
        """A call recorded without a firm belongs to no tab. Attributing it to the firm on screen
        would assert something we did not record; hiding it entirely would make it invisible
        everywhere. So it is scoped out and counted — the AML console's posture for an
        unattributable profile."""

        class _Unattributed(_Session):
            async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _Result:
                self.statements.append((str(clause), dict(params or {})))
                return _Result([{"mine": 2, "unattributed": 5}])

        entity = pe.entity_for("morgan-lloyd", "investments")
        assert entity is not None
        record = await pe._describe_outbound(_Unattributed(), "morgan-lloyd", entity, 7)
        assert record["rows"] == 2
        assert record["unattributed"] == 5

    @pytest.mark.asyncio
    async def test_describe_merges_both_sources_in_registry_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The order is the console's NAVIGATION. Reading the two sources separately is an
        implementation detail that must not reorder the sidebar."""
        lake = [
            {"key": e.key, "label": e.label, "availability": "ok", "rows": 0}
            for e in pe.entities_for("morgan-lloyd")
            if e.source == "lake"
        ]
        monkeypatch.setattr(pe, "_describe_sync", lambda *_a: lake)
        described = await pe.describe("morgan-lloyd", [], session=_Session(), tenant_id=7)
        assert [r["key"] for r in described] == [e.key for e in pe.entities_for("morgan-lloyd")]

    @pytest.mark.asyncio
    async def test_without_a_session_the_outbound_tabs_are_omitted_not_faked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A caller with no session gets the lake tabs alone. Better than three tabs claiming a
        row count nothing counted."""
        monkeypatch.setattr(pe, "_describe_sync", lambda *_a: [])
        assert await pe.describe("morgan-lloyd", []) == []


class TestLookupRegistry:
    """Which vendor fields are references, and to what.

    The stakes here are the `cam_schemerefs` mistake in another costume: a plausible identifier of
    the WRONG KIND. `schemeReference` is `raw.schemes.cam_name` (`MLSIPP/01417`), not the primary
    key `cam_schemeid` — sending the latter is refused by every scoped action, and the refusal
    reads as "that scheme does not exist".
    """

    def test_the_identifier_fields_are_mapped(self) -> None:
        mapped = pe.lookup_fields("morgan-lloyd")
        assert mapped["contactId"] == "contacts"
        assert mapped["cam_contributor"] == "contacts"
        assert mapped["schemeReference"] == "schemes"
        assert mapped["schemeRef"] == "schemes"
        assert mapped["benefitApplicationId"] == "benefits"

    def test_a_scheme_is_picked_by_its_reference_not_its_primary_key(self) -> None:
        """THE ONE THAT MATTERS. `cam_schemeid` is a GUID the scoped actions refuse."""
        entity = pe.lookup_entity("morgan-lloyd", "schemes")
        assert entity is not None
        assert entity.lookup_value == "cam_name"
        assert entity.lookup_value != "cam_schemeid"

    def test_every_mapped_field_resolves_to_a_pickable_entity(self) -> None:
        """A mapping to an entity that cannot be picked from would render a picker that is always
        empty — which reads as "this firm has none"."""
        for field, key in pe.lookup_fields("morgan-lloyd").items():
            assert pe.lookup_entity("morgan-lloyd", key) is not None, f"{field} -> {key}"

    def test_every_lookup_column_is_one_the_entity_shows(self) -> None:
        """A value or label column absent from the projection is one nothing has ever verified
        against the real relation."""
        for entity in pe.entities_for("morgan-lloyd"):
            if not entity.lookup_value:
                continue
            assert entity.lookup_value in entity.columns, entity.key
            assert set(entity.lookup_label) <= set(entity.columns), entity.key
            if entity.lookup_hint:
                assert entity.lookup_hint in entity.columns, entity.key

    def test_an_outbound_entity_cannot_be_picked_from(self) -> None:
        """`audit.api_log` rows are calls we made, not records to reference."""
        assert pe.lookup_entity("morgan-lloyd", "investments") is None

    def test_an_entity_with_no_lookup_declared_is_refused(self) -> None:
        assert pe.lookup_entity("morgan-lloyd", "bank_transactions") is None
        assert pe.lookup_entity("morgan-lloyd", "nope") is None


class TestLookupOptions:
    def _run(
        self, monkeypatch: pytest.MonkeyPatch, conn: _Conn, key: str = "contacts", **kwargs: Any
    ) -> list[dict[str, Any]]:
        entity = pe.lookup_entity("morgan-lloyd", key)
        assert entity is not None
        monkeypatch.setattr("app.platforms._core_connect_sync", lambda _p: conn)
        return pe._options_sync("morgan-lloyd", entity, **kwargs)

    def test_it_returns_value_label_and_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _Conn(
            ["contactid", "firstname", "lastname", "emailaddress1", "modifiedon"],
            [("guid-1", "Ada", "Lovelace", "ada@example.com")],
        )
        out = self._run(monkeypatch, conn, slice_ids=["t"], q="")
        assert out == [{"value": "guid-1", "label": "Ada Lovelace", "hint": "ada@example.com"}]

    def test_a_missing_name_part_is_skipped_not_padded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "Ada  " with a trailing space is a rendering bug people notice and distrust."""
        conn = _Conn(
            ["contactid", "firstname", "lastname", "emailaddress1", "modifiedon"],
            [("guid-1", "Ada", None, "ada@example.com")],
        )
        assert self._run(monkeypatch, conn, slice_ids=["t"], q="")[0]["label"] == "Ada"

    def test_an_unnamed_record_falls_back_to_its_identifier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Still pickable. A blank line in a list is not."""
        conn = _Conn(
            ["contactid", "firstname", "lastname", "emailaddress1", "modifiedon"],
            [("guid-1", None, None, None)],
        )
        assert self._run(monkeypatch, conn, slice_ids=["t"], q="")[0]["label"] == "guid-1"

    def test_rows_with_no_identifier_are_excluded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Offering one produces a call the vendor refuses."""
        conn = _Conn(["contactid", "firstname", "emailaddress1", "modifiedon"], [])
        self._run(monkeypatch, conn, slice_ids=["t"], q="")
        assert "IS NOT NULL" in conn.statements[-1][0]

    def test_the_firm_narrowing_is_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _Conn(["contactid", "firstname", "emailaddress1", "modifiedon"], [])
        self._run(monkeypatch, conn, slice_ids=["tenant-a"], q="")
        sql, params = conn.statements[-1]
        assert "ml_tenant_id" in sql
        assert "tenant-a" in params

    def test_search_is_a_bound_parameter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _Conn(["contactid", "firstname", "emailaddress1", "modifiedon"], [])
        self._run(monkeypatch, conn, slice_ids=["t"], q="O'Brien")
        sql, params = conn.statements[-1]
        assert "O'Brien" not in sql
        assert "%O'Brien%" in params

    def test_the_identifier_itself_is_searchable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An operator holding the GUID from somewhere else should be able to confirm it."""
        conn = _Conn(["contactid", "firstname", "emailaddress1", "modifiedon"], [])
        self._run(monkeypatch, conn, slice_ids=["t"], q="guid")
        assert '"contactid" AS VARCHAR) ILIKE' in conn.statements[-1][0]

    def test_the_result_is_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A picker is a search box, not a table."""
        conn = _Conn(["contactid", "firstname", "emailaddress1", "modifiedon"], [])
        self._run(monkeypatch, conn, slice_ids=["t"], q="")
        assert pe._LOOKUP_LIMIT in conn.statements[-1][1]

    def test_an_unbuilt_relation_offers_nothing_rather_than_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The drawer falls back to a free-text box. Raising would take down a form whose other
        thirty fields are fine."""

        class _Missing(_Conn):
            def execute(self, sql: str, params: list[Any] | None = None) -> _Cursor:
                raise RuntimeError("Catalog Error: does not exist")

        assert self._run(monkeypatch, _Missing([]), slice_ids=["t"], q="") == []

    def test_a_renamed_key_column_offers_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Better an empty picker than one whose values the vendor will refuse."""
        conn = _Conn(["firstname", "lastname"], [("Ada", "Lovelace")])
        assert self._run(monkeypatch, conn, slice_ids=["t"], q="") == []

    def test_the_connection_is_always_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _Conn(["contactid", "firstname", "emailaddress1", "modifiedon"], [])
        self._run(monkeypatch, conn, slice_ids=["t"], q="")
        assert conn.closed


class TestJoinedNames:
    """A column of GUIDs answers nothing anybody asked.

    Dataverse stores a relationship as `_thing_value`, so `raw.schemes` names its client as
    `cam_individualclient_value` — a GUID. The schemes tab showed the scheme reference THREE times
    (`cam_name`, `cam_schemeref` identical to it, and `cam_schemereference` null on every row, so
    a column of dashes) and not one fact about whose scheme it was.
    """

    def _schemes(self) -> pe.EntityDef:
        found = pe.entity_for("morgan-lloyd", "schemes")
        assert found is not None
        return found

    def test_the_scheme_resolves_its_client(self) -> None:
        entity = self._schemes()
        assert [j.alias for j in entity.joins] == ["client"]
        join = entity.joins[0]
        assert join.column == "cam_individualclient_value"
        assert join.relation == "raw.contacts"
        assert join.key == "contactid"

    def test_the_alias_is_a_declared_column(self) -> None:
        """Otherwise it is resolved and never shown."""
        entity = self._schemes()
        for join in entity.joins:
            assert join.alias in entity.columns, join.alias

    def test_the_reference_is_not_repeated(self) -> None:
        """One fact, one column. `cam_name` is kept over its twin because it is the value the
        scoped actions are proven to accept; `cam_schemereference` is null on every row."""
        columns = set(self._schemes().columns)
        assert "cam_name" in columns
        assert "cam_schemeref" not in columns
        assert "cam_schemereference" not in columns

    def test_a_missing_join_relation_costs_a_column_not_the_page(self) -> None:
        """One unbuilt model must not take down a tab whose own relation is fine."""

        class _NoContacts(_Conn):
            def execute(self, sql: str, params: list[Any] | None = None) -> _Cursor:
                if "contacts" in sql:
                    raise RuntimeError("Catalog Error: raw.contacts does not exist")
                return super().execute(sql, params)

        conn = _NoContacts(["cam_name", "cam_individualclient_value", "modifiedon"])
        probe = pe._probe(conn, self._schemes())
        assert probe.joins == []
        assert not probe.error
        assert "cam_name" in probe.columns
        assert "client" in probe.missing

    def test_a_missing_foreign_key_drops_the_join(self) -> None:
        conn = _Conn(["cam_name", "modifiedon"])
        assert pe._probe(conn, self._schemes()).joins == []

    def test_the_join_is_left_so_an_unlinked_row_still_shows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scheme whose client we cannot resolve is still a scheme. An INNER join would hide it,
        which reads as "this firm has fewer schemes than it does"."""
        # No rows: the stub echoes its whole column list as the cursor description, which does
        # not model a projection. What this asserts is the SQL, not the marshalling.
        conn = _Conn(
            [
                "cam_name",
                "cam_individualclient_value",
                "contactid",
                "firstname",
                "lastname",
                "modifiedon",
            ],
            [],
        )
        monkeypatch.setattr("app.platforms._core_connect_sync", lambda _p: conn)
        pe._rows_sync(
            "morgan-lloyd",
            self._schemes(),
            slice_ids=["t"],
            q="",
            sort=None,
            descending=True,
            limit=10,
            offset=0,
        )
        select = [s for s, _ in conn.statements if "LEFT JOIN" in s]
        assert select, "the join should be LEFT"
        assert "INNER JOIN" not in select[-1]

    def test_the_joined_relation_is_aliased(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both relations carry `modifiedon` and `createdon`. Unqualified, the query is ambiguous
        rather than merely wrong."""
        conn = _Conn(
            [
                "cam_name",
                "cam_individualclient_value",
                "contactid",
                "firstname",
                "lastname",
                "modifiedon",
            ],
            [],
        )
        monkeypatch.setattr("app.platforms._core_connect_sync", lambda _p: conn)
        pe._rows_sync(
            "morgan-lloyd",
            self._schemes(),
            slice_ids=["x"],
            q="",
            sort=None,
            descending=True,
            limit=10,
            offset=0,
        )
        sql = [s for s, _ in conn.statements if "LEFT JOIN" in s][-1]
        assert " t LEFT JOIN " in sql
        assert 'j0."contactid" = t."cam_individualclient_value"' in sql
        assert 't."ml_tenant_id"' in sql, "the firm narrowing must be qualified too"

    def test_the_joined_name_is_searchable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ "Which scheme is Halloway's" is the question the tab exists to answer."""
        conn = _Conn(
            [
                "cam_name",
                "cam_individualclient_value",
                "contactid",
                "firstname",
                "lastname",
                "modifiedon",
            ],
            [],
        )
        monkeypatch.setattr("app.platforms._core_connect_sync", lambda _p: conn)
        pe._rows_sync(
            "morgan-lloyd",
            self._schemes(),
            slice_ids=["t"],
            q="Halloway",
            sort=None,
            descending=True,
            limit=10,
            offset=0,
        )
        sql, params = next((s, p) for s, p in conn.statements if "ILIKE" in s)
        assert "concat_ws" in sql
        assert "%Halloway%" in params

    def test_every_join_column_pair_is_declared_not_guessed(self) -> None:
        """No caller value reaches this SQL — every part is a registry literal."""
        for entity in pe.entities_for("morgan-lloyd"):
            for join in entity.joins:
                assert join.column and join.relation and join.key and join.label and join.alias


class TestPerTenantProviders:
    """Seccl and SEI read `tenant_{uid}.*` — the firm's own slice, computed by the provider's
    chain — rather than filtering a whole-firm relation again here.

    Two reasons that beats a predicate: the console and the pipeline cannot disagree about what a
    firm's slice is, and the conformed columns are the same words for both custodians. Morgan
    Lloyd stays on `raw.*` because its thirteen tabs have no conformed equivalent.
    """

    def test_both_providers_are_registered(self) -> None:
        assert {e.key for e in pe.entities_for("seccl")} >= {
            "clients",
            "accounts",
            "transactions",
            "valuations",
            "positions",
            "instruments",
            "models",
            "nodes",
        }
        assert {e.key for e in pe.entities_for("sei")} == {
            "clients",
            "accounts",
            "transactions",
            "valuations",
            "instruments",
            "nodes",
        }

    def test_the_schema_is_the_narrowing(self) -> None:
        """`slice_column=None` throughout: an org filter on top would be a second, weaker copy of
        a decision the pipeline has already made."""
        for provider in ("seccl", "sei"):
            for entity in pe.entities_for(provider):
                assert "{uid}" in entity.relation, entity.key
                assert entity.slice_column is None, entity.key

    def test_the_uid_is_substituted(self) -> None:
        entity = pe.entity_for("seccl", "accounts")
        assert entity is not None
        assert pe.resolve(entity, "ten_abc123").relation == "tenant_abc123.accounts"

    def test_a_uid_that_is_not_an_identifier_is_refused(self) -> None:
        """The one value in this module that is not a registry literal, and it lands in an
        identifier position — so it is checked rather than trusted."""
        from app.platforms import PlatformUnavailable

        entity = pe.entity_for("seccl", "accounts")
        assert entity is not None
        for bad in ('ten_a"; DROP SCHEMA public --', "ten a", "ten-a.b", ""):
            with pytest.raises(PlatformUnavailable, match="not a usable tenant identifier"):
                pe.resolve(entity, bad)

    def test_morgan_lloyd_is_untouched_by_the_substitution(self) -> None:
        """It reads whole-firm `raw.*` narrowed by `ml_tenant_id`; `resolve` must be a no-op."""
        entity = pe.entity_for("morgan-lloyd", "contacts")
        assert entity is not None
        assert pe.resolve(entity, "ten_abc") is entity
        assert entity.slice_column == "ml_tenant_id"

    def test_every_search_and_sort_column_is_shown(self) -> None:
        """Searching or sorting on a column the operator cannot see reads as a broken filter."""
        for provider in ("seccl", "sei"):
            for entity in pe.entities_for(provider):
                assert set(entity.search) <= set(entity.columns), entity.key
                if entity.order_by:
                    assert entity.order_by in entity.columns, entity.key

    def test_every_entity_is_pickable_by_its_conformed_key(self) -> None:
        """`external_id` is the conformed identity (docs/platform-model.md §3), so a picker over
        any of these sends the value the merge keys on."""
        for provider in ("seccl", "sei"):
            for entity in pe.entities_for(provider):
                assert entity.lookup_value == "external_id"
                assert set(entity.lookup_label) <= set(entity.columns), entity.key
