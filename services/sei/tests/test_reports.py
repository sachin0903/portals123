"""Drop-filename parsing. The names below are real ones from the p04 corpus."""

from __future__ import annotations

from datetime import date

import pytest

from sei_sync.reports import KNOWN_REPORTS, parse, table_name, to_utf8


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        ("Accounts", "accounts"),
        ("AccountOptionalFields", "account_optional_fields"),
        # Acronym runs must not shatter into single letters.
        ("EODPositions", "eod_positions"),
        ("EODPosValAggr", "eod_pos_val_aggr"),
        ("EODPositionsSupplemental", "eod_positions_supplemental"),
        ("FSLTaxData", "fsl_tax_data"),
        ("AssetInvClas", "asset_inv_clas"),
        ("ModelsIntExt", "models_int_ext"),
        ("TransactionDetailUpd", "transaction_detail_upd"),
        ("UserTeamRoleDetail", "user_team_role_detail"),
    ],
)
def test_table_name_handles_acronyms(report: str, expected: str) -> None:
    assert table_name(report) == expected


def test_parse_encrypted_drop() -> None:
    drop = parse("GWP_UK3100_Out_EODPosValAggr_20260327.out.pgp")
    assert drop is not None
    assert drop.group_id == "3100"
    assert drop.report == "EODPosValAggr"
    assert drop.table == "eod_pos_val_aggr"
    assert drop.file_date == date(2026, 3, 27)
    assert drop.encrypted is True
    assert drop.known is True
    assert drop.plaintext_filename == "GWP_UK3100_Out_EODPosValAggr_20260327.out"


def test_parse_plaintext_drop() -> None:
    """SEI's older i10 files arrived unencrypted; the same name minus .pgp."""
    drop = parse("GWP_UK3100_Out_Reference_20251028.out")
    assert drop is not None
    assert drop.encrypted is False
    assert drop.plaintext_filename == drop.filename


def test_preview_files_are_skipped() -> None:
    """PREVIEW_ copies are drafts superseded by the real file — ingesting them
    would double-count every report."""
    assert parse("PREVIEW_GWP_UK3100_Out_Accounts_20260327.out.pgp") is None


@pytest.mark.parametrize(
    "name",
    [
        "archive",
        "GWP_UK3100_Out_Accounts.out",  # no date
        "GWP_UK3100_Out_Accounts_2026032.out",  # short date
        "GWP_UK3100_In_Accounts_20260327.out",  # inbound, not a drop
        "Accounts_20260327.out",
        "",
    ],
)
def test_parse_rejects_non_drops(name: str) -> None:
    assert parse(name) is None


def test_parse_rejects_impossible_date() -> None:
    """A well-formed name with a nonsense date must not become date(2026, 13, 45)."""
    assert parse("GWP_UK3100_Out_Accounts_20261345.out.pgp") is None


def test_object_keys_partition_by_report_and_date() -> None:
    drop = parse("GWP_UK3100_Out_Clients_20260327.out.pgp")
    assert drop is not None
    assert drop.archive_key() == (
        "sei/sftp/clients/dt=2026-03-27/GWP_UK3100_Out_Clients_20260327.out.pgp"
    )
    # The staged copy drops .pgp — it is the decrypted CSV.
    assert drop.staged_key() == (
        "sei/staged/clients/dt=2026-03-27/GWP_UK3100_Out_Clients_20260327.out"
    )


def test_archive_and_staged_zones_do_not_overlap() -> None:
    """The models glob the staged zone. An encrypted original that landed under the
    same prefix would be matched by ``*.out`` globs on some engines and read as CSV, so
    the two zones are kept separate rather than distinguished by extension alone."""
    drop = parse("GWP_UK3100_Out_Clients_20260327.out.pgp")
    assert drop is not None
    assert not drop.staged_key().startswith("sei/sftp/")
    assert not drop.archive_key().startswith("sei/staged/")


def test_dt_partition_survives_the_env_removal() -> None:
    """The bucket is per environment, so the env segment went — but ``dt=`` must stay:
    it is the hive partition the raw models prune on and the cursor they read."""
    drop = parse("GWP_UK3100_Out_Clients_20260327.out.pgp")
    assert drop is not None
    assert "dt=2026-03-27" in drop.archive_key()
    assert "dt=2026-03-27" in drop.staged_key()
    assert "/i31/" not in drop.archive_key()


def test_unknown_report_is_parsed_but_flagged() -> None:
    """SEI adds feeds without telling us: ingest, but mark it for a model."""
    drop = parse("GWP_UK3100_Out_SomeNewFeed_20260327.out.pgp")
    assert drop is not None
    assert drop.known is False
    assert drop.table == "some_new_feed"


def test_known_reports_matches_the_corpus_count() -> None:
    assert len(KNOWN_REPORTS) == 47
    # The cherry-picked transformation SQL misspells this one as "upcomming".
    assert "UpcomingActivities" in KNOWN_REPORTS


class TestStagedZoneIsUtf8:
    """`to_utf8` is what lets every raw model read plain UTF-8.

    SEI's corpus is mixed and DuckDB's `read_csv` takes ONE encoding, so a reader setting cannot
    cover it: utf-8 refuses the Latin-1 files, latin-1 refuses the valid-UTF-8 files whose bytes
    fall in 0x80-0x9F, and a per-report setting is wrong because the same report has arrived both
    ways on different days. Normalising at the staging boundary is the only place one rule works.
    """

    def test_a_clean_file_passes_through_byte_identical(self) -> None:
        """The common case is every byte ASCII, and it must not be rewritten: an unnecessary
        transcode would rewrite every staged object and invalidate every model's fingerprint."""
        data = b"ID|TOWN\n1|Plain\n"
        assert to_utf8(data) == (data, "utf-8")

    def test_utf8_already_survives_untouched(self) -> None:
        """`CostsCharges` and `RoleDetail` are valid UTF-8 carrying a euro sign and double-encoded
        smart quotes — bytes in 0x80-0x9F that strict ISO-8859-1 rejects outright."""
        data = "ID|FEE\n1|\u20ac10 \u201cannual\u201d\n".encode()
        assert any(0x80 <= b <= 0x9F for b in data), "the C1 bytes are the point of this case"
        assert to_utf8(data) == (data, "utf-8")

    def test_a_latin1_file_is_transcoded(self) -> None:
        """A single 0xE9 is `é` in Latin-1 and not valid UTF-8 at all — the Jersey street names in
        `Clients` and `ContactDetails`."""
        transcoded, encoding = to_utf8(b"ID|TOWN\n1|Le M\xe9nage\n")
        assert encoding == "cp1252"
        assert transcoded.decode("utf-8") == "ID|TOWN\n1|Le Ménage\n"

    def test_cp1252_not_iso_8859_1(self) -> None:
        """CP1252 is the decode because it is the superset that also covers 0x80-0x9F. 0x93/0x94
        are smart quotes there and undefined C1 controls in ISO-8859-1, so a Latin-1 decode would
        keep them as control characters in what is meant to be text."""
        transcoded, _ = to_utf8(b"ID|NOTE\n1|\x93quoted\x94\n")
        assert transcoded.decode("utf-8") == "ID|NOTE\n1|\u201cquoted\u201d\n"

    def test_the_output_is_always_valid_utf8(self) -> None:
        """The contract the models depend on, asserted directly for every shape above."""
        for data in (b"a|b\n", "\u20ac".encode(), b"\xe9", b"\x93\x94", b"\xff"):
            transcoded, _ = to_utf8(data)
            transcoded.decode("utf-8")  # raises if the contract is broken
