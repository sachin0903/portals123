# SEI — the file-drop custodian

SEI Wealth Platform (SWP) is our largest custodian and the only provider that is a
**file feed** rather than an API one. Every night SEI writes a set of pipe-delimited
reports to an SFTP endpoint, each PGP-encrypted to the Graphene public key. This doc
covers the ingestion pipeline, the model layers, and the per-tenant fan-out.

Related: [`storage.md`](storage.md) (bucket layout, §9 ingestion), [`datalake.md`](datalake.md)
(DuckLake catalogues), [`data-architecture.md`](data-architecture.md).

> **Reading note (Aug 2026).** `app/fanout.py` is **deleted**. The per-tenant narrowing it
> describes now lives in `pkgs/book-sink` — `predicate.sei()` carries the same shape-awareness for
> the same reasons, and `_SEI_NO_SUBFIRM_TABLES` and friends moved with it. The sections below are
> kept because they record how those shapes were learned (each from a slice that came out wrong);
> read the module name as historical. See [`provider-pipeline.md`](provider-pipeline.md).

## 1. The pipeline

```
SEI SFTP (gws.ftp.seic.com)
  └─> {core}/sei/sftp/{report}/dt=YYYY-MM-DD/{name}.out.pgp    byte-exact, write-once
        └─> PGP decrypt + trailer verify
              └─> {core}/sei/staged/{report}/dt=…/{name}.out    clean CSV
                    └─> DuckLake catalogue `sei` (raw.* -> ref.* -> platform.*)
                          └─> per-tenant fan-out (firm / subfirm / DSR)
```

The two zones are deliberate: **sftp** is the evidence of what the custodian actually
delivered, kept exactly as sent; **staged** is the machine-readable form, because DuckDB
cannot read PGP. Neither carries an environment segment — the bucket is per environment
(one object-store tenant per deployment), so an `i31` key and a `p04` key cannot collide
in the first place, and stamping the env into the path only made every reader interpolate
a value it already knew from the bucket it was reading.

### The pull is a model, not a side cron

The whole diagram is **one interlace DAG**. `raw.files` (`services/sei/models/raw_files.py`)
is its head: the model lists the SFTP drop, archives and decrypts what is new, verifies
each trailer, and returns one row per file — and all 47 `raw.*` report models declare
`depends_on=("raw.files",)`, so a run cannot read a drop it has not fetched.

Before this, `sei-sync pull` was a separate cron whose only record of what happened was a
log line, and the 47 models read whatever objects happened to be in the bucket when they
fired. If the pull failed, the reports still ran and quietly re-read yesterday's drop as
though it were today's. `moon run sei-sync:pull` still exists — it is the manual door, and
what `--local-dir` development uses.

The **daemon** (`moon run sei-sync:dev`) is the HTTP API plus the scheduler. It starts
through `book_sink.startup_run`: `interlace serve --no-apply`, then one full
`POST /runs {}` once `/health` reports `ok`. The models' crons (`raw.files` 05:00
Tue–Sat, reports 05:30, the other API models 07:00–08:05) still fire on that schedule.
`raw.api_transactions` is every fifteen minutes. A tick asks every account only for the
days after the last one it covered, through yesterday — SEI refuses today's date, and
there is no last-modified, so the grain is a day. `merge` on `transaction_id` updates a
row when that day is read again. A tick that is already covered through yesterday makes
no calls. A receipt we have just lodged is also re-read by the API worker every fifteen
minutes (`app.integrations.sei.receipt_status`), which writes the activity state onto the
Graphene row. From
interlace 2.8.3 a scheduled fire also runs everything downstream of that model, so
the 05:00 pull carries the reports. A model's own cron still does not pull its
ancestors, which is why the later slots stay later. `CronTrigger` does not fire on
first sight; the startup run is what fills a boot between slots. A succeeded run
inside 30 minutes is not enqueued again (`SYNC_ON_START_DEBOUNCE_SECONDS`;
`SYNC_ON_START=0` turns the kick off).

`raw.files` is a ledger, so the outcome is now queryable rather than logged:

| column                                     |                                                                            |
| ------------------------------------------ | -------------------------------------------------------------------------- |
| `filename`, `report`, `table`, `drop_date` | which file, which report, which day                                        |
| `status`                                   | `synced` (fetched this run) · `present` (already in the bucket) · `failed` |
| `archive_key`, `staged_key`                | where both copies landed                                                   |
| `bytes`, `sftp_modified_at`                | what SEI sent, and what its listing claimed                                |
| `trailer_rows`, `rows_read`                | the verified record count, NULL when this run did not read the file        |
| `error`, `processed_at`                    | why a `failed` file failed, and when it was last attempted                 |

**Retry needs no queue.** A failed file leaves an incomplete pair of objects behind, so
the next run's existence check sees it as outstanding and fetches it again; the ledger is
keyed on `filename`, so the successful row supersedes the failed one. A settled night
costs one SFTP listing plus two HEADs per file and downloads nothing.

`present` rows carry their `trailer_rows`/`rows_read` forward from the run that did read
the file (via interlace's `this` handle over the previous materialisation) — otherwise the
first skip would hollow out every historical row. On a full rebuild those counts are
genuinely unknown to the run and stay NULL, which is a different fact from zero rows.

### Backfilled history is invisible to an already-promoted report

The 47 report models are **incremental on `dt`** — interlace hands each one the max `dt`
from its previous materialisation and the read becomes `WHERE dt > cursor`. That is right
for a nightly drop and wrong for a backfill: archiving ten months of older drops leaves
every one of those partitions _behind_ the cursor, so a report that is already promoted at
its current fingerprint will read nothing and report zero new rows — which looks exactly
like a quiet night.

Observed doing precisely this: `raw.files` landed 3,899 files spanning 2025-10-23 →
2026-08-03, and `raw.active_blocks` — already built off a five-day drop, cursor sitting at
2026-08-03 — then added 0 rows, while calling the same model with `cursor=None` returns
74,353 rows over 78 partitions.

A report picks up backfilled history only when its previous materialisation is discarded.
A fingerprint change does that for free (`plan` says `breaking → rebuild`, so `previous` is
None and the read is unfiltered); otherwise the promoted table has to go. There is no
`--full-refresh` on `interlace run`. So: **backfill first, then let the reports rebuild** —
if they are already promoted, the pull has to be followed by something that resets them.

## 1a. The SWP REST API — the second source

**The drop is a delta, and that is the problem this solves.** Each `transaction_header` file
contains only that day's transactions (measured: 2,743 rows on 27 Mar 2026, every one dated
27 Mar). SEI keeps about five days on the SFTP endpoint, so a stretch of missed drops is a
permanent hole in `raw.transaction_header` that no amount of re-pulling can fill. The local
corpus shows the shape of the problem — 13 days of March 2026, and April/May/June absent
entirely.

`raw.api_transactions` (`services/sei/models/api_transactions.py`) refills it. A first scan
starts at that firm's earliest `ACCOUNT_OPEN_DATE` — 28 Jun 2024 for the oldest firm on the
book measured 14 Oct 2026. With no open date the first scan is the lookback.
`ACCOUNT_INCEPTION_DATE` is the wrong bound: it reaches 23 Nov 2016, and the API refuses
anything outside 36 months.

`raw.asset_prices` (`services/sei/models/api_asset_prices.py`) is disabled.
`POST /assetpriceproxyservice/historicalassetprices` takes one as-of date and a
list of identifiers (ISIN, else SEDOL, else CUSIP, else ticker). On 14 Oct 2026 i31's
lowercase `assetpriceproxyservice` proxy was deployed and had no flow for that resource:
every casing returned `404 unknown-resource`, and a capital in the proxy name was
`Unable to identify proxy`. The model returns an empty table and does not call SEI. The
Assets report remains the latest price.

`raw.api_models` names each distinct `MODEL_ID`. SEI has no model-by-id read, so the
call is `POST .../model/assignment/byAccount` against one Capital portfolio that
already uses the model. The answer is the name and the type (an implementation model,
on the books measured). It is not a target return, an ongoing charge, or the target
holdings — `ModelsIntExt` and `ModelAlloc` are still empty — so no model version is
minted. A name that is still the model's own id in the book is replaced; a name a firm
has typed is left.

`raw.api_allowances` reads `isaJisaContributionDetails` on account details for every
ISA and JISA. That block states the tax year the file does not. The book's limit stays
used plus remaining, because a flexible ISA's replacement room makes that sum larger
than the 20,000 statutory field on the same block. A SIPP returns the block as null.
The SIPP profile service is not deployed, and the file's pension amount columns are
unpopulated, so a SIPP allowance is not invented.

