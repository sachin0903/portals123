"""DuckLake catalogue provisioning (docs/datalake.md).

The `datalakes` Postgres database hosts one DuckLake catalogue per tenant as a metadata
schema named by the tenant's immutable uid (``METADATA_SCHEMA`` on attach). This module
owns the tiny provisioning step: ensure the schema exists and is readable by
``lake_reader`` (the read-only attach role ``app/lake`` uses). The connection role here
is ``lake_writer`` (``DATALAKE_DATABASE_URL``), which then owns the schema — DuckLake
creates its ``ducklake_*`` tables in it on the ETL's first (writer) attach.

Blank ``DATALAKE_DATABASE_URL`` disables the step (storage provisioning still succeeds —
the catalogue can be ensured later by re-running provision).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings

# Tenant uids are `ten_<hex>`; keep a strict allowlist because schema names cannot be bound
# parameters (they are identifiers, interpolated below after this check).
_SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

_engine: AsyncEngine | None = None


def configured() -> bool:
    return bool(settings.datalake_database_url)


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        # Provisioning is rare; keep the pool minimal.
        _engine = create_async_engine(settings.datalake_database_url, pool_size=1, max_overflow=2)
    return _engine


def reset_engine() -> None:
    """Testing hook: drop the cached engine after settings change."""
    global _engine
    _engine = None


async def ensure_tenant_catalog(lake_schema: str) -> None:
    """Idempotently create the tenant's catalogue schema in the datalakes DB and let
    lake_reader see it (table SELECTs come from the DB-level default privileges)."""
    if not _SCHEMA_RE.match(lake_schema):
        raise ValueError(f"invalid lake schema name '{lake_schema}'")
    async with _get_engine().begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{lake_schema}"'))
        await conn.execute(text(f'GRANT USAGE ON SCHEMA "{lake_schema}" TO lake_reader'))


async def reset_tenant_catalog(lake_schema: str) -> None:
    """Remove every tenant-lake relation and recreate its empty metadata schema.

    The shared provider catalogues are never touched.  This is intentionally a
    separate operation from storage provisioning: reset keeps the tenant's
    bucket and credentials but removes the data catalogue they address.
    """
    if not configured():
        return
    if not _SCHEMA_RE.match(lake_schema):
        raise ValueError(f"invalid lake schema name '{lake_schema}'")
    async with _get_engine().begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{lake_schema}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{lake_schema}"'))
        await conn.execute(text(f'GRANT USAGE ON SCHEMA "{lake_schema}" TO lake_reader'))


async def ensure_provider_catalogs(schemas: Iterable[str]) -> None:
    """Let lake_reader see the provider catalogue schemas (ml / seccl / sei).

    Tenant schemas get this from ``ensure_tenant_catalog`` at provisioning time, but
    provider catalogues are created by the interlace daemons running as lake_writer,
    and nothing was granting USAGE on them — so every lake_reader ATTACH against a
    provider catalogue failed with "permission denied for schema", taking the admin
    Platforms warehouse counts, SQL console and enablement option lists with it.
    Bootstrap can't do it (the schemas don't exist yet) and ALTER DEFAULT PRIVILEGES
    doesn't cover schemas, so it is done here — idempotently, at startup, which also
    repairs databases created before this existed.
    """
    if not configured():
        return
    async with _get_engine().begin() as conn:
        for schema in schemas:
            if not _SCHEMA_RE.match(schema):
                raise ValueError(f"invalid lake schema name '{schema}'")
            # CREATE first: granting on a not-yet-created schema would error, and the
            # daemon creates it on its first run, which may be after we start.
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            await conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO lake_reader'))
            await conn.execute(
                text(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO lake_reader')
            )


async def repoint_catalogue(lake_schema: str, old_bucket: str, new_bucket: str) -> int:
    """Point a tenant's catalogue at a renamed bucket. Returns the rows rewritten (0 or 1).

    This is the whole cost of a bucket rename, and the reason one is affordable: DuckLake
    stores each data file's path RELATIVE to the catalogue's ``data_path``, so the bucket
    name lives in exactly ONE ``ducklake_metadata`` row per catalogue. Every existing
    snapshot survives untouched — nothing is rewritten, re-read or rebuilt.

    Idempotent: a catalogue already naming ``new_bucket`` matches nothing and returns 0.
    """
    if not _SCHEMA_RE.match(lake_schema):
        raise ValueError(f"invalid lake schema name '{lake_schema}'")
    async with _get_engine().begin() as conn:
        result: Any = await conn.execute(
            text(
                f'UPDATE "{lake_schema}".ducklake_metadata '  # noqa: S608 — name allowlisted above
                "   SET value = replace(value, :old, :new) "
                " WHERE key = 'data_path' AND value LIKE :like"
            ),
            {
                "old": f"s3://{old_bucket}/",
                "new": f"s3://{new_bucket}/",
                "like": f"s3://{old_bucket}/%",
            },
        )
        return int(result.rowcount or 0)


async def catalog_exists(lake_schema: str) -> bool:
    async with _get_engine().connect() as conn:
        row: Any = await conn.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
            {"s": lake_schema},
        )
        return row.scalar_one_or_none() is not None
