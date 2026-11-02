"""Plumbing for the generated raw models: connection, object-store secret, audit.

Everything *semantic* about a raw model — which report, which columns get which
type, the read options — lives inline in ``models/raw_reports.py``, because
interlace fingerprints a Python model from its own source and anything hidden in a
helper is invisible to change detection. This module is deliberately the part that
carries no meaning: open a DuckDB connection, teach it the core bucket, run the
model's own SQL, and report conversion losses.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import pyarrow as pa

from sei_sync.columns import loss_audit, typed_columns
from sei_sync.settings import SeiSettings

logger = logging.getLogger("sei_sync.models")


def _secret_sql(settings: SeiSettings) -> str:
    parts = [
        "TYPE s3",
        f"KEY_ID '{settings.s3_access_key_id}'",
        f"SECRET '{settings.s3_secret_access_key}'",
    ]
    if settings.s3_endpoint:
        host = settings.s3_endpoint.removeprefix("https://").removeprefix("http://")
        parts += [
            f"ENDPOINT '{host}'",
            f"USE_SSL {'true' if settings.endpoint_url.startswith('https') else 'false'}",
            "URL_STYLE 'path'",
        ]
    return f"CREATE SECRET sei_source ({', '.join(parts)})"


def _read(table: str, sql: str, cursor: date | None, empty_columns: tuple[str, ...]) -> pa.Table:
    """Run one raw model's SQL against the staged objects.

    Its own DuckDB connection: interlace owns the warehouse connection, and this is
    a source read against object storage, not warehouse SQL.
    """
    import duckdb

    settings = SeiSettings()
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        conn.execute(_secret_sql(settings))

        # The model's SQL carries {bucket}; nothing else in it is braced. The SEI
        # environment used to be interpolated here too — it left the paths when the
        # bucket became per-environment, so there was nothing left for it to
        # disambiguate.
        query = sql.format(bucket=settings.core_bucket)
        params: list[Any] = []
        if cursor is not None:
            # Window functions run after WHERE, and a drop date maps to whole files,
            # so filtering partitions out cannot disturb _row within those that stay.
            query += " WHERE dt > ?"
            params.append(cursor)
        try:
            result = conn.execute(query, params).arrow()
        except duckdb.IOException:
            # No objects under the prefix — a report SEI declares but has never delivered here
            # (`FSLTaxData` on the i31 drop: 46 of the 47 known reports arrive, this one never has).
            # An empty table is the truthful answer and must not fail the other 46.
            #
            # IT MUST SATISFY THE MODEL'S WHOLE CONTRACT — its KEY and its CURSOR — because
            # interlace reads both off the previous materialisation and neither failure names the
            # real reason (there is no file):
            #
            #   key ("_source_file", "_row"): the merge's DELETE selects the key columns from the
            #     new snapshot, so a stub without `_row` fails with `Referenced column "_row" not
            #     found` — which is what a stub carrying only `_source_file` did.
            #   cursor "dt": on any run AFTER the first, interlace asks the previous snapshot for
            #     `max(dt)`, so a stub without it fails with `Referenced column "dt" not found`.
            #     That one hides until a re-run: the first build has no previous materialisation to
            #     ask, so an `apply` succeeds and only the second one breaks.
            #
            # `dt` is a DATE on the real tables (a hive partition read as one), and matching that
            # matters: the cursor is compared against a date, and a VARCHAR here would either
            # compare wrongly or fail the merge on a type mismatch the day the report arrives.
            logger.info("sei raw.%s: no staged objects yet", table)
            return _empty(empty_columns)

        # Materialise before anything else touches this connection. DuckDB's
        # ``.arrow()`` hands back a RecordBatchReader that streams from the
        # connection, and both the audit query below and the ``conn.close()`` in the
        # finally block silently empty it — the rows are simply gone, and the model
        # returns 0 rows with the right schema, which looks like an empty drop.
        if isinstance(result, pa.RecordBatchReader):
            result = result.read_all()

        _log_conversion_loss(conn, table, sql, settings, params)
        return result
    finally:
        conn.close()


def _log_conversion_loss(
    conn: Any, table: str, sql: str, settings: SeiSettings, params: list[Any]
) -> None:
    """Warn about source values that failed to convert to their declared type.

    The conversions are lenient so one malformed value cannot fail a night's ingest.
    That is only defensible if a bad value is loud — this is that loudness, and it is
    how an all-NULL date column shows up on the first night rather than in a review
    months later.
    """
    audit = loss_audit(table)
    if audit is None:
        return
    # Audit the untyped source, not the model's output: the point is to compare what
    # SEI sent against what converted, and the output no longer holds the text.
    source = sql.split("FROM ", 1)[1].format(bucket=settings.core_bucket)
    where = " WHERE dt > ?" if params else ""
    try:
        row = conn.execute(audit.format(source=source) + where, params).fetchone()
    except Exception as exc:  # an audit must never fail the ingest it is auditing
        logger.warning("sei raw.%s: conversion audit failed: %s", table, exc)
        return
    if row is None:
        return
    lost = {column: count for column, count in zip(typed_columns(table), row, strict=True) if count}
    if lost:
        logger.warning(
            "sei raw.%s: %d column(s) lost values converting to their declared type: %s",
            table,
            len(lost),
            ", ".join(f"{column}={count}" for column, count in sorted(lost.items())),
        )


#: The type each stub column takes. `dt` is a DATE on the real tables (a hive partition read as
#: one) and matching that matters: the cursor is compared against a date, and a VARCHAR would either
#: compare wrongly or fail the merge on a type mismatch the day the report starts arriving.
_EMPTY_TYPES: dict[str, pa.DataType] = {
    "_source_file": pa.string(),
    "_row": pa.int64(),
    "dt": pa.date32(),
}


def _empty(columns: tuple[str, ...]) -> pa.Table:
    """A zero-row table with exactly the columns the caller says its model needs."""
    unknown = [c for c in columns if c not in _EMPTY_TYPES]
    if unknown:
        raise ValueError(f"no declared type for empty column(s) {unknown}")
    return pa.table({c: pa.array([], _EMPTY_TYPES[c]) for c in columns})


async def read_report(
    table: str,
    cursor: Any,
    sql: str,
    empty_columns: tuple[str, ...] = ("_source_file", "_row", "dt"),
) -> pa.Table:
    """Await the staged read for one report off the event loop.

    ``empty_columns`` is what the stub publishes when the report has no objects, and the MODEL
    passes it — deliberately, so it lands inside `inspect.getsource(fn)`. interlace fingerprints a
    Python model from its own source and nothing it calls, so a shape declared only here is
    invisible to change detection: correcting it cannot rebuild the wrong table it already
    produced, which is how a stub missing `dt` survived `run`, `restate` AND a dropped snapshot.
    """
    return await asyncio.to_thread(_read, table, sql, cursor, empty_columns)
