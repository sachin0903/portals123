# seccl — Seccl platform sync on interlace v2

Replaces the v1 dataflow pull (forced full replace of every table every 30
minutes, in-memory watermarks, no webhooks) with change-capture-first sync:

```
Seccl outgoing webhooks ──HMAC──▶ gateway (:8101) ──enrich──▶ interlace streams (:8100)
                                                                    │ flush triggers
raw.* extractors (cron)  ──cursor / full_merge──▶ warehouse ◀── staging merges
                                                     │
                                              seccl.* views (baseline ⊕ deltas)
```

Three schemas, one per layer: **`raw`** lands the API verbatim (snake_cased,
nested values as JSON), **`staging`** merges the webhook streams, and **`seccl`**
is the consumption layer the per-tenant chain projects from. `streams` holds the
durable webhook log.

That chain is `pkgs/book-sink`, registered by `models/tenants.py` INSIDE this
project (`docs/provider-pipeline.md`) — not the deleted `app/fanout.py`, and the
relation is `platform.<table>`, never a `{env}__`-prefixed one: the environment
prefix was removed and `tests/test_platforms.py` guards against its return.

- **Webhook-fed** (realtime): transactions, positions, clients, exceptions,
  messages, transfer rejections. The gateway verifies the `webhook-signature`
  HMAC, enriches thin events into full records via the API, and publishes to
  `POST /streams/{name}`; interlace re-runs the staging merges on every flush.
- **Cursor extractors** (the endpoints with date filters): notifications
  (`createdFrom`), messages (`fromDate`), transactions (`transactionDateFrom` +
  open-status sweep), asset prices (`dateFrom`). The cursor is
  `max(column)` of the model's own previous materialisation — injected by
  interlace, so incremental state lives in the warehouse and survives restarts.
  Notifications, messages and transactions also read that max from the
  environment view when the injected cursor is empty (a changed model is a new
  table) and republish the stored rows with the new scan, so a rebuild does
  not list the endpoint with no date filter.
- **full_merge listings** (endpoints with no change signal): clients, accounts,
  users, nodes, models, client products, scheme products, master parties,
  statics, assets, portfolios, illustrations + per-entity fan-outs. A full pull
  is unavoidable API-side, but it lands as a SQL set-difference diff — only
  new/changed/vanished rows are written.
- **Client documents** (`raw.files`, then `raw.file_copies`): a message whose
  `payload.id` names a file has a PDF behind it (contract notes, statements,
  illustrations, tax packs). Seccl has no firm document library — every PDF in
  the collection is a client message. The firm-scoped calls are an accounts CSV
  (`POST /data/accounts`) and a blank drawdown form, neither of which is filed
  here. `raw.files` anti-joins its own table, then mints the pre-signed URL,
  downloads and uploads **every pending document concurrently** (bounded by
  `SECCL_CONCURRENCY`). Bytes land in the core bucket at
  `s3://platforms/raw/seccl/files/{firm}/{type}/{client}/`; only metadata lands
  in the warehouse. `raw.file_copies` then copies each PDF into the tenant
  bucket at `files/provider/seccl/{type}/{client}/{name}` and files a
  `documents.documents` row (`source='provider'`, not deletable) against the
  client, once that client is in the tenant's book. `{name}` is Seccl's
  filename, which is already the file id, so the id is not prefixed a second
  time. A generic name such as `note.pdf` is still prefixed. The library's
  created date is the message `created` time, not the night the copy ran.
  A document that fails, or
  whose client has not been merged yet, is retried next run and is never fatal
  to the batch.

## Entities

