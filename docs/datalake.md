# Datalake — per-tenant DuckLake on Postgres + S3

The analytical plane. Each tenant has a **DuckLake** lake: catalogue metadata in the
dedicated **`datalakes` Postgres database**, parquet data under the tenant's own S3 bucket
(`t-{uid}/datalake/`, see `docs/storage.md`). DuckDB is stateless, ephemeral compute — any
process attaches a catalogue, reads parquet from S3, and disappears. The API consumes the
lake **in-process** via the `app/lake` package; there is no separate query service.

```
                    Postgres — `datalakes` database (catalogue metadata only)
                    ├── ml / seccl / …          (DuckLake catalogues: platforms staged zone, one per provider)
                    └── ten_{uid} …             (one DuckLake catalogue per tenant — METADATA_SCHEMA)
                                   ▲ catalogue transactions (snapshot isolation, optimistic retry)
        ┌──────────────────────────┼──────────────────────────┐
   ETL / fan-out              apps/api  app/lake           maintenance jobs
   (interlace; the one        (pool of single-tenant       (expire_snapshots,
   logical writer per         READ-ONLY DuckDB attaches;   cleanup_old_files,
   tenant catalogue)          Arrow / DataFrame results)   delete_orphaned_files)
        │                          │
        ▼ parquet                  ▼ parquet (read)
   s3://t-{uid}/datalake/…      s3://t-{uid}/datalake/…        (per-tenant buckets, scoped creds)
   s3://platforms/datalake/…                             (core bucket, core creds only)
```

## 1. Layout

- **Catalogue host**: the `datalakes` database (locally on the same Postgres 18 instance,
  created by `apps/api/migrations/bootstrap/03-datalakes.sql`; production provisions it out of
  band). It holds only catalogue metadata — parquet never lands in Postgres.
- **Catalogue per tenant**: a schema named by the tenant's immutable uid
  (`auth.tenants.uid`, e.g. `ten_ab12cd34ef567890`), selected with the `METADATA_SCHEMA`
  attach option. The whole-firm staged zone gets one catalogue schema per provider
  (`morgan_lloyd`, `seccl`, `sei`) — matching interlace's one-writer-per-catalogue model, one
  sync service per provider.

  > **The schema IS the provider code (0134), and there is no map any more.** Morgan Lloyd's
  > catalogue was `ml` while its registry key was `morgan_lloyd`, so `providers.provider_schema`
  > and `book_sink.register.provider_schema` each carried a lookup to bridge the two — and that
  > bridge was forgotten once: `materialise._read_conformed` read the bare provider name, so it
  > read schema `morgan_lloyd` when the data was in `ml`, and an entire tenant's slice reported
  > as unavailable. Which is indistinguishable from a firm that genuinely has no data there. Both
  > functions now derive it (`code.replace('-', '_')` — a hyphen is not legal in an unquoted SQL
  > identifier), so a fourth custodian needs no entry anywhere.
  >
  > **THE RENAME IS AN OPERATIONAL STEP, not a migration.** The catalogues live in the
  > `datalakes` database, which alembic does not manage — `migrations/bootstrap/03-datalakes.sql`
  > creates it on first init and nothing versions it after. A deployment carrying Morgan Lloyd
  > data must run, against `DATALAKE_DATABASE_URL`, before or with the deploy:
  >
  > ```sql
  > ALTER SCHEMA ml RENAME TO morgan_lloyd;
  > ```
  >
  > Skipping it is not a crash: the reader finds no schema, reports the slice unavailable, and
  > that reads as "this firm has no data with this custodian" — the exact failure the map was
  > removed to prevent. The **parquet prefix deliberately stays `datalake/ml/`**: DuckLake records
  > an absolute path per data file, so the prefix is where the files ARE rather than a name
  > anything derives, and moving it would strand every one of them.

- **Data**: parquet under `s3://t-{uid}/datalake/…` (`DATA_PATH`). The optional
  `ENCRYPTED` attach flag stores per-file parquet keys in the catalogue; it must be chosen
  at catalogue creation.
