"""app/lake: attach-statement construction, and the LakeConnection surface (Arrow / df /
register for OLTP joins) against a real in-memory DuckDB."""

from typing import Any

import pytest

from app import lake
from app.config import settings
from app.lake import attach
from app.lake.attach import LakeUnavailable, attach_statements


def _config(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "uid": "ten_ab12cd34ef567890",
        "bucket": "t-42",
        "endpoint": "http://localhost:9000",
        "region": None,
        "data_path": "s3://t-42/datalake/",
        "lake_schema": "ten_ab12cd34ef567890",
        "access_key_id": "svc-key",
        "secret_access_key": "svc-secret",
        "scoped": True,
    }
    return {**defaults, **overrides}


def test_attach_statements_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "datalake_catalog_dsn", "dbname=datalakes host=localhost")
    statements = attach_statements(_config())

    secret = next(s for s in statements if s.startswith("CREATE SECRET"))
    assert "SCOPE 's3://t-42'" in secret  # credential pinned to the tenant bucket
    assert "ENDPOINT 'localhost:9000'" in secret  # scheme stripped
    assert "USE_SSL false" in secret  # http endpoint
    assert "URL_STYLE 'path'" in secret

    attach_sql = next(s for s in statements if s.startswith("ATTACH"))
    assert attach_sql.startswith(
        "ATTACH 'ducklake:postgres:dbname=datalakes host=localhost' AS lake"
    )
    assert "DATA_PATH 's3://t-42/datalake/'" in attach_sql
    assert "METADATA_SCHEMA 'ten_ab12cd34ef567890'" in attach_sql
    assert "READ_ONLY" in attach_sql
    assert statements[-1] == "USE lake"


def test_https_and_plain_aws_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "datalake_catalog_dsn", "dbname=datalakes")
    https = attach_statements(_config(endpoint="https://s3.example.com"))
    secret = next(s for s in https if s.startswith("CREATE SECRET"))
    assert "ENDPOINT 's3.example.com'" in secret
    assert "USE_SSL true" in secret

    aws = attach_statements(_config(endpoint="", region="eu-west-2"))
    secret = next(s for s in aws if s.startswith("CREATE SECRET"))
    assert "ENDPOINT" not in secret  # plain AWS: default endpoint, virtual-hosted
    assert "REGION 'eu-west-2'" in secret


def test_sql_string_escaping() -> None:
    assert attach._sql_str("it's") == "'it''s'"


def test_oltp_attach_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """With an OLTP DSN, the connection pre-attaches `oltp` READ_ONLY and pins the
    extension to ONE Postgres connection (claims on one connection of a parallel scan
    would silently drop rows — RLS fails closed)."""
    monkeypatch.setattr(settings, "datalake_catalog_dsn", "dbname=datalakes")
    statements = attach_statements(_config())
    assert not any("oltp" in s for s in statements)  # blank DSN => no attach

    monkeypatch.setattr(settings, "datalake_oltp_dsn", "dbname=portals user=app password=app")
    statements = attach_statements(_config())
    assert "SET pg_connection_limit = 1" in statements
    oltp = next(s for s in statements if s.startswith("ATTACH 'dbname=portals"))
    assert "AS oltp (TYPE postgres, READ_ONLY)" in oltp
    assert statements.index("SET pg_connection_limit = 1") < statements.index(oltp)


def _setting(conn: Any, name: str) -> str:
    return conn.execute("SELECT current_setting(?)", [name]).fetchone()[0]


def test_memory_db_carries_the_configured_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uncapped, each pooled DuckDB may take 80% of the host and a thread per core."""
    monkeypatch.setattr(settings, "lake_memory_limit", "256MB")
    monkeypatch.setattr(settings, "lake_threads", 3)
    conn = attach.open_memory_db()
    try:
        assert _setting(conn, "memory_limit") == "244.1 MiB"  # 256 MB, as DuckDB prints it
        assert _setting(conn, "threads") == 3
        assert _setting(conn, "temp_directory").endswith("graphene-lake")
        assert not _setting(conn, "temp_directory").startswith(".")
    finally:
        conn.close()


def test_tenant_and_core_connections_both_open_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both pools' openers go through `open_memory_db`, so neither escapes the caps."""
    from app.lake import core as lake_core

    opened: list[str] = []

    class _Stop(Exception):
        pass

    def _fake() -> Any:
        opened.append("open")
        raise _Stop

    monkeypatch.setattr(attach, "open_memory_db", _fake)
    monkeypatch.setattr(lake_core, "open_memory_db", _fake)
    monkeypatch.setattr(settings, "datalake_catalog_dsn", "dbname=datalakes")

    with pytest.raises(_Stop):
        attach._connect_sync(_config())
    with pytest.raises(_Stop):
        lake_core.connect("seccl")
    assert opened == ["open", "open"]


