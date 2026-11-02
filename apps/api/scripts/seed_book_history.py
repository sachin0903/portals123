"""Seed `platform.transactions` + `platform.valuations` from the committed dev extract (dev only).

WHY THIS EXISTS. A local deployment has a real book — clients, accounts, `platform.links` rows —
and an EMPTY ledger: `platform.transactions` and `platform.valuations` are written only by the
provider pipeline (`docs/provider-pipeline.md`), which needs a custodian's credentials, the
DuckLake catalogue and the interlace daemons. So every transactions surface renders its empty
state, which is indistinguishable from "this firm has no movements" — the same class of confusion
`app/lake/fixture.py` exists to prevent for holdings, and the reason the account record shipped
invented fixture rows for a year.

SOURCE: `docs/seccl.duckdb`, the anonymised, downsampled Seccl extract already in the repository
and already serving local holdings. It keeps REAL Seccl account ids, so `platform.links` resolves
straight into it — the same property that makes the holdings fallback work — and it carries 8,301
transactions across all 33 linked accounts plus 366 days of daily valuations.

FAITHFUL, NOT FLATTERING. The canonical `type` mapping below is copied verbatim from
`services/seccl/models/platform/transactions.sql`, and `amount` is taken the way `pkgs/book-sink`
takes it (`coalesce(gross_amount, net_amount, 0)`). The point is that a local row looks EXACTLY
like a pipeline row, including where that is unflattering:

  * `amount` is UNSIGNED. Seccl carries direction in `movement_type`, the conformed layer keeps it
    that way and the merge copies it across, so a fee reads as a positive number here just as it
    does on a real deployment. Do not "fix" it in this script: a display defect that only shows up
    against real data is precisely what a faithful seed is for.
  * a transaction whose (type, movement, subtype) combination the mapping does not recognise is
    SKIPPED and COUNTED, never guessed into 'adjustment' — the materialiser files it on
    `platform.merge_rejects`, and money filed under the wrong type is not recoverable where a
    reject is.

Idempotent: upserts on `(tenant_id, source_system, source_transaction_id)` for transactions and
`(account_id, as_of_date, source_system)` for valuations, so re-running writes no new rows.
`--shift-to-today` is the one thing a re-run does change: it is measured from today, so running it
a week later moves the whole ledger a week forward. That is deliberate — the flag's job is to keep
the newest movement recent — and it moves every row by the same offset, so nothing slides apart.
Omit the flag if you want dates that never move.

Run:  uv run --directory apps/api python scripts/seed_book_history.py [tenant_alias]
        --shift-to-today   move every date forward by ONE offset, so the newest date in the extract
                           lands today (it stops in Dec 2025, so an unshifted ledger reads as
                           months stale). Transactions and valuations keep their real spacing.
        --transactions-only / --valuations-only
        --clear            delete this provider's existing rows for the tenant first
Requires: migrations applied + a local Postgres (pnpm db:up).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

DEFAULT_TENANT_ALIAS = "graphene"
PROVIDER = "seccl"

#: The extract, searched upward from this file — the same reason `app/lake/fixture.py` searches
#: rather than counting `parents[n]`: a moved module fails by reporting "no extract", which reads
#: as "no data" rather than as a broken path.
_RELATIVE = Path("docs/seccl.duckdb")


def extract_path() -> Path:
    for parent in [Path.cwd(), *Path(__file__).resolve().parents]:
        candidate = parent / _RELATIVE
        if candidate.is_file():
            return candidate
    raise SystemExit(f"Could not find {_RELATIVE} -- run from the repository or a child of it.")


# The canonical type, COPIED VERBATIM from services/seccl/models/platform/transactions.sql so a
# local row is typed by the same rule the pipeline types it by. The extract's column names are
# Seccl's own (`transaction_type`/`transaction_sub_type`), which are that model's `source_type`
# and `source_subtype`. NULL where nothing matches -- the caller skips and counts those.
_CANONICAL_TYPE = """
CASE
    WHEN t.transaction_type = 'Order' AND t.movement_type = 'Invest' THEN 'buy'
    WHEN t.transaction_type = 'Order' AND t.movement_type IN ('Raise', 'Sell') THEN 'sell'
    WHEN t.transaction_type = 'Payment' AND t.movement_type = 'In' THEN 'deposit'
    WHEN t.transaction_type = 'Payment' AND t.movement_type = 'Out' THEN 'withdrawal'
    WHEN t.transaction_type IN ('Product Transfer', 'Stock Transfer', 'Cash Transfer')
         AND t.movement_type = 'In' THEN 'transfer_in'
    WHEN t.transaction_type IN ('Product Transfer', 'Stock Transfer', 'Cash Transfer')
         AND t.movement_type = 'Out' THEN 'transfer_out'
    WHEN t.transaction_sub_type ILIKE '%fee%' OR t.transaction_sub_type ILIKE '%charge%'
         THEN 'fee'
    WHEN t.transaction_sub_type ILIKE '%interest%' THEN 'interest'
    WHEN t.transaction_sub_type ILIKE '%dividend%' OR t.transaction_sub_type ILIKE '%income%'
         OR t.transaction_sub_type ILIKE '%distribution%' THEN 'dividend'
    WHEN t.transaction_sub_type ILIKE '%tax%' THEN 'tax'
    WHEN t.transaction_type = 'Cash' AND t.movement_type = 'In' THEN 'deposit'
    WHEN t.transaction_type = 'Cash' AND t.movement_type = 'Out' THEN 'withdrawal'
    WHEN t.transaction_type = 'Notional' THEN 'adjustment'