- **Roles** (created by the same bootstrap script; two LOGIN roles):
  - `lake_writer` — owns the `datalakes` DB, creates tenant schemas, owns the DuckLake
    metadata tables. Used by provisioning and the ETL.
  - `lake_reader` — read-only; what `app/lake` attaches as. DB-level default privileges
    plus a per-schema `USAGE` grant at provisioning make everything the writer creates
    readable.
  - Grant invariant (CI-assertable): the OLTP roles (`authenticated`, `anon`, `app`) have
    zero access to `datalakes`; the lake roles have zero access to the `portals` database.
  - Provider catalogue schemas are created by their sync service (as `lake_writer`); for
    `lake_reader` to read one directly, grant it `USAGE` on that schema (tenant schemas
    get this automatically at provisioning; the fan-out itself runs as the writer).

## 2. Reading — `app/lake` (in the API)

`apps/api/app/lake` is a library, not a service. It pools **single-tenant** DuckDB
connections: each is in-memory, attached read-only to exactly one tenant's catalogue with
one S3 secret scoped to that tenant's bucket — so a connection is structurally incapable
of reaching another tenant, regardless of the SQL it runs. Endpoint authorisation stays
where it already lives (the API's sessions/permissions).

```python
from app import lake

# Lake-only query -> Arrow table
table = await lake.query(tenant_uid, "SELECT * FROM platform.valuations WHERE ...")

# Lake x OLTP join, RLS applied on the OLTP side: pass the request's claims and the
# attached `oltp` catalog carries the caller's row-level-security context.
async with lake.connection(tenant_uid, claims=principal_claims) as conn:
    frame = await conn.df(
        "SELECT c.email, s.valuation FROM oltp.platform.clients c "
        "JOIN seccl.clients s ON s.client_ref = c.seccl_ref"
    )
    clients = await conn.models(ClientValuation, "SELECT …")   # pydantic, JSON-ready
```

- Results as **Arrow** (`arrow()`), **pandas** (`df()`), plain dicts (`rows()`) or
  **pydantic models** (`models(Model, sql)`) — the last is the JSON path for FastAPI
  `response_model`s; the frame surfaces are for analytics/reports.
- Plain OLTP queries (auth, documents, …) never touch this module — use the normal
  sessions/models.
- **Pool** (`app/lake/pool.py`): acquire prefers an idle connection already attached to
  the tenant (attaching is the expensive step); otherwise opens a new one within
  `LAKE_POOL_MAX_PER_TENANT` / `LAKE_POOL_MAX_CONNECTIONS`, evicting the
  least-recently-used idle connection when full; otherwise waits. Errored connections are
  discarded, not repooled.
- **Resource caps** (`attach.py::duckdb_config`, shared by both pools): every connection
  opens with `LAKE_MEMORY_LIMIT` (default `512MB`) and `LAKE_THREADS` (default 2), and
  spills to `$TMPDIR/graphene-lake`. Left unset, DuckDB gives each in-memory database 80%
  of the host's RAM and a thread per core, and idle pooled connections keep the S3 file
  cache they filled, so the API could grow until the container limit stopped it. The
  ceiling is now `(LAKE_POOL_MAX_CONNECTIONS + LAKE_CORE_POOL_MAX) × LAKE_MEMORY_LIMIT` —
  6 GB at the defaults; a limit is a cap, not a reservation — so size the container for
  that, or lower the caps. The spill path is not DuckDB's default `.tmp`, which is
  relative to `/app` and not writable by the image's `app` user.

### Core catalogue pool

CRM display reads and the platform overview do not use the per-tenant pool. They read a
provider's **core** catalogue, where every firm is a schema `tenant_{uid}`. Isolation is
that schema name (`require_uid` in `app/lake/holdings.py`), not a separate attach per firm.

`app/lake/core.py` keeps a small pool keyed by provider (`LAKE_CORE_POOL_MAX`, default 4,
never below 3). A connection is opened once — extensions, the S3 secret, the catalogue
attach — and checkout lends it to one thread at a time. DuckDB connections are not
thread-safe, so the pool does not share one across threads. `/crm/home` gathers holdings,
transactions and the value series concurrently, and each holds its own connection. A
failed body discards the connection. `connect()` remains for a caller that needs a private
one.

**The pool is warmed at startup** (`lake_core.warm`, from the API lifespan, in the
background). The first `connect` in a fresh process pays for everything a later one does
not: `INSTALL ducklake/postgres/httpfs` downloads each extension when the container has no
`~/.duckdb` cache (measured on a fresh extension directory: ~1.1 s, ~1.0 s and ~0.8 s —
about 3 s before the S3 secret and the attach are even issued), and the attach itself is a
Postgres connection to the catalogue host. Left to the request path, that cost landed on
the first clients or accounts listing after every deploy — the page's shell was on screen
and its streamed rows sat on the skeleton for several seconds, which reads as the listing
being slow rather than as the process being new. The warm-up opens and releases one
connection per provider, custodians first (`warm_order`, bounded by the pool cap), so the
first checkout for each reuses an idle attach. Best-effort: an unconfigured catalogue
(`CoreUnavailable`) is logged once at info and stops the pass; any other failure is a
warning for that provider only. A request that arrives before the pass reaches its
provider opens its own connection exactly as before. The image does not yet pre-install
the extensions at build time (`apps/api/Dockerfile`); doing so would take the download out
of startup as well as out of the request path.

The client and account listings do not scan the warehouse for cash and growth. The
pipeline already writes both onto `platform.valuations`, and the listing reads the latest
row in the same query as the portfolio value. The account record's cash tile, the cash on
the client record's accounts, and a cash withdrawal use that same custodian balance. The
positions relation keeps only the latest file, so an account a later file omits would
otherwise show £0 and refuse the withdrawal against a balance the custodian has already
stated. The holdings table, the allocation split and the performance series still scan.

- **Credentials** (`app/lake/attach.py`): the attach config (bucket, `DATA_PATH`,
  `METADATA_SCHEMA`, S3 credential) comes from `storage.tenants` +
  `storage.tenant_secrets` via `tenant_storage.get_lake_config` (service-role read,
  TTL-cached). While a tenant has no minted service user (backend IAM is best-effort,
  `docs/storage.md` §7), the platform credential is used with a per-attach `SCOPE` —
  IAM-scoped per-tenant credentials replace it as backends support them.

### OLTP access with RLS

Every lake connection pre-attaches the portals database **read-only** as `oltp`
(`DATALAKE_OLTP_DSN`, the `app` login). Passing `claims=` to `lake.connection()` runs the
body inside **one DuckDB transaction**, which makes the postgres extension hold a single
Postgres transaction shared by all OLTP reads; the claims are published into it
transaction-scoped (`SET LOCAL ROLE authenticated` +
`set_config('request.jwt.claims', …, true)`) — the same contract as
`db.py::tenant_session`, verified by a canary (`auth.tenant_id()` read back through the
attach) before the body runs. Row-level security then applies to `oltp.…` catalog scans
and `postgres_query('oltp', …)` exactly as on the request path, and the context ends with
the transaction (rolled back on exit — reads only).

Two rules the attach encodes:

- `SET pg_connection_limit = 1` — the extension parallelises scans over several Postgres
  connections; claims exist on one, so a parallel scan would silently drop rows (RLS fails
  closed). One connection, correct results.
- Claims cannot be session-scoped: outside a DuckDB transaction the extension wraps every
  statement in its own rolled-back Postgres transaction, so no session state survives.

`register()` (an Arrow table / DataFrame fetched through a normal RLS-bound session, then
joined in SQL) remains available — useful when the OLTP rows are already in hand.

The attach, as issued (`app/lake/attach.py::attach_statements`):

```sql
INSTALL ducklake; INSTALL postgres; INSTALL httpfs;
CREATE SECRET lake_s3 (TYPE s3, KEY_ID …, SECRET …, SCOPE 's3://t-42',
                       ENDPOINT 'localhost:9000', USE_SSL false, URL_STYLE 'path');
ATTACH 'ducklake:postgres:dbname=datalakes … user=lake_reader' AS lake (
  DATA_PATH 's3://t-42/datalake/',
  METADATA_SCHEMA 'ten_ab12cd34ef567890',
  READ_ONLY
);
USE lake;
```

## 3. Provisioning

Part of tenant storage provisioning (`docs/storage.md` §7): after the bucket,
`app/datalake.py::ensure_tenant_catalog` idempotently creates the uid-named schema in the
`datalakes` DB (as `lake_writer`, via `DATALAKE_DATABASE_URL`) and grants `lake_reader`
usage. Blank `DATALAKE_DATABASE_URL` skips the step; a failure leaves the registry row
`provisioning` for the same retry endpoint as the bucket. DuckLake creates its
`ducklake_*` tables in the schema on the ETL's first writer attach.

## 4. Writing

**One logical writer per tenant catalogue: the ETL** (interlace fan-out from
`platforms/datalake/` — grant-filtered per `docs/storage.md` §9), attaching as
`lake_writer` with write-capable per-tenant S3 credentials. DuckLake tolerates concurrent
writers (snapshot isolation; conflicting commits retry optimistically), but single-writer
keeps contention and reasoning simple. The API never writes to `lake/`.

Concurrency/durability model: a write stages parquet to S3 first, then commits visibility
as a single catalogue transaction — a writer crash leaves the catalogue consistent and the
uploaded files orphaned-and-invisible (swept by maintenance). Readers see committed
snapshots; a long-lived read connection sees new commits on its next query without
reattaching.

interlace opens hosted catalogues natively (`database: "ducklake:postgres:${DSN}"` +
`data_path`/`metadata_schema`/`secrets` in `interlace.yaml`, values `${VAR}`-interpolated
from the environment): `services/morgan-lloyd` writes the `ml` catalogue with parquet in
`platforms/datalake/ml/`, models on daily crons fired by `interlace serve`
(`moon run morgan-lloyd-sync:dev`; one-shot `:run`). `services/seccl` still runs on its
local file warehouse until it is migrated the same way. quack remains a single-node dev
convenience — never a tenant-facing surface.

**The tenant's slice** is no longer projected by the API. It is a layer of the provider's own
interlace DAG (`pkgs/book-sink`, docs/provider-pipeline.md): the firm's slice, the book's column
names delivered into Postgres, the merge, and the firm's own catalogue refreshed — one chain, filtered
by `platform.tenant_platforms.config`. `app/fanout.py` and its listener are deleted; a run is
triggered on enablement or by `POST /admin/{platforms/{provider},tenants/{id}/providers/{provider}}/refresh`.

## 5. Maintenance

Per catalogue, on a schedule (job still to build):

```sql
CALL ducklake_expire_snapshots('lake', older_than => now() - INTERVAL '30 days');
CALL ducklake_cleanup_old_files('lake', cleanup_all => true);
CALL ducklake_delete_orphaned_files('lake', cleanup_all => true);  -- crash leftovers
```

Retention interacts with `lake/archive/` (WORM, FCA 6-year — `docs/storage.md` §3):
archive tables keep snapshots for the full horizon.

## 6. Local development

`pnpm db:up` provides everything: Postgres with the `datalakes` DB + lake roles
(`apps/api/migrations/bootstrap/03-datalakes.sql`, first init only — `pnpm db:reset` after changing
it) and RustFS. API env (`apps/api/.env.local`):

```
DATALAKE_DATABASE_URL=postgresql+psycopg://lake_writer:lake_writer@localhost:5432/datalakes
DATALAKE_CATALOG_DSN=dbname=datalakes host=localhost port=5432 user=lake_reader password=lake_reader
DATALAKE_OLTP_DSN=dbname=portals host=localhost port=5432 user=app password=app
```

## 7. API taxonomy — where operations live

| Surface                           | Trust/auth                                                                                   | Use for                                                                 | Examples                                                                     |
| --------------------------------- | -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| **Plain functions** (in-process)  | the API's own process                                                                        | anything the API itself does                                            | `object_store.ensure_bucket`, `datalake.ensure_tenant_catalog`, `lake.query` |
| **Admin APIs** (`/admin/*`)       | user JWT → operations principal + granular RBAC permission; audited; in the OpenAPI client   | actions a **human** (ops staff / admin portal) initiates                | `POST /admin/tenants`, `POST /admin/tenants/{id}/storage/provision`          |
| **Internal APIs** (`/internal/*`) | shared `INTERNAL_API_TOKEN` (constant-time, 404 on failure), out of schema, network-internal | **service-to-service** — one service needs something only another holds | `/internal/env-config` (portal ← WorkOS secrets)                             |

Rule of thumb: _human intent → admin API; machine-to-machine → internal API; the API
talking to itself → a function._ Nothing tenant-facing ever hits `/internal/*`; portals
always go through the API, which owns authorisation.

## When the state db and the catalogue disagree

interlace records what it has built in a SQLite state db beside each provider's project
(`services/*/.interlace/state.db`); the tables themselves live in the DuckLake catalogue. The two
are independent, so anything that empties the catalogue **without telling interlace** — a
restored Postgres, a hand-dropped schema — leaves the daemon reading a model it
believes it built. `pnpm db:reset` (`api:db-reset`) deletes the host control planes
(`services/{seccl,sei,morgan-lloyd}/.interlace/{state,streams}.db`, plus the WAL sidecars)
after `down -v`. Restart a daemon that was already running; it still holds the old file.

```
Catalog Error: Table with name client_products__6b9736c5ca246de1 does not exist!
Did you mean "client_products__6b9736c5ca246de1__stage"?
```

**`interlace gc` does not fix this**, which is the intuitive first thing to reach for. It reclaims
snapshots _no environment references_ — and here the promoted environment references all of them,
so it keeps every one and changes nothing:

```
Would remove 0 snapshot(s), dropped 0 table(s); 43 snapshot(s) kept.
```

**Nor does a sync.** Change detection consults the same state db, so it skips exactly the models
that are missing.

`interlace reset --yes` is the wipe that does both: it drops environment views and the snapshot
schemas, and clears the control plane (terminal models stay recorded, so external tables are not
re-delivered into). Then a run rebuilds:

```bash
interlace reset --yes
interlace run
```

Deleting the state db also works and is worse: it discards the incremental ledger and the run
history along with the stale claims, so every incremental model silently reprocesses its whole
window.

**In a deployment**, that sequence is one button: admin → Platforms → the provider → **Rebuild**
(`POST /admin/platforms/{provider}/rebuild`, `platforms:write`). It is behind a confirm and separate
from Sync on purpose — it resets Interlace-owned state before rebuilding, so reads against that
provider fail until the run lands, and for a file-drop custodian like SEI it re-reads the whole drop.

Note the CLI needs the environment loaded (`DATALAKE_WRITER_DSN`, `S3_*`); the moon tasks supply it
from `/.env` + the service's own, a bare `interlace …` does not.

## Tenant data reset and demo lake

The non-production tenant data tool drops and recreates only the tenant's DuckLake metadata
schema and deletes the tenant bucket's `datalake/` objects. Shared provider catalogue schemas
and the core `platforms` bucket are never touched. The bucket, IAM users, encrypted credentials
and `storage.tenants` registry row are configuration and survive.

`reset_and_seed` then writes a deterministic fictional `platform.{positions,instruments,
valuations,transactions}` set into the tenant-owned catalogue. `seed_book` does the same
lake write after deleting only the previous `seeded_demo` clients and their book — it does
not drop the tenant catalogue or object store, so a live firm's real provider slice and
documents survive. Rows are keyed to the demo account links in OLTP and are used only by
the display seam. Money-movement checks continue to read the provider's own slice directly
and therefore fail closed while provider refresh is paused. The write **drops** each
relation before creating it: DuckLake `CREATE OR REPLACE` does not widen a column that was
first inferred as NULL-typed, which is how a later seed that wrote `book_cost` as a float
still served SQL NULL (overview dashes, Growth `+£0.00`, Cash still populated). The demo
holdings reader also fills leftover NULLs from the valuation series, so a tenant that has
not been re-seeded still shows a cost and a gain.