def test_claims_statements_shape() -> None:
    """The RLS context opens a DuckDB transaction (the extension then holds ONE Postgres
    transaction shared by every OLTP read) and publishes the claims transaction-scoped —
    SET LOCAL ROLE + set_config(..., true), mirroring tenant_session."""
    begin, statement = attach.claims_statements({"tenant_id": 42, "sub": "it's"})
    assert begin == "BEGIN"
    assert statement.startswith("CALL postgres_execute('oltp', '")
    assert "SET LOCAL ROLE authenticated;" in statement
    assert "''request.jwt.claims''" in statement  # nested quoting survives
    assert "true)" in statement  # transaction-scoped (is_local)
    assert "it''''s" in statement  # JSON value escaped through both layers

    assert attach.end_claims_statements() == ["ROLLBACK"]


async def test_unconfigured_catalog_is_unavailable() -> None:
    # the autouse fixture blanks datalake settings
    with pytest.raises(LakeUnavailable):
        await attach.open_tenant_connection("ten_x")


async def test_lake_connection_arrow_df_and_register() -> None:
    """The public result surface, on a plain in-memory DuckDB (no ducklake needed):
    Arrow out, DataFrame out, and register() joining an external frame in SQL."""
    import duckdb
    import pandas as pd

    conn = lake.LakeConnection(duckdb.connect(":memory:"))

    table = await conn.arrow("SELECT 1 AS id, 'alice' AS name")
    assert table.column_names == ["id", "name"]
    assert table.num_rows == 1

    # Strings as well as integers: `df` materialises through Arrow (`to_pandas`) rather than
    # DuckDB's own dict-based converter, and a string column is the half that changes — under
    # pandas 3 the old path rebuilt every one of them as a pyarrow array a second time.
    frame = await conn.df("SELECT range AS id, 'row' || range AS label FROM range(3)")
    assert list(frame["id"]) == [0, 1, 2]
    assert list(frame["label"]) == ["row0", "row1", "row2"]

    # The OLTP-join pattern: register an RLS-fetched frame, join it in SQL.
    oltp = pd.DataFrame({"id": [1, 2], "email": ["a@x.com", "b@x.com"]})
    await conn.register("platform_clients", oltp)
    joined = await conn.arrow(
        "SELECT p.email FROM platform_clients p JOIN (SELECT 1 AS id) l USING (id)"
    )
    assert joined.to_pylist() == [{"email": "a@x.com"}]
    await conn.unregister("platform_clients")


async def test_rows_and_pydantic_models() -> None:
    """rows() gives plain dicts; models() validates into pydantic — the JSON path."""
    import duckdb
    from pydantic import BaseModel

    class Client(BaseModel):
        id: int
        name: str

    conn = lake.LakeConnection(duckdb.connect(":memory:"))
    rows = await conn.rows("SELECT 1 AS id, 'alice' AS name")
    assert rows == [{"id": 1, "name": "alice"}]

    clients = await conn.models(
        Client, "SELECT range::int AS id, 'c' || range AS name FROM range(2)"
    )
    assert clients == [Client(id=0, name="c0"), Client(id=1, name="c1")]


async def test_connection_context_discards_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed body must discard the pooled connection, a clean one must repool it."""
    releases: list[bool] = []

    class FakePool:
        async def acquire(self, uid: str) -> Any:
            from app.lake.pool import PooledConnection

            return PooledConnection(tenant_uid=uid, conn=object(), in_use=True)

        async def release(self, pooled: Any, *, discard: bool = False) -> None:
            releases.append(discard)

    monkeypatch.setattr(lake, "_pool", FakePool())
    async with lake.connection("ten_a"):
        pass
    with pytest.raises(RuntimeError):
        async with lake.connection("ten_a"):
            raise RuntimeError("boom")
    assert releases == [False, True]
    monkeypatch.setattr(lake, "_pool", None)


async def test_connection_applies_and_clears_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    """With claims + an OLTP DSN, the context publishes the RLS context on acquire and
    clears it on clean release; a canary failure discards the connection."""
    releases: list[bool] = []
    events: list[str] = []

    class FakePool:
        async def acquire(self, uid: str) -> Any:
            from app.lake.pool import PooledConnection

            return PooledConnection(tenant_uid=uid, conn=object(), in_use=True)

        async def release(self, pooled: Any, *, discard: bool = False) -> None:
            releases.append(discard)

    async def fake_apply(self: Any, claims: dict[str, Any]) -> None:
        events.append(f"apply:{claims['tenant_id']}")

    async def fake_end(self: Any) -> None:
        events.append("end")

    monkeypatch.setattr(lake, "_pool", FakePool())
    monkeypatch.setattr(settings, "datalake_oltp_dsn", "dbname=portals user=app")
    monkeypatch.setattr(lake.LakeConnection, "_apply_claims", fake_apply)
    monkeypatch.setattr(lake.LakeConnection, "_end_claims", fake_end)

    async with lake.connection("ten_a", claims={"tenant_id": 42}):
        pass
    assert events == ["apply:42", "end"]
    assert releases == [False]

    # Canary failure => the connection is discarded, never yielded.
    async def apply_boom(self: Any, claims: dict[str, Any]) -> None:
        raise RuntimeError("claims canary mismatch")

    monkeypatch.setattr(lake.LakeConnection, "_apply_claims", apply_boom)
    with pytest.raises(RuntimeError):
        async with lake.connection("ten_a", claims={"tenant_id": 42}):
            raise AssertionError("body must not run")
    assert releases == [False, True]
    monkeypatch.setattr(lake, "_pool", None)
