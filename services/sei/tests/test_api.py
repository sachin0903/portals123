"""The SWP API sync: windowing, paging, flattening and the cursor's lookback.

Everything here runs against fakes. The real constraints these encode were measured against
i31 on 04 Aug 2026 (see ``sei_sync.api``'s docstring) — a 30-day cap, a yesterday horizon,
mandatory account scoping and no last-modified filter — and this is what stops them being
re-learned from a 400 in production.
"""

from __future__ import annotations

import base64
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from sei_sync.api import (
    ACCOUNT_TEMPLATES_PATH,
    CLIENT_TEMPLATES_PATH,
    DEFAULT_EMPLOYEE_SERVICE_ID,
    EMPLOYEE_USERS_PATH,
    FIRM_DEFAULT_ATTR_PATH,
    HISTORY_SAFETY_DAYS,
    LIST_DOCUMENTS_PATH,
    MAX_HISTORY_MONTHS,
    MAX_PAGE_SIZE,
    MAX_WINDOW_DAYS,
    RETRIEVE_DOCUMENT_PATH,
    AccountRef,
    SeiApi,
    SeiApiError,
    SyncStats,
    earliest_start,
    horizon,
    windows,
)
from sei_sync.api_rows import SCHEMA, flatten
from sei_sync.pull import _processing_org_id, fetch_employee_users
from sei_sync.settings import SeiSettings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "models"))
import api_transactions  # noqa: E402

# One real row, copied verbatim from the i31 response for firm 3106 / MSP000804.
REAL_ROW: dict[str, Any] = {
    "_firm_id": "3106",
    "accountDetails": {
        "accountNumber": "MSP000804",
        "externalReferenceId": None,
        "accountName": "GIA - Daniscan Future Limited",
        "accountType": 157,
        "accountTypeDescription": "Investment Account",
    },
    "transactionDetails": {
        "transactionId": 380294840,
        "transactionDescription": "Cash Dividend 1.6 USD Linde PLC For 83 Shares",
        "portfolioDetails": {
            "portfolioId": 58342941,
            "portfolioName": "Portfolio-01 (Capital)",
            "portfolioType": 189,
            "portfolioTypeDescription": "CAPITAL",
        },
        "transactionType": "Cash Dividend",
        "submittedBy": "System",
        "submittedByTeam": None,
        "submittedDate": "2026-03-27",
        "transactionDate": "2026-03-27",
        "tradeDate": "2026-03-26",
        "settlementDate": "2026-03-26",
        "statementDate": "2026-03-26",
        "localTransactionValue": "132.80",
        "localCurrencyCode": "USD",
        "reversalFlag": False,
        "adjustmentFlag": False,
        "suppressionFlag": False,
        "assetDetails": {
            "globalAssetId": "554169822",
            "instrumentId": "554378470",
            "sedol": "BNZHB81",
            "isin": None,
            "cusip": None,
            "ticker": None,
        },
        "activityId": 1275868636,
        "orderId": None,
        "baseCurrency": "GBP",
        "localNetTransactionAmount": "132.80",
        "baseNetTransactionAmount": "99.32",
        "netChargesAmount": "0.00",
        "netTaxesAmount": "0.00",
        "transactionQuantity": None,
        "localTransactionGrossAmount": "132.80",
        "localBookValue": None,
        "baseBookValue": None,
    },
}

NOW = datetime(2026, 8, 4, 6, 0, tzinfo=UTC)


# --- windowing ---------------------------------------------------------------------


def test_windows_never_exceed_seis_thirty_day_cap() -> None:
    """SEI answers a wider range with 'should be between 0 to 30 days', so the split has to
    happen before the call, not after the rejection."""
    spans = list(windows(date(2025, 10, 23), date(2026, 8, 3)))
    assert spans
    for start, end in spans:
        assert (end - start).days < MAX_WINDOW_DAYS


def test_windows_are_contiguous_and_cover_the_range_exactly() -> None:
    """A gap between windows is a silently missing day of transactions."""
    start, end = date(2026, 1, 1), date(2026, 4, 15)
    spans = list(windows(start, end))
    assert spans[0][0] == start
    assert spans[-1][1] == end
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:], strict=False):
        assert (next_start - prev_end).days == 1


def test_an_empty_range_yields_no_windows() -> None:
    """The normal state of an incremental run that is already current — not an error."""
    assert list(windows(date(2026, 8, 3), date(2026, 8, 1))) == []


def test_a_single_day_is_one_window() -> None:
    assert list(windows(date(2026, 8, 3), date(2026, 8, 3))) == [
        (date(2026, 8, 3), date(2026, 8, 3))
    ]


def test_horizon_is_yesterday_because_today_is_refused_as_future() -> None:
    assert horizon(date(2026, 8, 4)) == date(2026, 8, 3)


# --- the 36-month floor ------------------------------------------------------------


def test_earliest_start_is_thirty_six_months_back_inside_the_limit() -> None:
    """Measured against i31 on 04 Aug 2026 by bisection: 2023-08-12 accepted, 2023-07-31
    refused with 'Start Date must be within last 36 months' (422)."""
    floor = earliest_start(date(2026, 8, 4))
    assert date(2023, 8, 4) < floor <= date(2023, 8, 12)
    assert MAX_HISTORY_MONTHS == 36


