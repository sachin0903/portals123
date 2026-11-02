"""Read-only attach to the shared SEI core catalogue (docs/datalake.md).

Mirrors `apps/api/app/lake/core.py` + `attach.py` deliberately, not independently reinvented:
this is the identical operation apps/api already does — a read-only DuckDB attach to a
provider's whole-firm DuckLake catalogue, as `lake_reader`, one credential scoped to the core
bucket. `services/sei`'s own `sei-sync` daemon is the only writer; this service, like
`app/lake`, never attaches any other way.

`metadata_schema` is hardcoded to `"sei"` — this service only ever reads SEI's catalogue, never a
second provider's — and `data_path` is omitted on purpose: DuckLake recorded it at ATTACH time
when `sei-sync` first created the catalogue, so a reader does not restate it (same as `core.py`).

This module stays provider-agnostic (it knows nothing about Intelliflo specifically) — a
connection's own provider-specific views (e.g. `providers/intelliflo/views.py`'s
`create_views(con)`) are each provider's own job to create after calling `open_lake()`, the same
way each provider owns its own queries.
"""

import logging

from intelliflo_sync.shared import config

logger = logging.getLogger(__name__)

_METADATA_SCHEMA = "sei"


def _sql_str(value: str) -> str:
    """Single-quote a string for interpolation into DuckDB DDL (ATTACH options and CREATE
    SECRET fields are not bind-parameterisable)."""
    return "'" + value.replace("'", "''") + "'"


def _redact(text: str) -> str:
    """DuckLake errors quote the whole catalogue connection string, password included."""
    import re

    return re.sub(r"(?i)(password\s*=\s*)\S+", r"\1***", str(text))


def open_lake():
    """A fresh in-memory DuckDB, READ_ONLY attached to the SEI core catalogue and `USE`d as the
    default catalog — so callers select straight from `platform.*` / `raw.*` / `ref.*`."""
    if not config.DATALAKE_CATALOG_DSN:
        raise RuntimeError(
            "DATALAKE_CATALOG_DSN is not configured — the SEI lake is unavailable. "
            "See apps/api/.env.example for the local value (lake_reader on the datalakes DB)."
        )

    import duckdb

    con = duckdb.connect(":memory:")
    for ext in ("ducklake", "postgres", "httpfs"):
        con.execute(f"INSTALL {ext}; LOAD {ext};")

    secret = [
        "TYPE s3",
        f"KEY_ID {_sql_str(config.S3_ACCESS_KEY_ID)}",
        f"SECRET {_sql_str(config.S3_SECRET_ACCESS_KEY)}",
    ]
    if config.S3_CORE_BUCKET:
        secret.append(f"SCOPE {_sql_str('s3://' + config.S3_CORE_BUCKET)}")
    if config.S3_ENDPOINT_HOST:
        secret += [
            f"ENDPOINT {_sql_str(config.S3_ENDPOINT_HOST)}",
            f"USE_SSL {'true' if config.S3_USE_SSL else 'false'}",
            "URL_STYLE 'path'",
        ]
    con.execute(f"CREATE SECRET core_s3 ({', '.join(secret)})")

    try:
        con.execute(
            f"ATTACH 'ducklake:postgres:{config.DATALAKE_CATALOG_DSN}' AS core "
            f"(METADATA_SCHEMA {_sql_str(_METADATA_SCHEMA)}, READ_ONLY)"
        )
    except Exception as exc:
        # `from None`: the original exception carries the unredacted connection string
        raise RuntimeError(f"Could not attach the SEI lake: {_redact(exc)}") from None
    con.execute("USE core")
    return con