| Model                                                                              | Source                           | Sync                                          | Schedule           |
| ---------------------------------------------------------------------------------- | -------------------------------- | --------------------------------------------- | ------------------ |
| `streams.*` — transactions/positions/clients/exceptions/messages/transfers         | webhooks                         | realtime, drift=evolve, 30d retention         | on delivery        |
| `raw.transactions`                                                                 | /portfoliotransaction            | cursor + open-status sweep                    | hourly weekdays    |
| `raw.notifications`, `raw.messages`                                                | /notification, /message          | cursor                                        | every 30m weekdays |
| `raw.asset_prices`                                                                 | /price per asset                 | cursor                                        | 07:00 weekdays     |
| `raw.clients/accounts/assets/portfolios/nodes/users/models/client_products`        | lists                            | full_merge diff                               | nightly 02:00      |
| `raw.account_details/account_summaries/account_reports`, `raw.portfolio_summaries` | per-entity fan-out               | full_merge diff                               | nightly 02:30      |
| `raw.fee_structures`                                                               | /v1/fee-structures               | full_merge diff (empty is legitimate)         | nightly 02:00      |
| `raw.illustrations`, `raw.third_party_products`                                    | lists                            | full_merge diff                               | nightly 03:00      |
| `raw.master_parties`, `raw.nationalities`, `raw.transaction_codes`                 | lists/static                     | full_merge diff                               | weekly             |
| `raw.files`                                                                        | `POST /files/{type}` per message | `this` anti-join, bytes to the core bucket    | nightly 03:30      |
| `raw.file_copies`                                                                  | `raw.files`, `raw.messages`      | copy into the tenant bucket + `documents.*`   | nightly 03:45      |
| `raw.dashboard_information`, `raw.dashboard_exceptions`                            | dashboard                        | full refresh (singleton)                      | hourly weekdays    |
| `staging.*`                                                                        | streams                          | merge, latest event per key                   | on stream flush    |
| `seccl.clients`                                                                    | staging ⊕ raw                    | view (current-state overlay)                  | —                  |
| `seccl.transactions`                                                               | staging ⊕ raw + joins            | view (overlay + business derivations)         | —                  |
| `seccl.positions`                                                                  | accounts.positions[] ⊕ webhooks  | view (current holdings)                       | —                  |
| `seccl.nodes`                                                                      | `raw.nodes`                      | table (recursive lineage paths)               | with `raw.nodes`   |
| `platform.fee_packages`                                                            | `raw.fee_structures`             | table (rule derived; firm-wide)               | with the raw pull  |
| `derived.account_terms`                                                            | `/account/{id}` + `raw.models`   | view — fees and standing payments/withdrawals | with the fan-out   |
| `derived.book_costs`                                                               | `raw.transactions` + accounts    | replace — pooled cost basis + realised gain   | nightly 02:45      |

The book has carried `platform.accounts.{fees,payments,withdrawals}` since 0002 and nothing wrote
them, so every account record read "none recorded". `derived.account_terms` shapes the fan-out's
own charge and `recurringPayments` into those lists; `platform.accounts` LEFT JOINs them. SEI
publishes typed NULLs for the same columns (it has no per-account schedule in the drop).

The same boundary dropped the statement facts on `platform.transactions`. The conformed
transactions model now publishes `report_group_kind`, the dealing/other charge split, `fee_type`,
pooled `book_cost` / `realised_gain` (from `derived.book_costs`), `trade_date`,
`equalisation_payment` and `subscription_amount` (a per-movement fact; remaining ISA allowance
stays `platform.accounts.allowance_*`). SEI stubs those columns as typed NULLs so the shared
book-sink select still binds.

## Running

```bash
moon run seccl-sync:install
moon run seccl-sync:dev        # interlace daemon: streams API + scheduler on :8100
moon run seccl-sync:gateway    # webhook gateway on :8101
moon run seccl-sync:plan | apply | run | test | lint | typecheck
```

The models' crons (listings 02:00, fan-outs 02:30, files 03:30, cursors every
30 minutes weekdays 06:00–18:00) fire from the scheduler. CronTrigger does not
fire on first sight, so a boot between slots waits until the next one.

Copy `.env.example` to `.env` (or export the vars) first — `apply`/`run` work
standalone (stream tables are ensured automatically). Production auth is
**private_key_jwt**: Seccl registers your public key (kid) against an Auth0
M2M client; the PEM signs 60s client assertions exchanged for ~2h bearer
tokens (auto-refreshed).

Register the webhooks once per environment (after the gateway is publicly
reachable):