def test_earliest_start_uses_calendar_months_not_1095_days() -> None:
    """SEI's message says months. Across leap years the two differ, and the difference
    lands exactly on the boundary that 422s."""
    assert earliest_start(date(2026, 1, 15)) == date(2023, 1, 15) + timedelta(
        days=HISTORY_SAFETY_DAYS
    )


@pytest.mark.parametrize("today", [date(2027, 3, 31), date(2024, 2, 29), date(2026, 5, 31)])
def test_earliest_start_survives_a_day_that_does_not_exist_36_months_back(today: date) -> None:
    """31 May minus 36 months is fine, but the arithmetic must not construct 31 February on
    the way — it walks back to the last valid day rather than raising."""
    assert earliest_start(today) < today


def test_an_open_date_beyond_the_floor_is_clamped_not_rejected() -> None:
    """The floor SLIDES. An open date that was inside the window ages out of it, and
    422-ing every account then is strictly worse than reading as far back as allowed."""
    start, end = api_transactions._range(
        None, _settings(), today=date(2026, 8, 4), opened=date(2020, 1, 1)
    )
    assert start == earliest_start(date(2026, 8, 4))
    assert end == date(2026, 8, 3)


def test_an_open_date_inside_the_floor_is_used_verbatim() -> None:
    start, _ = api_transactions._range(
        None, _settings(), today=date(2026, 8, 4), opened=date(2025, 10, 23)
    )
    assert start == date(2025, 10, 23)


def test_a_stale_cursor_lookback_is_also_clamped() -> None:
    """The lookback subtracts from the cursor, so a very old cursor plus a wide lookback can
    reach past the floor too — the clamp is applied to both paths, not just the backfill."""
    settings = _settings(api_lookback_days=400)
    start, _ = api_transactions._range(date(2023, 10, 1), settings, today=date(2026, 8, 4))
    assert start == earliest_start(date(2026, 8, 4))


# --- the client certificate --------------------------------------------------------


def test_pfx_base64_is_the_certificate() -> None:
    """Base64 is the ONLY form. A `SEI_PFX_PATH` fallback existed until Aug 2026 and is gone:
    a deployment sets one secret rather than mounting a file, and two forms meant two precedence
    rules to keep in step with `pkgs/sei` — which had already drifted."""
    settings = SeiSettings(pfx_base64=base64.b64encode(b"pkcs12-bytes").decode())
    assert settings.pfx_material() == b"pkcs12-bytes"


def test_malformed_pfx_base64_is_named_not_passed_to_tls() -> None:
    """A partial certificate surfaces as an opaque handshake failure requests later, so it
    is rejected here with the variable's name in the message."""
    with pytest.raises(RuntimeError, match="SEI_PFX_BASE64"):
        SeiSettings(pfx_base64="not valid base64!!").pfx_material()


def test_no_certificate_names_the_one_variable_to_set() -> None:
    with pytest.raises(RuntimeError, match="SEI_PFX_BASE64"):
        SeiSettings(pfx_base64="").pfx_material()


def test_a_file_path_is_no_longer_a_setting() -> None:
    """The PGP key keeps its path fallback (it is developer-facing and a 9KB armoured key is
    awkward to paste); the certificate does not."""
    assert "pfx_path" not in SeiSettings.model_fields
    assert "pgp_private_key_path" in SeiSettings.model_fields


def test_the_client_refuses_to_start_without_a_certificate() -> None:
    """Checked before the first request, so the failure names configuration rather than
    arriving as a TLS error mid-backfill."""
    from sei_sync.api import SeiApi, SeiApiError

    settings = _settings(
        oauth_username="u",
        oauth_password="p",
        appkey="k",
        pfx_password="pw",
        pfx_base64="",
    )
    with pytest.raises(SeiApiError, match="SEI_PFX_BASE64"):
        SeiApi(settings)


# --- flattening --------------------------------------------------------------------


def test_a_real_response_row_flattens_to_the_declared_schema() -> None:
    table = flatten([REAL_ROW], fetched_at=NOW)
    assert table.schema == SCHEMA
    row = table.to_pylist()[0]
    assert row["transaction_id"] == 380294840
    assert row["firm_id"] == "3106"
    assert row["account_number"] == "MSP000804"
    assert row["transaction_type"] == "Cash Dividend"
    assert row["portfolio_id"] == 58342941
    assert row["sedol"] == "BNZHB81"
    assert row["adjustment_flag"] is False


def test_money_arrives_as_a_string_and_lands_as_a_decimal() -> None:
    """SEI sends "132.80", not 132.8. Left to inference the column type would drift with
    whatever the first batch held — the same trap the SFTP raw layer declares its way out of."""
    row = flatten([REAL_ROW], fetched_at=NOW).to_pylist()[0]
    assert str(row["local_transaction_value"]) == "132.8000"
    assert str(row["base_net_transaction_amount"]) == "99.3200"
    assert str(row["net_charges_amount"]) == "0.0000"
    assert row["local_book_value"] is None  # null stays null, not zero


def test_api_dates_are_iso_unlike_the_drops_yyyymmdd() -> None:
    row = flatten([REAL_ROW], fetched_at=NOW).to_pylist()[0]
    assert row["transaction_date"] == date(2026, 3, 27)
    assert row["trade_date"] == date(2026, 3, 26)
    # The booking date: the only field that shows an amendment arriving late.
    assert row["submitted_date"] == date(2026, 3, 27)


