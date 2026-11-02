"""The firm's own holdings — what the withdrawal gate is checked on.

It used to be `docs/seccl.duckdb`: an anonymised, downsampled dev extract committed to this
repository, read as THE positions for every firm in every deployment. Both ways of being wrong
were silent. Either the account's provider id was absent from the extract, so the gate found no
position and refused every withdrawal on that account for ever; or the id WAS present — the
extract keeps real Seccl ids — and the gate decided against someone else's anonymised numbers.

Two properties are load-bearing and neither is visible from reading the query:

* the SCHEMA is the narrowing (`tenant_{uid}.positions` is the firm's own slice, computed by the
  provider's chain), so a uid reaching an identifier position is validated rather than trusted;
* "could not read" RAISES where "holds nothing" returns `[]`. The fixture reader returned `[]`
  for a missing file, so a money control could not tell an absent warehouse from an empty
  account — and it refuses on both, which is right, but only one of them is worth paging about.
"""

from __future__ import annotations

import ast
import logging
from decimal import Decimal
from typing import Any

import pytest

from app.lake import core as lake_core
from app.lake import holdings as lake_holdings
from app.routers import crm as crm_router


class _Cursor:
    def __init__(self, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
        self.description = [(c,) for c in columns]
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _Conn:
    def __init__(self, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
        self._columns, self._rows = columns, rows
        self.statements: list[tuple[str, list[Any]]] = []
        self.closed = False

    def execute(self, sql: str, params: list[Any] | None = None) -> _Cursor:
        self.statements.append((sql, list(params or [])))
        return _Cursor(self._columns, self._rows)

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_it_reads_the_firms_own_slice(monkeypatch: pytest.MonkeyPatch) -> None:
    """`tenant_{uid}.positions`, not a whole-firm relation filtered a second time: the console
    and the money control must not disagree about what a firm's slice is."""
    conn = _Conn(["account_external_id", "value"], [("ACC1", 100.0)])
    opened: list[str] = []

    def _connect(provider: str) -> _Conn:
        opened.append(provider)
        return conn

    monkeypatch.setattr(lake_core, "connect", _connect)

    rows = await lake_holdings.firm_holdings("seccl", "ten_abc123", ["ACC1"])
    again = await lake_holdings.firm_holdings("seccl", "ten_abc123", ["ACC1"])

    assert rows == [{"account_external_id": "ACC1", "value": 100.0}]
    assert again == rows
    sql, params = conn.statements[0]
    assert "tenant_abc123.positions" in sql
    assert params == [["ACC1"]], "the account ids are BOUND, never interpolated"
    assert opened == ["seccl"], "a released connection is reused; INSTALL/ATTACH runs once"
    assert conn.closed is False


@pytest.mark.asyncio
async def test_search_filters_and_caps_positions_inside_the_lake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spotlight needs six matches, not every holding transferred into Python and filtered there."""
    conn = _Conn(
        ["account_external_id", "asset_id", "asset_name", "isin", "value"],
        [("ACC1", "FUND1", "IFSL Global Equity", "GB00IFSL0001", 100.0)],
    )
    monkeypatch.setattr(lake_core, "connect", lambda _p: conn)

    rows = await lake_holdings.search_firm_holdings(
        "seccl", "ten_abc123", ["ACC1"], q="ifsl", limit=7
    )

    assert rows[0]["asset_name"] == "IFSL Global Equity"
    sql, params = conn.statements[0]
    assert "p.asset_name ILIKE ?" in sql
    assert "p.isin ILIKE ?" in sql
    assert "p.quantity IS NULL OR p.quantity >= 0" in sql
    assert "LIMIT ?" in sql
    assert params == [["ACC1"], "%ifsl%", "%ifsl%", 7]


def test_a_typed_wildcard_is_a_letter() -> None:
    """``_`` and ``%`` in a query are characters of a name, not LIKE wildcards."""
    assert lake_holdings.contains_pattern(r"v_n%guard") == r"%v\_n\%guard%"


@pytest.mark.asyncio
async def test_the_vendors_own_classification_becomes_asset_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`platform.positions` carries no `asset_type`; it carries `position_type`, which is Seccl's
    own `positionType` (`Cash` | `Stock`). Mapping it into the slot `instructions.is_cash_holding`
    already reads keeps ONE definition of what counts as cash — the money-market funds that are
    cash by NAME rather than by type still count, and the Cash a screen shows cannot drift from
    the cash a withdrawal is checked against."""
    conn = _Conn([], [])
    monkeypatch.setattr(lake_core, "connect", lambda _p: conn)
    await lake_holdings.firm_holdings("seccl", "ten_abc123", ["ACC1"])
    sql = conn.statements[0][0]
    assert "p.position_type" in sql and "AS asset_type" in sql
    assert "p.market_value" in sql and "AS value" in sql


@pytest.mark.asyncio
async def test_a_uid_that_is_not_an_identifier_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The uid lands in an identifier position, which no bind parameter can carry."""
    monkeypatch.setattr(
        lake_core, "connect", lambda _p: pytest.fail("must not reach the warehouse")
    )
    with pytest.raises(lake_holdings.HoldingsUnavailable):
        await lake_holdings.firm_holdings("seccl", "ten_abc; DROP TABLE x --", ["ACC1"])


@pytest.mark.asyncio
async def test_an_unreadable_warehouse_raises_rather_than_reading_as_empty(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """THE DISTINCTION THIS MODULE EXISTS FOR. The fixture reader answered `[]` for a missing
    file, so a money control saw the same value for "the lake is not there" and "this account
    holds nothing".

    The MESSAGE names no configuration, because a firm reads it and cannot act on it; the reason
    is logged instead, so an operator still has the one thing that identifies the fault."""

    def _down(_provider: str) -> Any:
        raise lake_core.CoreUnavailable("DATALAKE_CATALOG_DSN not configured")

    monkeypatch.setattr(lake_core, "connect", _down)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(
            lake_holdings.HoldingsUnavailable, match="Holdings are temporarily unavailable"
        ) as raised:
            await lake_holdings.firm_holdings("seccl", "ten_abc123", ["ACC1"])
    assert "DATALAKE_CATALOG_DSN" not in str(raised.value)
    assert "DATALAKE_CATALOG_DSN" in caplog.text


@pytest.mark.asyncio
async def test_a_firm_whose_chain_never_ran_raises_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `tenant_{uid}` schema at all — DuckDB reports a catalog error, and reading that as
    "this account holds nothing" is how a gate decides on an absence it has not understood."""

    class _Missing(_Conn):
        def execute(self, sql: str, params: list[Any] | None = None) -> _Cursor:
            raise RuntimeError("Catalog Error: Table with name positions does not exist!")

    monkeypatch.setattr(lake_core, "connect", lambda _p: _Missing([], []))
    # Still a DIFFERENT message from the unreadable-warehouse branch above, which is what lets the
    # two be told apart in a log without either naming infrastructure to a firm.
    with pytest.raises(
        lake_holdings.HoldingsUnavailable, match="Holdings from this custodian are temporarily"
    ):
        await lake_holdings.firm_holdings("seccl", "ten_abc123", ["ACC1"])


@pytest.mark.asyncio
async def test_no_accounts_asks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty id list is a caller with nothing to ask about, not a warehouse question."""
    monkeypatch.setattr(
        lake_core, "connect", lambda _p: pytest.fail("must not reach the warehouse")
    )
    assert await lake_holdings.firm_holdings("seccl", "ten_abc123", []) == []


def test_no_module_reads_the_committed_extract() -> None:
    """`docs/seccl.duckdb` is an anonymised, downsampled dev extract that was read as THE
    positions for every firm in every deployment — by a withdrawal gate, a holdings page, an
    activity feed and a value chart.

    The tripwire is a source scan rather than a comment, because the failure it guards is a NEW
    caller: the file is still in the repository (it is a useful reference for Seccl's shape), and
    the reader that opened it was ordinary-looking code.

    STRING LITERALS, via the AST, not a substring of the file. Naming the extract in prose is how
    this module and `instructions.py` explain what they replaced — a scan that counted those would
    make the honest explanation the thing that fails.

    ONE module is allowed to open it, and the allowlist is the point: `app/lake/fixture.py` serves
    the extract to the DISPLAY reads only (holdings table, activity feed, value chart) on a
    non-production deployment whose provider chain has not run, where the alternative is a client
    being shown an empty portfolio beside a real balance. It is named here rather than exempted by
    a rule, so a second reader still trips the wire and has to argue for itself. What must never
    come back is the money path: `test_lake_fixture.py` asserts `instructions.py` cannot reach it,
    so an unreadable warehouse still REFUSES a withdrawal rather than guessing from dev data.
    """
    import ast
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    permitted = {"app/lake/fixture.py"}
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        # `.as_posix()` so the allowlist match (and reported path) use forward slashes on every
        # platform — `str(Path)` yields backslashes on Windows, which never matched the allowlist,
        # so fixture.py flagged itself there.
        rel = path.relative_to(app_dir.parent).as_posix()
        if rel in permitted:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "seccl.duckdb" in node.value and not _is_docstring(tree, node):
                    offenders.append(rel)
    assert not offenders, f"these reach for the committed Seccl extract: {sorted(set(offenders))}"


def _is_docstring(tree: ast.AST, node: ast.Constant) -> bool:
    """Whether this literal is a docstring — prose, not a path being opened."""
    import ast as _ast

    for parent in _ast.walk(tree):
        if isinstance(
            parent, _ast.Module | _ast.FunctionDef | _ast.AsyncFunctionDef | _ast.ClassDef
        ):
            body = getattr(parent, "body", [])
            first = body[0] if body else None
            if (
                isinstance(first, _ast.Expr)
                and isinstance(first.value, _ast.Constant)
                and first.value is node
            ):
                return True
    return False


class TestTheSliceIsKeyedOnProviderAndFirm:
    """`by_firm` groups on (provider, firm), and the display path resolves the provider from data.

    A slice is `tenant_{uid}.<relation>` inside ONE provider's catalogue. Grouping by uid alone
    only worked while every caller passed the literal `"seccl"` — which meant a firm custodied at
    SEI resolved no accounts, read nothing, and was shown an empty holdings table, an empty
    allocation donut and a flat chart beside an OLTP `value` that is real. Indistinguishable, to
    the person reading it, from owning nothing.
    """

    def test_two_custodians_in_one_firm_are_read_separately(self) -> None:
        grouped = lake_holdings.by_firm(
            {
                "SC-1": {"tenant_uid": "ten_a", "provider": "seccl"},
                "SC-2": {"tenant_uid": "ten_a", "provider": "seccl"},
                "MSP000762": {"tenant_uid": "ten_a", "provider": "sei"},
            }
        )
        assert grouped == {
            ("seccl", "ten_a"): ["SC-1", "SC-2"],
            ("sei", "ten_a"): ["MSP000762"],
        }

    def test_one_custodian_across_two_firms_still_splits(self) -> None:
        """The lineage case `by_firm` already existed for: a parent firm reading its own book sees
        accounts belonging to more than one tenant."""
        grouped = lake_holdings.by_firm(
            {
                "A": {"tenant_uid": "ten_a", "provider": "seccl"},
                "B": {"tenant_uid": "ten_b", "provider": "seccl"},
            }
        )
        assert grouped == {("seccl", "ten_a"): ["A"], ("seccl", "ten_b"): ["B"]}

    def test_an_account_with_no_provider_is_dropped_not_guessed(self) -> None:
        """Reading an id against the wrong catalogue finds nothing and reads as "holds nothing",
        so an unattributed id is left out rather than sent to a default."""
        assert lake_holdings.by_firm({"A": {"tenant_uid": "ten_a"}}) == {}
        assert lake_holdings.by_firm({"A": {"provider": "seccl"}}) == {}

    def test_the_custodians_are_derived_from_the_registry(self) -> None:
        """Not hardcoded, and disjoint from the administrators: an account administered by Morgan
        Lloyd carries an `entity_type='account'` link too, and ML's project publishes no positions
        relation — reading it would raise HoldingsUnavailable and degrade the page to the extract.
        """
        from app import providers

        custodians = set(providers.custodians())
        assert custodians == {"seccl", "sei"}
        assert not custodians & set(providers.administrators())
        assert custodians | set(providers.administrators()) == set(providers.PROVIDERS)

    def test_no_display_read_names_a_custodian_in_a_literal(self) -> None:
        """The tripwire for the defect itself: `'seccl'` was a string literal in the read path in
        eight places across `routers/crm.py` and `instructions.py`, and nothing in `docs/` recorded
        that the portals were single-custodian by construction.

        Scans STRING CONSTANTS via the AST, not the file text, so the comments explaining what this
        replaced are not what fails.
        """
        import ast
        import pathlib

        app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
        offenders: list[str] = []
        for rel in ("app/routers/crm.py", "app/instructions.py", "app/lake/holdings.py"):
            path = app_dir.parent / rel
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in {"seccl", "sei"}
                    and not _is_docstring(tree, node)
                ):
                    offenders.append(f"{rel}:{node.lineno}")
        assert not offenders, (
            "the display path must resolve its custodian from platform.links, not name one: "
            f"{sorted(offenders)}"
        )


class TestANegativePositionIsNotAHolding:
    """A negative quantity is the out-leg of an unsettled share-class migration, not something a
    client owns. 140 of 473 rows on the dev book carry one, and rendering them as holdings put
    "-1,864%" in a client's own Return column."""

    def test_a_negative_quantity_row_is_not_a_holding(self) -> None:
        row = {"asset_name": "WS Epworth", "quantity": -559191.9}
        assert not crm_router._is_current_holding(row)

    def test_a_decimal_negative_quantity_is_not_a_holding(self) -> None:
        assert not crm_router._is_current_holding(
            {"asset_name": "WS Epworth", "quantity": Decimal("-559191.9")}
        )

    def test_an_ordinary_position_is(self) -> None:
        assert crm_router._is_current_holding({"asset_name": "Epworth", "quantity": 39213.59})

    def test_cash_survives_even_when_overdrawn(self) -> None:
        """QUANTITY, not value: cash is worth what it is and carries no quantity, and an adviser
        has to be able to see an overdrawn balance rather than have it filtered away."""
        assert crm_router._is_current_holding(
            {"asset_name": "Cash", "quantity": None, "value": -2500.0}
        )

    def test_a_row_that_states_no_quantity_is_kept(self) -> None:
        """Absence is not a negative. A provider that publishes no quantity must not have its
        positions silently dropped."""
        assert crm_router._is_current_holding({"asset_name": "Something"})

    def test_zero_is_kept_here_and_excluded_upstream(self) -> None:
        """The lake reads already drop a zero VALUATION; this rule is only about the sign."""
        assert crm_router._is_current_holding({"asset_name": "Something", "quantity": 0})

    def test_the_withdrawal_gate_does_not_read_through_this_filter(self) -> None:
        """`instructions.py` calls `lake_holdings.firm_holdings` directly, and must keep doing so:
        dropping a negative position there would make `funding_basis` report more money available
        than the account has. A display read may simplify; a money gate may not."""
        import ast
        import pathlib

        path = pathlib.Path(__file__).resolve().parent.parent / "app" / "instructions.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "firm_holdings" in called
        assert "_firm_holdings" not in called