### What the API actually permits (verified against i31, 04 Aug 2026)

|             |                                                                                                                              |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Auth        | mTLS (SEI-issued pfx) + OAuth password grant; token lasts 12 h                                                               |
| Scoping     | **A firm-wide query is refused** — "Either clientId or externalPartyId or accountNumber or externalReferenceId is mandatory" |
| Window      | capped: "range should be between 0 to 30 days"                                                                               |
| Horizon     | `endDate` may not be in the future — **today is rejected**, so yesterday is the limit                                        |
| Date format | `YYYY-MM-DD`; anything else 400s naming the format                                                                           |
| `dateType`  | 1 Transaction · 2 Trade · 3 Settlement — **there is no last-modified**                                                       |
| History     | **36 months, sliding** — older 422s with "Start Date must be within last 36 months"                                          |
| Paging      | `pageSize` 1–100; response carries `totalPageCount`                                                                          |
| Key         | `transactionId`                                                                                                              |

**Dates cannot replace the identifier.** Tried every combination — `startDate` alone,
`startDate`+`endDate`, `endDate` alone, no dates at all, plus `platformUser` and
`portfolioType` — and all six return the same 400. There is no firm-wide transaction query
to be found; the per-account fan-out is the only shape available.

Two dead ends worth recording, so nobody spends the afternoon on them again:

- **SEI's own doc example is wrong.** It shows
  `/api/v1/swp/transactiondetailsservice/transactiondetailsservice/transactions?firmId=…&dateType=1`
  — firm-only, no identifier. The doubled service segment is a documentation artefact and
  **404s**; the single-segment path with the same firm-only parameters gives the usual 400.
  Tried with our `3106` and with their example's `94015`: same result either way.
- **`/retrieveactivities/activities`** takes the four identifiers as _optional_ alongside
  `firmId` and looked like the bulk route. It returns **401** for our AppKey in every form,
  firm-wide or account-scoped. That is an entitlement to request from SEI, not a code gap.

### Client documents, and the firm pack

SEI has no separate firm-document service. `POST /retrieveendclientdocumentservice/documents/list`
is one call per party (`partyId`, the client's `CLIENT_ID`). `includeFirmGenericDocs` adds a
group whose `documentTypeDesc` is **Firm Generic Documents** — fact sheets and packs that belong
to the firm, repeated on every party's list. They are stored once and filed on every enabled
tenant of that firm, with no client subject, so staff see them and they are not dumped into
every client's portal. A client document (statements, UK tax, contract notes, costs and charges,
fee invoices, performance) is filed under each party whose list returned it, once that party is
a contact in the tenant's book.

The bytes are fetched by `docretrievecontentservice` (`documentId`) into the core bucket at
`sei/documents/{feed}/{firm}/{documentId}-{filename}`, then copied to the tenant bucket at
`files/provider/sei/{feed}/{documentId}-{filename}` and filed by `documents.ingest_provider_document`
(`source = 'provider'`, not deletable). `raw.client_documents` does the fetch;
`raw.document_copies` does the copy. A document whose client has not been merged yet waits.

Both paths are all-lowercase, which is what i31's Apigee proxy has (verified 22 Sep 2026:
the list returns 200 `documentList` or 400 `Invalid entity ID`; the content service reaches
the document-management backend). The camelCase service names
`retrieveEndClientDocumentService` and `docRetrieveContentService` 404
`Unable to identify proxy for host: sei-default`, the same trap as `enrollmentAccountTemplates`.
`x-sei-enabled: false` on the two operations is stale. A 404 on the lowercase path still fails
the run rather than recording an empty book. US tax documents are not requested; that sibling
keeps a capital S (`docretrieveustaxService`) and the all-lowercase spelling 404s.

### Credentials

mTLS identity as `SEI_PFX_BASE64` (`base64 -w0 sei.pfx`), and that is now the **only** form —
the `SEI_PFX_PATH` fallback was removed in Aug 2026. A deployment sets one secret instead of
mounting a file, no certificate sits on disk beside the code, and one form means one precedence
rule: while there were two, `pkgs/sei`'s `configured` check asked for the PATH while its
`ssl_context` and `pfx_material()` preferred the base64, so a container supplying only base64 was
refused for want of a file it could not mount. `settings.pfx_material()` names the variable at
fault; `SeiApi` checks it before the first request, so a misconfiguration is a named error rather
than a TLS failure
part-way through a backfill.

**The 36-month floor slides, so it is computed, not configured.** Bisected against i31:
2023-08-12 accepted, 2023-07-31 refused. `api.earliest_start()` returns 36 _calendar_ months
back (SEI's message says months, and across leap years that differs from 1,095 days by
enough to matter) less a 7-day margin, and `_range` clamps to it. Verified at the boundary:
the computed floor and one day earlier are accepted, eight days earlier 422s — so the margin
is load-bearing, not decoration. Without the clamp an open date that ages past the floor
would 422 on every account the morning it did; with it, the run degrades to "as far back
as allowed" and says so in the log.

A full three-year backfill is therefore ~36 windows × 1,162 accounts ≈ **42,000 requests**.
An open date inside the floor avoids the empty months before the book. A firm still starts
at its own earliest open date, clamped to this floor, so a firm opened in 2026 is not
walked back through 2023.

**So it is a drill-down, not a feed.** One request per account per 30-day window: 1,162
accounts over ten months is ~11,600 requests. The open date bounds a first run. With no
open date the model reads only the lookback window rather than quietly attempting all of
history.

The account list comes from `raw.accounts` — the model declares it as an upstream, so the
drop is the _discovery_ mechanism for the API's fan-out and both live in one DAG. The drop
carries one row per account portfolio, so the pairs are de-duplicated first (11,112 rows →
1,162 accounts).

### Measured against the drop (first run, 07 Aug 2026) — they are not the same data

The premise this model was written on — "the API can re-read any range the API still serves", so a
missed drop is recoverable — **does not hold**, and only running both and diffing them shows it. A
30-day window (from 8 Jul 2026) returned **3,302 rows over 897 accounts in 30s**;
74 of the 1,162 accounts asked answered `422 Account is either not available or inactive`, which is
expected and counted rather than raised.

|                                     | API (`raw.api_transactions`) | SFTP (`raw.transaction_header`) |
| ----------------------------------- | ---------------------------- | ------------------------------- |
| rows                                | 3,302                        | 24                              |
| accounts                            | 897                          | 12                              |
| `transaction_date` span             | 10–31 Jul                    | 30 Jul – 6 Aug                  |
| firms                               | 3102, 3106, 3108, 3114       | 3102, 3108                      |
| `transaction_id` in common          | **0**                        |                                 |
| `(account, date, amount)` in common | **0**                        |                                 |

**The type vocabularies do not intersect.** All 3,302 API rows are one of four types — Periodic Fee
2,376 · Currency Interest Posting 890 · Cash Receipt 20 · Cash Disbursement 16 — and **no trade or
transfer appears at all**. The drop's 24 rows are Cash Transfer 14 · Free Receipt 4 · Cash Receipt 3
· Periodic Fee 2 · Buy 1. Narrowed to the ten accounts present in both, over 10–31 Jul where both
could see the same events, the API returns 23 rows (fees, interest, one receipt, one disbursement)
and the drop returns 4 (all Cash Transfer) — still nothing in common.

This is not us asking narrowly: `_get_page` sends no type filter and `dateType=1`, the same
transaction-date semantic the drop publishes. The 07 Aug sample's calendars barely overlap
(API 10–31 Jul vs drop 30 Jul–6 Aug), so "zero ids in common" and "cash side only" were an
artefact of that window — superseded by the 21 Sep 2026 reconciliation below.

Two more differences worth knowing before relying on either:

- **The API lags the drop by about a week.** `endDate` was 2026-08-06 and nothing after 31 Jul came
  back, on `transaction_date` or `settlement_date`. The drop had 6 Aug. So the API is the broader
  source (897 accounts vs 12) and the drop is the fresher one.
- **The same field is spelt differently.** The drop's `TRANSACTION_TYPE` is a numeric code joining
  `ref.codes` (`8`, `14`); the API gives the description (`Cash Transfer`, `Periodic Fee`). The two
  cannot be unioned without mapping one onto the other. Id spaces looked disjoint in this 30-day
  sample; over a longer horizon they do join (see below) — `transaction_id` is a cross-source key
  when the two books are actually the same events.

### Full reconciliation, 21 Sep 2026