def test_a_row_without_a_transaction_id_is_dropped_not_defaulted() -> None:
    """It is the merge key. A NULL there collapses every such row onto one."""
    bad = {"_firm_id": "3106", "transactionDetails": {"transactionDate": "2026-03-27"}}
    assert flatten([bad], fetched_at=NOW).num_rows == 0
    assert flatten([bad, REAL_ROW], fetched_at=NOW).num_rows == 1


def test_an_empty_response_still_carries_the_declared_schema() -> None:
    """A quiet window must not change the table's shape and trigger a rebuild."""
    table = flatten([], fetched_at=NOW)
    assert table.num_rows == 0
    assert table.schema == SCHEMA


def test_unparseable_dates_and_money_become_null_not_zero() -> None:
    row = dict(REAL_ROW)
    row["transactionDetails"] = {
        **REAL_ROW["transactionDetails"],
        "transactionDate": "27/03/2026",
        "localTransactionValue": "n/a",
    }
    out = flatten([row], fetched_at=NOW).to_pylist()[0]
    assert out["transaction_date"] is None
    assert out["local_transaction_value"] is None


# --- the model's window arithmetic --------------------------------------------------


def _settings(**over: Any) -> SeiSettings:
    base: dict[str, Any] = {
        "api_base_url": "https://test.mtls.api.seic.com/api/v1/swp",
        "api_lookback_days": 30,
    }
    return SeiSettings(**{**base, **over})


def test_a_first_run_without_an_open_date_stays_bounded() -> None:
    """An unbounded first run is tens of thousands of requests. No open date means the lookback."""
    start, end = api_transactions._range(None, _settings(), today=date(2026, 8, 4))
    assert end == date(2026, 8, 3)
    assert (end - start).days == 30


def test_a_first_run_honours_the_account_open_date() -> None:
    start, end = api_transactions._range(
        None, _settings(), today=date(2026, 8, 4), opened=date(2025, 10, 23)
    )
    assert (start, end) == (date(2025, 10, 23), date(2026, 8, 3))
    assert len(list(windows(start, end))) == 10


def test_an_incremental_run_overlaps_its_own_cursor() -> None:
    """SEI cannot filter by last-modified, so a correction booked today against an older
    transaction is unreachable by a strict `> cursor` read. The overlap is the only way to
    see it; merge on transaction_id makes re-reading free."""
    start, end = api_transactions._range(date(2026, 7, 20), _settings(), today=date(2026, 8, 4))
    assert start == date(2026, 6, 20)
    assert end == date(2026, 8, 3)


def test_an_incremental_run_continues_the_day_after_the_cursor() -> None:
    """The default overlap is none. A day already covered is not asked for again."""
    start, end = api_transactions._range(
        date(2026, 8, 2), _settings(api_lookback_days=0), today=date(2026, 8, 4)
    )
    assert (start, end) == (date(2026, 8, 3), date(2026, 8, 3))


def test_a_caught_up_book_asks_for_nothing() -> None:
    start, end = api_transactions._range(
        date(2026, 8, 3), _settings(api_lookback_days=0), today=date(2026, 8, 4)
    )
    assert start > end
    assert list(windows(start, end)) == []


def test_a_cursor_ahead_of_the_horizon_produces_an_empty_range() -> None:
    """Belt and braces on the "nothing to do" path — windows() must yield nothing rather
    than the client requesting a backwards range."""
    start, end = api_transactions._range(
        date(2026, 8, 4), _settings(api_lookback_days=0), today=date(2026, 8, 4)
    )
    assert start > end
    assert list(windows(start, end)) == []


def test_a_string_cursor_is_accepted_as_well_as_a_date() -> None:
    """interlace derives the cursor from the warehouse, and a DATE column can come back as
    either depending on the engine."""
    assert api_transactions._range("2026-07-20", _settings(), today=date(2026, 8, 4)) == (
        date(2026, 6, 20),
        date(2026, 8, 3),
    )


# --- account fan-out ---------------------------------------------------------------


