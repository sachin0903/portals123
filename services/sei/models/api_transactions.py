"""``raw.api_transactions`` — transactions from the SWP REST API, keyed and incremental.

The SFTP drop is a daily delta and SEI keeps about five days on the endpoint, so a missed
stretch never arrives in `raw.transaction_header`. This model is the other half: the same
transaction-date query (`dateType=1`, no type filter), merged under the file when an id is
in both. On the covered days the two largely agree, including buys and sells (docs/sei.md
§1a, 21 Sep 2026). The API is still required for the days the files no longer hold. The
firms requested are the `FIRM_ID` rows of the SFTP Reference report (`ref.firms`).

It depends on **`raw.accounts`** because the API refuses a firm-wide transaction query —
"Either clientId or externalPartyId or accountNumber or externalReferenceId is mandatory" —
so the account list has to come from somewhere, and the drop already carries it. That makes
the SFTP feed the discovery mechanism for the API's fan-out, and puts both in one DAG.

**First run vs later runs.** With no stored scan a firm is read from its earliest
`ACCOUNT_OPEN_DATE` (28 Jun 2024 for the oldest firm on the book measured 14 Oct 2026),
clamped to SEI's 36-month floor. With no open date the first scan is the lookback.
A later run starts the day after `covered_through`. `SEI_API_LOOKBACK_DAYS` (default 0)
steps back from that when a correction to an older date has to be re-merged.
`covered_through` is the end of the last successful scan, written onto the rows of that
scan. It is not `max(transaction_date)`: interlace's cursor is that maximum, and on a
quiet book it sits on the last movement, so every later run would re-walk the empty
months after it. A firm with no rows yet is still read from its own open date. The
scan mark is one date for the whole table, and applying it to a firm added later would
publish only the lookback.

A rebuild is a new physical table, so the injected cursor is None. The environment view
still names the previous materialisation until the new one is promoted, and this model
reads that view: the scan continues from `covered_through` (or from the latest stored
transaction date until a scan has recorded one) and the stored rows are carried forward.
A lookback published on its own would replace the ledger with a month of it.

**The grain is a day.** `dateType` offers transaction, trade and settlement date — there
is no last-modified, and `endDate` may not be today — so a run cannot see a timestamp.
It asks for each uncovered day through yesterday, for every account, and `merge` on
`transaction_id` updates a row when that day is read again. A non-zero
`SEI_API_LOOKBACK_DAYS` is the overlap that re-reads older dates for a correction.
`submitted_date` records when SEI actually booked each row, so a late amendment stays
visible as one.

Disabled by default: blank `SEI_API_BASE_URL` returns an empty typed table, so a deployment
with no API credentials runs the SFTP pipeline unchanged rather than failing every night.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
from interlace import model

from sei_sync.accounts import account_refs, opened_by_firm
from sei_sync.api import AccountRef, as_day, backfill_start, earliest_start, horizon
from sei_sync.api_rows import SCHEMA, flatten
from sei_sync.firms import resolve_firm_ids
from sei_sync.pull import fetch_transactions
from sei_sync.settings import SeiSettings

logger = logging.getLogger("sei_sync.models")


@model(
    name="raw.api_transactions",
    strategy="merge",
    key=("transaction_id",),
    cursor="transaction_date",
    depends_on=("raw.accounts", "ref.firms"),
    # Every fifteen minutes. A tick that is already covered through yesterday makes no
    # SEI calls. The account list is whatever `raw.accounts` last published; this cron
    # does not pull the drop.
    schedule={"cron": "30 5 * * 2-6"},
    tags=("sei", "raw", "api"),
    description="SEI SWP API transactions, per account, incremental with a lookback window",
)
async def raw_api_transactions(raw_accounts: Any, cursor: Any, ref_firms: Any = None) -> pa.Table:
    settings = SeiSettings()
    now = datetime.now(tz=UTC)
    if not settings.api_base_url:
        logger.info("sei raw.api_transactions: SEI_API_BASE_URL unset — API sync disabled")
        return flatten([], fetched_at=now)

    accounts = _accounts(raw_accounts, resolve_firm_ids(ref_firms))
    if not accounts:
        logger.warning("sei raw.api_transactions: no accounts to sync; is raw.accounts empty?")
        return flatten([], fetched_at=now)

    # One scan mark for the whole table. A firm that already has rows is read from that
    # mark minus the lookback. A firm with none — one the Reference report added after the
    # first backfill — is read from its own earliest ACCOUNT_OPEN_DATE. Using the mark
    # for that firm would publish only the lookback, which is how a July-opened book
    # arrived with three days of transactions.
    # The split is by firm, not by account: an account with no rows inside a firm that has
    # already been fetched is not a reason to walk three years of every other account.
    stored = _stored_sync()
    anchor = _anchor(cursor, stored)
    seen = None if stored is None or not stored.firms else stored.firms
    groups = _fetch_groups(accounts, anchor, settings, today=now.date(), seen_firms=seen)
    if not groups:
        logger.info("sei raw.api_transactions: nothing new (cursor %s is current)", anchor)
        # A rebuild with nothing left to ask still has to republish the stored rows.
        # An empty table here is what the view would swap to.
        return _publish(
            flatten([], fetched_at=now),
            cursor=cursor,
            stored=stored,
            mark=stored.covered_through if stored else None,
            advance=False,
        )

    tables: list[Any] = []
    stats = None
    for group, start, end in groups:
        logger.info("sei raw.api_transactions: %d account(s), %s..%s", len(group), start, end)
        found, group_stats = await fetch_transactions(settings, group, start, end, fetched_at=now)
        tables.extend(found)
        stats = group_stats if stats is None else _add_stats(stats, group_stats)
    if stats is not None and stats.failures:
        # Not raised: a partial backfill is worth keeping, and the next run's overlap
        # re-reads whatever these accounts missed.
        logger.warning(
            "sei raw.api_transactions: %d account(s) failed, e.g. %s",
            len(stats.failures),
            next(iter(stats.failures.items())),
        )
    # Concatenated rather than built from dicts: fetch_transactions already converted each
    # account, so the 1.4M-row case never exists as Python objects all at once.
    fresh = pa.concat_tables(tables) if tables else flatten([], fetched_at=now)
    # Every group ends on the same horizon. A failure must not move the mark past the
    # accounts that did not answer: the next run then repeats this window.
    # A failed scan keeps the previous mark. Stamping NULL would clear it on every
    # row this window rewrote, and those are the rows that hold the mark.
    window_end = None if stats is None or stats.failures else groups[-1][2]
    mark = window_end if window_end is not None else (stored.covered_through if stored else None)
    return _publish(fresh, cursor=cursor, stored=stored, mark=mark, advance=window_end is not None)


def _range(
    cursor: Any,
    settings: SeiSettings,
    *,
    today: date,
    opened: date | None = None,
) -> tuple[date, date]:
    """The window to request, clamped to what SEI will actually answer.

    ``end`` is yesterday (a future ``endDate`` is refused) and ``start`` never precedes the
    36-month floor. The floor SLIDES, so an open date that was inside the window ages
    out of it — clamping turns that into "as far back as allowed".

    A cursorless start is the firm's earliest account open date when ``opened`` is set.
    With no open date it is the lookback.
    """
    end = horizon(today)
    if cursor is None:
        start = backfill_start(opened, settings, today=today)
    else:
        anchor = cursor if isinstance(cursor, date) else date.fromisoformat(str(cursor)[:10])
        # The covered day is already stored. The next ask is the day after it, unless an
        # overlap is configured so a correction on an older date is re-merged.
        if settings.api_lookback_days <= 0:
            start = anchor + timedelta(days=1)
        else:
            start = anchor - timedelta(days=settings.api_lookback_days)
        floor = earliest_start(today)
        if start < floor:
            logger.warning(
                "sei raw.api_transactions: %s precedes SEI's 36-month floor; starting at %s",
                start,
                floor,
            )
            start = floor
    return start, end


def _opened_by_firm(accounts: list[AccountRef]) -> dict[str, date]:
    """Earliest ``ACCOUNT_OPEN_DATE`` per firm. A firm with none is absent."""
    return opened_by_firm(accounts)


def _fetch_groups(
    accounts: list[AccountRef],
    cursor: Any,
    settings: SeiSettings,
    *,
    today: date,
    seen_firms: set[str] | None,
) -> list[tuple[list[AccountRef], date, date]]:
    """Windows to request. A firm with no stored rows is read from its open date.

    ``seen_firms is None`` means we could not tell which firms already have rows. Every
    account then uses the cursor window — a failed lookup must not turn the nightly run
    into a three-year pull of the firms that are already loaded.
    """
    end = horizon(today)
    opened = _opened_by_firm(accounts)
    if cursor is None:
        buckets: dict[date, list[AccountRef]] = {}
        for account in accounts:
            start = backfill_start(opened.get(account.firm_id), settings, today=today)
            buckets.setdefault(start, []).append(account)
        return [(group, start, end) for start, group in sorted(buckets.items()) if start <= end]
    if seen_firms is None:
        incremental_start, end = _range(cursor, settings, today=today)
        if incremental_start > end:
            return []
        return [(accounts, incremental_start, end)]
    incremental_start, end = _range(cursor, settings, today=today)
    fresh = [account for account in accounts if account.firm_id not in seen_firms]
    known = [account for account in accounts if account.firm_id in seen_firms]
    groups: list[tuple[list[AccountRef], date, date]] = []
    if known and incremental_start <= end:
        groups.append((known, incremental_start, end))
    fresh_buckets: dict[date, list[AccountRef]] = {}
    for account in fresh:
        start = backfill_start(opened.get(account.firm_id), settings, today=today)
        fresh_buckets.setdefault(start, []).append(account)
    for start, group in sorted(fresh_buckets.items()):
        if start <= end:
            groups.append((group, start, end))
    return groups


@dataclass(frozen=True)
class StoredSync:
    """What the last materialisation already holds, read from the environment view."""

    firms: set[str]
    covered_through: date | None
    latest_transaction: date | None


def _anchor(cursor: Any, stored: StoredSync | None) -> Any:
    """The date the next window is measured from.

    ``covered_through`` wins: it is how far a scan has already asked. The injected
    cursor is ``max(transaction_date)``, which a rebuild does not have (the new table
    is empty) and which a quiet book leaves on the last movement.
    """
    if stored is not None and stored.covered_through is not None:
        return stored.covered_through
    if cursor is not None:
        return cursor
    if stored is not None and stored.latest_transaction is not None:
        return stored.latest_transaction
    return None


def _stamp(table: pa.Table, day: date | None) -> pa.Table:
    """Set ``covered_through`` on every row. ``None`` leaves the previous mark in place."""
    column = pa.array([day] * table.num_rows, type=pa.date32())
    if "covered_through" in table.column_names:
        table = table.remove_column(table.schema.get_field_index("covered_through"))
    return table.append_column("covered_through", column)


def _align(table: pa.Table, schema: pa.Schema) -> pa.Table:
    """Cast onto ``schema``, nullability included.

    DuckDB exports every column nullable and prefers ``large_string``.
    ``concat_tables`` rejects that against the declared schema
    (``Schema at index 1 was different``), which is how a rebuild of the
    stored history fails.
    """
    columns: list[pa.Array] = []
    for field in schema:
        if field.name not in table.column_names:
            columns.append(pa.nulls(table.num_rows, type=field.type))
            continue
        column = table.column(field.name)
        if column.type != field.type:
            column = column.cast(field.type)
        columns.append(column)
    return pa.Table.from_arrays(columns, schema=schema)


def _prefer_fresh(existing: pa.Table, fresh: pa.Table) -> pa.Table:
    """Stored rows, with this scan's copy of an id winning.

    A rebuild writes a new table. Returning only the lookback would publish that
    month and drop the rest when the view moves.
    """
    if fresh.num_rows and existing.num_rows:
        keep = pc.invert(
            pc.is_in(existing.column("transaction_id"), value_set=fresh.column("transaction_id"))
        )
        existing = existing.filter(keep)
    existing = _align(existing, fresh.schema)
    if existing.num_rows == 0:
        return fresh
    if fresh.num_rows == 0:
        return existing
    return pa.concat_tables([existing, fresh])


def _publish(
    fresh: pa.Table,
    *,
    cursor: Any,
    stored: StoredSync | None,
    mark: date | None,
    advance: bool,
) -> pa.Table:
    """The table this run commits: the scan, plus stored history when the target is new.

    ``advance`` is set only when the scan finished. ``mark`` is then the horizon.
    A failed scan passes the previous mark so a rewritten row does not clear it.
    """
    scanned = _apply_mark(fresh, mark)
    rebuilding = cursor is None and stored is not None and stored.latest_transaction is not None
    if rebuilding:
        existing = _existing_rows()
        if existing is None or existing.num_rows == 0:
            # The view named these rows a moment ago. Publishing the lookback alone
            # would replace them. Failing leaves the previous materialisation in place.
            raise RuntimeError(
                "sei raw.api_transactions: stored history could not be reread; not replacing it"
            )
        if fresh.num_rows == 0 and advance and mark is not None:
            existing = _stamp(existing, mark)
        logger.info(
            "sei raw.api_transactions: continuing from stored history (%d row(s))",
            existing.num_rows,
        )
        return _prefer_fresh(existing, scanned)
    if fresh.num_rows == 0 and advance and mark is not None:
        # A quiet window still has to move the mark, or the next start stays put while
        # the horizon advances and the request grows by a day every night.
        sample = _one_row()
        if sample is not None and sample.num_rows:
            return _stamp(sample, mark)
    return scanned


def _apply_mark(table: pa.Table, mark: date | None) -> pa.Table:
    """Stamp ``mark``, or drop the column when there is nothing safe to write.

    A NULL stamp is a write: the merge would clear ``covered_through`` on every
    row this batch updates. Leaving the column out leaves the stored mark alone.
    """
    if table.num_rows == 0:
        return table
    if mark is None:
        if "covered_through" not in table.column_names:
            return table
        return table.remove_column(table.schema.get_field_index("covered_through"))
    return _stamp(table, mark)


_DESCRIBE = "DESCRIBE SELECT * FROM sei.raw.api_transactions"
_SUMMARY_MARKED = (
    "SELECT max(covered_through) AS covered_through, max(transaction_date) AS latest_transaction, "
    "count(*) AS n FROM sei.raw.api_transactions"
)
_SUMMARY_UNMARKED = (
    "SELECT NULL AS covered_through, max(transaction_date) AS latest_transaction, "
    "count(*) AS n FROM sei.raw.api_transactions"
)
_FIRMS = (
    "SELECT DISTINCT CAST(firm_id AS VARCHAR) FROM sei.raw.api_transactions "
    "WHERE firm_id IS NOT NULL"
)
_ALL_ROWS = "SELECT * FROM sei.raw.api_transactions"
_ONE_ROW = "SELECT * FROM sei.raw.api_transactions LIMIT 1"


def _stored_sync() -> StoredSync | None:
    """Firms and scan mark already stored, or None when that cannot be read.

    An empty result is None too: the table holding ~100k rows reading as empty is a
    failed lookup, and treating it as "nobody has been fetched" would backfill every firm.
    """
    try:
        con = _catalogue()
    except Exception as exc:  # noqa: BLE001 - a failed lookup must not widen the pull
        logger.warning("sei raw.api_transactions: could not read existing rows: %s", exc)
        return None
    if con is None:
        return None
    try:
        columns = {row[0] for row in con.execute(_DESCRIBE).fetchall()}
        summary = con.execute(
            _SUMMARY_MARKED if "covered_through" in columns else _SUMMARY_UNMARKED
        ).fetchone()
        firms = con.execute(_FIRMS).fetchall()
    except Exception as exc:  # noqa: BLE001 - a failed lookup must not widen the pull
        logger.warning("sei raw.api_transactions: could not read existing rows: %s", exc)
        return None
    if summary is None or not summary[2]:
        logger.warning("sei raw.api_transactions: existing rows read as empty; lookback only")
        return None
    return StoredSync(
        firms={str(firm) for (firm,) in firms},
        covered_through=_as_day(summary[0]),
        latest_transaction=_as_day(summary[1]),
    )


def _existing_rows() -> pa.Table | None:
    return _arrow(_ALL_ROWS)


def _one_row() -> pa.Table | None:
    return _arrow(_ONE_ROW)


def _arrow(sql: str) -> pa.Table | None:
    try:
        con = _catalogue()
    except Exception as exc:  # noqa: BLE001 - the caller decides whether a miss is fatal
        logger.warning("sei raw.api_transactions: could not reread stored rows: %s", exc)
        return None
    if con is None:
        return None
    try:
        table = con.execute(sql).arrow()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sei raw.api_transactions: could not reread stored rows: %s", exc)
        return None
    if isinstance(table, pa.RecordBatchReader):
        table = table.read_all()
    return table


def _catalogue() -> Any | None:
    """A read-only attach of this service's catalogue, or None when it is not configured."""
    from sei_sync.catalogue import attach

    return attach(what="raw.api_transactions")


def _as_day(value: Any) -> date | None:
    return as_day(value)


def _add_stats(left: Any, right: Any) -> Any:
    left.accounts += right.accounts
    left.done += right.done
    left.rows += right.rows
    left.requests += right.requests
    left.retries += right.retries
    left.failures.update(right.failures)
    return left


def _accounts(handle: Any, firms: list[str] | None) -> list[AccountRef]:
    """Distinct (firm, account) pairs from the drop-derived accounts table.

    The drop has one row per account *portfolio*, so the same account number appears many
    times; de-duplicating here is what stops the fan-out re-requesting the same account.
    ``firms`` is the Reference report. None means do not filter, which the model
    does not use: an unreadable report is an empty list and requests nobody.
    """
    return account_refs(handle, firms)


__all__ = ["SCHEMA", "raw_api_transactions"]