A later 30-day sample and the first backfill established the populations were not identical
but did **not** classify the gap or compare overlapping fields. `scripts/reconcile_sftp_vs_api.py`
does: local p04 `TransactionHeader` (2025-10-24 → 2026-03-27) plus the live SFTP (2026-09-14 →
18), against i31 `/transactiondetailsservice/transactions` (`dateType=1`, adjustments on) for
2,056 accounts, 24,673 requests, 0 retries, 386 account failures (385 inactive, one HTTP 500).

|                           |   Drop |    API |    On days the drop covers |
| ------------------------- | -----: | -----: | -------------------------: |
| Distinct `transaction_id` | 65,078 | 95,331 |                            |
| In both                   |        |        | **56,527** (86.9% of drop) |
| Drop only                 |        |        |          **8,551** (13.1%) |
| API only                  |        | 38,803 |                  **4,854** |

The 38,803 API-only figure is mostly the **Apr–Aug 2026 hole in the file corpus**, not API
inventing rows: restrict to covered transaction-dates and it falls to 4,854. `transaction_id`
joins cleanly when it joins at all — 56,527 of 56,528 ID hits also match account and date; the
one exception is a recycled id (Periodic Fee on `MSP000384` 2026-09-15 in the drop vs Cash
Receipt on `G074SEI001` 2026-04-13 in the API).

**Drop-only is not “trades and transfers.”** Rolled to `TRANSACTION_TYPE`:

| Code | Name (file description)                         | Drop-only |
| ---- | ----------------------------------------------- | --------: |
| 19   | Cash Dividend / Equalisation / Interest Payment |     6,219 |
| 14   | Periodic Fee                                    |     1,341 |
| 92   | FX Spot                                         |       735 |
| 2    | Sell                                            |        70 |
| 1    | Buy                                             |        58 |
| 17   | Bonus Issue                                     |        44 |
| 4    | Free Receipt                                    |        33 |
| rest | receipts, disbursements, CA, …                  |        50 |

API-only on covered days is the inverse mix: Periodic Fee 2,676 · Free Receipt 658 · Cash
Dividend 550 · Cash Receipt 384 · Sell 319 · Buy 163. Both sources miss some of every
major type; neither is a subset.

**Overlapping rows agree on the money, not on every identifier.**

| Field                                              | Result on 56,528 ID joins                                                                                                                                                                                                        |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Account + transaction date                         | 56,527 match; 1 recycled id                                                                                                                                                                                                      |
| Currency                                           | 0 mismatches                                                                                                                                                                                                                     |
| Signed net amount                                  | 3 mismatches, of which 2 are Reinvestment (file net 0, API carries the consideration) and 1 is the recycled id                                                                                                                   |
| Quantity                                           | **sign convention** (file sells are negative; API is unsigned) plus ≤0.01 rounding. After `abs()`, 10 remaining diffs — all FX-Spot where the API omits quantity                                                                 |
| Gross                                              | 3,639 mismatches, almost all Currency Interest Postings where the file gross is 0 and the API fills the net                                                                                                                      |
| `TRANSACTION_DESCRIPTION` vs API `transactionType` | 6,397 “mismatches” — the file description is the **subtype** (`Interest Income`, `Contribution`, `Equalisation`); the API is the Reference type name (`Currency Interest Posting`, `Cash Receipt`, `Cash Dividend`). Codes join. |
| `INSTRUMENT_ID`                                    | Equal on only 12,923 / 56,528. Parallel id spaces; SEDOL often matches when both have one. Do not join instruments on this column across sources.                                                                                |

**Live production SFTP is not the i31 API.** The Sep 2026 drop (1,358 rows) shares **one**
id with i31, and that id is the collision above. The 86.9% overlap is the **p04-era files**
(Oct 2025–Mar 2026) against i31. Do not tell a firm they can replace tonight’s drop with the
test API; the numbers that look like a complement were measured on a historical slice where
the two books still rhymed.

**The 8,550 file-only ids are four causes, not a mysterious grain gap.** Probed 21 Sep 2026
against the same parquet and with live i31 re-queries (1-day vs 30-day windows,
`includeSuppressedTransactionFlag`, `dateType` 1/2/3, `transactionType` 17/19/92).

| Cause                    |      Rows | What it is                                                                                                                                                                                                                                                                | Ruled in by                                                                                                                                                                                                                                                       |
| ------------------------ | --------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dummy CA memos           | **6,212** | `DUMMY_TRANSACTION_FLAG=Y`, net 0, type 19 Equalisation / Interest Payment / Cash Dividend, all with a `CA_EVENT_ID`                                                                                                                                                      | Same account, same day, the API returns other rows. Re-query with `dateType` 2/3, `includeSuppressed`, or `transactionType=19` still misses the id. The 369 equalisations / 783 interest payments / 6,120 dividends that **do** join are the non-dummy cash ones. |
| Live production vs i31   | **1,357** | 14–18 Sep 2026 SFTP (almost all Periodic Fee on the 15th)                                                                                                                                                                                                                 | 1,358 live rows share one id with i31, and that id is the collision.                                                                                                                                                                                              |
| 30-day window drops FX   |   **735** | FX-Spot is in the API on a **1-day** query and gone on a 2-day or 30-day query for the same account. `totalPageCount=1`, page 2 is empty — not paging. Missed FX sits on busy days (mean 25.7 other file txns that day); the 13 that joined sit on quiet days (mean 3.1). | Live: `MSP000076` id `375058225` on 2026-01-16 is returned for `2026-01-16..16` and omitted for `2026-01-15..16` and for `2026-01-01..30`. Our fetch always uses 30-day windows.                                                                                  |
| Default omits suppressed |  **~246** | File `SUPPRESSION_INDICATOR=2`: Bonus Issue, Free Receipt/Delivery, Reverse Stock Split, most residual buys/sells, a handful of fees                                                                                                                                      | Live: Bonus Issue `377514283` and Free Receipt `372946632` and Buy `374045259` are absent until `includeSuppressedTransactionFlag=true`. The recon fetch only sends `includeAdjustmentTransactionFlag`.                                                           |

**Closed / inactive accounts are not the transaction gap.** 385 accounts 422'd ("not available or inactive") and one HTTP 500. The file has **no** `ACCOUNT_STATUS_CODE=6` (closed) in the queried set — 361 of the 422s are **active in the production file**. SEI's own spec says this endpoint "does not support the inactive account or client"; on i31 that means _not provisioned in test_, not _closed in production_. Those 385 accounts carried **five** file-only transactions between them (one inactive, four on the HTTP 500 account of which three are the Sep fees above). They inflate the request count, not the ledger diff.

**Paging is not the gap.** Of 440 accounts that have both API rows and residual file-only rows, 439 do not have an API row-count multiple of 100. Forcing page 2 when `totalPageCount=1` returns an empty page.

**`dateType` is not the gap for dummy CA.** Type 19 memos are absent on transaction, trade and settlement date. It _does_ change the _other_ rows in the window (trade-date picks up Acquisition Lot Adjustments the transaction-date call misses) — which is why a lookback still matters — but it does not recover the dummy ids.

On the p04 slice, **excluding dummy memos, 98.3% of file ids join the API** (56,527 / 57,508). The remaining 1.7% is FX (window) plus suppressed CA/trades (flag). Buys and sells that are _not_ suppressed match at 99.6–99.7%.

