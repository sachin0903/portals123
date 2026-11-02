"""Timestamp-cursor extractors: the endpoints that DO take a date filter.

The cursor is the max of the named column in the model's own previous
materialisation — interlace injects it, so incremental position lives in the
warehouse instead of a side store (v1 kept it in process memory and lost it on
every restart). Each pull overlaps the cursor by a few days; the keyed merge
makes the overlap idempotent.

A changed model is a new table, so that max is None and the unfiltered list
would run again. Notifications, messages and transactions call ``resume``,
which reads the environment view (it still points at the previous
materialisation) and ``keep``, which republishes those rows with the new scan.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
from interlace import model

from seccl_sync.client import SecclClient
from seccl_sync.cursor_state import keep, resume
from seccl_sync.extract import date_floor, dedupe_by
from seccl_sync.records import records_to_table

# statuses a transaction can still move out of; a date-filtered pull would miss
# those status changes, so they are re-pulled in full each run
_OPEN_STATUSES = ("Calculating", "Checking", "Pending", "Placed", "Waiting", "Deleted")


@model(
    name="raw.notifications",
    strategy="merge",
    key="id",
    cursor="created",
    schedule={"cron": "30 5 * * 2-6"},
    tags=("seccl", "raw"),
    description="Notifications (exception queue); incremental via createdFrom",
)
async def raw_notifications(cursor: Any) -> pa.Table:
    effective, existing = resume(cursor, "notifications", "created")
    params: dict[str, Any] = {}
    if effective is not None:
        params["createdFrom"] = date_floor(effective, overlap_days=1)
    async with SecclClient() as api:
        records = await api.get_list(f"/notification/{api.firm_id}", params)
    table = records_to_table(records, spine={"id": pa.string(), "created": pa.string()})
    return keep(cursor, existing, table, "id")


@model(
    name="raw.messages",
    strategy="merge",
    key="id",
    cursor="created",
    schedule={"cron": "30 5 * * 2-6"},
    tags=("seccl", "raw"),
    description="Client messages/documents; incremental via fromDate",
)
async def raw_messages(cursor: Any) -> pa.Table:
    effective, existing = resume(cursor, "messages", "created")
    params: dict[str, Any] = {}
    if effective is not None:
        params["fromDate"] = date_floor(effective, overlap_days=1)
    async with SecclClient() as api:
        records = await api.get_list(f"/message/{api.firm_id}", params)
    table = records_to_table(records, spine={"id": pa.string(), "created": pa.string()})
    return keep(cursor, existing, table, "id")


@model(
    name="raw.transactions",
    strategy="merge",
    key="id",
    cursor="transaction_date",
    schedule={"cron": "30 5 * * 2-6"},
    tags=("seccl", "raw"),
    description="Portfolio transactions; incremental via transactionDateFrom + open-status sweep",
)
async def raw_transactions(cursor: Any) -> pa.Table:
    effective, existing = resume(cursor, "transactions", "transaction_date")
    records: list[dict[str, Any]] = []
    async with SecclClient() as api:
        path = f"/portfoliotransaction/{api.firm_id}"
        if effective is None:
            records = await api.get_list(path)
        else:
            # status changes don't move transactionDate, so sweep every open
            # status in full, then pull the recent date window
            for status in _OPEN_STATUSES:
                records.extend(await api.get_list(path, {"status": status}))
            records.extend(
                await api.get_list(
                    path, {"transactionDateFrom": date_floor(effective, overlap_days=3)}
                )
            )
    records = dedupe_by(records, "id")  # sweep + window overlap
    # `charges` rides in the spine so the column always exists — the mart's
    # charge-split SQL must compile even where the API never sends the field.
    # `subscription_amount` is how much the movement changed the ISA/JISA allowance.
    # Spined like `charges`: the column has to exist even when this firm never sends it,
    # or the accounts model cannot turn remaining + used into a limit.
    table = records_to_table(
        records,
        spine={
            "id": pa.string(),
            "transaction_date": pa.string(),
            "charges": pa.string(),
            "subscription_amount": pa.string(),
        },
    )
    return keep(cursor, existing, table, "id")


@model(
    name="raw.asset_prices",
    strategy="merge",
    key="id",
    cursor="price_date",
    depends_on="raw.assets",
    schedule={"cron": "0 7 * * 1-5"},
    tags=("seccl", "raw"),
    description="Asset prices; per-asset history pull incremental via dateFrom",
)
async def raw_asset_prices(raw_assets: Any, cursor: Any) -> pa.Table:
    asset_ids = [value for value in raw_assets.table().column("id").to_pylist() if value]
    params: dict[str, Any] | None = None
    if cursor is not None:
        params = {"dateFrom": date_floor(cursor, overlap_days=3)}
    async with SecclClient() as api:
        records = await api.get_many(
            [f"/price/{api.firm_id}/{asset_id}" for asset_id in asset_ids], params
        )
    records = dedupe_by(records, "id")
    return records_to_table(records, spine={"id": pa.string(), "price_date": pa.string()})
