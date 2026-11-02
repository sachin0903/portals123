"""SEI drop filenames -> (report, date) and the object keys they land under.

Every file in the drop is named::

    GWP_UK{group}_Out_{ReportName}_{YYYYMMDD}.out[.pgp]

``group`` is SEI's delivery-group id (3100 across our whole p04 corpus), NOT a
firm id — the firm scoping lives *inside* the files, in each row's FIRM_ID. That
is exactly the shape the tenant fan-out assumes: whole-group data lands core-side
and each tenant's slice is selected by firm/subfirm/DSR (docs/storage.md §9).

SEI also writes ``PREVIEW_``-prefixed copies of most reports; those are drafts
that get superseded by the real file, so they are always skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

# GWP_UK3100_Out_EODPosValAggr_20251218.out.pgp
_FILENAME = re.compile(
    r"^GWP_UK(?P<group>\d+)_Out_(?P<report>[A-Za-z0-9]+)_(?P<date>\d{8})\.out(?P<pgp>\.pgp)?$"
)

# Acronym-aware CamelCase -> snake_case: EODPosValAggr -> eod_pos_val_aggr,
# FSLTaxData -> fsl_tax_data, AccountOptionalFields -> account_optional_fields.
_ACRONYM_THEN_WORD = re.compile(r"(?<=[A-Z0-9])(?=[A-Z][a-z])")
_WORD_THEN_CAPITAL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# The 47 reports SEI delivers today (verified against the p04 corpus). Unknown
# report names are still ingested — SEI adds feeds without telling us — but they
# are logged, because a new report usually needs a model to be useful.
KNOWN_REPORTS: frozenset[str] = frozenset(
    {
        "AccountOptionalFields",
        "Accounts",
        "ActiveBlocks",
        "AssetInvClas",
        "AssetOptFields",
        "Assets",
        "ClientAccountLink",
        "Clients",
        "ContactDetails",
        "CostsCharges",
        "CustodyNostroPositions",
        "EODChangedPositions",
        "EODPosValAggr",
        "EODPositions",
        "EODPositionsSupplemental",
        "FSLTaxData",
        "FeeComputation",
        "FeeGroup",
        "FeePackageRule",
        "FeePackageUsage",
        "FundCutoff",
        "FxForward",
        "InterestRateTypes",
        "InvestmentDecisionMaker",
        "ModelAlloc",
        "ModelsIntExt",
        "PaidTo",
        "PartyOptionalFields",
        "PortfolioGroups",
        "PortfolioPerformance",
        "ProductGovernance",
        "RecCashActivities",
        "Reference",
        "Relationships",
        "RoleDetail",
        "StatementEvents",
        "StatementInstance",
        "StatementPackage",
        "Taxlots",
        "TransactionDetail",
        "TransactionDetailUpd",
        "TransactionHeader",
        "TransactionHeaderUpd",
        "TransactionRegulatory",
        "UpcomingActivities",
        "UserDetail",
        "UserTeamRoleDetail",
    }
)

# The reports are pipe-delimited with a header row and a record-count trailer.
DELIMITER = "|"
TRAILER_PREFIX = b"Total Number of Records:"


class TrailerMismatch(RuntimeError):
    """The declared record count disagrees with the rows delivered."""


def split_trailer(data: bytes) -> tuple[bytes, int]:
    """Strip SEI's ``Total Number of Records: |N`` trailer and verify N.

    Every report ends with that line, and N is exactly the number of data rows
    (excluding the header). Checking it is the only integrity signal the feed
    gives us, and it catches the failure mode a file transfer actually has — a
    truncated or partially-written drop, which would otherwise land silently and
    look like a quiet day.

    Returns the CSV without the trailer, plus the verified row count.
    """
    body = data.rstrip(b"\r\n")
    newline = body.rfind(b"\n")
    last = body[newline + 1 :]
    if not last.startswith(TRAILER_PREFIX):
        raise TrailerMismatch(f"no record-count trailer found; last line was {last[:60]!r}")

    declared_text = last[len(TRAILER_PREFIX) :].strip().lstrip(DELIMITER.encode()).strip()
    try:
        declared = int(declared_text)
    except ValueError as exc:
        raise TrailerMismatch(f"unparseable record count {declared_text[:40]!r}") from exc

    csv = body[:newline] if newline != -1 else b""
    csv = csv.rstrip(b"\r\n")
    if not csv:
        # Header-only file: SEI still sends one for a report with no rows.
        actual = 0
    else:
        actual = csv.count(b"\n")  # lines minus the header
    if actual != declared:
        raise TrailerMismatch(f"trailer declares {declared} records but {actual} rows delivered")
    return csv + b"\n", declared


def to_utf8(data: bytes) -> tuple[bytes, str]:
    """Transcode a drop file to UTF-8. Returns (bytes, the encoding it was read as).

    THE STAGED ZONE IS UTF-8, and this is what makes that true. It belongs here rather than in the
    models because SEI's corpus is genuinely MIXED and no single reader setting covers it:

    * `Clients` and `ContactDetails` have arrived as Latin-1 — single `0xE8`/`0xE9` bytes (`è`, `é`,
      in Jersey street names) that are not valid UTF-8 at all;
    * `CostsCharges` and `RoleDetail` arrive as valid UTF-8 every day, but carrying bytes in
      `0x80`-`0x9F` (`0xE2 0x82 0xAC` = €, and the `0xC3 0xA2 0xE2 0x82 0xAC …` sequences that
      are smart quotes double-encoded upstream).

    DuckDB's `read_csv` offers utf-8, utf-16 and latin-1 and nothing else, so **both** choices
    fail: utf-8 refuses the first group (invalid continuation byte), and latin-1 refuses the
    second, because ISO-8859-1 leaves `0x80`-`0x9F` as C1 controls. Declaring one per report does
    not work either — `Clients` was Latin-1 on one day and clean ASCII on the next, so it is not a
    property of the report.

    So the boundary normalises instead. The staged copy is already a DERIVED artefact — the trailer
    is stripped from it — and the byte-exact evidence lives in the `sftp/` archive, untouched.
    UTF-8 first, because a file that is already valid UTF-8 must pass through byte-identical; then
    CP1252 rather than ISO-8859-1, because it is the superset that also decodes `0x80`-`0x9F` and
    agrees with Latin-1 above `0xA0`, which is where the accented characters actually are.

    Note what this does NOT fix: `RoleDetail` carries U+FFFD (`0xEF 0xBF 0xBD`) 244 times. That is a
    replacement character SEI's own pipeline wrote before sending, so the original character is
    already gone and no decoding recovers it.
    """
    try:
        data.decode("utf-8")
        return data, "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252").encode("utf-8"), "cp1252"


def table_name(report: str) -> str:
    """``EODPosValAggr`` -> ``eod_pos_val_aggr`` (the raw table / staged prefix)."""
    spaced = _ACRONYM_THEN_WORD.sub("_", report)
    return _WORD_THEN_CAPITAL.sub("_", spaced).lower()


@dataclass(frozen=True)
class DropFile:
    """One file in an SEI drop.

    ``size`` and ``modified_at`` are what the *source* reports about the file and are
    None until something has listed it with attributes (SFTP ``listdir_attr``, a local
    ``stat``). They are recorded on the ``raw.files`` ledger, so a short file can be
    told apart from a file SEI rewrote.
    """

    filename: str
    group_id: str
    report: str
    file_date: date
    encrypted: bool
    size: int | None = None
    modified_at: datetime | None = None

    @property
    def table(self) -> str:
        return table_name(self.report)

    @property
    def known(self) -> bool:
        return self.report in KNOWN_REPORTS

    @property
    def plaintext_filename(self) -> str:
        return self.filename.removesuffix(".pgp")

    def archive_key(self) -> str:
        """Where the file SEI sent is archived, byte-for-byte. Write-once.

        ``sei/sftp/{feed}/dt={YYYY-MM-DD}/{object}`` — "sftp" because that is what the
        zone holds: exactly what came off SEI's SFTP drop, still PGP-encrypted.

        No environment segment: the bucket is per environment (one RustFS/S3 tenant per
        deployment), so an i31 key and a p04 key cannot collide in the first place, and
        stamping the env into the path only made every reader interpolate a value it
        already knew from the bucket it was reading.
        """
        return f"sei/sftp/{self.table}/dt={self.file_date.isoformat()}/{self.filename}"

    def staged_key(self) -> str:
        """Where the decrypted, pipe-delimited CSV lands for the models to read.

        A sibling zone rather than the same prefix: the models glob this one, and the
        two zones have different lifetimes — the archive is regulated evidence of what
        the custodian delivered, the staged copy is a re-derivable convenience.
        """
        return f"sei/staged/{self.table}/dt={self.file_date.isoformat()}/{self.plaintext_filename}"


def parse(filename: str) -> DropFile | None:
    """Parse a drop filename, or None when it isn't one we ingest.

    Returns None for PREVIEW_ drafts, directories and anything not matching the
    GWP naming — the remote directory also holds ``archive/`` and stray files.
    """
    if filename.startswith("PREVIEW_"):
        return None
    match = _FILENAME.match(filename)
    if match is None:
        return None
    stamp = match["date"]
    try:
        file_date = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
    except ValueError:
        return None
    return DropFile(
        filename=filename,
        group_id=match["group"],
        report=match["report"],
        file_date=file_date,
        encrypted=bool(match["pgp"]),
    )
