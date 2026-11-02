"""The ``raw.files`` ledger: what each run records, and how a failure gets retried.

The pull loop is exercised through fakes (see ``test_ingest``); what is under test here
is the ledger the ``raw.files`` model builds from it — the statuses, the carried-forward
counts, and the shape of the Arrow table on a night with nothing new.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from test_ingest import PLAIN_DAY, FakeS3, FakeSource, _settings, report_bytes, s3  # noqa: F401

from sei_sync.ingest import FAILED, PRESENT, SYNCED, ingest
from sei_sync.reports import DropFile

# models/ is not a package on the path (interlace discovers it by file), so the model
# module is imported by location, as test_generated_models does with scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "models"))
import raw_files  # noqa: E402


def _rows(result: Any) -> dict[str, dict[str, Any]]:
    table = raw_files._to_table(result.files, {})
    return {row["filename"]: row for row in table.to_pylist()}


def test_a_first_pull_records_every_file_as_synced(s3: FakeS3) -> None:  # noqa: F811
    rows = _rows(ingest(_settings(), FakeSource(PLAIN_DAY)))

    assert {row["status"] for row in rows.values()} == {SYNCED}
    clients = rows["GWP_UK3100_Out_Clients_20260327.out"]
    assert clients["report"] == "Clients"
    assert clients["table"] == "clients"
    assert clients["drop_date"] == date(2026, 3, 27)
    assert clients["archive_key"].startswith("sei/sftp/clients/dt=2026-03-27/")
    assert clients["staged_key"].startswith("sei/staged/clients/dt=2026-03-27/")
    # Verified against SEI's own trailer, which is the only integrity signal the feed
    # gives us — recording it is what makes a short drop arguable after the fact.
    assert clients["trailer_rows"] == 2
    assert clients["rows_read"] == 2
    assert clients["bytes"] == len(report_bytes(2))
    assert clients["error"] is None


def test_a_settled_night_records_present_and_fetches_nothing(s3: FakeS3) -> None:  # noqa: F811
    ingest(_settings(), FakeSource(PLAIN_DAY))
    source = FakeSource(PLAIN_DAY)

    rows = _rows(ingest(_settings(), source))

    assert {row["status"] for row in rows.values()} == {PRESENT}
    assert source.reads == []  # no download: the ledger costs HEADs only


def test_present_rows_carry_forward_the_verified_counts(s3: FakeS3) -> None:  # noqa: F811
    """A file that is not re-read cannot be re-counted, so the count comes from the run
    that did read it. Without this the first skip hollows out every historical row."""
    first = ingest(_settings(), FakeSource(PLAIN_DAY))
    previous = raw_files._previous_counts(_handle(raw_files._to_table(first.files, {})))

    second = ingest(_settings(), FakeSource(PLAIN_DAY))
    table = raw_files._to_table(second.files, previous)

    rows = {row["filename"]: row for row in table.to_pylist()}
    for row in rows.values():
        assert row["status"] == PRESENT
        assert row["trailer_rows"] == 2


def test_a_rebuild_leaves_unverified_counts_null(s3: FakeS3) -> None:  # noqa: F811
    """`this` is None on a rebuild. The counts of already-staged files are genuinely
    unknown to that run, and NULL says so — it must not be reported as zero rows."""
    ingest(_settings(), FakeSource(PLAIN_DAY))
    rows = _rows(ingest(_settings(), FakeSource(PLAIN_DAY)))
    assert all(row["trailer_rows"] is None for row in rows.values())


def test_a_failure_is_recorded_with_its_reason(s3: FakeS3) -> None:  # noqa: F811
    bad = "GWP_UK3100_Out_Clients_20260327.out"
    rows = _rows(ingest(_settings(), FakeSource(PLAIN_DAY, fail_on={bad})))

    assert rows[bad]["status"] == FAILED
    assert "simulated SFTP read failure" in rows[bad]["error"]
    assert rows[bad]["trailer_rows"] is None
    # The rest of the night still landed.
    assert rows["GWP_UK3100_Out_Accounts_20260327.out"]["status"] == SYNCED


def test_a_failed_file_is_retried_and_supersedes_its_row(s3: FakeS3) -> None:  # noqa: F811
    """No retry queue: a failure leaves an incomplete pair of objects, so the next run
    sees the file as outstanding. The merge key is the filename, so the successful row
    replaces the failed one rather than accumulating beside it."""
    bad = "GWP_UK3100_Out_Clients_20260327.out"
    first = _rows(ingest(_settings(), FakeSource(PLAIN_DAY, fail_on={bad})))
    assert first[bad]["status"] == FAILED

    second = _rows(ingest(_settings(), FakeSource(PLAIN_DAY)))

    assert second[bad]["status"] == SYNCED
    assert second[bad]["trailer_rows"] == 2
    assert second[bad]["error"] is None
    assert raw_files.SCHEMA.field("filename").nullable is False


def test_a_failed_row_does_not_inherit_an_earlier_success(s3: FakeS3) -> None:  # noqa: F811
    """A `failed` row showing last week's row count reads as a healthy file with an
    error bolted on. Only `present` inherits."""
    good = ingest(_settings(), FakeSource(PLAIN_DAY))
    previous = raw_files._previous_counts(_handle(raw_files._to_table(good.files, {})))

    bad = "GWP_UK3100_Out_Clients_20260327.out"
    broken = ingest(
        _settings(),
        FakeSource(PLAIN_DAY, fail_on={bad}),
        force=True,  # force re-reads, which is how the second attempt fails
    )
    table = raw_files._to_table(broken.files, previous)

    row = next(r for r in table.to_pylist() if r["filename"] == bad)
    assert row["status"] == FAILED
    assert row["trailer_rows"] is None


def test_an_empty_drop_still_produces_the_declared_schema(s3: FakeS3) -> None:  # noqa: F811
    """A weekend produces no rows. The table must still carry the declared schema, or
    the merge sees a schema change and rebuilds the ledger."""
    table = raw_files._to_table(ingest(_settings(), FakeSource([])).files, {})
    assert table.num_rows == 0
    assert table.schema == raw_files.SCHEMA


def test_the_ledger_records_what_the_sftp_listing_reported(s3: FakeS3, tmp_path: Path) -> None:  # noqa: F811
    """size and mtime come off the listing, so a file SEI rewrote is distinguishable
    from one it truncated. LocalSource stats the files the same way SFTP lists them."""
    from sei_sync.ingest import LocalSource

    name = "GWP_UK3100_Out_Clients_20260327.out"
    (tmp_path / name).write_bytes(report_bytes(3))

    rows = _rows(ingest(_settings(), LocalSource(tmp_path)))

    assert rows[name]["bytes"] == len(report_bytes(3))
    assert rows[name]["sftp_modified_at"] is not None
    assert rows[name]["sftp_modified_at"].tzinfo is not None


def test_previous_counts_tolerates_a_ledger_without_the_columns() -> None:
    """An older materialisation may not carry every column. Reading it must degrade to
    "no counts", not raise and take the night's pull with it."""
    table = pa.table({"filename": ["a.out"]})
    assert raw_files._previous_counts(_handle(table)) == {"a.out": {}}
    assert raw_files._previous_counts(None) == {}


def _handle(table: pa.Table) -> Any:
    """A stand-in for interlace's RelationHandle: one single-pass ``.table()``."""

    class Handle:
        def table(self) -> pa.Table:
            return table

    return Handle()


@pytest.mark.parametrize("status", [SYNCED, PRESENT, FAILED])
def test_statuses_are_the_closed_set_the_docs_describe(status: str) -> None:
    assert status in {"synced", "present", "failed"}


def test_drop_file_metadata_defaults_to_unknown() -> None:
    """parse() reads a filename, not a filesystem — size and mtime are None until
    something lists the file with attributes."""
    drop = DropFile(
        filename="x",
        group_id="3100",
        report="Clients",
        file_date=date(2026, 3, 27),
        encrypted=False,
    )
    assert drop.size is None and drop.modified_at is None
    assert datetime.now(tz=UTC).tzinfo is UTC  # the ledger's stamps are tz-aware
