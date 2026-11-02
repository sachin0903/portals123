"""Per-tenant DuckLake attach: config resolution + connection construction (docs/datalake.md).

Isolation lives here, not in caller auth: each DuckDB connection is in-memory, attaches
exactly one tenant's catalogue **read-only** (``METADATA_SCHEMA`` = the tenant uid,
catalogue role = lake_reader) and carries one S3 secret scoped to that tenant's bucket.
A connection is structurally incapable of reading another tenant's lake.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from typing import Any

from sqlalchemy import text

from app import tenant_storage
from app.config import settings


class LakeUnavailable(RuntimeError):
    """The tenant has no active lake (unknown uid, suspended storage) or the catalogue
    DSN is not configured."""


_cache: dict[str, tuple[float, dict[str, Any]]] = {}


async def fetch_lake_config(uid: str) -> dict[str, Any]:
    """The tenant's attach config from the storage registry (service-role read — the
    secrets table is unreadable to the request role), TTL-cached per tenant."""
    now = time.monotonic()
    hit = _cache.get(uid)
    if hit and now - hit[0] < settings.lake_config_cache_seconds:
        return hit[1]
    from app.db import SessionLocal  # local import: avoid engine construction at import time

    async with SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        config = await tenant_storage.get_lake_config(session, uid)
    if config is None:
        raise LakeUnavailable(f"no active lake for tenant '{uid}'")
    _cache[uid] = (now, config)
    return config


def clear_config_cache() -> None:
    _cache.clear()


def invalidate_config(uid: str) -> None:
    """Drop one tenant's cached attach config (suspension must not wait out the TTL)."""
    _cache.pop(uid, None)


def _sql_str(value: str) -> str:
    """Single-quote a string for interpolation into DuckDB DDL (ATTACH options and
    CREATE SECRET fields are not bind-parameterisable)."""
    return "'" + value.replace("'", "''") + "'"


def s3_secret_sql(
    name: str,
    *,
    key_id: str,
    secret: str,
    scope: str = "",
    endpoint: str = "",
    region: str = "",
) -> str:
    """`CREATE SECRET` for an S3-compatible store, as DuckDB spells it.

    Shared with `app/platforms.py`'s core-catalogue attach, which had its own copy of this and of
    `_sql_str` — same fields, same endpoint-host stripping, same `URL_STYLE 'path'`, written twice.
    The only difference between the two call sites is WHICH bucket the credential is scoped to,
    which is the argument.

    `SCOPE` is what pins a credential to one bucket, so it is passed rather than defaulted: a
    tenant's secret must never be able to read the core bucket, and blank means unscoped.
    """
    parts = [
        "TYPE s3",
        f"KEY_ID {_sql_str(key_id)}",
        f"SECRET {_sql_str(secret)}",
    ]
    if scope:
        parts.append(f"SCOPE {_sql_str(scope)}")
    if endpoint:
        host = endpoint.removeprefix("https://").removeprefix("http://")
        parts += [
            f"ENDPOINT {_sql_str(host)}",
            f"USE_SSL {'true' if endpoint.startswith('https') else 'false'}",
            "URL_STYLE 'path'",
        ]
    if region:
        parts.append(f"REGION {_sql_str(region)}")
    return f"CREATE SECRET {name} ({', '.join(parts)})"


def attach_ducklake_sql(
    alias: str, dsn: str, *, metadata_schema: str, data_path: str = "", read_only: bool = True
) -> str:
    """`ATTACH` a hosted DuckLake catalogue.

    `data_path` is omitted for the CORE catalogue, whose parquet prefix is a property of the
    provider's own project rather than of a reader — which is exactly why the two call sites
    could not share a single fixed statement and each grew its own.
    """
    options = [f"METADATA_SCHEMA {_sql_str(metadata_schema)}"]
    if data_path:
        options.insert(0, f"DATA_PATH {_sql_str(data_path)}")
    if read_only:
        options.append("READ_ONLY")
    return f"ATTACH 'ducklake:postgres:{dsn}' AS {alias} ({', '.join(options)})"


