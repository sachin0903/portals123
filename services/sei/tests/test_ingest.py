"""The pull loop: selection, idempotency and per-file failure isolation.

Runs against fakes for both ends (drop source + object store) so the SEI SFTP
allowlist and real key material are irrelevant here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

from sei_sync.ingest import IngestResult, LocalSource, ingest, select
from sei_sync.reports import DropFile, parse
from sei_sync.settings import SeiSettings


def _settings(**over: Any) -> SeiSettings:
    base: dict[str, Any] = {
        "env": "i31",
        "core_bucket": "platforms",
        "pgp_passphrase": "pw",
        # Never read: every fixture below is plaintext, so no decryptor is built.
        "pgp_private_key_base64": "",
    }
    return SeiSettings(**{**base, **over})


def _drop(name: str) -> DropFile:
    parsed = parse(name)
    assert parsed is not None
    return parsed


def report_bytes(rows: int = 1) -> bytes:
    """A drop file as SEI actually sends one: header, rows, record-count trailer."""
    body = b"".join(f"{i}|value-{i}\n".encode() for i in range(rows))
    return b"A|B\n" + body + f"Total Number of Records: |{rows}\n".encode()


# Two data rows, correctly declared.
VALID_BODY = report_bytes(2)


@dataclass
class FakeSource:
    drops: list[DropFile]
    bodies: dict[str, bytes] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)
    fail_on: set[str] = field(default_factory=set)

    def list_drops(self) -> list[DropFile]:
        return list(self.drops)

    def read(self, drop: DropFile) -> bytes:
        self.reads.append(drop.filename)
        if drop.filename in self.fail_on:
            raise RuntimeError("simulated SFTP read failure")
        return self.bodies.get(drop.filename, VALID_BODY)


@dataclass
class FakeS3:
    """Enough of the boto3 S3 surface for objects.exists/put."""

    objects: dict[str, bytes] = field(default_factory=dict)
    puts: list[str] = field(default_factory=list)

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, int]:  # noqa: N803
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:  # noqa: N803
        self.objects[Key] = Body
        self.puts.append(Key)


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> FakeS3:
    fake = FakeS3()
    monkeypatch.setattr("sei_sync.ingest.client", lambda _settings: fake)
    return fake


PLAIN_DAY = [
    _drop("GWP_UK3100_Out_Clients_20260327.out"),
    _drop("GWP_UK3100_Out_Accounts_20260327.out"),
]


def test_select_narrows_by_date_window() -> None:
    drops = [
        _drop("GWP_UK3100_Out_Clients_20260325.out"),
        _drop("GWP_UK3100_Out_Clients_20260326.out"),
        _drop("GWP_UK3100_Out_Clients_20260327.out"),
    ]
    chosen = select(drops, since=date(2026, 3, 26), until=date(2026, 3, 26))
    assert [d.file_date for d in chosen] == [date(2026, 3, 26)]


def test_select_narrows_by_report_table() -> None:
    chosen = select(PLAIN_DAY, reports={"clients"})
    assert [d.table for d in chosen] == ["clients"]


def test_ingest_writes_raw_and_staged(s3: FakeS3) -> None:
    result = ingest(_settings(), FakeSource(PLAIN_DAY))
    assert len(result.raw_written) == 2
    assert len(result.staged_written) == 2
    assert not result.failed
    raw_key = "sei/sftp/clients/dt=2026-03-27/GWP_UK3100_Out_Clients_20260327.out"
    staged_key = "sei/staged/clients/dt=2026-03-27/GWP_UK3100_Out_Clients_20260327.out"
    assert raw_key in s3.objects
    assert staged_key in s3.objects
    # The raw copy is byte-exact evidence of what SEI sent, trailer included; the
    # staged copy is clean CSV with the (verified) trailer removed.
    assert s3.objects[raw_key] == VALID_BODY
    assert b"Total Number of Records" in s3.objects[raw_key]
    assert b"Total Number of Records" not in s3.objects[staged_key]
    assert s3.objects[staged_key] == b"A|B\n0|value-0\n1|value-1\n"
    # Verified row counts are reported per report table.
    assert result.rows == {"clients": 2, "accounts": 2}


def test_truncated_file_is_rejected_not_staged(s3: FakeS3) -> None:
    """The trailer declares more rows than arrived — a partial transfer. It must not
    land in staged, or the models would read a short day as a real one."""
    truncated = b"A|B\n0|value-0\nTotal Number of Records: |9\n"
    drop = PLAIN_DAY[0]
    source = FakeSource([drop], bodies={drop.filename: truncated})

    result = ingest(_settings(), source)

    assert "declares 9 records but 1 rows delivered" in result.failed[drop.filename]
    assert result.staged_written == []
    assert not any(key.startswith("sei/staged/") for key in s3.objects)
    # The raw evidence is still kept — that is how the truncation gets diagnosed.
    assert any(key.startswith("sei/sftp/") for key in s3.objects)


def test_missing_trailer_is_rejected(s3: FakeS3) -> None:
    drop = PLAIN_DAY[0]
    source = FakeSource([drop], bodies={drop.filename: b"A|B\n0|value-0\n"})
    result = ingest(_settings(), source)
    assert "no record-count trailer" in result.failed[drop.filename]
    assert result.staged_written == []


def test_header_only_report_is_valid(s3: FakeS3) -> None:
    """SEI sends a header + 'Records: |0' for a report with nothing in it that day."""
    drop = PLAIN_DAY[0]
    source = FakeSource([drop], bodies={drop.filename: report_bytes(0)})
    result = ingest(_settings(), source)
    assert not result.failed
    assert len(result.staged_written) == 1
    assert result.rows == {"clients": 0}


def test_ingest_is_idempotent_and_does_not_refetch(s3: FakeS3) -> None:
    """A second run must cost HEADs only — no download, no re-upload. This is what
    makes re-running a month of history cheap."""
    ingest(_settings(), FakeSource(PLAIN_DAY))
    s3.puts.clear()

    source = FakeSource(PLAIN_DAY)
    result = ingest(_settings(), source)

    assert result.skipped == [d.filename for d in PLAIN_DAY]
    assert result.raw_written == [] and result.staged_written == []
    assert s3.puts == []
    assert source.reads == []  # the whole point: nothing was fetched again


def test_force_rewrites_existing_objects(s3: FakeS3) -> None:
    ingest(_settings(), FakeSource(PLAIN_DAY))
    s3.puts.clear()
    result = ingest(_settings(), FakeSource(PLAIN_DAY), force=True)
    assert len(result.raw_written) == 2
    assert len(s3.puts) == 4  # raw + staged for both


def test_one_bad_file_does_not_abandon_the_run(s3: FakeS3) -> None:
    """A single unreadable file must not cost us the rest of the night's drop."""
    source = FakeSource(PLAIN_DAY, fail_on={"GWP_UK3100_Out_Clients_20260327.out"})
    result = ingest(_settings(), source)

    assert set(result.failed) == {"GWP_UK3100_Out_Clients_20260327.out"}
    assert len(result.staged_written) == 1
    assert any("accounts" in key for key in result.staged_written)


def test_empty_selection_is_not_an_error(s3: FakeS3) -> None:
    """A weekend or holiday means no drop; that is normal, not a failure."""
    result = ingest(_settings(), FakeSource([]))
    assert result == IngestResult()
    assert s3.puts == []


def test_plaintext_files_need_no_key_material(s3: FakeS3) -> None:
    """No PGP key is configured in these settings at all — an all-plaintext
    selection must not demand one."""
    result = ingest(_settings(pgp_private_key_base64="", pgp_passphrase=""), FakeSource(PLAIN_DAY))
    assert not result.failed
    assert len(result.staged_written) == 2


def test_local_source_lists_and_reads(tmp_path: Any) -> None:
    (tmp_path / "GWP_UK3100_Out_Clients_20260327.out").write_bytes(b"A|B\n1|2\n")
    (tmp_path / "PREVIEW_GWP_UK3100_Out_Clients_20260327.out").write_bytes(b"draft")
    (tmp_path / "unrelated.txt").write_bytes(b"nope")

    source = LocalSource(tmp_path)
    drops = source.list_drops()

    assert [d.table for d in drops] == ["clients"]
    assert source.read(drops[0]) == b"A|B\n1|2\n"