```bash
uv run python -m seccl_sync.webhooks_setup            # all six
uv run python -m seccl_sync.webhooks_setup --kinds transaction,position
```

## Running against production

```bash
uv run --env-file .env.production interlace plan | apply | run
```

`.env.production` is gitignored (`.env.*`). It is the only production env file.
Isolate the run with its own **`DATALAKE_WRITER_DSN`** — a separate database — rather than a
separate metadata schema: `metadata_schema` is a literal `seccl` in `interlace.yaml` because
both `provider_schema()` implementations derive it and neither reads a variable, so a run
pointed at `seccl_prod` wrote where no reader looks. Give it its own `SECCL_STATE_PATH` /
`SECCL_STREAM_PATH` too, and set `SECCL_FILES_MAX_PER_RUN` for the first document pull (an
established firm has thousands). The deployed pipeline does not read this file: production
credentials are the GitHub `prod` Environment (`SECCL_PRIVATE_KEY_B64` is a secret there).
Everything the extractors do is a read (`POST /files/{type}` only mints a download URL) — but
**do not** run `webhooks_setup` against production unless you mean to register webhooks there.

## Schema evolution — what interlace does and doesn't absorb

Seccl adds fields regularly, so the landing layer leans on evolution rather than
declared schemas. Verified behaviour for these `merge` / `full_merge`
models:

| Seccl changes               | Result                                                                                                                                                                                                        |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **adds a column**           | Absorbed automatically. `records_to_table` unions the keys across the batch, and interlace adds the column to the physical table on the next merge — no declaration, no fingerprint change, values populated. |
| **stops sending a column**  | The column survives, but re-merged rows get NULL for it. `merge` replaces whole rows, so a field missing from one batch erases the values it had captured for those keys.                                     |
| **changes a column's type** | The column is retyped and the table **rebuilt** — prior rows for that model are lost.                                                                                                                         |

So evolution covers the common case (new fields) and the two destructive cases
are type drift. That is why the `seccl.*` layer casts every numeric explicitly
and why key/cursor columns (and `charges`) are pinned in each model's `spine`:
a pinned column keeps its declared type no matter what the batch looks like.

## Webhooks are optional

The pull-side sync is complete on its own: `interlace apply`/`run` ensure the
declared stream tables (empty) so the staging merges and seccl views build without
the gateway or any webhook registration — the seccl views then serve the raw
baselines verbatim. Deploy the gateway + register the webhooks when you want
intraday freshness; nothing else changes.

## What the previous integration's transform layer maps to

The old dataflow's raw extracts are all here (every entity, same endpoints).
Its transform SQL maps as follows:

- **Ported**: node lineage (`seccl.nodes` — minus the hardcoded per-firm fee
  tiers, which are configuration, not warehouse data), position unnesting
  (`seccl.positions`), and the transaction business derivations
  (`seccl.transactions`: resolved ISIN, executed overrides, order grouping,
  event dates, charge split, fee classification, DFM/adviser rebalance origin).
  One row per transaction id — the old destination-account doubling (reversed
  In/Out for the receiving account) is a balance-layer concern, not this grain.
- **Deferred (the analytics layer — port when tenant reporting needs it)**:
  cash/asset balances + dailies, TWRR/MWRR performance chains, the
  book-cost/CGT engine (30-day share matching, Seccl's 2dp running-total
  rounding), fee estimation. The reconciliation conventions these must follow
  are recorded under "Seccl API knowledge" below.
- **Excluded (firm-specific, not generic warehouse)**: Epworth CSV overlays
  and reports, True Potential daily-valuation exports, CPI benchmark chains,
  hardcoded node→firm fee tiers.

## Seccl API knowledge

Verified against the staging API **and against production** (a full run over the
SELCT production firm — see "Running against production" below); the enums and
conventions come from years of running the previous integration against it.

- **Envelope**: lists are `{"data": [...], "meta": {"count": N}}`, single
  resources `{"data": {...}}`. Pagination is page-number only
  (`?page=N&pageSize=M`, total from `meta.count`) — no cursor tokens. Several
  list endpoints return the full set when `pageSize` is omitted but still carry
  `meta.count`; always paginate them so a server-side cap can't truncate.