**API-only on covered days (4,854)** is the other direction, and it is mostly not a fetch bug: Periodic Fee 2,676 of which **1,319 are Sep** (i31's own fee run, disjoint from the production drop) and **1,350 are March** (extra i31 fees on days we hold files). The rest is extra i31 activity (Free Receipt, Cash Dividend, trades) plus 56 flagged adjustments. The 38,803 API-only figure overall remains the Apr–Aug hole in the file corpus.

### Why it re-reads its own cursor

There is no last-modified filter, so a correction booked today against a transaction dated
last month cannot be selected by any date SEI offers. The incremental read therefore starts
at `covered_through - SEI_API_LOOKBACK_DAYS` (default 30) and `merge` on `transaction_id`
absorbs the repeats. `covered_through` is the end of the last scan that finished, written
onto the rows of that scan. It is not `max(transaction_date)`. interlace's own cursor is
that maximum, and a quiet book leaves it on the last movement, so the next run would
re-request every empty month between that movement and yesterday. A scan that does not
finish does not move the mark. A quiet scan that returns nothing still moves it, by
rewriting one stored row, or the window would grow by a day every night.

A rebuild is a new physical table, so the injected cursor is empty. The environment view
still names the previous materialisation until the new one is promoted. The model reads
that view, continues from the stored mark (or from the latest stored transaction date
until a scan has recorded one) and republishes the stored rows with the new scan. The
strategy stays `merge`. `full_merge` of a lookback window would delete every transaction
outside it.

`submitted_date` is carried through from the payload — it is the only field that
distinguishes a transaction booked late from one dated late, so a late amendment stays
visible as one. `includeAdjustmentTransactionFlag=true` is always sent: excluding
adjustments would leave the ledger showing the pre-amendment figure.

### The first full backfill, measured

Run 04 Aug 2026: 1,242 accounts × 36 windows, **16m 51s**, 120,375 rows, 0 retries.

|                                  |                                                    |
| -------------------------------- | -------------------------------------------------- |
| Rows / distinct `transaction_id` | 120,375 / 120,375 — the key is clean               |
| Accounts returning data          | 1,028 of 1,242                                     |
| Date range returned              | 2024-07-15 → 2026-07-31                            |
| Adjustments · reversals          | 6,929 · 740                                        |
| Accounts refused (422)           | 81 — "Account is either not available or inactive" |

Two things the run settled that the small sample had hidden.

**The three-year request only reached mid-2024.** Not a clamp — the book genuinely starts
then (2024 holds 457 rows against 65,461 for 2025). Firms `3110` and `3112` returned nothing
at all. So "3 years back" costs the requests whether or not the data exists.

**The API does NOT supersede the drop.** The first backfill compared id sets on the days the
then-loaded drop covered (56,497 in both · 7,208 drop-only · 907 API-only). The 21 Sep 2026
reconciliation above is the one to cite, and the 13% file-only slice is now attributed
(dummy CA memos · production vs i31 · 30-day windows dropping FX · suppressed rows the
default fetch omits). Two hypotheses from that first backfill still hold: it is **not
closed accounts**, and a single-account `includeSuppressed` probe can look like a no-op
(24 rows either way) if that account has no suppressed CA. **Do not treat
`raw.api_transactions` as a superset of `raw.transaction_header`.** A 1-day window plus
`includeSuppressedTransactionFlag=true` would close FX and the suppressed slice; it would
not close dummy memos or a production drop versus the test API.

`platform.transactions` publishes both. A `transaction_id` the file has stays with the
file (signed amounts, description code, `*_Upd` amendments). An id only the API returned
is appended, which is how a missed drop — SEI keeps about five days on SFTP — still
reaches the book. The firm's identity for that request is the SFTP Reference report. `ref.firms` is
`TYPE = 'FIRM_ID'` on the latest drop, and the API models (transactions, enrolment
templates, employee users, client documents, model assignments, ISA allowances) request every firm on it. An unreadable
report requests nobody. Chester Rose and Vertex sat in the drop while a hand-maintained
firm list never asked the API for them.

The scan mark is one date for the whole table. A firm that already has API rows
is re-read from `covered_through - SEI_API_LOOKBACK_DAYS`. A firm with none yet — one the
Reference report added after the first backfill — is read from its earliest
`ACCOUNT_OPEN_DATE`, or the history for that firm would be only the lookback. The SFTP files still carry
every firm in the delivery group, but those files are a few days of activity, not the
life of the account. The API description is resolved to the file's numeric code through
`ref.codes` before the same canonical-type CASE runs. The API's instrument id is not the
assets-report id, so the link is by ISIN, then SEDOL.

The ledger's `amount` for SEI is the cash that moved: net, then gross, then the
asset-currency book value (`book_sink.entities._TRANSACTIONS`). Net is what left or arrived
— a buy's net is the gross plus charges, a dividend's net is the gross less tax. Gross is
`0.0000` (a figure, not a NULL) on interest, fees and FX, where the money is the net, and
both cash columns are 0 on a stock transfer, where the cost is `BOOK_VALUE_IN_ASSET_CURRENCY`
(the API's `local_book_value`, already signed). `nullif` steps past a zero. A row with none
of the three still lands as 0. Other providers stay gross, then net.

Disabled by default: blank `SEI_API_BASE_URL` returns an empty typed table, so a deployment
with no API credentials runs the SFTP pipeline unchanged.

### Environments and the IP allowlist

SEI runs several environments (`i03`, `i04`, `i10`, `i31`, `p04`). Only some have SFTP
provisioned — the integration account serves `i31`, and `p04` is production. `SEI_ENV`
records which environment the credentials belong to; it no longer appears in the object
keys, because the bucket is already per environment.

**SEI allowlists SFTP by source IP.** Off-allowlist, authentication fails with
`AuthenticationException: transport shut down or saw EOF`, which is indistinguishable
from a wrong password — `sei_sync/sftp.py` says so in the error rather than sending
anyone credential-hunting. Run pulls from a host whose egress IP SEI has allowlisted —
the Coolify host, **or a developer machine on the company VPN**, which shares that egress.
Verified 03 Aug 2026: SFTP authenticates from a VPN-connected laptop and lists the drop
(five days retained, `GWP_UK3100_Out_*_YYYYMMDD.out.pgp`). The earlier wording named only
the Coolify host, which read as "impossible locally" and is why nobody had run a first sync.
To develop with no network at all, point `--local-dir` at a directory of drop files.

**The key was the real blocker, and it is now configured.** Decryption needs
`SEI_PGP_PRIVATE_KEY_BASE64` (preferred) or `SEI_PGP_PRIVATE_KEY_PATH`
(`sei_sync/settings.py::private_key_armour`, base64 first then the file). Neither was set,
and the key is not in the monitoring repo — that holds SEI's **API** credentials (i31
OAuth + appkey/secret, `certs/sei.pfx` for mTLS) and the SFTP password, but no PGP
material. Without it a pull archives the encrypted drop and stops. Both the key and its
passphrase are now in `services/sei/.env` (gitignored); **they still need to be set as
Coolify secrets** before a deployed run can decrypt anything.

### File naming

```
GWP_UK{group}_Out_{ReportName}_{YYYYMMDD}.out[.pgp]
```

`group` is SEI's delivery-group id (3100 across our whole corpus), **not** a firm id —
firm scoping lives inside the files, in each row's `FIRM_ID`. That is what makes the
tenant fan-out possible: whole-group data lands core-side and each tenant's slice is
selected per-row. `PREVIEW_`-prefixed copies are drafts superseded by the real file and
are always skipped. 47 report types are delivered today (`reports.py::KNOWN_REPORTS`);
unrecognised ones are still ingested but logged, since SEI adds feeds without notice.

### The record-count trailer

Every report ends with a line `Total Number of Records: |N`, where N is exactly the
data-row count. It is the only integrity signal the feed offers, and it catches the
failure mode a file transfer actually has — a truncated or partially-written drop, which
would otherwise land silently and look like a quiet day. `sei-sync pull` verifies it,
strips it from the staged copy, and reports the verified counts. A mismatch fails that
one file: the raw evidence is still kept (that is how you diagnose it) but nothing is
staged, so the models cannot read a short day as a real one.

### A declared report SEI has never delivered

`KNOWN_REPORTS` lists 47; the i31 drop delivers **46**. `FSLTaxData` has never arrived, so its glob
matches nothing and DuckDB raises `IO Error: No files found` — which `raw_read._read` turns into an
empty table rather than a failure, because one undelivered report must not fail the other 46.

That stub **must carry the model's whole key**. Every raw model is `strategy="merge"` on
`("_source_file", "_row")` and the merge's DELETE selects the key from the new snapshot, so a stub
publishing only `_source_file` failed with `Binder Error: Referenced column "_row" not found` — a
message naming a column, two steps from the cause, which is that there is no file. Pinned by
`tests/test_dag_contract.py`.

It must also carry the model's **CURSOR**, and that half hid behind the first: `cursor="dt"` means
interlace asks the previous materialisation for `max(dt)` on every run AFTER the first, so a stub
without `dt` passes an `apply` (no previous snapshot to ask) and fails the re-run with
`Referenced column "dt" not found`. `dt` is a DATE on the real tables and the stub matches that: the
cursor is compared against a date, and a VARCHAR would either compare wrongly or fail the merge on a
type mismatch the day the report arrives.

**`empty_columns` is passed BY THE MODEL, and that placement is the point.** interlace fingerprints a
Python model from `inspect.getsource(fn)` and nothing it calls, so a shape declared only in
`raw_read` is invisible to change detection — correcting it cannot rebuild the wrong table it already
produced. That is not theoretical: with the shape hidden in the helper, the broken two-column snapshot
survived `interlace run`, `interlace restate` AND a hand-dropped snapshot, because every one of them
still resolved to the same fingerprint. Moving the declaration into the model makes a change to it a
`breaking → rebuild`, which is what it always was.

The cost is honest and one-off: it moved all 47 raw fingerprints, so the whole SEI project rebuilt
once (~15 minutes, staged by layer).

**Still latent:** the stub leaves a snapshot with only those three columns. If SEI ever starts
delivering the report, the same model returns its real width and cannot merge into that — verified
both ways, positionally (`table has 2 columns but N values were supplied`) and `BY NAME`
(`does not have a column with name …`). The remedy is a rebuild of that model, and it now HAS one,
because `empty_columns` is inside the fingerprint.

### The drop is Latin-1, not UTF-8

Every `raw.*` read passes `encoding = 'latin-1'`, and it has to. DuckDB's `read_csv` assumes UTF-8
and **refuses** a file that is not — `Invalid unicode (byte sequence mismatch) detected` — which
fails the whole model, and a failed model reads as "this firm has no data".

It hid because it is **intermittent by drop**: most days these files are pure ASCII, so a report
reads fine for a week and then breaks the night a client with an accented address appears. On the
corpus in hand the offending bytes number six — four `0xE8` and two `0xE9` (`è` and `é`, in Jersey
street names) — each a single byte, which is what identifies the encoding rather than corruption.
Latin-1 and CP1252 agree above `0xA0`, so the choice does not affect these, and Latin-1 is a superset
of ASCII, so the pure-ASCII drops decode identically and nothing else moves.

Worth separating from the trailer check above, because the two look alike and are not: the pull is
**bytes throughout** (`split_trailer` never decodes), so it verified those files happily and reported
0 failed. The encoding only bites at the model read, which is why "SEI has synced" and "SEI's models
build" were never the same statement.

### PGP

`pgpy` 0.6.0 — what the earlier `sei_reports` implementation used — is **broken on
Python 3.13**: it imports `imghdr`, removed from the stdlib by PEP 594. `pgpy13` is the
maintained fork, imports under the same `pgpy` name, and is verified against a real
`p04` drop. The key is supplied base64-encoded via `SEI_PGP_PRIVATE_KEY_BASE64` (a
mounted-file path is the fallback) and unlocked **once per run** — unlocking is a KDF
over the passphrase and doing it per file dominated the old implementation's runtime.

## 2. Model layers

| Layer        | Location                | What it is                                                                                                           |
| ------------ | ----------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `raw.*`      | `models/raw_reports.py` | One model per report, `read_csv` over the staged partitions. 47 of them, generated from the report registry.         |
| `ref.*`      | `models/ref/*.sql`      | The `Reference` report split into dimensions: firms, subfirms, DSR teams, DSR types, everything else as `ref.codes`. |
| `platform.*` | `models/platform/*.sql` | The published entity layer — the same concepts Seccl exposes, plus the tenancy spine. See §3.                        |

Layer naming follows Seccl (`raw.<entity>` → `staging.*` → published entities), so
the two custodians present the portals with the same shapes under the same conventions.

**The consumption schema is `platform`, for every provider** (`fanout.PLATFORM_SCHEMA`).
Two reasons it is not named after the provider: a DuckLake catalogue is attached under
the interlace project name, so a schema called `sei` inside the `sei` catalogue makes
every reference ambiguous (`Binder Error: Ambiguous reference to catalog or schema`);
and one shared name means the fan-out, the metric sources and the portals never need a
provider→schema lookup to say "clients". `platform` is the Graphene-canonical shape as
against provider-shaped `raw`/`staging` — the same distinction the OLTP `platform`
schema draws. Which provider a relation came from is already the catalogue
(`provider_schema`: `ml` / `seccl` / `sei`), so repeating it would carry no
information.

There is **no hand-written DDL**. interlace models own their own materialisation and
`plan/apply` issues additive `ALTER`s when a source gains a column, so a new SEI field
flows through on its own: `read_csv` picks it up (`union_by_name=true`), the raw table
gains it, and a `SELECT *` model inherits it. The cherry-picked `migrations/*.sql` only
existed because `sei_reports` drove a hand-rolled psycopg `MERGE` that needed its targets
pre-created; they were deleted.

Three decisions in the raw layer:

- **Read as text, materialise as types.** SEI ships untyped text, and letting DuckDB
  sniff would drift the schema between drops — a column blank on Monday sniffs as
  VARCHAR and as BIGINT on Tuesday. So the _read_ is `all_varchar` and each model then
  converts the columns we have declared a type for. `raw.*` therefore carries real
  `DATE` / `DECIMAL(38,10)` / `BOOLEAN` / `TIMESTAMPTZ`, and the consumption layer
  filters and joins them directly. See "Declared types" below — deferring the casts was
  a real bug, not a stylistic choice.
- **The key is `(_source_file, _row)`**, not a business key. Every staged object is
  immutable, so that pair is stable and unique — which makes re-reading a date
  idempotent without knowing the natural key of all 47 reports (SEI documents them
  inconsistently and several have none).
- **Reads are incremental on `dt`.** interlace injects the previous max, so a nightly
  run touches only new partitions instead of re-reading an unbounded history.

### Declared types

`src/sei_sync/schema.py` maps report → column → type and is **generated**: `moon run
sei-sync:profile-schema` reads every staged object, classifies each column by the shape of
its values, and rewrites the module for review as a diff. 344 of 1,859 columns are typed
(96 `DATE`, 193 `DECIMAL`, 53 `BOOLEAN`, 2 `TIMESTAMPTZ`); everything else stays
VARCHAR, deliberately:

- **identifiers** — leading zeros are real in this feed (5 columns carry them), and
  numeric typing would silently destroy them. They are join keys, never arithmetic.
- **numeric enumeration codes** (`ADDRESS_TYPE`, `ACTIVITY_STATUS`, …) — categorical,
  and they join to `ref.codes.code`, which is text.
- **columns never populated** in the corpus — we do not guess a type we have not seen.

`sei_sync/columns.py` turns that map into the conversions. Two things it is careful
about. **Dates are parsed, not cast**: SEI writes `20260327` and
`try_cast('20260327' AS DATE)` is NULL, so a cast produces an all-NULL column and never
fails. **Conversion is lenient but audited**: every expression is `try_*` so one
malformed value cannot fail a night's ingest, and each model then runs a per-column loss
count and logs any non-blank value that failed to convert. That audit earned its keep on
the first run — it caught `clients.PREF_TAX_FORM_DEL_UPD_DT` declared as a plain
timestamp when its values are `2024-10-16 02:00:07 PM +01:00` (12-hour clock, AM/PM, and
an offset), losing 8,303 of 8,321 values.

`models/raw_reports.py` is **generated** too (`moon run sei-sync:generate-models`), and each
model's SQL is written out in full rather than shared through a helper. That is not
stylistic: interlace fingerprints a Python model from `inspect.getsource(fn)` alone, so
a generic extractor closure fingerprints _identically_ for all 47 reports and edits to
the read logic behind it are invisible — `plan` reports no change and `run` merges into
the existing physical table, keeping the old column types. Declared types could not land
at all that way. `sei-sync:test` fails if the checked-in module is stale.

`quote`/`escape` are disabled in the reader: the feed has no quoting convention at all
(zero `"` characters across the corpus), so DuckDB's default would let a stray quote in
a client name swallow the rest of the row. `max_line_size` is raised because the wide
reports exceed the 2MB default.

### The conformed layer

Every `platform.*` entity publishes the §3 spine — `external_id`, `key`, `lineage`,
`source_updated_at`, `as_of_date`, `data` — so the materialiser reads SEI the same way it
reads Seccl. Built and checked against the p04 corpus (77 drop dates, 37.3M rows verified):

| entity                | rows   | what it is                                                                                                    |
| --------------------- | ------ | ------------------------------------------------------------------------------------------------------------- |
| `orgs`                | 515    | firms (6) + subfirms (6) + DSR teams (503), one model                                                         |
| `accounts`            | 1,104  | one row per account                                                                                           |
| `clients`             | 824    | one row per client                                                                                            |
| `assets`              | 6,384  | the instrument universe (global: `lineage = []`)                                                              |
| `positions`           | —      | EOD holdings, one row per taxlot                                                                              |
| `transactions`        | —      | full history, signed, amendments applied                                                                      |
| `valuations`          | 940    | account × day, summed from positions                                                                          |
| `taxlots`             | 60,062 | cost basis and acquisition detail — the CGT input                                                             |
| `instrument_charges`  | 38,826 | MiFID ex-ante/ex-post cost disclosures                                                                        |
| `upcoming_activities` | 13,593 | pending trades, corporate actions, expected cash                                                              |
| `transaction_details` | 5,141  | the fee/tax/treatment lines behind a transaction                                                              |
| `relationships`       | 3,444  | beneficial owners, trustees, guardians                                                                        |
| `contact_points`      | 3,371  | addresses, phones, emails per entity — richer than what reaches `crm.contacts` (see `sei-data-sources.md` §9) |
| `cash_schedules`      | 130    | recurring contributions and withdrawals                                                                       |

`dsr_grants` and `tenancy` remain internal spine models rather than conformed entities.

**`orgs` supersedes `firms` / `subfirms` / `dsr_teams`.** Those three published different
shapes, which is the whole reason `app/fanout.py` needs `_SEI_NO_SUBFIRM_TABLES`,
`_SEI_SCALAR_DSR_TABLES` and `_SEI_GLOBAL_TABLES`. An org is an org; the level is a column.
`external_id` is type-qualified (`team:200119`) because SEI's three id spaces share a numeric
range and collide; `key` stays bare because that is what a tenant's config holds.

**The fan-out predicate has NOT been switched over.** The spine is published _alongside_ the
existing tenancy columns, not instead of them. Today's predicate is firm AND subfirm AND DSR;
`list_has_any(lineage, …)` is OR, so flipping it would widen any slice configured on more
than one axis — a tenant-isolation regression. That is §6 step 5 and needs the
`tenant_platforms` config migrated to leaf org keys first.

**Three false key claims surfaced** when `unique` checks were added, all pre-existing:
`assets` had 6,123 duplicate `INSTRUMENT_ID`s, `positions` 43 (its composite key omitted the
portfolio), and 127 accounts resolved to no org at all. Every model now passes its checks.

## 3. The tenant fan-out

A tenant's SEI slice is selected by keys held in
`platform.tenant_platforms.config` and validated by `app/providers.py`:

| Key                               | Shape                    | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| --------------------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `firm_id`                         | one string, **required** | The tenant's SEI identity. 6 firms exist.                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `client_template_individual_id`   | one string, optional     | The SEI template a new **individual** is opened with. Set on the tenant Platforms SEI card.                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `client_template_organisation_id` | one string, optional     | The SEI template a new **organisation** is opened with. A company, trust, or charity uses this one.                                                                                                                                                                                                                                                                                                                                                                                                         |
| `client_template_id`              | one string, optional     | The individual template, also written when that side is saved, so a connection from before the split still opens a person. When neither typed key is set, this one template is used for both kinds. Absent on a side, and the firm has exactly one client template: that one is used. Absent, and the firm has several: enrolment refuses rather than reuse the other kind's template. The product's enrolment template opens the account, not the client.                                                  |
| `subfirm_ids`                     | list, optional           | Read slice. Absent = the whole firm.                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `dsr_ids`                         | list, optional           | DSR (Data Segregation Rule) servicing teams. Absent = the whole firm. When set, the same teams are the read slice and the grants sent on a new client and a new account.                                                                                                                                                                                                                                                                                                                                    |
| `custody_orgs`                    | `{onshore?, offshore?}`  | Write map: which subfirm a **new account** opens in. Set on the tenant Platforms SEI card (optional onshore and offshore dropdowns; blank is onshore / the whole firm). `subfirm_ids` is derived from the picked orgs so the read slice follows the write map. Absent = no org split (Blackfinch 3102): Graphene custody is still stored, nothing is sent as the account org.                                                                                                                               |
| `custody_dsrs`                    | `{onshore?, offshore?}`  | Optional DSR teams for a new account opened in that custody, in addition to the firm's `dsr_ids` and the optional subfirm. Each side is a list; either side may name teams without a subfirm. A side with no list uses `dsr_ids`. The firm's teams stay on the client and on the account. A custody's teams are added on both, under that scope's relationship id. They do not replace the firm's teams. When `dsr_ids` is absent, a new client is granted the teams of the custodies this enrolment opens. |
| `model_dsrs`                      | `{model uid: [team id]}` | Optional DSR teams for a model. A new client and a new account that use that model receive these teams in addition to the firm's and the custody's. A team the book has never granted is left off and does not replace the other grants. Set on the tenant Platforms SEI card. A save with no model teams clears the map.                                                                                                                                                                                   |

DSR teams are a **list**; an absent key means "do not narrow on that axis" — not "match
nothing". Coverage is uneven and real: five of six firms have subfirms, and DSR-team
counts run from 2 to 434, which is why the DSR picker is a searchable multi-select
(`pkgs/ui` `MultiSelect`) fed from live warehouse data rather than a hand-maintained
list. The client templates are two Selects of that firm's client templates, one for an
individual and one for an organisation. Each custody
side is an optional subfirm Select plus an optional DSR multi-select.
Each catalogue model has its own DSR multi-select.
The two client templates, `custody_dsrs` and `model_dsrs` are not slice filters. The platforms
card owns them: a blank control omits the key and the save clears it.
`client_template_id` is cleared with the individual side, because that control
is what writes it. Contribution codes are not on that card, so the same save
leaves them.

Enabling SEI also connects the shelf. GIA, ISA, JISA and SIPP gain a
`product_platforms` row with `role='custodian'` (`providers.attach_custodians`).
The product list and the journey both require that role; a row with a null role
does not count, and an account opened without a custodian makes every later
action skip. The enrolment template stays a per-product choice
(`account_open_reference`). A third-party bond or pension is not attached.
Disabling SEI does not remove the mapping.

`app/fanout.py::_predicate` applies ONE clause shape to every SEI mart table, because
every row carries the same tenancy triple (see below). This replaced an earlier
per-table DSR column map — SEI names the raw DSR column after the entity
(`client_dsr_team_id` vs `account_dsr_team_id`), and reusing one for the other silently
returns zero rows.

`platform.assets` is the exception: firm-wide reference data with no tenancy columns
(`_SEI_GLOBAL_TABLES`), projected whole — an instrument is not owned by a tenant, and
narrowing it would empty a table every tenant needs in full.

### The tenancy spine — how every entity becomes filterable

`platform.tenancy` is one row per account carrying `firm_id`, `subfirm_id` and the
account's DSR grants **as a LIST**. Every `platform.*` entity joins it, so a single
predicate shape filters all of them:

```sql
firm_id = '3106'
  AND subfirm_id IN ('704254')                        -- optional
  AND list_has_any(dsr_team_ids, ['704718','409655']) -- optional
```

Three ways to model an account's several grants; only one is filterable:

|     | Shape                                     | Verdict                                                                                       |
| --- | ----------------------------------------- | --------------------------------------------------------------------------------------------- |
| 1   | `dsr_team_id_1..10` numbered columns      | what SEI sends — unfilterable                                                                 |
| 2   | one row per grant (`platform.dsr_grants`) | correct, but joining it to every fact multiplies rows and every count then needs a `DISTINCT` |
| 3   | one LIST column (`platform.tenancy`)      | account stays one row, containment test, **same predicate on every table**                    |

(3) is why `app/fanout.py` no longer needs a per-table DSR column map — that
special-casing is gone. It mirrors how Seccl matches its scalar-or-list `node_id`.
`platform.dsr_grants` remains as the auditable long form: what you query to answer
"why does this tenant see this account". The adviser employee dropdown reads the latest
full grant file instead, because this table is only the newest warehouse drop and that
drop is sometimes a thin extract.

Verified on the p04 drop — one predicate, three entity grains:

```
firm_id='3106' AND list_has_any(dsr_team_ids, ['704718','409655'])
  platform.accounts        440 /  1,104
  platform.positions    12,453 / 25,932
  platform.transactions  2,332 /  2,787
```

### Entity layer — matching Seccl

| Concept            | Seccl                | SEI                                                         | Source report           |
| ------------------ | -------------------- | ----------------------------------------------------------- | ----------------------- |
| ownership root     | `seccl.nodes`        | `platform.firms`, `platform.subfirms`, `platform.dsr_teams` | Reference               |
| tenancy resolution | node lineage         | `platform.tenancy`, `platform.dsr_grants`                   | ClientAccountLink       |
| clients            | `seccl.clients`      | `platform.clients`                                          | Clients                 |
| accounts           | (in positions)       | `platform.accounts`                                         | Accounts                |
| holdings           | `seccl.positions`    | `platform.positions`                                        | EODPositions            |
| transactions       | `seccl.transactions` | `platform.transactions`                                     | TransactionHeader(+Upd) |
| asset universe     | `raw.assets`         | `platform.assets`                                           | Assets                  |

Daily prices are not loaded. `raw.asset_prices` is disabled: the historical-price resource
is not a flow on i31. The Assets report carries the latest price only.

Still to build, with their source reports: `valuations` (EODPosValAggr), `cash_balances` (CustodyNostroPositions), `fees`
(FeeComputation/FeeGroup/FeePackage\*), `users` (UserDetail/UserTeamRoleDetail),
`models` (ModelsIntExt/ModelAlloc), `taxlots`, `costs_charges`, `statements`
(Statement\*), `upcoming_activities`, `transaction_details` (TransactionDetail+Upd).
The remaining reports (AccountOptionalFields, ActiveBlocks, AssetInvClas, …) are
attribute side-tables that belong as joins onto the entities above rather than as
entities of their own.

### De-exploding the numbered columns

201 numbered columns across 15 reports, and they are **three different problems** —
treating them alike would corrupt the data:

| Pattern                    | Example                                                | Correct treatment                                                                   |
| -------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| repeating entity           | `ACCOUNT_DSR_TEAM_{TAG,ID}_1..10` (80 cols)            | **unpivot to rows** → `platform.dsr_grants`, then pivot to a LIST for filtering     |
| one value split over lines | `CORRESPONDENCE_ADDRESS_LINE1..3`, `ADDRESS_LINE_1..8` | **collapse to a list** — unpivoting invents 5 addresses where there is one          |
| paired qualifier           | `DEBIT_CREDIT_FLAG_1..16`                              | **not a group at all** — each flag qualifies the money column immediately before it |

That last one is the dangerous one. Every amount SEI sends is **unsigned**, with its
direction in a companion flag (`NET_TRANSACTION_AMOUNT`→`_1`,
`BASE_TRANSACTION_AMOUNT`→`_2`, `PRICE`→`_3`, `NET_FEE_AMT`→`_4`, …; values only `C`/`D`,
and both C and D rows are positive). **Summing the raw column adds debits to credits and
is wrong by construction.** `platform.transactions` and `platform.positions` resolve the flag
into the sign once and do not re-export it, so no consumer has to know the convention
existed.

### DSR grants are (type, team) pairs, not ids — and the type is the tenant signal

This is the part of SEI's model most likely to be got wrong, so it is worth stating
plainly. **SEI does not carry the DSR grant as a column on accounts or clients.** It
lives in the `client_account_link` report as ten numbered slots, each a `(TAG, ID)` pair:

```
ACCOUNT_DSR_TEAM_TAG_1..10 / ACCOUNT_DSR_TEAM_ID_1..10
CLIENT_DSR_TEAM_TAG_1..10  / CLIENT_DSR_TEAM_ID_1..10
(and the parallel *_DSR_EMP_* slots — individual advisers, a different grain)
```

`TAG` is the DSR **type** (joins `ref.dsr_types`, i.e. Reference `DSR_TYPE_NAME`), `ID`
is the DSR **team** (joins `ref.dsr_teams`). Both resolve completely against the p04 drop —
10/10 tags and 222/222 team ids. The slot **number is meaningless**: positional storage,
not a rank. Slots 1–5 are all heavily populated (1774 / 1659 / 939 / 939 / 927 accounts),
so multi-grant is the norm and reading only slot 1 loses most of the picture.

`platform.dsr_grants` unpivots all of it to one row per (subject, grant) — this is what
Sachin's `client_accounts_dsr` was reaching for. The `DISTINCT` in it is load-bearing:
`client_account_link` has one row per client _on_ an account, so a joint account repeats
its account-level grants once per owner. Deduping took 8,433 rows down to **5,102** — 40%
was joint-account double-counting, which would have inflated any per-tenant coverage
figure.

**Why the type decides tenancy.** An account carries ~3.5 DSR grants of different types.
Only some name an advisory firm:

| DSR type (firm 3102/3106)                                       | Account grants | Identifies a firm?                                    |
| --------------------------------------------------------------- | -------------- | ----------------------------------------------------- |
| IFA Firm                                                        | 1642           | **yes**                                               |
| Fee Receiving IFA Firm                                          | 715            | **yes**                                               |
| Legal Entity / SEI Custody / Client Service Team / Service Type | 939 each       | no — operational, shared by every account in the firm |
| Branch Administration, Service Team, Model Manager Team         | 5–114          | no                                                    |

So a bare `dsr_ids` list is ambiguous and over-matches: configure a tenant with a team id
that happens to be an "SEI Custody" grant and it pulls in the firm's whole book. Filtered
to the firm-identifying types, firm 3102 resolves to **180 named advisory firms** (Vision
Independent Financial, AFH Wealth Management, …) over 369 accounts, and 3106 to 28 over
509 — that list _is_ the tenant candidate set.

The type vocabulary is **per firm and inconsistent**: 3102/3106 say "IFA Firm", 3110/3114
say "Adviser Firm", and 3108/3112 have no firm-level type at all. There is therefore no
global rule; `ref.dsr_types.identifies_firm` is a naming-based **hint** for the operator
choosing grants, never an automatic authorisation decision. The admin DSR picker shows
each team's types in its hint and sorts the firm-identifying ones first, because
otherwise there is no way to tell which of 503 teams means "this tenant's book".

The picker labels and sorts by type so an operator picks advisory-firm grants rather
than operational ones, and `platform.tenancy.adviser_firm_dsr_ids` isolates that subset for
callers that want to enforce it. Selecting an operational DSR is still _possible_ — the
predicate matches whatever ids are configured — so this is guidance, not a constraint.

The admin picker options come from `GET /admin/providers/{provider}/options`
(`app/platforms.py::provider_options`), which reads `ref.firms` / `ref.subfirms` /
`platform.dsr_teams` from the core catalogue as `lake_reader`. Each option carries a `parent`
(its owning firm) so the card cascades: choose a firm, and only that firm's DSR teams and
custody orgs remain selectable. Seccl's `node_ids` picker is fed the same way from its
`raw_nodes` model. Empty option lists mean the models have not run yet, and the card
falls back to manual id entry.

## 4. Evaluating the cherry-picked models (`0a51470`)

`services/sei/{migrations,transformations}` came from Sachin's `feat/add-sei-models`
branch. They encode real and useful domain knowledge — the DSR/subfirm scoping model
especially — but they are **not runnable as delivered**. Findings, worst first:

### Blocking

1. **Not interlace-shaped.** `migrations/*.sql` + `transformations/{config,merges,sql}`
   is the `sei_reports` runtime (a bespoke APScheduler + psycopg merge loop), not
   interlace models. The raw/ref layers above replace the ingestion half; the mart half
   still needs porting.
2. **Every `source_sql_path` is wrong.** All 13 entries in `transformations/config.py`
   point at `src/sei_data_models/transformations/sql/…` — the `sei_reports` tree, not
   `services/sei/transformations/sql/…`. None would resolve.
3. **`pyproject.toml` was a lift-and-shift**: project name `graphene-platform`, entry
   point `sei_data_models.main:run`, `package-dir = {"" = "src"}` with no `src/` present,
   dependencies of psycopg2/APScheduler/requests/resend/openpyxl/pyzipper and **no
   interlace at all**. Replaced wholesale.
4. **No file handles the record-count trailer.** `Total Number of Records: |N` would be
   ingested as a data row in all 47 tables. Now stripped and verified at ingest (§1).
5. **`migrations/*.sql` were redundant** — hand-written DDL for tables interlace
   materialises and evolves itself. Deleted (§2). The column _types_ they declared were
   the one thing worth keeping, and they are now declared on the raw layer instead
   (`sei_sync.schema`, §2) rather than restated per mart.

### Correctness bugs

0. **Every date in the consumption layer was NULL, and nothing failed.** Not inherited
   from Sachin's tree — introduced when the mart layer deferred casting. SEI writes dates
   as `YYYYMMDD`, which DuckDB cannot `CAST` to `DATE`, and `try_cast` swallows it: nine
   columns across four models (`transaction_date`, `trade_date`, `settlement_date`,
   `opened_on`, `last_traded_on`, `updated_on`, `date_of_birth`, `commenced_on`,
   `as_of_date`) were 100% NULL. Two of them were declared model _keys_, and
   `platform.accounts`'s "most recently updated" tiebreak silently did nothing, leaving
   the pick to be lowest portfolio id. Fixed at the root by typing the raw layer (§2);
   `not_null` checks on the date columns now gate promotion.
1. **`platform.transactions` published a one-day window.** TransactionHeader is a daily
   _activity_ file, not a snapshot — each drop carries only that day's transactions and
   no id is ever redelivered — but the model took `max(dt)`, so it held 2,787 of 11,235
   rows and silently discarded the rest. Its amendment merge could not work either: the
   44 `*_Upd` rows in the corpus arrive in the 03-27 drop and correct transactions dated
   03-25, so against a one-day base they matched nothing and the merge appended them as
   44 unrelated rows. Both fixed; the key is now `transaction_id` alone (globally unique,
   11,235 of 11,235) rather than a pair whose second column was always NULL.
2. **`platform.clients` dropped recoverable tenancy for 786 of 2,774 clients (28%)** by
   taking `firm_id` only from client-scope DSR grants, when every one of those clients
   carries a `FIRM_ID` on its own row. A client with no firm fans out to nobody, so they
   were invisible to their own tenant. Now the grant wins where present and the client's
   own row is the fallback; `not_null: firm_id` gates it.

3. **`subfirms.sql` emits `subfirms_id`** while migration 004, `config.py`'s
   `_SUBFIRM_FILTER` and the fan-out all use `subfirm_id`. The subfirm filter would
   never match. Fixed in `ref/subfirms.sql`.
4. **`exchange_rates.sql` uses `"GBP" as "base_currency_code"`.** In DuckDB double
   quotes denote an _identifier_, so this resolves to a column named `GBP` and errors —
   it needs `'GBP'`.
5. **`_DSR_FILTER` is singular** (`op: "="` over one `dsr_id`). The requirement is DSR
   id(s); a firm routinely grants several teams. Now `IN` over `dsr_ids`.
6. **One DSR predicate reused across tables.** `config.py` correctly splits
   `_DSR_FILTER` (`account_dsr_team_id`) from `_CLIENT_DSR_FILTER`
   (`client_dsr_team_id`), but `app/fanout.py` applied a single clause to every table —
   which would silently return zero rows for whichever spelling was wrong. Now
   table-aware: the tenancy spine gives every _entity_ one predicate shape, and the
   _dimension_ tables are narrowed on the axes they actually have. That second half was
   missing until recently — `platform.firms` has no `subfirm_id` and `platform.dsr_teams`
   has no `dsr_team_ids` list, so the uniform predicate raised
   `Binder Error: Referenced column "subfirm_id" not found` and aborted the whole
   tenant's fan-out the moment an operator picked a subfirm or a DSR team. The tests only
   covered the four entity tables where the uniform shape happens to hold, so the gate
   stayed green; `test_fanout_predicate_sei_binds_against_every_table` now runs every
   generated predicate through DuckDB against each table's real column set.
7. **`dsrs.sql` includes `firm_id = '1'`** — SEI's own internal operations teams (Mutual
   Funds Services, Tax Services, …). Never a tenant grant; excluded in `ref/dsr_teams.sql`.

