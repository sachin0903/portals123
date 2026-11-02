"""A read-only attach of this service's DuckLake catalogue.

The API models read their own previous materialisation so a rebuild can continue
from the stored scan mark. The environment view still names that table until the
new one is promoted.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("sei_sync.models")


def attach(*, what: str) -> Any | None:
    """The ``sei`` catalogue, or None when it cannot be attached.

    A miss is not raised. The caller then keeps the lookback and does not widen
    a nightly run into a backfill.
    """
    dsn = os.environ.get("DATALAKE_WRITER_DSN", "").strip()
    if not dsn:
        logger.warning("sei %s: no DATALAKE_WRITER_DSN; lookback only", what)
        return None
    try:
        import duckdb
    except ImportError:
        logger.warning("sei %s: duckdb unavailable; lookback only", what)
        return None
    bucket = os.environ.get("S3_CORE_BUCKET", "").strip()
    endpoint = os.environ.get("S3_ENDPOINT_HOST", "").strip()
    key_id = os.environ.get("S3_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("S3_SECRET_ACCESS_KEY", "").strip()
    if not bucket or not endpoint or not key_id or not secret:
        logger.warning("sei %s: object store unset; lookback only", what)
        return None
    host = endpoint.removeprefix("https://").removeprefix("http://")
    use_ssl = "true" if endpoint.startswith("https") else "false"

    def quoted(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

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
        f"ATTACH 'ducklake:postgres:{dsn}' AS sei ("
        f" DATA_PATH {quoted('s3://' + bucket + '/datalake/sei/')},"
        " METADATA_SCHEMA 'sei', READ_ONLY)"
    )
    return con
