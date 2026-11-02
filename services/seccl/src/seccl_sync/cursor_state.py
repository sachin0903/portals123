"""Resume a date-filtered pull from rows already in the warehouse.

interlace injects ``max(cursor column)`` from the model's previous physical
table. That is the start date, and a later run of the same model uses it. A
changed model is a new table, so the injected value is None and the extractor
would list the endpoint with no date filter. The environment view still names
the previous materialisation until this run is promoted, which is the copy
read here. The stored rows are returned with the new scan so the new table is
not only the overlap.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

logger = logging.getLogger("seccl_sync.models")


def resume(injected: Any, relation: str, column: str) -> tuple[Any, pa.Table | None]:
    """``(start, stored rows)``. A start already in hand does not touch the warehouse."""
    if injected is not None:
        return injected, None
    table = _read(relation)
    if table is None or table.num_rows == 0 or column not in table.column_names:
        return None, None
    values = [value for value in table.column(column).to_pylist() if value is not None]
    if not values:
        return None, None
    logger.info("seccl %s: continuing from stored %s %s", relation, column, max(values))
    return max(values), table


def prefer_fresh(existing: pa.Table, fresh: pa.Table, key: str) -> pa.Table | None:
    """Stored rows, with this scan's copy of a key winning. None when the key is absent."""
    if key not in existing.column_names or key not in fresh.column_names:
        return None
    if fresh.num_rows and existing.num_rows:
        keep = pc.invert(pc.is_in(existing.column(key), value_set=fresh.column(key)))
        existing = existing.filter(keep)
    if existing.num_rows == 0:
        return fresh
    if fresh.num_rows == 0:
        return existing
    # Permissive widens int64 onto a stored double. A date window of whole-pound
    # amounts infers integers; the history table already holds doubles. Default
    # promotion refuses that pair. A real clash (string against double) still raises.
    return pa.concat_tables([existing, fresh], promote_options="permissive")


def keep(injected: Any, existing: pa.Table | None, fresh: pa.Table, key: str) -> pa.Table:
    """Publish ``fresh`` on an ordinary run, or fresh plus stored rows when rebuilding."""
    if injected is not None or existing is None:
        return fresh
    try:
        combined = prefer_fresh(existing, fresh, key)
    except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
        raise RuntimeError(
            f"seccl raw cursor: stored {key} rows could not be combined with this scan; "
            "not replacing them"
        ) from exc
    if combined is None:
        raise RuntimeError(
            f"seccl raw cursor: stored rows have no {key!r}; not replacing them with this scan"
        )
    return combined


_READ = {
    "notifications": "SELECT * FROM seccl.raw.notifications",
    "messages": "SELECT * FROM seccl.raw.messages",
    "transactions": "SELECT * FROM seccl.raw.transactions",
}


def _read(relation: str) -> pa.Table | None:
    sql = _READ.get(relation)
    if sql is None:
        logger.warning("seccl %s: not a cursor relation", relation)
        return None
    con = _catalogue()
    if con is None:
        return None
    try:
        table = con.execute(sql).arrow()
    except Exception as exc:  # noqa: BLE001 - a miss falls back to the full list
        logger.warning("seccl %s: could not read stored rows: %s", relation, exc)
        return None
    if isinstance(table, pa.RecordBatchReader):
        table = table.read_all()
    return table


def _catalogue() -> Any | None:
    dsn = os.environ.get("DATALAKE_WRITER_DSN", "").strip()
    bucket = os.environ.get("S3_CORE_BUCKET", "").strip()
    endpoint = os.environ.get("S3_ENDPOINT_HOST", "").strip()
    key_id = os.environ.get("S3_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("S3_SECRET_ACCESS_KEY", "").strip()
    if not dsn or not bucket or not endpoint or not key_id or not secret:
        logger.warning("seccl cursor: warehouse is not configured; a missing cursor lists in full")
        return None
    try:
        import duckdb
    except ImportError:
        logger.warning("seccl cursor: duckdb unavailable; a missing cursor lists in full")
        return None
    host = endpoint.removeprefix("https://").removeprefix("http://")
    use_ssl = "true" if endpoint.startswith("https") else "false"

    def quoted(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    try:
        con = duckdb.connect()
        for ext in ("ducklake", "postgres", "httpfs"):
            con.execute(f"INSTALL {ext}")
            con.execute(f"LOAD {ext}")
        con.execute(
            "CREATE SECRET core_s3 ("
            f" TYPE s3, KEY_ID {quoted(key_id)}, SECRET {quoted(secret)},"
            f" ENDPOINT {quoted(host)}, USE_SSL {use_ssl}, URL_STYLE 'path',"
            f" SCOPE {quoted('s3://' + bucket)})"
        )
        con.execute(
            f"ATTACH 'ducklake:postgres:{dsn}' AS seccl ("
            f" DATA_PATH {quoted('s3://' + bucket + '/datalake/seccl/')},"
            " METADATA_SCHEMA 'seccl', READ_ONLY)"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("seccl cursor: could not attach the catalogue: %s", exc)
        return None
    return con