class _Handle:
    """Stand-in for interlace's RelationHandle over raw.accounts."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        import pyarrow as pa

        self._table = (
            pa.Table.from_pylist(rows) if rows else pa.table({"FIRM_ID": [], "ACCOUNT_NUMBER": []})
        )

    def table(self) -> Any:
        return self._table


def test_accounts_are_deduplicated_across_portfolio_rows() -> None:
    """The drop carries one row per account PORTFOLIO, so an account appears many times.
    Without the de-dup the fan-out requests the same account once per portfolio."""
    handle = _Handle(
        [
            {"FIRM_ID": "3102", "ACCOUNT_NUMBER": "100020"},
            {"FIRM_ID": "3102", "ACCOUNT_NUMBER": "100020"},
            {"FIRM_ID": "3102", "ACCOUNT_NUMBER": "100021"},
        ]
    )
    assert api_transactions._accounts(handle, None) == [
        AccountRef("3102", "100020"),
        AccountRef("3102", "100021"),
    ]


def test_the_same_account_number_under_two_firms_stays_two_accounts() -> None:
    """firmId is required alongside accountNumber, so the pair is the identity."""
    handle = _Handle(
        [
            {"FIRM_ID": "3102", "ACCOUNT_NUMBER": "100020"},
            {"FIRM_ID": "3106", "ACCOUNT_NUMBER": "100020"},
        ]
    )
    assert len(api_transactions._accounts(handle, None)) == 2


def test_an_unseen_firm_is_backfilled_and_a_known_one_uses_the_cursor() -> None:
    """The cursor is one date for the whole table. A firm added later must not inherit it.

    An account with no rows of its own, inside a firm that already has some, stays on the
    lookback. Backfilling every such account is a three-year pull of the firms already loaded.
    """
    settings = _settings(api_lookback_days=30)
    accounts = [
        AccountRef("3116", "CRFP00000", date(2026, 7, 1)),
        AccountRef("3106", "known"),
        AccountRef("3106", "never-fetched"),
    ]
    groups = api_transactions._fetch_groups(
        accounts,
        date(2026, 10, 1),
        settings,
        today=date(2026, 10, 3),
        seen_firms={"3106"},
    )
    by_account = {a.account_number: (start, end) for group, start, end in groups for a in group}
    assert by_account["CRFP00000"] == (date(2026, 7, 1), date(2026, 10, 2))
    assert by_account["known"] == (date(2026, 9, 1), date(2026, 10, 2))
    assert by_account["never-fetched"] == (date(2026, 9, 1), date(2026, 10, 2))


def test_an_account_open_date_beats_the_configured_backfill() -> None:
    """The book opens in 2024. Requesting from 2023-09-01 walks months SEI has nothing for."""
    settings = _settings()
    accounts = [AccountRef("3102", "a", date(2024, 6, 28))]
    groups = api_transactions._fetch_groups(
        accounts, None, settings, today=date(2026, 10, 14), seen_firms=set()
    )
    assert len(groups) == 1
    assert groups[0][1] == date(2024, 6, 28)
    assert groups[0][2] == date(2026, 10, 13)


def test_each_firm_starts_at_its_own_open_date() -> None:
    """A firm opened in 2026 is not walked back to the oldest account on the platform."""
    settings = _settings()
    accounts = [
        AccountRef("3102", "old", date(2024, 6, 28)),
        AccountRef("3116", "new", date(2026, 7, 2)),
    ]
    groups = api_transactions._fetch_groups(
        accounts, None, settings, today=date(2026, 10, 14), seen_firms=set()
    )
    by_account = {a.account_number: start for group, start, _end in groups for a in group}
    assert by_account == {"old": date(2024, 6, 28), "new": date(2026, 7, 2)}


def test_a_firm_added_later_starts_at_its_open_date_not_the_lookback() -> None:
    settings = _settings(api_lookback_days=30)
    accounts = [
        AccountRef("3106", "known", date(2024, 11, 6)),
        AccountRef("3118", "added", date(2026, 6, 3)),
    ]
    groups = api_transactions._fetch_groups(
        accounts,
        date(2026, 10, 1),
        settings,
        today=date(2026, 10, 14),
        seen_firms={"3106"},
    )
    by_account = {a.account_number: (start, end) for group, start, end in groups for a in group}
    assert by_account["known"] == (date(2026, 9, 1), date(2026, 10, 13))
    assert by_account["added"] == (date(2026, 6, 3), date(2026, 10, 13))


def test_an_open_date_before_the_floor_is_clamped() -> None:
    """Inception reaches 2016. The open date is the one we use, and even that clamps."""
    settings = _settings()
    accounts = [AccountRef("3102", "a", date(2016, 11, 23))]
    groups = api_transactions._fetch_groups(
        accounts, None, settings, today=date(2026, 10, 14), seen_firms=set()
    )
    assert groups[0][1] == earliest_start(date(2026, 10, 14))


def test_the_earliest_open_date_on_an_account_is_kept() -> None:
    """A portfolio row can repeat the account. The earlier open date is the start."""
    handle = _Handle(
        [
            {"FIRM_ID": "3102", "ACCOUNT_NUMBER": "a", "ACCOUNT_OPEN_DATE": date(2025, 1, 2)},
            {"FIRM_ID": "3102", "ACCOUNT_NUMBER": "a", "ACCOUNT_OPEN_DATE": date(2024, 6, 28)},
        ]
    )
    assert api_transactions._accounts(handle, None) == [AccountRef("3102", "a", date(2024, 6, 28))]


def test_the_scan_mark_beats_the_newest_transaction_date() -> None:
    """A quiet book leaves max(transaction_date) on the last movement. The next run
    has to start from how far the scan reached, or it re-walks the empty months."""
    stored = api_transactions.StoredSync(
        firms={"3116"},
        covered_through=date(2026, 10, 2),
        latest_transaction=date(2024, 7, 15),
    )
    assert api_transactions._anchor(date(2024, 7, 15), stored) == date(2026, 10, 2)


def test_a_rebuild_continues_from_the_latest_stored_transaction() -> None:
    """No covered_through yet, and the injected cursor is empty because the new table is.
    The latest stored transaction date is the start, not the three-year backfill."""
    stored = api_transactions.StoredSync(
        firms={"3116"},
        covered_through=None,
        latest_transaction=date(2026, 9, 30),
    )
    assert api_transactions._anchor(None, stored) == date(2026, 9, 30)
    assert api_transactions._anchor(None, None) is None


def test_a_rebuilt_table_keeps_stored_rows_and_prefers_the_new_copy(monkeypatch) -> None:
    existing = pa.table(
        {"transaction_id": [1, 2], "note": ["old", "old"], "covered_through": [None, None]}
    )
    fresh = pa.table({"transaction_id": [2], "note": ["new"], "covered_through": [None]})
    monkeypatch.setattr(api_transactions, "_existing_rows", lambda: existing)
    stored = api_transactions.StoredSync(
        firms={"3116"}, covered_through=None, latest_transaction=date(2026, 9, 30)
    )
    out = api_transactions._publish(
        fresh, cursor=None, stored=stored, mark=date(2026, 10, 2), advance=True
    )
    got = {row["transaction_id"]: row for row in out.to_pylist()}
    assert set(got) == {1, 2}
    assert got[1]["note"] == "old"
    assert got[2]["note"] == "new"
    assert got[2]["covered_through"] == date(2026, 10, 2)


def test_a_rebuild_accepts_the_warehouse_column_nullability() -> None:
    """DuckDB exports every column nullable. concat_tables refuses that against the schema."""
    existing = pa.table(
        {
            "transaction_id": pa.array([1, 2], type=pa.int64()),
            "note": pa.array(["old", "old"], type=pa.large_string()),
        }
    ).cast(
        pa.schema(
            [
                pa.field("transaction_id", pa.int64(), nullable=True),
                pa.field("note", pa.large_string()),
            ]
        )
    )
    fresh = pa.Table.from_arrays(
        [pa.array([2], type=pa.int64()), pa.array(["new"], type=pa.string())],
        schema=pa.schema(
            [
                pa.field("transaction_id", pa.int64(), nullable=False),
                pa.field("note", pa.string()),
            ]
        ),
    )
    out = api_transactions._prefer_fresh(existing, fresh)
    assert out.schema == fresh.schema
    got = {row["transaction_id"]: row["note"] for row in out.to_pylist()}
    assert got == {1: "old", 2: "new"}


def test_a_quiet_scan_moves_the_mark_on_one_stored_row(monkeypatch) -> None:
    sample = pa.table({"transaction_id": [7], "covered_through": [date(2026, 9, 1)]})
    monkeypatch.setattr(api_transactions, "_one_row", lambda: sample)
    fresh = flatten([], fetched_at=NOW)
    out = api_transactions._publish(
        fresh, cursor=date(2026, 10, 1), stored=None, mark=date(2026, 10, 2), advance=True
    )
    assert out.column("transaction_id").to_pylist() == [7]
    assert out.column("covered_through").to_pylist() == [date(2026, 10, 2)]


def test_a_failed_scan_does_not_clear_the_mark() -> None:
    """NULL is a write. Stamping it would clear covered_through on every row this window updated."""
    fresh = pa.table({"transaction_id": [7], "covered_through": [date(2026, 9, 1)]})
    out = api_transactions._publish(
        fresh, cursor=date(2026, 10, 1), stored=None, mark=None, advance=False
    )
    assert "covered_through" not in out.column_names


def test_an_unreadable_history_does_not_widen_the_pull() -> None:
    settings = _settings(api_lookback_days=30)
    accounts = [AccountRef("3116", "CRFP00000"), AccountRef("3106", "known")]
    groups = api_transactions._fetch_groups(
        accounts,
        date(2026, 10, 1),
        settings,
        today=date(2026, 10, 3),
        seen_firms=None,
    )
    assert len(groups) == 1
    group, start, end = groups[0]
    assert [a.account_number for a in group] == ["CRFP00000", "known"]
    assert (start, end) == (date(2026, 9, 1), date(2026, 10, 2))


def test_an_empty_firm_list_requests_nobody() -> None:
    handle = _Handle([{"FIRM_ID": "3102", "ACCOUNT_NUMBER": "a"}])
    assert api_transactions._accounts(handle, []) == []


def test_firm_ids_narrows_the_fan_out() -> None:
    handle = _Handle(
        [
            {"FIRM_ID": "3102", "ACCOUNT_NUMBER": "a"},
            {"FIRM_ID": "3106", "ACCOUNT_NUMBER": "b"},
        ]
    )
    got = api_transactions._accounts(handle, ["3106"])
    assert got == [AccountRef("3106", "b")]


def test_blank_and_missing_identifiers_are_skipped() -> None:
    handle = _Handle(
        [
            {"FIRM_ID": "3102", "ACCOUNT_NUMBER": None},
            {"FIRM_ID": None, "ACCOUNT_NUMBER": "x"},
            {"FIRM_ID": "3102", "ACCOUNT_NUMBER": "ok"},
        ]
    )
    assert api_transactions._accounts(handle, None) == [AccountRef("3102", "ok")]


def test_an_absent_upstream_is_no_accounts_not_a_crash() -> None:
    assert api_transactions._accounts(None, None) == []


def test_an_upstream_missing_the_columns_degrades_to_no_accounts() -> None:
    """Better a loud warning and zero rows than an exception that fails the whole run."""
    handle = _Handle([{"SOMETHING_ELSE": "1"}])
    assert api_transactions._accounts(handle, None) == []


# --- paging ------------------------------------------------------------------------


class _FakeResponse:
    def __init__(
        self, payload: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self._payload = payload
        self.status_code = status
        self.text = ""
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeApi:
    """Exercises SeiApi.transactions' paging loop without auth or a socket."""

    def __init__(self, pages: list[list[dict[str, Any]]], total_pages: int | None = None) -> None:
        self.pages = pages
        self.total_pages = len(pages) if total_pages is None else total_pages
        self.requested: list[int] = []

    async def _get_page(self, account: Any, start: Any, end: Any, page: int, *, stats: Any) -> Any:
        self.requested.append(page)
        stats.requests += 1
        rows = self.pages[page - 1] if page <= len(self.pages) else []
        return {"data": {"listOfTransactionDetails": rows, "totalPageCount": self.total_pages}}


