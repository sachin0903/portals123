"""`app/lake/staff.py` — the custodian's employee directory, from the firm's own slice.

The picker behind the MANUAL half of the adviser↔custodian mapping: `auth.merge_provider_user`
(0123) attaches automatically wherever the custodian's address matches a login's exactly, and this
is for the ordinary case it declines to guess at, since a firm's SEI address and its Graphene login
address are frequently different.

The reads are stubbed the way `test_lake_holdings.py` stubs them — what is worth asserting is the
RELATION named, that the search term is BOUND rather than interpolated, and that an unreadable
warehouse raises instead of reading as "this firm has nobody".
"""

from __future__ import annotations

from typing import Any

import pytest

from app.lake import core as lake_core
from app.lake import staff as lake_staff


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


COLUMNS = [
    "external_id",
    "email",
    "display_name",
    "first_name",
    "last_name",
    "login_name",
    "is_active",
]
ROW = ("57817496", "m.a@firm.test", "Armasoiu, Maria", "Maria", "Armasoiu", "marmasoiu", True)
OTHER = ("57817497", "j.b@firm.test", "Bell, Jon", "Jon", "Bell", "jbell", True)


@pytest.fixture(autouse=True)
def _no_cached_slice() -> Any:
    """The slice is cached for a minute and single-flighted, so a test that did not clear it would
    be asserting on the previous test's connection."""
    lake_staff.forget_staff()
    yield
    lake_staff.forget_staff()


@pytest.mark.asyncio
async def test_it_reads_the_firms_own_users_relation(monkeypatch: pytest.MonkeyPatch) -> None:
    """`tenant_{uid}.users` — the slice that provider's own chain computed, filtered to employees
    (`USER_TYPE = 1`) upstream. Not a live vendor call: the API response carries no `userType` at
    all, so a picker fed from it would offer a firm its own clients as advisers."""
    conn = _Conn(COLUMNS, [ROW])
    monkeypatch.setattr(lake_core, "connect", lambda _p: conn)

    rows = await lake_staff.firm_staff("sei", "ten_abc123")

    assert rows == [dict(zip(COLUMNS, ROW, strict=True))]
    sql, _ = conn.statements[0]
    assert "tenant_abc123.users" in sql
    # The core pool keeps the attached connection. Closing it here would pay INSTALL and ATTACH
    # again on the next picker read.
    assert not conn.closed


@pytest.mark.asyncio
async def test_the_search_term_never_reaches_the_sql_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It was BOUND, which was correct; now it is not sent. The picker is typed into, so the slice
    is cached and the substring match runs in Python — the caller's value reaches no SQL, bound or
    otherwise. (The tenant uid still does, in an IDENTIFIER position where no bind parameter is
    allowed, which is what `require_uid` is for.)"""
    conn = _Conn(COLUMNS, [ROW, OTHER])
    monkeypatch.setattr(lake_core, "connect", lambda _p: conn)

    rows = await lake_staff.firm_staff("sei", "ten_abc123", search="  BELL  ")

    assert [r["login_name"] for r in rows] == ["jbell"], "trimmed, case-insensitive"
    sql, params = conn.statements[0]
    assert "bell" not in sql.lower()
    assert params == [lake_staff._SLICE_CAP], "the read's own bound, and nothing of the caller's"


@pytest.mark.asyncio
async def test_the_search_reads_the_three_fields_a_person_would_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`display_name`, `email`, `login_name` — the same three the `LIKE` covered."""
    monkeypatch.setattr(lake_core, "connect", lambda _p: _Conn(COLUMNS, [ROW]))
    for needle in ("armasoiu", "M.A@FIRM.TEST", "marmasoiu"):
        assert await lake_staff.firm_staff("sei", "ten_abc123", search=needle), needle
    assert await lake_staff.firm_staff("sei", "ten_abc123", search="nobody") == []


