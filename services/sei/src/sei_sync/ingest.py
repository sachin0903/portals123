"""The pull: SEI SFTP drop -> the core bucket -> decrypt -> staged.

Two object writes per file, both idempotent on the key already existing:

    sei/sftp/{table}/dt={date}/{name}.out.pgp      exactly what SEI sent
    sei/staged/{table}/dt={date}/{name}.out        decrypted, pipe-delimited

Keeping the encrypted original is the point of the sftp zone: it is the evidence
of what the custodian actually delivered, and re-decrypting is cheap. The staged
copy exists because DuckDB cannot read PGP — the models `read_csv` it directly.

The source is abstracted so a run can read the live SFTP drop or a local
directory of the same files (``--local-dir``), which is how this is developed
off the SEI IP allowlist.

Every file the loop touches yields a :class:`FileRecord`, and that is what the
``raw.files`` model materialises: the pull is a step in the interlace DAG rather than
a separate cron whose outcome lived only in a log. A file that fails is recorded as
``failed`` and left with no complete pair of objects, so the next run sees it as
outstanding and retries it — the retry needs no queue, only an honest ledger.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from sei_sync.decrypt import unlocked
from sei_sync.objects import client, exists, put
from sei_sync.reports import DropFile, parse, split_trailer, to_utf8
from sei_sync.settings import SeiSettings
from sei_sync.sftp import connect, discover, read_file

logger = logging.getLogger(__name__)

_PLAINTEXT_TYPE = "text/csv"
_ENCRYPTED_TYPE = "application/pgp-encrypted"


class DropSource(Protocol):
    """Where the drop files come from — live SFTP or a local directory."""

    def list_drops(self) -> list[DropFile]: ...

    def read(self, drop: DropFile) -> bytes: ...


@dataclass
class LocalSource:
    """A directory of drop files, for development off the SEI allowlist."""

    directory: Path

    def list_drops(self) -> list[DropFile]:
        drops: list[DropFile] = []
        for path in sorted(self.directory.iterdir()):
            drop = parse(path.name)
            if drop is None:
                continue
            info = path.stat()
            drops.append(
                replace(
                    drop,
                    size=info.st_size,
                    modified_at=datetime.fromtimestamp(info.st_mtime, tz=UTC),
                )
            )
        drops.sort(key=lambda d: (d.file_date, d.report))
        return drops

    def read(self, drop: DropFile) -> bytes:
        return (self.directory / drop.filename).read_bytes()


@dataclass
class SftpSource:
    """The live SEI drop. Holds an open client for the life of a run."""

    settings: SeiSettings
    client: object  # paramiko.SFTPClient, kept loose so paramiko stays a lazy import

    def list_drops(self) -> list[DropFile]:
        return discover(self.client, self.settings)  # type: ignore[arg-type]

    def read(self, drop: DropFile) -> bytes:
        return read_file(self.client, self.settings, drop)  # type: ignore[arg-type]


SYNCED = "synced"
PRESENT = "present"
FAILED = "failed"


@dataclass
class FileRecord:
    """One drop file's outcome — a row of the ``raw.files`` ledger.

    ``trailer_rows`` and ``rows_read`` are populated only when this run actually read
    the file: a ``present`` file is not re-downloaded to re-count it, and a ``failed``
    one may have died before the trailer was reachable. NULL there means "not verified
    on this pass", which is a different fact from zero rows.
    """

    filename: str
    report: str
    table: str
    drop_date: date
    status: str
    archive_key: str
    staged_key: str
    bytes: int | None = None
    sftp_modified_at: datetime | None = None
    trailer_rows: int | None = None
    rows_read: int | None = None
    error: str | None = None


@dataclass
class IngestResult:
    """What a run did. Counts are files, not bytes."""

    raw_written: list[str] = field(default_factory=list)
    staged_written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    # Verified data rows per report table, from each file's record-count trailer.
    rows: dict[str, int] = field(default_factory=dict)
    # One per file considered, in the order the loop reached them.
    files: list[FileRecord] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"{len(self.raw_written)} raw, {len(self.staged_written)} staged, "
            f"{sum(self.rows.values())} rows verified, "
            f"{len(self.skipped)} already present, {len(self.failed)} failed"
        )


def select(
    drops: Iterable[DropFile],
    *,
    since: date | None = None,
    until: date | None = None,
    reports: set[str] | None = None,
) -> list[DropFile]:
    """Narrow a drop listing by date window and report name (snake_case table)."""
    chosen = []
    for drop in drops:
        if since is not None and drop.file_date < since:
            continue
        if until is not None and drop.file_date > until:
            continue
        if reports is not None and drop.table not in reports:
            continue
        chosen.append(drop)
    return chosen


def ingest(
    settings: SeiSettings,
    source: DropSource,
    *,
    since: date | None = None,
    until: date | None = None,
    reports: set[str] | None = None,
    limit: int | None = None,
    force: bool = False,
) -> IngestResult:
    """Land the selected drop files in the core bucket, decrypting as we go.

    A file already present in both zones is skipped without being fetched, so a
    re-run over a month of history costs one HEAD per file rather than a download.
    """
    drops = select(source.list_drops(), since=since, until=until, reports=reports)
    if limit is not None:
        drops = drops[:limit]

    result = IngestResult()
    if not drops:
        logger.warning("no SEI drop files matched the selection; nothing to do")
        return result

    s3 = client(settings)
    bucket = settings.core_bucket
    needs_decrypt = any(d.encrypted for d in drops)

    # unlocked() is a no-op cost when the whole selection is already plaintext
    # (SEI's older i10 files) — don't demand key material we won't use.
    with unlocked(settings) if needs_decrypt else _no_decryptor() as decrypt:
        for drop in drops:
            archive_key = drop.archive_key()
            staged_key = drop.staged_key()
            record = FileRecord(
                filename=drop.filename,
                report=drop.report,
                table=drop.table,
                drop_date=drop.file_date,
                status=PRESENT,
                archive_key=archive_key,
                staged_key=staged_key,
                bytes=drop.size,
                sftp_modified_at=drop.modified_at,
            )
            result.files.append(record)
            try:
                have_raw = not force and exists(s3, bucket, archive_key)
                have_staged = not force and exists(s3, bucket, staged_key)
                if have_raw and have_staged:
                    result.skipped.append(drop.filename)
                    continue

                record.status = SYNCED
                blob = source.read(drop)
                # What SEI actually sent, whatever the listing claimed. The listing's
                # size is the server's view before the read; this is the byte count we
                # archived, which is the one a truncation argument turns on.
                record.bytes = len(blob)
                if not have_raw:
                    put(
                        s3,
                        bucket,
                        archive_key,
                        blob,
                        content_type=_ENCRYPTED_TYPE if drop.encrypted else _PLAINTEXT_TYPE,
                    )
                    result.raw_written.append(archive_key)

                if not have_staged:
                    if drop.encrypted:
                        if decrypt is None:  # pragma: no cover — guarded by needs_decrypt
                            raise RuntimeError("encrypted file with no decryptor available")
                        plaintext = decrypt(blob)
                    else:
                        plaintext = blob
                    # Verify SEI's record-count trailer and drop it, so the staged
                    # zone is clean CSV. A mismatch means a truncated drop: raise,
                    # which records this file as failed and leaves no staged object
                    # for the models to read as if it were complete.
                    csv, rows = split_trailer(plaintext)
                    # …and transcode it, so the staged zone is UTF-8 whatever SEI sent. The
                    # corpus is mixed and DuckDB can be told only one encoding, so normalising
                    # here is what lets every model read plainly (reports.to_utf8 explains).
                    # Byte-exact evidence is the `sftp/` archive above, which is untouched.
                    csv, encoding = to_utf8(csv)
                    if encoding != "utf-8":
                        logger.info("SEI %s was %s, transcoded to UTF-8", drop.filename, encoding)
                    put(s3, bucket, staged_key, csv, content_type=_PLAINTEXT_TYPE)
                    result.staged_written.append(staged_key)
                    result.rows[drop.table] = result.rows.get(drop.table, 0) + rows
                    record.trailer_rows = rows
                    record.rows_read = rows
            except Exception as exc:
                # One bad file must not abandon the rest of the night's drop.
                logger.warning("SEI ingest failed for %s: %s", drop.filename, exc)
                result.failed[drop.filename] = str(exc)
                record.status = FAILED
                record.error = str(exc)

    logger.info("SEI ingest complete: %s", result.summary)
    return result


def ingest_sftp(settings: SeiSettings, **kwargs: object) -> IngestResult:
    """Run a pull against the live SEI SFTP drop."""
    with connect(settings) as sftp:
        return ingest(settings, SftpSource(settings, sftp), **kwargs)  # type: ignore[arg-type]


class _no_decryptor:
    """Context manager yielding None, so the ingest loop has one shape."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None
