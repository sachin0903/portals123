"""``raw.asset_prices`` — one price per instrument per day, from the SWP API.

Disabled. On 14 Oct 2026 i31's ``assetpriceproxyservice`` proxy was deployed and
had no flow for ``historicalassetprices``: every casing returned
``404 unknown-resource``, and a capital in the proxy name was
``Unable to identify proxy``. The model stays so the scan is here when that
resource exists. A run returns an empty table and does not call SEI.

``POST /assetpriceproxyservice/historicalassetprices`` takes a firm id, a list of
identifiers and one as-of date. It does not take a range, so a backfill is one
request per batch per day. The Assets report already carries the latest ``PRICE``;
this model is the history that report does not keep.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
from interlace import model

from sei_sync.api import as_day, backfill_start, earliest_start, horizon
from sei_sync.asset_prices import SCHEMA, flatten
from sei_sync.settings import SeiSettings

logger = logging.getLogger("sei_sync.models")


@model(
    name="raw.asset_prices",
    strategy="merge",
    key=("input_scheme", "external_id", "as_of_date"),
    cursor="as_of_date",
    depends_on=("raw.assets", "raw.accounts", "ref.firms"),
    tags=("sei", "raw", "api"),
    description="Disabled: i31 has no historicalassetprices flow",
)
async def raw_asset_prices(
    raw_assets: Any, cursor: Any, raw_accounts: Any = None, ref_firms: Any = None
) -> pa.Table:
    # i31's assetpriceproxyservice proxy has no historicalassetprices flow
    # (404 unknown-resource, every casing, 14 Oct 2026). Do not call it.
    del raw_assets, cursor, raw_accounts, ref_firms
    now = datetime.now(tz=UTC)
    logger.info("sei raw.asset_prices: disabled; historicalassetprices is not a deployed flow")
    return flatten([], fetched_at=now, firm_id="", as_of=now.date(), instruments={})


def _window(
    cursor: Any,
    stored: StoredSync | None,
    opened: date | None,
    settings: SeiSettings,
    *,
    today: date,
) -> tuple[date, date]:
    """Lookback from the latest ``as_of_date``, or the open date when no scan has finished."""
    end = horizon(today)
    anchor = _anchor(cursor, stored)
    if anchor is None:
        return backfill_start(opened, settings, today=today), end
    day = anchor if isinstance(anchor, date) else date.fromisoformat(str(anchor)[:10])
    start = day - timedelta(days=settings.api_lookback_days)
    floor = earliest_start(today)
    if start < floor:
        start = floor
    return start, end


@dataclass(frozen=True)
class StoredSync:
    """The latest ``as_of_date`` already stored. That is how far the scan has asked."""

    latest: date | None


def _anchor(cursor: Any, stored: StoredSync | None) -> Any:
    """The injected cursor on a live table, else the previous materialisation.

    A rebuild's cursor is empty because the new table is. ``as_of_date`` is the
    day we asked for, so its maximum is the scan mark: a quiet night still writes
    yesterday once the call returns a price.
    """
    if cursor is not None:
        return cursor
    if stored is not None:
        return stored.latest
    return None


def _align(table: pa.Table, schema: pa.Schema) -> pa.Table:
    """Cast onto ``schema``, nullability included. See the transactions model."""
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


def _row_key(table: pa.Table) -> pa.Array:
    scheme = table.column("input_scheme").cast(pa.string())
    external = table.column("external_id").cast(pa.string())
    day = table.column("as_of_date").cast(pa.string())
    return pc.binary_join_element_wise(scheme, external, day, "#")


def _prefer_fresh(existing: pa.Table, fresh: pa.Table) -> pa.Table:
    """Stored rows, with this scan's copy of a day winning."""
    if fresh.num_rows and existing.num_rows:
        keep = pc.invert(pc.is_in(_row_key(existing), value_set=_row_key(fresh)))
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
) -> pa.Table:
    """The scan, plus stored history when the target table is new.

    A rebuild's cursor is empty. Returning only the lookback would publish that
    stretch and drop the rest when the view moves.
    """
    rebuilding = cursor is None and stored is not None and stored.latest is not None
    if not rebuilding:
        return fresh
    existing = _existing_rows()
    if existing is None or existing.num_rows == 0:
        raise RuntimeError(
            "sei raw.asset_prices: stored history could not be reread; not replacing it"
        )
    logger.info(
        "sei raw.asset_prices: continuing from stored history (%d row(s))", existing.num_rows
    )
    return _prefer_fresh(existing, fresh)


_SUMMARY = "SELECT max(as_of_date) AS latest, count(*) AS n FROM sei.raw.asset_prices"
_ALL_ROWS = "SELECT * FROM sei.raw.asset_prices"


def _stored_sync() -> StoredSync | None:
    """The scan mark already stored, or None when that cannot be read.

    An empty result is None too: a table that reads as empty is a failed lookup,
    and treating it as "never fetched" would backfill every instrument again.
    """
    try:
        con = _catalogue()
    except Exception as exc:  # noqa: BLE001 - a failed lookup must not widen the pull
        logger.warning("sei raw.asset_prices: could not read existing rows: %s", exc)
        return None
    if con is None:
        return None
    try:
        summary = con.execute(_SUMMARY).fetchone()
    except Exception as exc:  # noqa: BLE001 - a failed lookup must not widen the pull
        logger.warning("sei raw.asset_prices: could not read existing rows: %s", exc)
        return None
    if summary is None or not summary[1]:
        logger.warning("sei raw.asset_prices: existing rows read as empty; not a lookback")
        return None
    return StoredSync(latest=as_day(summary[0]))


def _existing_rows() -> pa.Table | None:
    try:
        con = _catalogue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sei raw.asset_prices: could not reread stored rows: %s", exc)
        return None
    if con is None:
        return None
    try:
        table = con.execute(_ALL_ROWS).arrow()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sei raw.asset_prices: could not reread stored rows: %s", exc)
        return None
    if isinstance(table, pa.RecordBatchReader):
        table = table.read_all()
    return table


def _catalogue() -> Any | None:
    from sei_sync.catalogue import attach

    return attach(what="raw.asset_prices")


__all__ = ["SCHEMA", "raw_asset_prices"]