@pytest.mark.asyncio
async def test_paging_follows_total_page_count() -> None:
    from sei_sync.api import SeiApi

    fake = _FakeApi([[{"a": 1}] * MAX_PAGE_SIZE, [{"b": 2}] * 7])
    stats = SyncStats()
    rows = await SeiApi.transactions(
        fake, AccountRef("3106", "MSP000804"), date(2026, 3, 1), date(2026, 3, 27), stats=stats
    )
    assert len(rows) == MAX_PAGE_SIZE + 7
    assert fake.requested == [1, 2]


@pytest.mark.asyncio
async def test_paging_stops_on_an_empty_batch_even_if_more_pages_are_claimed() -> None:
    """A response that promises pages and delivers none would otherwise spin forever."""
    from sei_sync.api import SeiApi

    fake = _FakeApi([[{"a": 1}], []], total_pages=99)
    stats = SyncStats()
    rows = await SeiApi.transactions(
        fake, AccountRef("3106", "x"), date(2026, 3, 1), date(2026, 3, 2), stats=stats
    )
    assert len(rows) == 1
    assert fake.requested == [1, 2]


class _RetryClient:
    """Returns a scripted sequence of statuses, then 200s forever."""

    def __init__(self, statuses: list[int], retry_after: str | None = None) -> None:
        self.statuses = list(statuses)
        self.retry_after = retry_after
        self.calls = 0

    async def get(self, url: str, params: Any, headers: Any) -> Any:
        self.calls += 1
        status = self.statuses.pop(0) if self.statuses else 200
        payload = {"data": {"listOfTransactionDetails": [], "totalPageCount": 0}}
        return _FakeResponse(
            payload, status, headers={"Retry-After": self.retry_after} if self.retry_after else {}
        )