END
"""

_TRANSACTIONS_SQL = (
    """
SELECT
    t.account_id                                       AS account_external_id,
    cast(t.id AS VARCHAR)                              AS source_transaction_id,
"""
    + _CANONICAL_TYPE
    + """                                               AS type,
    t.transaction_type                                 AS source_type,
    t.transaction_sub_type                             AS source_subtype,
    t.isin,
    t.asset_name,
    -- The merge's own expression: pkgs/book-sink entities.py
    -- `coalesce(s.gross_amount, s.net_amount, 0) AS amount`.
    cast(coalesce(t.amount, t.consideration, 0) AS DOUBLE)  AS amount,
    coalesce(t.currency, 'GBP')                        AS currency,
    cast(t.quantity AS DOUBLE)                         AS quantity,
    cast(t.price AS DOUBLE)                            AS price,
    cast(t.consideration AS DOUBLE)                    AS settlement_amount,
    cast(t.fee_amount AS DOUBLE)                       AS fee_amount,
    cast(t.transaction_date AS DATE)                   AS transaction_date,
    cast(t.payment_date AS DATE)                       AS value_date,
    cast(t.intended_settlement_date AS DATE)           AS settlement_date,
    cast(t.completed_date AS DATE)                     AS posted_on,
    lower(t.status)                                    AS status,
    t.movement_type,
    t.fee_type,
    t.order_id,
    t.provider_ref
FROM {seccl}.transactions t
WHERE t.account_id IN (SELECT UNNEST(?::VARCHAR[]))
-- ONE ROW PER TRANSACTION ID, which is the conformed grain: the Seccl model's header says so
-- outright ("Destination-account doubling -- the reversed In/Out perspective for the receiving
-- account -- belongs to the balance layers, not this grain") and enforces it with a
-- `unique: external_id` check.
--
-- The extract is a MART table, not the raw feed, so it carries both legs of an internal transfer
-- under one id: 30 ids, 60 rows, all `Cash Transfer / Internal`. Without this the upsert on
-- `(tenant_id, source_system, source_transaction_id)` silently collapsed each pair, and the
-- survivor was a
-- CORRUPTION rather than a loss -- it kept the first leg's `account_id` (the DO UPDATE does not
-- touch it) while taking the second leg's type and amount, so one account showed a transfer in
-- the opposite direction to the money that actually moved.
--
-- Ordered so the choice is deterministic and a re-run is a no-op. The accepted cost, stated
-- rather than hidden: an internal transfer appears on one of its two accounts. That is exactly
-- what the pipeline produces, which is the point.
QUALIFY row_number() OVER (PARTITION BY t.id ORDER BY t.account_id, t.movement_type) = 1
ORDER BY t.transaction_date
"""
)

#: Rows vs distinct ids, so the dedupe above reports what it dropped instead of leaving the
#: difference to be spotted in a row count.
_LEG_COUNT_SQL = """
SELECT count(*) AS rows, count(DISTINCT t.id) AS ids
FROM {seccl}.transactions t
WHERE t.account_id IN (SELECT UNNEST(?::VARCHAR[]))
"""

# Account-level daily valuations: the asset series summed per account per day, with the cash
# balance beside it. `platform.valuations` is account x date, which is the grain the conformed
# layer publishes and the grain the value chart reads.
_VALUATIONS_SQL = """
WITH assets AS (
    SELECT account_id, date,
           sum(valuation)         AS market_value,
           sum(total_book_cost)   AS book_cost,
           sum(growth)            AS unrealised_gain,
           sum(opening_valuation) AS opening_value
    FROM {seccl}.asset_balances_daily
    WHERE account_id IN (SELECT UNNEST(?::VARCHAR[])) AND valuation IS NOT NULL
    GROUP BY account_id, date
),
cash AS (
    SELECT account_id, date, sum(balance) AS cash_value
    FROM {seccl}.cash_balances_daily
    WHERE account_id IN (SELECT UNNEST(?::VARCHAR[]))
    GROUP BY account_id, date
)
SELECT coalesce(a.account_id, c.account_id)  AS account_external_id,
       coalesce(a.date, c.date)              AS as_of_date,
       -- An account with cash and no assets is a real state (a funded account before its first
       -- order), so it gets a valuation row rather than being dropped by an inner join.
       cast(coalesce(a.market_value, 0) + coalesce(c.cash_value, 0) AS DOUBLE) AS market_value,
       cast(c.cash_value AS DOUBLE)          AS cash_value,
       cast(a.book_cost AS DOUBLE)           AS book_cost,
       cast(a.unrealised_gain AS DOUBLE)     AS unrealised_gain,
       cast(a.opening_value AS DOUBLE)       AS opening_value