### Inconsistencies

10. **Source-table names don't match the reports**: `transaction_headers`,
    `transaction_details`, `user_details`, `user_team_role_details`, `clients_address`
    vs the actual `TransactionHeader`, `TransactionDetail`, `UserDetail`,
    `UserTeamRoleDetail`, `ContactDetails`. A mapping layer is needed when porting.
11. **`client_accounts_dsr` vs `client_account_dsrs`** — the same table spelled two ways
    (12 references vs 1).
12. **`upcomming_activities.sql`** is misspelled; the report is `UpcomingActivities`.
13. **`firms.sql` has a trailing comma before `FROM`** — legal in DuckDB, not elsewhere.
14. **`valuations` uses a different date shape** to every other position table
    (`as_of_date` as a real DATE vs `position_as_of_date` as a `%d-%b-%Y` string).
    Sachin worked around it with a dedicated filter; the underlying inconsistency should
    be normalised in the mart layer instead.

### Sound and worth keeping

- The three-level **firm → subfirm → DSR** scoping model, which is now the fan-out
  contract.
- `client_accounts_dsr` as the client/account→DSR resolution table — the right shape for
  answering "which tenant may see this account", and it correctly kept
  `account_dsr_team_tag` alongside the id. Realised properly as `platform.dsr_grants`
  (§3), which unpivots all ten slots and both scopes rather than a single column.