def attach_statements(config: dict[str, Any]) -> list[str]:
    """The statements that turn a fresh in-memory DuckDB into this tenant's read-only lake
    connection. The catalogue is attached as `lake` and made the default catalog."""
    statements = ["INSTALL ducklake", "INSTALL postgres", "INSTALL httpfs"]

    statements.append(
        s3_secret_sql(
            "lake_s3",
            key_id=config["access_key_id"],
            secret=config["secret_access_key"],
            # SCOPE pins this credential to the tenant's bucket only.
            scope="s3://" + config["bucket"],
            endpoint=config.get("endpoint") or "",
            region=config.get("region") or "",
        )
    )
    statements.append(
        attach_ducklake_sql(
            "lake",
            settings.datalake_catalog_dsn,
            metadata_schema=config["lake_schema"],
            data_path=config["data_path"],
        )
    )

    if settings.datalake_oltp_dsn:
        # OLTP pre-attach for lake↔OLTP joins WITH RLS. One Postgres connection only:
        # the extension parallelises scans over several connections, and claims set on
        # one would not exist on the others — RLS would fail closed and rows would be
        # silently missing. Claims themselves are applied per-acquire (claims_statements).
        statements.append("SET pg_connection_limit = 1")
        statements.append(
            f"ATTACH {_sql_str(settings.datalake_oltp_dsn)} AS oltp (TYPE postgres, READ_ONLY)"
        )

    statements.append("USE lake")
    return statements


def claims_statements(claims: dict[str, Any]) -> list[str]:
    """Open the RLS context: a DuckDB transaction + the request's claims on the attached
    OLTP connection, transaction-scoped — mirroring ``db.py::tenant_session``.

    Outside a DuckDB transaction the postgres extension wraps every operation in its own
    rolled-back Postgres transaction, so no session state survives between statements.
    INSIDE one, it holds a single Postgres transaction shared by ``postgres_execute``,
    ``postgres_query`` and catalog scans — so ``SET LOCAL ROLE`` + a transaction-scoped
    ``set_config`` apply to every OLTP read until the transaction ends, then vanish."""
    import json

    inner = (
        "SET LOCAL ROLE authenticated; "
        f"SELECT set_config('request.jwt.claims', {_sql_str(json.dumps(claims))}, true)"
    )
    return ["BEGIN", f"CALL postgres_execute('oltp', {_sql_str(inner)})"]


def end_claims_statements() -> list[str]:
    """Close the RLS context. ROLLBACK (reads only — nothing to commit): the Postgres
    transaction ends and the role + claims evaporate with it."""
    return ["ROLLBACK"]


def duckdb_config() -> dict[str, Any]:
    """The resource caps every request-path DuckDB opens with.

    Unset, DuckDB gives each in-memory database 80% of the host's RAM and a thread per core, and
    the pools keep up to ``lake_pool_max_connections + lake_core_pool_max`` of them alive, each
    holding the S3 file cache it has filled. Spill goes to the system temp dir: the default
    ``.tmp`` is relative to the working directory, which the image's ``app`` user cannot write.
    """
    return {
        "memory_limit": settings.lake_memory_limit,
        "threads": settings.lake_threads,
        "temp_directory": os.path.join(tempfile.gettempdir(), "graphene-lake"),
    }


def open_memory_db() -> Any:
    """A fresh in-memory DuckDB with :func:`duckdb_config`'s caps applied."""
    import duckdb

    return duckdb.connect(":memory:", config=duckdb_config())


def _connect_sync(config: dict[str, Any]) -> Any:
    conn = open_memory_db()
    for statement in attach_statements(config):
        conn.execute(statement)
    return conn


async def open_tenant_connection(uid: str) -> Any:
    """A fresh in-memory DuckDB attached read-only to this tenant's lake."""
    if not settings.datalake_catalog_dsn:
        raise LakeUnavailable("DATALAKE_CATALOG_DSN not configured")
    config = await fetch_lake_config(uid)
    return await asyncio.to_thread(_connect_sync, config)
