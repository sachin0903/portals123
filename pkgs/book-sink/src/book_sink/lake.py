"""Project a tenant's slice into the tenant's OWN DuckLake catalogue.

The third leg, and the one that could not be a SQL model. interlace's `attach:` config takes a plain
URI, and a DuckLake catalogue needs two ATTACH OPTIONS a URI cannot carry -- `DATA_PATH` (the
parquet lives in the tenant's own bucket) and `METADATA_SCHEMA` (one schema per tenant in the
shared `datalakes` database). So this owns its own DuckDB connection, and interlace's part is to
schedule it after the conformed models and to materialise its manifest.

A faithful port of `app/fanout.py::_fanout_tenant_sync`, with two deliberate differences:

* it reads the CORE conformed relation and applies the predicate itself, rather than reading the
  per-tenant filter models. Those are promoted at the END of an apply, so within the same run their
  environment view is either absent or still pointing at the previous fingerprint -- reading them
  would make this leg's correctness depend on when it happened to run;
* it returns a manifest instead of writing a log line, so "did this firm's lake refresh, and with
  how many rows" is answerable from the warehouse.

NO TENANT CREDENTIAL IS NEEDED, and it is worth saying because the credential question is what
`docs/provider-pipeline.md` §3 held this leg back on: the projection writes with the CORE
object-store credential the daemon already holds, exactly as the fan-out does today. The per-tenant
scoped credentials exist for firm STAFF reading their own lake and for the API's own read path --
neither of which is this.
"""

from __future__ import annotations

import logging
from typing import Any

from book_sink.config import SinkConfig, sql_str

logger = logging.getLogger("book_sink.lake")

#: The schema a provider's slice occupies INSIDE a tenant's catalogue. Not the provider's name as
#: a catalogue: one tenant can hold accounts at two custodians, and the names would collide.
PLATFORM_SCHEMA = "platform"


class LakeUnavailable(RuntimeError):
    """The tenant's catalogue cannot be opened, so its lake must not be reported as refreshed."""


def attach_statements(
    settings: SinkConfig, *, uid: str, bucket: str, lake_schema: str
) -> list[str]:
    """The statements that turn a fresh in-memory DuckDB into "core readable, this tenant writable".

    A pure function so it can be asserted on: the failure mode this guards against is a wrong
    endpoint or a wrong metadata schema, neither of which raises -- the parquet lands where the
    reader never looks, which reads as "the catalogue is empty" and is indistinguishable from "this
    firm has no data".
    """
    dsn = settings.require_datalake()
    statements = [f"INSTALL {ext}; LOAD {ext};" for ext in ("ducklake", "postgres", "httpfs")]

    secret = ["TYPE s3", f"KEY_ID {sql_str(settings.s3_access_key_id)}"]
    secret.append(f"SECRET {sql_str(settings.s3_secret_access_key)}")
    endpoint = settings.s3_endpoint
    if endpoint:
        host = endpoint.removeprefix("https://").removeprefix("http://")
        secret += [
            f"ENDPOINT {sql_str(host)}",
            f"USE_SSL {'true' if endpoint.startswith('https') else 'false'}",
            # Path style, because a bucket-per-tenant on a self-hosted store has no DNS for
            # `tnt-{uid}.host` -- virtual-host addressing simply fails to resolve.
            "URL_STYLE 'path'",
        ]
    statements.append(f"CREATE SECRET tenant_lake_s3 ({', '.join(secret)})")

    statements.append(
        f"ATTACH {sql_str('ducklake:postgres:' + dsn)} AS core ("
        f"METADATA_SCHEMA {sql_str(lake_schema)}, READ_ONLY)"
    )
    # The tenant's own catalogue: its parquet in its own bucket, its metadata in its own schema
    # (created by storage provisioning -- this never creates one, so an unprovisioned tenant fails
    # loudly here instead of quietly acquiring a lake nothing registered).
    statements.append(
        f"ATTACH {sql_str('ducklake:postgres:' + dsn)} AS tenant ("
        f"DATA_PATH {sql_str(f's3://{bucket}/datalake/')}, "
        f"METADATA_SCHEMA {sql_str(uid)})"
    )
    statements.append(f"CREATE SCHEMA IF NOT EXISTS tenant.{PLATFORM_SCHEMA}")
    return statements


def projection_sql(relation: str, where: str) -> str:
    """One relation's projection: the tenant's slice of the core conformed table.

    CREATE OR REPLACE, so the tenant table is the slice as it stands rather than an accumulation --
    a row the provider stopped publishing must stop being in the firm's lake too. Identifiers come
    from the entity declarations; the predicate's values are quoted by `book_sink.predicate`.
    """
    target = f"tenant.{PLATFORM_SCHEMA}.{relation}"
    source = f"core.{PLATFORM_SCHEMA}.{relation}"
    return f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM {source} WHERE {where}"  # noqa: S608


def is_missing_relation_error(exc: BaseException) -> bool:
    """A core table the catalogue does not have yet, rather than a bad predicate.

    The lake Python model opens a FRESH DuckDB and ATTACHes the catalogue. Conformed models are
    `materialise: virtual` and land in DuckLake only when the apply promotes, so a run that
    rebuilds `platform.nodes` and then projects the tenant lake in the same apply reads a table
    that is not there yet (`Catalog Error: Table with name nodes does not exist!`). Failing the
    model aborts the whole run before promotion, which is how the missing table persists.
    """
    message = str(exc).lower()
    return "does not exist" in message


def project_relations(connection: Any, predicates: dict[str, str]) -> dict[str, int]:
    """Project every relation whose core table is present. Skip (and log) a missing one."""
    counts: dict[str, int] = {}
    for relation, where in predicates.items():
        try:
            connection.execute(projection_sql(relation, where))
        except Exception as exc:  # noqa: BLE001 — DuckDB's CatalogException is not a library error
            if is_missing_relation_error(exc):
                logger.warning(
                    "book_sink: core.%s.%s is not in the catalogue yet; skipping",
                    PLATFORM_SCHEMA,
                    relation,
                )
                continue
            raise
        row = connection.execute(
            f"SELECT count(*) FROM tenant.{PLATFORM_SCHEMA}.{relation}"  # noqa: S608
        ).fetchone()
        counts[relation] = int(row[0]) if row else 0
    return counts


def project(
    settings: SinkConfig,
    *,
    uid: str,
    bucket: str,
    lake_schema: str,
    predicates: dict[str, str],
) -> dict[str, int]:
    """Project every relation in `predicates` into the tenant's catalogue. Rows per relation.

    `lake_schema` is the CORE catalogue's metadata schema for this provider (`seccl`, `sei`, `ml`),
    not the provider's name, because Morgan Lloyd's catalogue attaches as `ml` and reading
    `morgan_lloyd` is how its materialise came to report an empty schema as "no data".
    """
    import duckdb

    connection = duckdb.connect(":memory:")
    try:
        for statement in attach_statements(
            settings, uid=uid, bucket=bucket, lake_schema=lake_schema
        ):
            connection.execute(statement)
        return project_relations(connection, predicates)
    finally:
        connection.close()


def manifest_rows(
    uid: str, provider: str, counts: dict[str, int], stamped: Any
) -> list[dict[str, Any]]:
    """The manifest, one row per relation -- what the Platforms page needs to answer "when did this
    firm's lake last refresh, and with how many rows" without reading a log."""
    return [
        {
            "tenant_uid": uid,
            "provider": provider,
            "relation": relation,
            "rows": rows,
            "projected_at": stamped,
        }
        for relation, rows in sorted(counts.items())
    ]