- The per-table natural keys in `config.py` (e.g. holdings keyed on account + portfolio
  - ISIN + SEDOL + date) are considered and match the grain of the reports.

## 5. Known gaps

- **The consumption layer is partial.** Ten entities are built (§3); roughly a dozen more
  are mapped but unwritten, and `transformations/` is still the unported reference for the
  business logic in them. `raw.*` covers all 47 reports, so nothing in the feed is lost.
  Note `transformations/` is not importable as it stands (`config.py` imports
  `services.sei.transformations.merges` and every `source_sql_path` points at the
  `sei_reports` tree) and the gates do not cover it: `sei-sync:typecheck` is `mypy src`, so
  mypy never sees `models/` or `transformations/`.
- **Only 8 of 47 raw models are built.** The eight the consumption layer depends on are
  materialised; the other 39 are ingested to object storage and planned but never
  applied, because of the write-write conflict below. `interlace plan` lists them as
  `added`.
- **14 orphaned relations are still in the catalogue** from earlier iterations of the
  layer (`marts.*`, `raw_reference`, `raw_client_account_link`, `ref.dsrs`,
  `ref.dsr_assignments`), and every `plan` lists them as `removed`. `run --select` never
  processes removals, and a full `apply` — which would clear them — currently trips the
  write-write conflict. Nothing reads them any more, but that was not true until
  recently: the admin DSR-team picker was still querying `ref.dsrs` and
  `ref.dsr_assignments`, so it kept working while serving whatever those orphans last
  held. It now reads `platform.dsr_teams`.