def _api_with(client: Any, recorder: Any = None) -> Any:
    """A SeiApi wired to a fake transport, bypassing __aenter__ (no TLS, no auth)."""
    api = SeiApi(
        _settings(
            oauth_username="u",
            oauth_password="p",
            appkey="k",
            pfx_password="pw",
            pfx_base64=base64.b64encode(b"x").decode(),
        ),
        recorder=recorder,
    )
    api._client = client
    api._token = "token"
    return api


@pytest.mark.asyncio
async def test_throttling_is_retried_rather_than_failing_the_account(monkeypatch) -> None:  # noqa: ANN001
    """A 429 says 'later', not 'no'. Dropping a whole account's window for a condition
    that clears in a second would lose data on a 42,000-request backfill."""
    monkeypatch.setattr("sei_sync.api.asyncio.sleep", _no_sleep)
    client = _RetryClient([429, 503])
    api = _api_with(client)
    stats = SyncStats()
    await api._get_page(AccountRef("3106", "x"), date(2026, 3, 1), date(2026, 3, 2), 1, stats=stats)
    assert client.calls == 3  # two rejections, then the answer
    assert stats.retries == 2
    assert stats.requests == 3


@pytest.mark.asyncio
async def test_a_bad_request_is_not_retried(monkeypatch) -> None:  # noqa: ANN001
    """400/422 will be wrong every time; retrying only multiplies the damage."""
    from sei_sync.api import SeiApiError

    monkeypatch.setattr("sei_sync.api.asyncio.sleep", _no_sleep)
    client = _RetryClient([422])
    api = _api_with(client)
    with pytest.raises(SeiApiError):
        await api._get_page(
            AccountRef("3106", "x"), date(2026, 3, 1), date(2026, 3, 2), 1, stats=SyncStats()
        )
    assert client.calls == 1


@pytest.mark.asyncio
async def test_retries_are_bounded(monkeypatch) -> None:  # noqa: ANN001
    """An endpoint that throttles forever must end the account, not the run."""
    from sei_sync.api import RETRY_ATTEMPTS, SeiApiError

    monkeypatch.setattr("sei_sync.api.asyncio.sleep", _no_sleep)
    client = _RetryClient([429] * 20)
    api = _api_with(client)
    with pytest.raises(SeiApiError, match="gave up"):
        await api._get_page(
            AccountRef("3106", "x"), date(2026, 3, 1), date(2026, 3, 2), 1, stats=SyncStats()
        )
    assert client.calls == RETRY_ATTEMPTS