FROM assets a
FULL OUTER JOIN cash c ON c.account_id = a.account_id AND c.date = a.date
ORDER BY as_of_date
"""


def read_extract(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    import duckdb

    location = extract_path()
    conn = duckdb.connect(str(location), read_only=True)
    try:
        # The catalog and the schema are BOTH named `seccl` (DuckDB names a catalog after the
        # file), so a bare `seccl.transactions` is ambiguous and binds to neither -- the same
        # qualification `app/lake/fixture.py` documents.
        qualified = f'"{location.stem}"."seccl"'
        cursor = conn.execute(sql.format(seccl=qualified), params)
        names = [d[0] for d in cursor.description or []]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    finally:
        conn.close()


def shift(rows: list[dict[str, Any]], keys: tuple[str, ...], days: int) -> None:
    """Move every date forward by `days`, in place.

    ALL rows by the SAME offset, so the spacing between a contribution and the fee it triggered is
    preserved. A per-row jitter would invent a sequence of events that never happened.
    """
    if not days:
        return
    delta = dt.timedelta(days=days)
    for row in rows:
        for key in keys:
            value = row.get(key)
            if isinstance(value, dt.date):
                row[key] = value + delta


def date_offset(ids: list[str]) -> int:
    """How many days to move EVERYTHING forward so the newest date in the extract lands today.

    ONE offset for transactions and valuations together, taken from the latest date in either.
    Shifting each relation onto today separately looks tidier and is wrong: the extract's
    transactions run three months past its valuations, so a per-relation offset would slide the two
    apart by that much — a deposit would stop lining up with the step it caused in the value chart,
    and reconciliation would look broken on data that reconciles perfectly.

    The consequence is stated rather than hidden: the valuation series ends where it really ends,
    some months short of today.
    """
    newest = read_extract(
        """
        SELECT max(d) AS newest FROM (
            SELECT max(cast(transaction_date AS DATE)) AS d FROM {seccl}.transactions
             WHERE account_id IN (SELECT UNNEST(?::VARCHAR[]))
            UNION ALL
            SELECT max(date) FROM {seccl}.asset_balances_daily
             WHERE account_id IN (SELECT UNNEST(?::VARCHAR[]))
            UNION ALL
            SELECT max(date) FROM {seccl}.cash_balances_daily
             WHERE account_id IN (SELECT UNNEST(?::VARCHAR[]))
        )
        """,
        [ids, ids, ids],
    )[0]["newest"]
    return (dt.date.today() - newest).days if newest else 0


async def resolve_accounts(s: Any, tenant_id: int) -> dict[str, dict[str, Any]]:
    """{seccl external_id -> {account_id, contact_id}} for this tenant's linked accounts.

    RLS is bypassed for seeding, so the tenant is narrowed EXPLICITLY on both tables — a seeder
    that wrote another tenant's ledger would be indistinguishable from a pipeline bug.
    """
    rows = (
        (
            await s.execute(
                text(
                    "SELECT pl.external_id, a.id AS account_id, a.contact_id "
                    "FROM platform.links pl "
                    "JOIN platform.accounts a ON a.id = pl.entity_id "
                    "WHERE pl.tenant_id = :t AND a.tenant_id = :t "
                    "  AND pl.provider = :p AND pl.entity_type = 'account' "
                    "  AND pl.link_type = 'primary'"
                ),
                {"t": tenant_id, "p": PROVIDER},
            )
        )
        .mappings()
        .all()
    )
    return {r["external_id"]: dict(r) for r in rows}


async def seed_transactions(
    s: Any,
    tenant_id: int,
    by_external: dict[str, dict[str, Any]],
    ids: list[str],
    days: int,
) -> None:
    rows = read_extract(_TRANSACTIONS_SQL, [ids])
    legs = read_extract(_LEG_COUNT_SQL, [ids])[0]
    print(f"\n{legs['rows']} transaction rows in the extract for these accounts.")
    if legs["rows"] != legs["ids"]:
        # Named, because a silent difference between "rows read" and "rows written" is what
        # hid the collapsed-pair corruption the dedupe above fixes.
        print(
            f"  {legs['rows'] - legs['ids']} are the second leg of a doubled internal "
            "transfer, dropped to keep one row per transaction id (the conformed grain)."
        )

    # Reported BY REASON, because "this firm looks empty" and "we refused 200 rows" read the same
    # on screen and only one of them is worth investigating.
    unmapped: dict[tuple[Any, Any, Any], int] = {}
    keep: list[dict[str, Any]] = []
    for row in rows:
        if row["type"] is None:
            key = (row["source_type"], row["movement_type"], row["source_subtype"])
            unmapped[key] = unmapped.get(key, 0) + 1
            continue
        keep.append(row)
    if unmapped:
        print(f"  skipped {sum(unmapped.values())} with no canonical type:")
        for (source_type, movement, subtype), count in sorted(
            unmapped.items(), key=lambda kv: -kv[1]
        ):
            print(f"    {count:>5}  {source_type} / {movement} / {subtype}")

    shift(keep, ("transaction_date", "value_date", "settlement_date", "posted_on"), days)

    statement = text(
        "INSERT INTO platform.transactions "
        "  (tenant_id, account_id, contact_id, source_system, source_transaction_id, type, "
        "   source_type, source_subtype, isin, asset_name, amount, currency, quantity, price, "
        "   settlement_amount, fee_amount, transaction_date, value_date, settlement_date, "
        "   posted_at, status, data) "
        "VALUES (:tenant_id, :account_id, :contact_id, :source_system, :source_transaction_id, "
        "        CAST(:type AS platform.transaction_type), :source_type, :source_subtype, :isin, "
        "        :asset_name, :amount, :currency, :quantity, :price, :settlement_amount, "
        "        :fee_amount, :transaction_date, :value_date, :settlement_date, :posted_at, "
        "        :status, CAST(:data AS jsonb)) "
        "ON CONFLICT (tenant_id, source_system, source_transaction_id) DO UPDATE SET "
        "  type = EXCLUDED.type, amount = EXCLUDED.amount, status = EXCLUDED.status, "
        "  transaction_date = EXCLUDED.transaction_date, value_date = EXCLUDED.value_date, "
        "  settlement_date = EXCLUDED.settlement_date, posted_at = EXCLUDED.posted_at, "
        "  data = EXCLUDED.data"
    )
    for row in keep:
        account = by_external[row["account_external_id"]]
        posted = row["posted_on"]
        await s.execute(
            statement,
            {
                "tenant_id": tenant_id,
                "account_id": account["account_id"],
                "contact_id": account["contact_id"],
                "source_system": PROVIDER,
                "source_transaction_id": row["source_transaction_id"],
                "type": row["type"],
                "source_type": row["source_type"],
                "source_subtype": row["source_subtype"],
                "isin": row["isin"],
                "asset_name": row["asset_name"],
                "amount": row["amount"],
                "currency": row["currency"],
                "quantity": row["quantity"],
                "price": row["price"],
                "settlement_amount": row["settlement_amount"],
                "fee_amount": row["fee_amount"],
                "transaction_date": row["transaction_date"],
                "value_date": row["value_date"],
                "settlement_date": row["settlement_date"],
                # `posted_at` is a timestamptz; the extract's completed_date is a date.
                "posted_at": (
                    dt.datetime.combine(posted, dt.time(), tzinfo=dt.UTC) if posted else None
                ),
                "status": row["status"],
                # The provider's own words, kept where the pipeline keeps them. `method` is read
                # from here by the listing (`t.data ->> 'method'`), which is why the movement
                # direction goes in rather than being dropped -- it is the only thing that says a
                # positive `amount` was money out.
                "data": json.dumps(
                    {
                        "movement_type": row["movement_type"],
                        "fee_type": row["fee_type"],
                        "order_id": row["order_id"],
                        "provider_ref": row["provider_ref"],
                        "seeded_from": "docs/seccl.duckdb",
                    }
                ),
            },
        )
    print(f"  wrote {len(keep)} transactions.")


async def seed_valuations(
    s: Any,
    tenant_id: int,
    by_external: dict[str, dict[str, Any]],
    ids: list[str],
    days: int,
) -> None:
    rows = [r for r in read_extract(_VALUATIONS_SQL, [ids, ids]) if r["as_of_date"]]
    print(f"\n{len(rows)} account-day valuations in the extract for these accounts.")

    shift(rows, ("as_of_date",), days)

    statement = text(
        "INSERT INTO platform.valuations "
        "  (tenant_id, account_id, as_of_date, source_system, market_value, cash_value, "
        "   book_cost, unrealised_gain, opening_value, currency) "
        "VALUES (:tenant_id, :account_id, :as_of_date, :source_system, :market_value, "
        "        :cash_value, :book_cost, :unrealised_gain, :opening_value, 'GBP') "
        "ON CONFLICT (account_id, as_of_date, source_system) DO UPDATE SET "
        "  market_value = EXCLUDED.market_value, cash_value = EXCLUDED.cash_value, "
        "  book_cost = EXCLUDED.book_cost, unrealised_gain = EXCLUDED.unrealised_gain, "
        "  opening_value = EXCLUDED.opening_value"
    )
    for row in rows:
        account = by_external[row["account_external_id"]]
        await s.execute(
            statement,
            {
                "tenant_id": tenant_id,
                "account_id": account["account_id"],
                "as_of_date": row["as_of_date"],
                "source_system": PROVIDER,
                "market_value": row["market_value"],
                "cash_value": row["cash_value"],
                "book_cost": row["book_cost"],
                "unrealised_gain": row["unrealised_gain"],
                "opening_value": row["opening_value"],
            },
        )
    print(f"  wrote {len(rows)} valuations.")


async def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Seed the local book's ledger from the extract.")
    parser.add_argument("tenant_alias", nargs="?", default=DEFAULT_TENANT_ALIAS)
    parser.add_argument(
        "--shift-to-today",
        action="store_true",
        help="move every date forward so the newest transaction lands today",
    )
    parser.add_argument("--transactions-only", action="store_true")
    parser.add_argument("--valuations-only", action="store_true")
    parser.add_argument(
        "--clear",
        action="store_true",
        help=f"delete existing source_system='{PROVIDER}' rows for the tenant first",
    )
    args = parser.parse_args(argv)

    # The posture `app/lake/fixture.py` and the email send guard take: anonymised numbers from
    # somebody else's book must never reach a real client.
    if settings.app_stage == "production":
        raise SystemExit("Refusing to seed a production deployment.")

    engine = create_async_engine(settings.migration_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))  # BYPASSRLS for seeding

        tenant_id = (
            await s.execute(
                text("SELECT id FROM auth.tenants WHERE alias = :a"), {"a": args.tenant_alias}
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            raise SystemExit(f"No tenant with alias '{args.tenant_alias}'")

        by_external = await resolve_accounts(s, tenant_id)
        if not by_external:
            raise SystemExit(
                f"Tenant '{args.tenant_alias}' has no {PROVIDER} account links -- nothing to key "
                "the extract by. Run scripts/ingest_seccl.py first."
            )
        ids = sorted(by_external)
        print(f"{len(ids)} {PROVIDER}-linked accounts on tenant '{args.tenant_alias}'.")

        if args.clear:
            for table in ("transactions", "valuations"):
                # Counted before deleting rather than off `rowcount`, which is on `CursorResult`
                # and not on the `Result` an AsyncSession is typed to return.
                scoped = "WHERE tenant_id = :t AND source_system = :p"
                count = (
                    await s.execute(
                        text(f"SELECT count(*) FROM platform.{table} {scoped}"),  # noqa: S608
                        {"t": tenant_id, "p": PROVIDER},
                    )
                ).scalar_one()
                await s.execute(
                    text(f"DELETE FROM platform.{table} {scoped}"),  # noqa: S608 - fixed names
                    {"t": tenant_id, "p": PROVIDER},
                )
                print(f"  cleared {count} {table}.")

        days = date_offset(ids) if args.shift_to_today else 0
        if days:
            print(f"  shifting every date forward by {days} days (newest lands today).")

        if not args.valuations_only:
            await seed_transactions(s, tenant_id, by_external, ids, days)
        if not args.transactions_only:
            await seed_valuations(s, tenant_id, by_external, ids, days)

    await engine.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(sys.argv[1:]))