- **`platform.accounts` is one row per account by a deterministic pick.** The Accounts
  report is account × portfolio × something further (14,462 rows for 1,104 accounts), so
  portfolio-level detail wants its own `platform.portfolios` model rather than multiplying
  this one. 977 of 1,104 accounts have a ClientAccountLink row; the rest resolve to no
  tenancy and so fan out to nobody — worth confirming those are house/nominee accounts.
- **`platform.clients` is one row per client by a deterministic pick**, for the same
  reason: the Clients report repeats the whole client block once per `ADDRESS_PURPOSE`
  (2,774 rows for 824 clients). The purposes are kept as a list, so nothing about them is
  lost, and where a client genuinely has more than one address (5 of 824) the primary one
  wins. Per-address and per-purpose detail, and `STOCK_AND_SHARE_ISA_ACCOUNT_NUMBER`
  (which varies within 30 clients), want their own models.
- **A national insurance number is `TAX_ID_TYPE` 7, not `NATIONAL_INSURANCE_NUMBER`.** That
  column is empty on every profiled drop. `ref.codes` type `TAX_ID_TYPE`: 7 is National
  Insurance Number (9 characters), 36 is "No Tax ID on File". A type-36 row still carries a
  `TAX_ID_NUMBER`, and it is a placeholder — one value shared by a firm — so it is not stored
  as a NINO. Email and phone come from ContactDetails, with the Clients report's own
  `EMAIL_ADDRESS` / `PHONE_NUMBER` when that feed has neither.