def test_retry_after_is_honoured_over_the_computed_backoff() -> None:
    """The server knows its own capacity better than an exponential guess."""
    from sei_sync.api import RETRY_MAX_SECONDS, _retry_after

    assert _retry_after(_FakeResponse({}, 429, headers={"Retry-After": "5"})) == 5.0
    # Capped, so a hostile or mistaken header cannot park the run for an hour.
    assert (
        _retry_after(_FakeResponse({}, 429, headers={"Retry-After": "9999"})) == RETRY_MAX_SECONDS
    )
    # The HTTP-date form is not parsed; fall back to the backoff rather than guessing.
    assert (
        _retry_after(
            _FakeResponse({}, 429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        )
        is None
    )
    assert _retry_after(_FakeResponse({}, 429, headers={})) is None


async def _no_sleep(_seconds: float) -> None:
    return None


def test_stats_summary_reports_requests_and_failures() -> None:
    stats = SyncStats(requests=12, rows=340, accounts=5, failures={"3106/x": "HTTP 500"})
    assert "340 transactions over 12 request(s)" in stats.summary
    assert "1 failed" in stats.summary
    assert "0 retried" in stats.summary


# --- the audit hook ----------------------------------------------------------------
# This module is a SECOND, independent SWP client living beside `pkgs/sei` (down to a second class
# also called `SeiApiError`), and it is the one the pipeline actually uses. It recorded nothing —
# so SEI's highest-volume outbound traffic, up to ~42,000 requests on a backfill, was absent from
# `audit.api_log` while `pkgs/sei`'s single cash-receipt endpoint was recorded faithfully. A gap
# that depends on which of two clients made the call is invisible from the log itself.


@pytest.mark.asyncio
async def test_a_successful_read_is_recorded_once() -> None:
    """The operation is the ENDPOINT, never the account id: a per-account operation name would
    fragment every count over the log, which is the bug Morgan Lloyd's `$select` projections
    caused and `_operation_of` was written to fix."""
    recorded: list[Any] = []
    api = _api_with(_RetryClient([]), recorded.append)

    await api._get_page(
        AccountRef("3106", "x"), date(2026, 3, 1), date(2026, 3, 2), 1, stats=SyncStats()
    )

    assert len(recorded) == 1
    assert recorded[0].provider == "sei"
    assert recorded[0].operation == "listTransactionDetails"
    assert recorded[0].ok is True
    # A success's body is dropped: at backfill volumes keeping them costs a great deal of storage
    # to answer nothing (`ApiCall.keeps_response`).
    assert recorded[0].response is None


@pytest.mark.asyncio
async def test_a_failure_keeps_the_vendors_own_message() -> None:
    """A failure's body is the whole point of the log — SEI's 400s name the offending field, and
    that is what an incident is reconstructed from."""
    recorded: list[Any] = []
    api = _api_with(_RetryClient([422]), recorded.append)

    with pytest.raises(SeiApiError):
        await api._get_page(
            AccountRef("3106", "x"), date(2026, 3, 1), date(2026, 3, 2), 1, stats=SyncStats()
        )

    assert len(recorded) == 1
    assert recorded[0].ok is False
    assert recorded[0].status_code == 422
    assert recorded[0].response is not None


@pytest.mark.asyncio
async def test_retries_are_one_row_not_one_per_attempt(monkeypatch) -> None:  # noqa: ANN001
    """Taken AFTER the retry loop, so one row describes one LOGICAL call. Otherwise a throttled
    backfill writes four audit rows for one thing that happened."""
    monkeypatch.setattr("sei_sync.api.asyncio.sleep", _no_sleep)
    recorded: list[Any] = []
    client = _RetryClient([429, 429])
    api = _api_with(client, recorded.append)

    await api._get_page(
        AccountRef("3106", "x"), date(2026, 3, 1), date(2026, 3, 2), 1, stats=SyncStats()
    )

    assert client.calls == 3
    assert len(recorded) == 1


@pytest.mark.asyncio
async def test_no_recorder_is_a_legitimate_state() -> None:
    """A script, a test or an SDK consumer outside this monorepo has no database to write to —
    which is exactly why these clients TAKE a recorder rather than reaching for one."""
    api = _api_with(_RetryClient([]))
    await api._get_page(
        AccountRef("3106", "x"), date(2026, 3, 1), date(2026, 3, 2), 1, stats=SyncStats()
    )


def test_account_template_path_uses_the_gateway_spelling_not_swagger_camelcase() -> None:
    """`enrollmentAccountTemplates` 404s on i31; the sibling client service's
    all-lowercase convention is what Apigee deployed. Same trap as
    `getAccountDetailsinfo`."""
    assert ACCOUNT_TEMPLATES_PATH == "/enrollmentaccounttemplates/crm/account/templates"
    assert CLIENT_TEMPLATES_PATH == "/enrollmentclienttemplates/crm/client/templates"
    assert "Account" not in ACCOUNT_TEMPLATES_PATH


def test_document_paths_are_the_lowercase_proxies_i31_routes() -> None:
    """Verified i31 22 Sep 2026. CamelCase `retrieveEndClientDocumentService` and
    `docRetrieveContentService` 404 `Unable to identify proxy`; the lowercase
    spellings return a business response. The US-tax sibling keeps a capital S
    and is not one of these paths."""
    assert LIST_DOCUMENTS_PATH == "/retrieveendclientdocumentservice/documents/list"
    assert RETRIEVE_DOCUMENT_PATH == "/docretrievecontentservice/"
    assert LIST_DOCUMENTS_PATH == LIST_DOCUMENTS_PATH.lower()
    assert RETRIEVE_DOCUMENT_PATH == RETRIEVE_DOCUMENT_PATH.lower()
    assert "ustax" not in LIST_DOCUMENTS_PATH
    assert "ustax" not in RETRIEVE_DOCUMENT_PATH


# --- employee users: processing org, not firm id -----------------------------------


def test_the_default_service_id_is_the_widest_i31_filter_not_the_openapi_example() -> None:
    """OpenAPI's example is 152; that returns 0 rows against processing org 3100. Service 1
    is a superset of every other populated id in 1–250 (312 users, all with emails)."""
    assert DEFAULT_EMPLOYEE_SERVICE_ID == "1"
    assert DEFAULT_EMPLOYEE_SERVICE_ID != "152"
    assert SeiSettings.model_fields["service_id"].default == "1"


def test_processing_org_id_reads_the_firm_defaults_cache() -> None:
    """Measured i31 22 Sep 2026: firm 3102's cache carries processingOrgId 3100."""
    body = {
        "data": {
            "result": {
                "FIRM_DEFAULT_ATTR_CACHE": {
                    "processingOrgId": 3100,
                    "processingOrgName": "IWA Market 7",
                    "firmName": "Blackfinch Investments Limited",
                }
            }
        }
    }
    assert _processing_org_id(body) == "3100"


def test_processing_org_id_does_not_fall_back_to_the_firm_id() -> None:
    """Sending firm 3102 as processingOrgId is HTTP 200 with zero rows — the failure the
    unscoped 400 had been hiding."""
    assert (
        _processing_org_id({"data": {"result": {"FIRM_DEFAULT_ATTR_CACHE": {"firmId": 3102}}}})
        is None
    )
    assert _processing_org_id({}) is None
    assert _processing_org_id(None) is None


class _ScriptedClient:
    """Captures GET params and returns a scripted sequence of bodies."""

    def __init__(self, script: list[tuple[int, dict[str, Any]]]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, params: Any, headers: Any) -> Any:
        status, payload = self.script.pop(0)
        self.calls.append({"url": url, "params": dict(params)})
        text = "" if status == 200 else '{"errors":[{"field":"x","reason":"nope"}]}'
        response = _FakeResponse(payload, status)
        response.text = text
        return response


@pytest.mark.asyncio
async def test_employee_users_sends_both_required_scopes() -> None:
    """Live i31 400s without processingOrgId (100014) then without serviceId (100015)."""
    client = _ScriptedClient(
        [
            (
                200,
                {
                    "employeeUsersForFirmList": [],
                    "metaData": {"pageNumber": 1, "pageSize": 100, "totalRecords": 0},
                },
            )
        ]
    )
    api = _api_with(client)
    await api.employee_users(processing_org_id="3100", service_id="1", page=1)
    assert client.calls[0]["url"].endswith(EMPLOYEE_USERS_PATH)
    assert client.calls[0]["params"] == {
        "pageNumber": 1,
        "pageSize": MAX_PAGE_SIZE,
        "processingOrgId": "3100",
        "serviceId": "1",
    }


class _EmployeeApi:
    """Stands in for SeiApi inside fetch_employee_users (no TLS, no auth)."""

    def __init__(
        self,
        *,
        defaults: dict[str, dict[str, Any]],
        pages: dict[str, list[dict[str, Any]]],
    ) -> None:
        self.defaults = defaults
        self.pages = pages
        self.default_calls: list[str] = []
        self.user_calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _EmployeeApi:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def firm_default_attributes(self, firm_id: str) -> dict[str, Any]:
        self.default_calls.append(firm_id)
        return self.defaults[firm_id]

    async def employee_users(
        self, *, processing_org_id: str, service_id: str, page: int = 1
    ) -> dict[str, Any]:
        self.user_calls.append(
            {
                "processing_org_id": processing_org_id,
                "service_id": service_id,
                "page": page,
            }
        )
        found = self.pages.get(processing_org_id, [])
        return {
            "employeeUsersForFirmList": found,
            "metaData": {"pageNumber": page, "pageSize": 100, "totalRecords": len(found)},
        }


@pytest.mark.asyncio
async def test_fetch_resolves_processing_org_from_firm_defaults_not_firm_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six firms in this delivery group share processing org 3100; the sweep is per org,
    not per firm, and never uses the firm id as processingOrgId."""
    fake = _EmployeeApi(
        defaults={
            "3102": {"data": {"result": {"FIRM_DEFAULT_ATTR_CACHE": {"processingOrgId": 3100}}}},
            "3106": {"data": {"result": {"FIRM_DEFAULT_ATTR_CACHE": {"processingOrgId": 3100}}}},
        },
        pages={
            "3100": [
                {"uniqueUserId": 1, "firmId": 3102, "emailId": "a@example.com"},
                {"uniqueUserId": 2, "firmId": 3106, "emailId": "b@example.com"},
            ]
        },
    )
    monkeypatch.setattr("sei_sync.pull.SeiApi", lambda _settings: fake)

    rows, stats = await fetch_employee_users(_settings(service_id="1"), firm_ids=["3102", "3106"])
    assert fake.default_calls == ["3102", "3106"]
    assert fake.user_calls == [{"processing_org_id": "3100", "service_id": "1", "page": 1}]
    assert [r["uniqueUserId"] for r in rows] == [1, 2]
    assert stats.failures == {}


@pytest.mark.asyncio
async def test_missing_firm_ids_is_a_named_failure_not_an_unscoped_sweep() -> None:
    """An unscoped GET 400s; treating that as 'SEI has no employees' would empty the
    table. An empty Reference report must fail closed before any request."""
    rows, stats = await fetch_employee_users(_settings(), firm_ids=[])
    assert rows == []
    assert "Reference report" in stats.failures["config"]


def test_firm_default_attr_path_is_the_cache_read() -> None:
    assert FIRM_DEFAULT_ATTR_PATH == "/cachereadservices/firm-default-attr-cache"