- **`pageSize` must stay small on the nested-payload lists.** Production answers
  `GET /account/list/{firm}?pageSize=500` with a bare **500 Internal Server
  Error** — an account embeds its whole `positions[]`, so the page is enormous.
  Every `EntitySpec` therefore pins `page_size=100` (the size the previous
  integration ran for years); illustrations need 10. Pinning `pageSize` also
  opts the entity out of `get_list`'s refetch-as-one-exactly-sized-page
  shortcut, which would reintroduce the same oversized request.
- **Scalar types drift per record, so numerics can land as VARCHAR.**
  `records_to_table` widens a column whose scalar types disagree, which is
  correct — but it means the landed type depends on the batch. In production
  `executedQuantity` and `bookValue` land VARCHAR while their partners
  `quantity`/`transferValue` land DOUBLE, so an untyped
  `coalesce(executed_quantity, quantity)` fails to bind ("cannot mix VARCHAR and
  DOUBLE"). **Every numeric in the `seccl.*` layer is `try_cast` on both sides**
  — never taken at its landed type.
- **Ids**: `firmId` (path segment, the tenant boundary), integer `nodeId`
  (hierarchical via `parentNodeId` — firms map to node-id sets), short
  alphanumeric client/account/asset/transaction ids. **Position ids are
  composite `{accountId}|{assetId or C}|{ccy}` strings and there is no
  GET /position endpoint** — a position is read from its account summary's
  `positions[]`.
- **Account ↔ model/wrapper**: `wrapperDetail.assetAllocationId` links an
  account to its model; `wrapperDetail.wrapperType` is the product (GIA/ISA/
  SIPP…). A model's target-return % is (ab)used into `models.url`.
- **Transactions**: `transactionType` ∈ Payment / Cash Transfer / Cash / Order /
  Stock / Stock Transfer / Product Transfer; `movementType` ∈ In / Out /
  Invest / Sell; lifecycle `status` ∈ Completed / Calculating / Checking /
  Pending / Placed / Waiting / Deleted (the non-completed set is what the
  open-status sweep re-pulls — status changes don't move `transactionDate`).
  `executedQuantity`/`executedAmount` supersede `quantity`/`amount`;
  `information` carries `originalIsin`, `eventExDate` (ex-date),
  `eventPayDate`, equalisation; `charges[].chargeType` splits Dealing vs other;
  orders group via `linkId`/`coreId`/`eventId` (>1 account per `eventId` ⇒ DFM
  rebalance).
- **Cash value-date conventions** (for balance building): Payment/Cash Transfer
  settle at `coalesce(completedDate, transactionDate)`; dividends/interest/fees
  accrue at `transactionDate`; orders at trade date.
- **Field shape quirks**: `nodeId` arrives scalar-or-array on several entities;
  `externalIds[].value` is string-or-object; `vulnerability.isVulnerable` is
  not always a bool; nested structs (`updateHistory`, `bankDetails`,
  `kycData`, `wrapperDetail`, …) drift shape between records — which is why
  `records_to_table` serialises nested values to JSON strings.
- **Pagination is lossy at page boundaries** (verified live, deterministic):
  a paged walk returns `meta.count` rows with a few duplicated and their
  counterparts never served — the client refetches small sets as one
  exactly-sized page and dedupes larger walks. Unpaginated requests 500 on
  big sets (transactions), so pagination can't just be dropped.
- **`/static/transactiontype` ids are not unique** — BUY/SELL repeat across
  sub-type × movement variants (one id even carries a trailing space:
  `'SELL '`); key on (id, subType, movementType).
- **Known Seccl data issues** (from reconciliation work): monthly report
  snapshots are internally inconsistent (`closingValue` ≠ next `openingValue`
  for most accounts — trust them for cashflow, not valuation); NAVs are stored
  to 6dp and period-boundary reports freeze at run-time prices for T+1/T+2
  funds; book costs use ex-date ordering with a 2dp running-total rounding;
  MWRR is Modified Dietz with the measurement period starting the
  first-of-month of the first transaction.

### The six platform findings, re-verified here

`~/dev/platform/docs/SECCL_PLATFORM_ISSUES.md` is the write-up for Seccl. All six
findings reproduce from this warehouse against production, so the models below
must keep accounting for them:

| #   | Finding                                               | Reproduced from                                                                                                                                                                                                                                                                                                                                    | What this layer must do                                                                                                                                                                                                                                                           |
| --- | ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Period reports frozen at generation-time NAVs         | `raw.asset_prices` holds **one row per (asset, price_date, insert_date)**: 24DPS `price_date` 2025-12-30 first seen 2025-12-31 @ 88.4943, and 2025-12-31 only seen 2026-01-02 @ 87.5077. Recurs at 2026-06-30 (87.3586 → 86.1756). Of 2025+ prices, 193,645 publish same-day but **74,933 at T+1, 49,929 at T+2, 22,270 at T+3**, tailing to T+11. | Keeping `insert_date` is what makes **as-of-date pricing** possible (latest insert ≤ valuation date = Seccl's own convention). Never collapse the price history to one row per date.                                                                                              |
| 2   | `closing_value` ≠ next `opening_value`                | Of the monthly transitions landed so far: **332 exact, 46 <£100, 299 £100–10k, 9 ≥£10k** — i.e. ~53% discontinuous, matching the 46%-exact figure in the doc.                                                                                                                                                                                      | Period reports are a frozen NAV snapshot. Value from our own daily series (`platform.valuations`), never from a windowed report.                                                                                                                                                  |
| 3   | In-specie transfers carry `book_value` = 0            | `00J4G78`'s two 2025-05-06 in-specie transfers: 24JD2 74,478.869 units @ £132,561.63 and 24JD3 10,456.888 @ £15,666.15 = **£148,227.78, both `book_value` 0.0**. Census of completed transfers: **Product Transfer/Cash 73/73 zero, In Specie 1/1 zero, External 22/149 zero** (Migration 329 and Internal 252 all priced).                        | Cashflows must be `coalesce(nullif(book_value, 0), transfer_value)` — otherwise ~£6.4M of arriving capital scores as performance. Also note `transaction_date` (2025-05-06 settlement) is **after** `completed_date` (2025-04-25), so date transfers by the **later** of the two. |
| 4   | Valuations rounded to 6dp though the feed carries 7dp | The four WS Epworth funds each hold 578–603 NAVs at **7dp** (max_dp 7 of 1,280 prices each); **7,663** of all 2025+ prices exceed 6dp.                                                                                                                                                                                                             | Value at full published precision; round only for display. Expect ~£1 drift per multi-million-unit position against Seccl.                                                                                                                                                        |
| 5   | 2dp running `total_book_cost`                         | **Not verifiable here** — the book-cost engine is in the deferred analytics layer.                                                                                                                                                                                                                                                                 | When ported, keep both a full-precision and a Seccl-matching 2dp running total (the platform's `total_book_cost` / `total_book_cost_adjusted`).                                                                                                                                   |
| 6   | `growth_percent` basis undocumented                   | `raw.account_summaries`: `00H6D7B` 0.3056, `0219579` 0.2172 (doc: 0.3082 / 0.2197 as at 17 Jul — market drift since), and `00J4G78` **1.1716** on an account whose true return is ~20–25%.                                                                                                                                                         | Treat `growth_percent` as _Seccl's_ completed-date-anchored Modified Dietz, not ground truth; compute our own and expect transfer-heavy accounts to disagree.                                                                                                                     |

## Caveats

- interlace v2 is consumed from PyPI as **`interlaced`** (`interlaced[service]`,
  currently the `2.0.0a3` prerelease) — no local checkout, so Docker/CI builds
  work unmodified. `uv` needs the prerelease opt-in, which the `>=2.0.0a3`
  specifier itself provides.
- A `full_merge` extractor that gets an empty listing raises instead of
  diff-deleting the table — an empty list from Seccl is treated as a fault.
- Marts views overlay webhook deltas over the raw baseline; a raw re-pull
  eventually supersedes stream rows (streams keep 30 days).