- **A SEI client's subfirm is the account's.** `RESIDING_SUBFIRM_ID` is blank on every row
  of the Clients report, so a slice that filters `subfirm_id IN (...)` publishes nobody and
  the book looks like the firm has no clients. `subfirm_ids` is the list of subfirms of the
  accounts they hold (`platform.tenancy`), and the predicate matches with `list_has_any`.
  A scalar pick would hide one subfirm of a client who holds two.
- **Python-model fingerprints do not cover the code they call.** interlace's
  `data_fingerprint` docstring promises "the dedented source plus closure bytecode
  digest", but `graph/project.py::_fingerprint_query` passes only
  `inspect.getsource(fn)`. So a change inside any helper a model calls is invisible to
  `plan`, and `run` merges into the existing physical table rather than rebuilding it —
  silent staleness. The raw layer works around it by generating each model with its SQL
  inline (§2); anything else here that grows a helper needs the same care, and the real
  fix belongs upstream.
- **Operational DSRs can still be selected.** The predicate matches whatever `dsr_ids`
  are configured; `identifies_firm` and the picker's ordering are guidance. Enforcing
  advisory-only grants would mean validating the config against
  `platform.tenancy.adviser_firm_dsr_ids` at enablement.
- ~~**`DATALAKE_ENV` disagrees with the daemons.**~~ **Fixed.** The setting is gone: the
  daemons promote to their default (production) environment, which is **unprefixed**, so
  `providers.promoted_relation` now always returns the bare name and `app/fanout.py` uses it
  instead of building its own `{env}__` string. That mismatch was why the platform row counts
  and metrics silently came back empty — a missing relation is skipped, not raised.
- **`interlace apply` races at high fan-out.** Applying all 63 models at once fails with
  `Catalog write-write conflict on alter with "core_s3"` (and once segfaulted) — a
  concurrency bug in interlace's own secret creation. Building by selector
  (`moon run sei-sync:apply -- --select tag:ref`) is a reliable workaround.
- **Only one active SFTP credential.** SEI has not provisioned SFTP for every
  environment; `i31` uses the integration account.
