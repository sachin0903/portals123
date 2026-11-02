"""Per-entity detail fan-outs. These endpoints have no change signal at all, so
the pulls are scheduled nightly and applied as full_merge diffs; the position
webhook stream carries the intraday movement that matters."""

from __future__ import annotations

from typing import Any

import pyarrow as pa
from interlace import model

from seccl_sync.client import SecclClient
from seccl_sync.records import records_to_table


def _ids(handle: Any) -> list[str]:
    return [value for value in handle.table().column("id").to_pylist() if value]


#: `/account/{firm}/{id}`'s ACCOUNT-TERMS fields, declared as a spine rather than inferred from the
#: batch — the same rule `raw.fee_structures` states and for the same failure.
#:
#: `derived.account_terms` reads all five, and a column that does not exist is a DuckDB Binder Error
#: that aborts the model. Every one of them is genuinely optional in the payload: an account with no
#: standing order has no `recurringPayments`, a firm that prices by hand has no `adviserCharge`, and
#: `adviserTieredCharges` appears only where the charge IS tiered — so on a small or simple firm the
#: whole column is absent and the terms model would fail on exactly the firm with least to say.
#:
#: Nested values are JSON strings by the records convention, hence VARCHAR for the two lists.
#:
#: Declared on THIS function rather than by widening a shared one: interlace fingerprints a Python
#: model from `inspect.getsource`, so this re-pulls the account fan-out and nothing else
#: (docs/sei.md records the opposite mistake: a shared extractor re-fingerprinting sixteen).
_DETAIL_SPINE: dict[str, pa.DataType] = {
    "id": pa.string(),
    # Crystallisation groupType. A book of GIAs never sends it, and platform.valuations reads
    # the column off this table — without it the model fails to bind.
    "portfolio_groups": pa.string(),
    "adviser_charge": pa.float64(),
    "adviser_tiered_charges": pa.string(),
    "recurring_payments": pa.string(),
    "auto_disinvest": pa.string(),
    "fee_structures": pa.string(),
}


@model(
    name="raw.account_details",
    strategy="full_merge",
    key="id",
    depends_on="raw.accounts",
    schedule={"cron": "30 2 * * *"},
    tags=("seccl", "raw"),
    description="Account detail records (one call per account) — the account's own terms",
)
async def raw_account_details(raw_accounts: Any) -> pa.Table:
    ids = _ids(raw_accounts)
    async with SecclClient() as api:
        records = await api.get_many([f"/account/{api.firm_id}/{one}" for one in ids])
    if ids and not records:
        raise RuntimeError("raw.account_details: empty fan-out; refusing to delete all rows")
    return records_to_table(records, spine=_DETAIL_SPINE)


@model(
    name="raw.account_summaries",
    strategy="full_merge",
    key="id",
    depends_on="raw.accounts",
    schedule={"cron": "30 2 * * *"},
    tags=("seccl", "raw"),
    description="Account valuation summaries (one call per account)",
)
async def raw_account_summaries(raw_accounts: Any) -> pa.Table:
    ids = _ids(raw_accounts)
    async with SecclClient() as api:
        records = await api.get_many([f"/account/summary/{api.firm_id}/{one}" for one in ids])
    if ids and not records:
        raise RuntimeError("raw.account_summaries: empty fan-out; refusing to delete all rows")
    return records_to_table(records, spine={"id": pa.string()})


@model(
    name="raw.account_reports",
    strategy="full_merge",
    key="id",
    depends_on="raw.accounts",
    schedule={"cron": "30 2 * * *"},
    tags=("seccl", "raw"),
    description="Inception-to-date account reports (one call per account)",
)
async def raw_account_reports(raw_accounts: Any) -> pa.Table:
    ids = _ids(raw_accounts)
    async with SecclClient() as api:
        records = await api.get_many([f"/account/report/{api.firm_id}/{one}" for one in ids])
    if ids and not records:
        raise RuntimeError("raw.account_reports: empty fan-out; refusing to delete all rows")
    return records_to_table(records, spine={"id": pa.string()})


@model(
    name="raw.portfolio_summaries",
    strategy="full_merge",
    key="id",
    depends_on="raw.portfolios",
    schedule={"cron": "30 2 * * *"},
    tags=("seccl", "raw"),
    description="Portfolio summaries (one call per client)",
)
async def raw_portfolio_summaries(raw_portfolios: Any) -> pa.Table:
    # `GET /portfolio/{firm}` ("List portfolio summaries") documents `id` as the client id,
    # and `GET /portfolio/summary/{firm}/{clientId}` is that same id. The two are not a
    # portfolio key and a client key.
    ids = _ids(raw_portfolios)
    async with SecclClient() as api:
        records = await api.get_many([f"/portfolio/summary/{api.firm_id}/{one}" for one in ids])
    if ids and not records:
        raise RuntimeError("raw.portfolio_summaries: empty fan-out; refusing to delete all rows")
    # `products` is where the ISA/JISA remaining subscription sits
    # (`products[].remainingSubscriptionAmount`). A firm whose summaries omit the array must
    # still have the column, or platform.accounts fails to bind.
    return records_to_table(records, spine={"id": pa.string(), "products": pa.string()})
