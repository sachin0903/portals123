# intelliflo-sync

The Intelliflo Office integration engine: pushes SEI client/valuation/transaction/income-statement
data to Intelliflo for firms approved in the `integrations` admin dashboard, and receives
Intelliflo's WebSub webhooks. Ported from the `sei_reports` repo's `src/integrations`
(branch `feat/porting-integrations-from-platform`) — see [`docs/integrations.md`](../../../docs/integrations.md)
for the `integrations.*` schema this reads and writes, which belongs to `apps/api/migrations`, not
this service.

Lives under `services/integrations/` (not a flat `services/intelliflo`) because Intelliflo is the
only provider today, not the only one planned — `src/intelliflo_sync/providers/` is built to hold
more.

## Layout

Three deployables from one package (see `Dockerfile`'s three targets and `moon.yml`'s three
`dev-*` tasks):

```
src/intelliflo_sync/
  scheduler.py        the cron scheduler — one job per (integration, cron), run via `intelliflo-engine`
  main.py             run every due feature once and exit, via `intelliflo-engine-run` (moon: run-once)
  tenant_scheduler.py tenant discovery/sync on its own schedule, via `intelliflo-tenant-sync`
  webhook/            the central webhook receiver (FastAPI), via `intelliflo-engine-webhook`
  providers/intelliflo/   the Intelliflo-specific sync logic (endpoints, transformer, webhook)
    views/            the SEI-datalake JOINs each feature's query builds on (see below)
    views.py          create_views(con) — runs every views/*.sql file on an open lake connection
  shared/             DB helpers, the lake connection, notifications, the scheduling/contract glue
  migrations/          intelliflo/seeds — local-only dev fixtures (`intelliflo-seed`)
```

No `scripts/pg/` and no `scripts/duck/` here: the `integrations` Postgres schema is created and
evolved by `apps/api/migrations` (ported back from `sei_reports` alongside this service), and the
SEI lake this service reads (`shared/lake.py`) is `services/sei`'s own catalogue — this service is
`lake_reader` there, never a writer, so it has no migrations of its own to run on either side.

**`providers/intelliflo/views/*.sql`** hold each feature's JOINs against `core.platform.*`/
`core.raw.*`/`core.ref.*` (`core` being `shared/lake.py`'s read-only attached catalogue) — the
parts of a query that don't depend on a call's own firm/dsr/date. `views.create_views(con)` runs
them on every `open_lake()` connection, each as a plain `CREATE OR REPLACE VIEW` in the
connection's own default catalog (`memory` — `core` is READ_ONLY, so nothing can be created
there). `providers/intelliflo/db.py`'s functions then only add the per-call filtering and
dedup/ranking that genuinely depends on a parameter (firm, dsr_codes, data_date) on top of the
view, instead of one large inline query per feature — the same split the original (un-ported)
`sei_reports` views had, just pointed at the new schema.

## Datalake migration status

`shared/lake.py` attaches read-only to the **shared SEI core catalogue** — the same whole-firm
`platform.*`/`raw.*` DuckLake `services/sei`'s own `sei-sync` daemon builds (`docs/datalake.md`),
not a lake of this service's own. This replaced an earlier, standalone DuckLake (`reporting.*`
tables, sei_reports' own, never ported to portals) that this service originally pointed at.

Each Intelliflo feature's query was migrated from the old shape onto the new one separately, since
the two schemas are not alike enough for a find-and-replace — entity tables (`platform.clients`,
`platform.accounts`) expose current state only, `platform.transactions` is a genuine full ledger
(every SFTP partition, explicitly not collapsed to the latest drop), but `platform.transaction_details`
and `platform.positions` — despite being built from the same report families — DO collapse to the
single latest day, which is wrong for the two features that need a specific day's data. Several old
columns (`ACCOUNT_TYPE`, `ACCOUNT_STATUS_CODE`) also map differently than before. **All
features are now migrated.**

| Feature              | Source                                                                                                                                                                                                      |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Transactions         | `platform.transactions` + `platform.instruments` + `ref.codes` (`views/transactions.sql`)                                                                                                                   |
| IncomeStatementItems | `raw.transaction_detail` (not `platform.transaction_details` — see below) + `platform.transactions` + `raw.client_account_link` (`views/income_statement_fee_lines.sql` + `views/client_account_links.sql`) |
| Valuations           | `raw.eod_positions` (not `platform.positions` — see below) + `platform.instruments` + `platform.accounts` (`views/positions.sql`)                                                                           |

Two of the three (`IncomeStatementItems`, `Valuations`) read a `raw.*` table directly instead of the
conformed model built from it, for the same reason in both cases: the conformed model keeps only
the single latest day's drop, confirmed by reading each model's own query (`platform.positions`:
`WITH latest AS (SELECT max(dt)...)`, no comment explaining why — the exact shape
`platform.transactions`, built from the same report family, explicitly rejected). A specific day's
fee lines or holdings is exactly what these two features need, so both go to `raw.*` directly with
their own dedup (latest known value for the same id — IncomeStatementItems — or a redelivery guard
within the one `dt` being read — Valuations) rather than inherit a "latest only" shape. Confirmed
against the live schema that `TransactionDetail`/`EODPositions` are themselves daily activity feeds
(new rows each day, not a full resend) — if that ever changes, the fix belongs in `services/sei`'s
own models, not here.

