"""``raw.files`` — the drop ledger, and the model that fills it by doing the pull.

This is the head of the SEI DAG. Every one of the 47 ``raw.*`` report models depends
on it, so a nightly ``run`` fetches the drop and *then* reads it, in one dependency
graph, instead of the old shape: a separate ``sei-sync pull`` cron whose only record
of what happened was a log line, and 47 models that read whatever objects happened to
be in the bucket by the time they fired. If the pull failed, the reports still ran and
quietly re-read yesterday.

A model is allowed to do this. interlace asks a Python model for Arrow and does not
care where it came from — so the extraction (SFTP listing, archive, PGP decrypt,
trailer verification, staging) happens here, and the rows it returns are the ledger of
that work: one per file SEI is currently offering.

**Status vocabulary.** ``synced`` — fetched and staged on this run. ``present`` — both
objects were already in the bucket, so nothing was fetched. ``failed`` — attempted and
errored, with ``error`` saying how.

**Retry needs no queue.** A failed file leaves an incomplete pair of objects behind, so
the next run's existence check sees it as outstanding and fetches it again; the merge
then replaces the ``failed`` row with a ``synced`` one. The only thing that makes this
work is that the ledger is keyed on the filename and tells the truth about the last
attempt.

**Why the previous materialisation is read.** A ``present`` file is not re-downloaded
just to re-count its rows, so this run cannot say what its trailer declared — but the
run that staged it could. ``this`` carries those counts forward, which keeps the ledger
complete instead of hollowing out every historical row the first time a file is
skipped. On a rebuild (``this`` is None) the counts of already-staged files are
genuinely unknown and stay NULL, which is the honest answer.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa
from interlace import model

from sei_sync.ingest import PRESENT, FileRecord, ingest, ingest_sftp
from sei_sync.previous import previous_table
from sei_sync.settings import SeiSettings

logger = logging.getLogger("sei_sync.models")

# Declared explicitly rather than inferred from the rows: a night with nothing new must
# still produce a typed empty table, or the merge sees a schema change and rebuilds.
SCHEMA = pa.schema(
    [
        pa.field("filename", pa.string(), nullable=False),
        pa.field("report", pa.string(), nullable=False),
        pa.field("table", pa.string(), nullable=False),
        pa.field("drop_date", pa.date32(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("archive_key", pa.string(), nullable=False),
        pa.field("staged_key", pa.string(), nullable=False),
        pa.field("bytes", pa.int64()),
        pa.field("sftp_modified_at", pa.timestamp("us", tz="UTC")),
        pa.field("trailer_rows", pa.int64()),
        pa.field("rows_read", pa.int64()),
        pa.field("error", pa.string()),
        pa.field("processed_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

_CARRIED = ("trailer_rows", "rows_read")


@model(
    name="raw.files",
    strategy="merge",
    key=("filename",),
    # 05:00, half an hour before the report crons. From interlace 2.8.3 a scheduled fire
    # runs this model and everything downstream, so the pull carries the reports. Ancestors
    # are still not included: a report's own cron does not fetch the drop, which is why
    # that gap stays. `depends_on` orders an ad-hoc `run` or a `--select +raw.accounts`.
    schedule={"cron": "0 5 * * 2-6"},
    tags=("sei", "raw", "ledger"),
    description="SEI drop files: SFTP -> archive -> decrypt -> staged, one row per file",
)
async def raw_files(this: Any) -> pa.Table:
    previous = _previous_counts(this)
    records = await asyncio.to_thread(_pull)
    return _to_table(records, previous)


def _pull() -> list[FileRecord]:
    """Run the pull against the live drop, or a local directory in development.

    ``since=None``: every file currently on the SFTP drop is a candidate, and the
    existence check in ``ingest`` is what makes that cheap — a settled night costs one
    listing plus two HEADs per file and downloads nothing. Filtering to yesterday
    instead would leave a file that failed last night unretried forever.
    """
    settings = SeiSettings()
    if settings.local_drop_dir:
        from pathlib import Path

        from sei_sync.ingest import LocalSource

        logger.info("sei raw.files: reading the local drop at %s", settings.local_drop_dir)
        return ingest(
            settings, LocalSource(Path(settings.local_drop_dir).expanduser()), since=None
        ).files
    return ingest_sftp(settings, since=None).files


def _previous_counts(this: Any) -> dict[str, dict[str, int | None]]:
    """Verified row counts from the last materialisation, keyed by filename."""
    table = previous_table(this)
    if table is None:
        return {}
    columns = set(table.column_names)
    if "filename" not in columns:  # pragma: no cover — an older shape of the ledger
        return {}
    carried = [name for name in _CARRIED if name in columns]
    return {
        row["filename"]: {name: row[name] for name in carried}
        for row in table.select(["filename", *carried]).to_pylist()
    }


def _to_table(records: list[FileRecord], previous: dict[str, dict[str, int | None]]) -> pa.Table:
    now = datetime.now(tz=UTC)
    rows = []
    for record in records:
        row: dict[str, Any] = {
            "filename": record.filename,
            "report": record.report,
            "table": record.table,
            "drop_date": record.drop_date,
            "status": record.status,
            "archive_key": record.archive_key,
            "staged_key": record.staged_key,
            "bytes": record.bytes,
            "sftp_modified_at": record.sftp_modified_at,
            "trailer_rows": record.trailer_rows,
            "rows_read": record.rows_read,
            "error": record.error,
            "processed_at": now,
        }
        # Only a `present` file inherits: it is the same bytes verified by an earlier
        # run. A `failed` row must not wear the counts of the last time this file
        # worked — that would read as a healthy file with an error attached.
        if record.status == PRESENT:
            row.update(previous.get(record.filename, {}))
        rows.append(row)
    return pa.Table.from_pylist(rows, schema=SCHEMA)