@pytest.mark.asyncio
async def test_the_page_size_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A picker asking for everything is still a picker. One SEI firm in the p04 corpus has 546
    employees, so an unbounded page is a real one and not a hypothetical."""
    monkeypatch.setattr(lake_core, "connect", lambda _p: _Conn(COLUMNS, [ROW, OTHER]))

    assert len(await lake_staff.firm_staff("sei", "ten_abc123", limit=100_000)) == 2
    assert len(await lake_staff.firm_staff("sei", "ten_abc123", limit=0)) == 1


@pytest.mark.asyncio
async def test_the_slice_is_read_once_however_much_is_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHY THE CACHE EXISTS. The core pool already avoids repeating INSTALL and ATTACH, but this
    is the estate's one INTERACTIVE lake reader: three characters typed quickly should not check
    out a connection at all. The relation is refreshed nightly."""
    connections: list[_Conn] = []

    def _connect(_provider: str) -> _Conn:
        conn = _Conn(COLUMNS, [ROW, OTHER])
        connections.append(conn)
        return conn

    monkeypatch.setattr(lake_core, "connect", _connect)
    for needle in ("b", "be", "bel", "bell"):
        await lake_staff.firm_staff("sei", "ten_abc123", search=needle)

    assert len(connections) == 1


@pytest.mark.asyncio
async def test_each_firm_gets_its_own_slice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keyed on (provider, uid), because the relation is the FIRM's own — a shared entry would
    hand one firm another's directory, which is the failure the whole slice model prevents."""
    opened: list[str] = []
    connections: list[_Conn] = []

    def _connect(provider: str) -> _Conn:
        opened.append(provider)
        conn = _Conn(COLUMNS, [ROW])
        connections.append(conn)
        return conn

    monkeypatch.setattr(lake_core, "connect", _connect)
    await lake_staff.firm_staff("sei", "ten_abc123")
    await lake_staff.firm_staff("sei", "ten_def456")
    await lake_staff.firm_staff("seccl", "ten_abc123")
    # One attached connection per provider. The second SEI firm reuses it; its schema is still
    # its own, which is what keeps one firm's directory off another's.
    assert opened == ["sei", "seccl"]
    sql = " ".join(statement for conn in connections for statement, _params in conn.statements)
    assert "tenant_abc123.users" in sql
    assert "tenant_def456.users" in sql


@pytest.mark.asyncio
async def test_a_uid_that_is_not_an_identifier_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The uid is the one value here that is not a literal and it lands in an identifier position,
    so it is checked rather than trusted — `platform_entities.resolve()`'s rule."""
    monkeypatch.setattr(lake_core, "connect", lambda _p: pytest.fail("must not connect"))
    with pytest.raises(lake_staff.HoldingsUnavailable):
        await lake_staff.firm_staff("sei", "ten_abc; DROP SCHEMA public")


@pytest.mark.asyncio
async def test_an_unreadable_warehouse_raises_rather_than_reading_as_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE DISTINCTION THIS MODULE EXISTS TO KEEP. A firm whose chain has never run has no
    `tenant_{uid}` schema at all, and an empty dropdown that means "we could not read" looks
    exactly like one that means "this firm has no staff at that custodian" — which are different
    problems for whoever is looking at it."""

    def _boom(_provider: str) -> Any:
        raise lake_core.CoreUnavailable("DATALAKE_CATALOG_DSN not configured")

    monkeypatch.setattr(lake_core, "connect", _boom)
    with pytest.raises(lake_staff.HoldingsUnavailable):
        await lake_staff.firm_staff("sei", "ten_abc123")


@pytest.mark.asyncio
async def test_a_catalog_error_is_converted_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing schema surfaces from DuckDB as a plain exception, not a `CoreUnavailable`."""

    def _boom(_provider: str) -> Any:
        raise RuntimeError('Catalog Error: Table with name "users" does not exist!')

    monkeypatch.setattr(lake_core, "connect", _boom)
    with pytest.raises(
        lake_staff.HoldingsUnavailable, match="Holdings from this custodian are temporarily"
    ):
        await lake_staff.firm_staff("sei", "ten_abc123")


@pytest.mark.asyncio
async def test_an_unavailable_read_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a SUCCESSFUL read is remembered. Caching the refusal would hold a firm's picker shut
    for the TTL after one transient catalogue blip, and the chain's first run would not be seen
    until it expired."""

    def _boom(_provider: str) -> Any:
        raise lake_core.CoreUnavailable("DATALAKE_CATALOG_DSN not configured")

    monkeypatch.setattr(lake_core, "connect", _boom)
    with pytest.raises(lake_staff.HoldingsUnavailable):
        await lake_staff.firm_staff("sei", "ten_abc123")

    monkeypatch.setattr(lake_core, "connect", lambda _p: _Conn(COLUMNS, [ROW]))
    assert await lake_staff.firm_staff("sei", "ten_abc123")