Valuations also had no prior working query to port at all — the old `vw_pension_provider_aums_daily`
lived in sei_reports' separate, un-ported `sei_daily_reports` module — so its mapping was rebuilt
from that view's own source directly (not reconstructed from a docstring), while deliberately
leaving out everything `platform.positions` computes that this feed doesn't send: account-base-
currency conversion, `DEBIT_CREDIT_FLAG` signing, unrealised-gain derivation, and the
`eod_positions_supplemental` join — all of that exists for `market_value`/`book_cost`/`gain`, none
of which Intelliflo's valuation batch wants. `UnitPrice`/`CurrencyCode` are the instrument's own
price and currency (`ASSET_PRICE`/`ASSET_CURRENCY`, straight off the position row), not an
account-base conversion — Intelliflo expects a price exactly as a custodian statement would quote
it. `Units` is `SETTLED_QTY`, not `QUANTITY_HELD` (`platform.positions`' own `quantity` column) —
matching this module's own pre-existing header comment ("Settled Quantity → Units"), which predates
this migration and reflects what the feature always sent. Grain is one row per taxlot, no
aggregation across taxlots of one holding, matching the old (un-ported) view exactly.

Deliberate behaviour changes from the old queries, documented in `providers/intelliflo/db.py`'s
docstrings and all _widening_ rather than narrowing what syncs:

- DSR filtering used to match one arbitrary numbered slot (`account_dsr_team_id_2`) out of ten the
  old SEI report carried, with no record anywhere of why slot 2. `platform.accounts`/
  `platform.transactions` already resolve an account's FULL granted team list; filtering against
  it is strictly more correct.
- Transactions: amounts arrive already signed (the model resolves SEI's separate
  `DEBIT_CREDIT_FLAG_n` columns once), so the old view's manual debit/credit math is gone —
  nothing to port there, just less code.

**IncomeStatementItems reads `raw.transaction_detail` directly, bypassing the conformed
`platform.transaction_details`.** That model keeps only the single latest day's drop
(`WITH latest AS (SELECT max(dt)...)`, no comment explaining why) — correct for an entity's
current state, wrong for an activity feed. `platform.transactions`, built from the same report
family, explicitly rejected that shape for the identical reason (its own docstring: TransactionHeader
is a daily activity file, and taking only the newest drop "published a one-day window and silently
discarded everything before it"). `TransactionDetail` behaves the same way — confirmed against the
live schema, not assumed. The query is written to be correct either way regardless: it deduplicates
`raw.transaction_detail` to the latest known value per `TRANSACTION_DETAIL_ID` across the whole
table (not capped to one global `max(dt)`), which gives the right answer whether a given id's row
is a daily activity entry (nothing lost) or a periodic resend (the latest resend already contains
everything, so this is equivalent to `platform.transaction_details`'s own shape). It's read directly
rather than through the conformed table so this feature doesn't depend on `platform.transaction_details`'s
"only the latest drop" choice being revisited in `services/sei`. Separately, `raw.client_account_link`'s
relationship column is named `"RELATIONSHIP"` — confirmed against the live schema, not guessed.

## Run

```bash
cp .env.example .env               # INTELLIFLO_*, PG_*, DATALAKE_CATALOG_DSN, SLACK/RESEND — see .env.example
moon run intelliflo-sync:install

moon run intelliflo-sync:dev-scheduler
moon run intelliflo-sync:dev-tenant-sync
moon run intelliflo-sync:run-once          # one-off backfill; RUN_PROVIDER/RUN_FEATURE/DATA_DATE filter it
```

`dev-webhook` (`:4000`, WebSub receiver) exists but isn't run — not needed today.

Local Intelliflo sandbox seed data: `APP_ENV=local uv run intelliflo-seed` (copy
`src/intelliflo_sync/migrations/intelliflo/seeds/local.example.yaml` to `local.yaml` first — see
that file and `migrations/seed.py`'s docstring).

## Known gaps from the port (not yet done)

- `shared/config.py` uses `pydantic-settings` now (done), but `providers/intelliflo/*` has its own
  OAuth client (`shared/base/auth.py`) instead of the already-ported `pkgs/intelliflo` one —
  consolidating is a follow-up, not required to run.
- `mypy` is configured but not run in `strict` mode (the ported code isn't annotated to that bar
  yet, mostly in `providers/intelliflo/integration.py`'s dynamically-typed row handling).
- The `raw.client_account_link` relationship column name (`"RELATIONSHIP"`) and the
  `TransactionDetail`/`EODPositions` daily-activity-feed behavior were both confirmed against a
  real, populated SEI datalake (not just reasoned about) — see "Datalake migration status" above.
