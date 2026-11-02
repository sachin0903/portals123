# Graphene Platform — Data Architecture Decision Record

**Status:** Accepted (June 2026) — the cross-cutting data-architecture decisions the schema and
migrations follow.
**Companion files:** [`identity-and-access.md`](./identity-and-access.md) (the identity/authz model) ·
[`proposed-schema.sql`](./proposed-schema.sql) (the validated north-star DDL, a **reference**) ·
[`auth.md`](./auth.md) (login flow) · [`api.md`](./api.md) (the API + the RLS claims contract).
_(A table-by-table `schema.md`, `advice-chain.md`, and the lakehouse `data-platform` docs are
forthcoming — designed here, written when that work is built.)_
**Target engines:** PostgreSQL 18 (OLTP + DuckLake catalog) · DuckDB / DuckLake (lakehouse) ·
S3-compatible object storage (RustFS).

> This document records **how the data architecture works end-to-end** across the operational
> store, the per-client lakehouse, the integrations that feed them, and the event/audit spine —
> and the decisions behind it. Where a decision changes a table, the change is made in the
> migration for the step that first needs it (§8), not in a monolithic baseline.

**Implementation status (kept current):** `0001_auth_core` is **built and tested** — the `auth`
and `audit` schemas, the claim-driven RLS helpers + `auth.resolve_claims` minter, and the
append-only audit spine, with the request-layer claims contract wired (`apps/api/app/db.py` +
`auth.py`) and tenant isolation proven against a real Postgres (`apps/api/tests/test_rls.py`).
`0002_tenant_storage` is **built** (the `storage` schema + the per-tenant
object-storage registry — see `docs/storage.md`), so the domain migrations below renumber:
`0003_platform_crm` onward is designed below and pending. Migrations are **SQL-first**
(`apps/api/migrations/sql/00NN_*.sql` applied by a thin Alembic wrapper); roles bootstrap via
`apps/api/migrations/bootstrap/01-roles.sql`.

---

## 1. Context

Graphene is a multi-tenant B2B financial-advice platform. Advice firms are the tenants; their
clients hold investment accounts on custody platforms (Seccl, SEI) and SIPP providers (Morgan
Lloyd), with intelliflo/io and CRMs such as Plannr to follow. A single account can be associated
with **several** providers in different roles (a SIPP has SEI as custodian and Morgan Lloyd as
scheme administrator), a client exists on **several** platforms at once, and money moves through
the system as a stream of transactions that must be reconciled, audited, and retained for the FCA
horizon.

Two questions drove these decisions:

1. **How do we model entities that span providers, and the transactions that flow through them,
   without fragmenting the domain or losing source fidelity?**
2. **How do we give each client full data independence (sovereignty) in analytics while still
   collating a firm's clients from one place — not querying a hundred databases?**

The answers split the world into **two stores with distinct jobs**, fed by **one ingestion
bridge**, with **one event/audit spine** for full auditability.

---

## 2. The two stores at a glance

|                                 | **Operational store (OLTP)**                                   | **Analytical store (lakehouse)**                             |
| ------------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------ |
| Engine                          | PostgreSQL 18 (the app schemas)                                | DuckDB + DuckLake (parquet + Postgres catalog)               |
| Job                             | Run the portals, workflows, sagas, audit                       | Heavy analytics, reporting, NL→SQL, per-client sovereignty   |
| Isolation                       | **RLS** (`auth.tenant_lineage()`) — one shared multi-tenant DB | **Physical** — one catalog schema + one S3 bucket per client |
| "Collate a firm's clients"      | Here — one RLS query over the firm's lineage                   | n/a                                                          |
| "Whose hardware is my data on?" | n/a                                                            | Here — per-client bucket, exportable on offboarding          |
| System of record                | Durable rows (operational truth)                               | Full transaction/event history (analytical truth)            |

Both live on **one Postgres instance** (§5): the OLTP app schemas and the DuckLake catalog
schemas (`lake_<tenant>`) co-host there, but client portfolio data only ever lands as parquet in
per-client buckets — Postgres holds catalog _metadata_, never client data.

```
                         Seccl / SEI / Morgan Lloyd
                                   |
                         one ingestion bridge (§6)
                       +-----------+-------------+
              operational subset          full history
                       |                        |
        +--------------+-------------+   +-------+----------------------+
        |  OLTP Postgres (RLS)       |   |  per-client DuckLake buckets |
        |  auth platform crm ...     |   |  s3://<tenant>/lake/...      |
        |  platform.transactions     |   |  seccl.* sei.* ml.* catalogues  |
        |  events.events (outbox)    +-->|  audit/events archive (§5 C3)|
        +----------------------------+   +------------------------------+
                  ^ portals (RLS)                ^ DuckDB read-only attach
```

---

## 3. Decision 1 — Canonical OLTP model + polymorphic mapping (never schema-per-integration)

**In the OLTP store there is one canonical domain model**, not one schema per integration.
Per-integration schemas would fragment the domain and make a cross-provider entity such as the
SIPP impossible to represent as a single thing. Provider specifics are handled by two existing
mechanisms, not by duplicating the domain:

- **`platform.product_providers`** — declares which providers participate in a product and in what
  `role` (`custodian` | `scheme_administrator` | `wrapper_provider`) with a `required` flag. The
  account inherits its sync targets from its product.

  > **SUPERSEDED (0122, Sept 2026): the intent is built; the table is not.** This table was never
  > created. What did get built is `platform.product_platforms` — the per-platform mapping, keyed
  > `(product_id, platform_id)` and carrying `external_id` (the value the vendor's API takes) plus
  > an allowlisted `data` — and it had no `role`, so the role existed only at PROVIDER level in
  > `providers.PROVIDER_META`. That is enough to say "Morgan Lloyd administers schemes" and not
  > enough to say the thing that matters: **a SIPP custodied at SEI and administered by Morgan
  > Lloyd could not declare it**, which is exactly what driving the provider calls from data needs.
  >
  > `0135_product_platform_roles.sql` adds `role` to `product_platforms` rather than creating a
  > second table, because that table is already at the right grain and a second home for one concept
  > leaves every reader deciding which to trust. `role` starts NULL on hand-made rows, and the
  > backfill fills it from each platform's own provider role. Several mappings may carry the same
  > role: 0042 deliberately made one shelf item available on several custodians; the account's own
  > provider/link says which one holds that account. A short-lived UNIQUE `(product_id, role)`
  > assumption contradicted that model and failed against the first deployed multi-custodian shelf;
  > 0151 removes it.
  >
  > **0130 finished it.** 0122's backfill was the only thing that ever wrote `role`, so every
  > mapping the pipeline created afterwards landed NULL — and `GET /catalog/products` PREFERS the
  > custodian's mapping when naming the platform a product is held at, so it was silently falling
  > through to "any mapping" on exactly the rows a running pipeline produces. The sink now stamps
  > it on every insert from `PROVIDER_ROLES`, and the product page shows it, so a shelf no longer
  > lists a custodian and a scheme administrator as interchangeable. `required` was dropped
  > unread: it had no writer, no reader and no test — requiredness is a firm's statement about its
  > own shelf and nothing had been built to make it.
  >
  > **Enablement now writes the administrator mapping.** `providers.set_provider` (and
  > `catalogue.upsert_product` for a SIPP created afterwards) inserts
  > `product_platforms(role='scheme_administrator')` for every SIPP when Morgan Lloyd
  > is enabled, copying `scheme_type`/`sipp_type` from the tenant config into `data` on a first
  > attach. Onboarding `setup_account` repeats the attach so a firm enabled before this existed
  > still gets the link, then calls `sync_client`. A SIPP can still name SEI or Seccl as
  > custodian on a separate row — unique is `(product_id, provider)`, not one role per product.
  >
  > **Enabling SEI writes the custodian mapping.** The same call attaches GIA, ISA, JISA and
  > SIPP as `role='custodian'`. The nightly merge only creates that row from an account
  > already at SEI, and the products form used to save the enrolment template with a null
  > role, which the journey (`role = 'custodian'`) does not see. A null role is filled from
  > the provider's declared role; an explicit role, the external id and the enrolment
  > template stay. A third-party bond or pension is not attached.

- **`platform.links`** — the polymorphic external-id map
  (`UNIQUE(provider, entity_type, entity_id, link_type)`): one entity carries many provider ids
  simultaneously (ML `ContactID` + `SchemeRef` + `PolicyNumber` **and** the SEI account id, all on
  one account row).

**The lakehouse takes the inverse shape** (Decision 4 / §6): source-faithful raw schemas
(`seccl.*`, `sei.*`, `ml.*`) staged per source, then conformed into `platform.*` — bronze → silver →
gold. Source fidelity belongs in the lake; the canonical model belongs in OLTP.

### Entity-association analysis

| Entity                 | Association                                                   | How it is modelled                                                                                                                                                       |
| ---------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Account**            | **multi-provider** (one logical account, many providers)      | one `platform.accounts` row + `product_providers` (declared roles) + `platform_links` (realised ids); convenience `primary_custodian` for the common single-custody case |
| **Contact (client)**   | **multi-platform** (SEI client id + ML contact id)            | `platform_links` (`entity_type='contact'`, one row per provider)                                                                                                         |
| **Model (portfolio)**  | **multi-platform** (may exist on both SEI and Seccl)          | `platform.model_platforms` join (mirrors `product_providers`)                                                                                                            |
| **Transaction**        | **single source** (one movement, one origin)                  | `platform.transactions.source_system` (seccl \| sei \| morgan_lloyd)                                                                                                     |
| **Contact ↔ Account**  | parties on an account (owner / joint / trustee / beneficiary) | `crm.account_contacts` (role-qualified join)                                                                                                                             |
| **Instrument / asset** | a transaction references a security                           | `isin`/asset ref inline on the transaction for now; a `platform.instruments` reference table is deferred                                                                 |

The headline correction to the naïve "account → source platform": **an account is not
single-source.** It is one row associated with multiple providers; only a _transaction_ has a
single source.

---

## 4. Decision 2 — Transactions are a first-class fact; processing state is separate

The OLTP store historically had a forward-looking _expectations_ ledger
(`integrations.transaction_expectations`) and provider component tables (transfers, contributions)
— but **no canonical record of actual realised transactions**. That is the gap this decision
closes, in three layers.

### 4.1 Layer 1 — `platform.transactions` (the fact)

The canonical, source-synced record of what actually happened. Lives in `platform` (domain truth
alongside `accounts`), not `integrations` (it is not provider plumbing).

- **Immutable mirror for inbound credits**, idempotent upsert on `(tenant_id, source_system, source_transaction_id)` — re-syncing
  is a no-op, never a duplicate. Not hand-edited.
- **Outbound exception (0179):** an adviser-instructed movement that has not landed yet is a
  Graphene-originated row (`source_system = graphene`, key the origin's public id, status
  `pending` → `submitted` → `posted`). The vendor id is on `platform.links`, not rewritten onto
  the unique key (that would collide with the feed upsert). Mapping: [`docs/transaction-status.md`](transaction-status.md).
- **Associations:** `account_id`, `contact_id` (denormalised for query + RLS), asset/`isin`,
  `expectation_id` (nullable — the forecast it realised), `source_system`, `source_transaction_id`.
  The provider transaction id stays **on the inbound row** (indexed/unique). Outbound vendor ids
  go on `platform.links` (`entity_type = 'transaction'`) so they do not collide with the feed
  upsert — that registry is otherwise for entity-level identity.
- **Classification:** a **normalised type taxonomy** —
  `buy | sell | dividend | interest | fee | tax | transfer_in | transfer_out | deposit |
withdrawal | corporate_action | adjustment` — **plus** the preserved raw `source_type` /
  `source_subtype` and the full raw payload in `data jsonb`. Normalise on the way in; never lose
  the source shape.
- Money/dates/tax columns; `synced_at`. Tenant-scoped + **client-scoped** RLS (by `account_id`).
  Partition by tenant/date when volume warrants.

### 4.2 Layer 2 — expectations reframed (the forecast)

`integrations.transaction_expectations` is **not** the transaction record; it is the _forecast /
instruction_ layer. The two are different and complementary:

> **Expectation = forecast. `platform.transactions` = realisation.**

Matching becomes **expectation → a real `platform.transactions` row** (via `expectation_id`),
replacing the opaque `matched_external_ref` string.

### 4.3 Layer 3 — processing/relationship state in separate annotation tables

Because the inbound fact table is an immutable synced mirror, **workflow state never lives on
those rows**. Graphene-originated outbound rows (`source_system = graphene`, 0179) are the
exception: their `status` is the instruction's progress (`docs/transaction-status.md`). Each
other operational workflow gets its own table that _references_ transactions.

**Worked example — natural income.** Paying dividend/interest out rather than reinvesting is a
workflow over a _subset_ of income transactions: a standing **mandate** at account level
(`platform.income_instructions`), plus a **processing/run layer** (`platform.payment_runs` +
`platform.payment_run_items`). "Which transactions have been processed" is an **auditable join**,
not a mutable flag on the fact row. The same expectation → transaction → processing shape
generalises to contributions, withdrawals/drawdown, and fee verification.

### 4.4 OLTP vs lakehouse for transactions

The **full** transaction history is the analytical system of record in **per-client DuckLake**.
OLTP `platform.transactions` holds the **operationally-relevant working set** the portals and
workflows act on. One ingestion bridge feeds both (§6); cold OLTP partitions can be pruned once
mirrored to the lake (§5 C4).

---

## 5. Decision 3 — Single Postgres, two workloads, three privilege islands

One Postgres instance, **one database, many schemas**, hosts both the RLS OLTP app schemas and the
DuckLake catalog schemas. (Separate databases are rejected: cross-database access needs FDW/dblink
and breaks the requirement of a single Postgres catalog/connection.)

- **Namespacing:** the OLTP schemas as-is; DuckLake catalogs under the reserved `lake_<tenant>` /
  `lake_external` prefix (+ DuckLake's internal `__ducklake_*`). The OLTP migrator never creates
  objects in `lake_*` and vice-versa.
- **Why co-hosting is tenable:** Postgres holds only DuckLake _catalog metadata_. Client portfolio
  parquet lives in **per-client S3/RustFS buckets** — the sovereignty boundary stays at object
  storage.
- **"Not querying 100+ DBs":** the portals always hit the single RLS OLTP store (tenant-scoped via
  `auth.tenant_lineage()`); analytics + sovereignty live in per-client DuckLake.

### Role / grant matrix (the privilege islands)

| Role                                  | Can touch                                               | Cannot touch | Used by                                                                     |
| ------------------------------------- | ------------------------------------------------------- | ------------ | --------------------------------------------------------------------------- |
| `app` (login)                         | nothing directly — `SET ROLE authenticated` per request | —            | the request path (then RLS-scoped)                                          |
| `authenticated` (RLS)                 | the OLTP schemas (DML, RLS-scoped)                      | `lake_*`     | request path                                                                |
| `service_role` (BYPASSRLS, OLTP only) | the OLTP schemas                                        | `lake_*`     | workers, webhooks, the cross-RLS `SECURITY DEFINER` helpers, share resolver |
| `migrator` (login, owner)             | DDL on the OLTP schemas                                 | `lake_*`     | Alembic only                                                                |
| `lake_writer` (new)                   | `lake_*` / `__ducklake_*` (owner, single-writer)        | OLTP schemas | the ETL / ingestion                                                         |
| `lake_reader` (new, read-only)        | `lake_*` catalog metadata (SELECT)                      | OLTP schemas | the DuckDB query engine                                                     |

**Implemented (0001):** `anon`/`authenticated`/`service_role` are the RLS principals; the `app`
login role is a member of `authenticated`+`service_role` and `SET LOCAL ROLE authenticated` per
request. Tables use `FORCE ROW LEVEL SECURITY` so even the owner (`migrator`) is bound — which is
why the cross-RLS `SECURITY DEFINER` helpers (`auth.resolve_claims`, the lineage triggers,
`audit.record`) are **owned by the BYPASSRLS `service_role`** (a migrator-owned definer would be
RLS-blocked against its own tables). `authenticated`↔`lake_*` zero-access is a **grant** invariant
(CI assertion to follow). The `lake_*` roles + per-role pools land with the lakehouse work.

### Risks accepted (consciously)

1. **Single failure domain** — a postmaster crash takes down the advice platform _and_ analytics.
2. The `authenticated`↔`lake_*` boundary is a **grant invariant** in one DB (CI assertion is the guard).
3. **Coupled upgrade cadence** (Postgres major + DuckLake catalog format) and higher WAL volume.

PITR on the single instance plus per-client S3 versioning/object-lock makes them tolerable.

---

## 6. Decision 4 — One ingestion bridge, two sinks

> **Superseded in part by `docs/storage.md` (July 2026):** raw/staged data lands in the core
> `platforms` bucket, not per-tenant — SEI DSR batches are whole-firm, so tenant identity is
> only known after grant resolution. Only the post-resolution fan-out writes per-tenant buckets.
> The two-sink shape below stands.

A **single Seccl/SEI/ML extractor** is the only thing talking to the platforms. It lands data in
an immutable raw zone, then fans out — it never writes both stores in a coupled way.

```
sources -> immutable raw landing  s3://<tenant>/lake/_raw/...  (keyed by (provider, external_ref))
  |- sink 1 (operational) -> OLTP via service_role + the provider_sync queue
  |     upsert platform.transactions; SEI credits/valuations; expectation->transaction matching
  |- sink 2 (analytical) -> per-client DuckLake bucket via lake_writer (node_id-filtered, single-writer)
```

- **Sink 1** upserts realised movements into `platform.transactions`, then runs **expectation →
  transaction matching**: an open expectation is matched within an amount/date tolerance, moving
  `expected → instructed → matched → reconciled`; no deterministic match raises an
  `integrations.reconciliation_breaks` row rather than guessing.
- **Sink 2** is the existing `data-platform` seed/ETL generalised to incremental loads.
- The sinks are **decoupled** at the raw-landing seam + `events.events`.
- **Idempotency, three layers:** raw object key `(provider, external_ref)`; event `idempotency_key`
  (`ON CONFLICT DO NOTHING`); OLTP upsert uniqueness
  (`platform.transactions (tenant_id, source_system, source_transaction_id)`).

A minimal, synchronous version of sink 1 (a scheduled read-only batch, no queue) is what the first
build uses to populate accounts/contacts/transactions for the transfers workbench (§7 step 2); the
queue-driven, saga-aware version arrives with the transfers work (step 3).

---

## 7. Decision 5 — Event-driven auditability: CQRS-lite, not event-sourcing

We want full auditability — **not** event-sourcing (state derived by folding an event stream).
The posture is: **durable row = system of record + transactional outbox + append-only event log +
immutable regulated snapshots** (CQRS-lite). Four refinements:

- **C1 — Transactional outbox.** `events.events` _is_ the outbox: producers INSERT into it inside
  the business transaction (with `idempotency_key`). A single `service_role` **relay** reads
  `WHERE processed = false ORDER BY id FOR UPDATE SKIP LOCKED`, enqueues the pgmq message(s), then
  stamps `processed_at`. At-least-once toward pgmq (consumers are idempotent).
- **C2 — Two-tier audit.** App-level **domain events** (`events.events`) carry business/regulatory
  semantics. DB-level **DML triggers** write `audit.log` on _sensitive_ tables — catching
  out-of-band writes (workers, psql, migrations) app events cannot see, stamping the actor via
  `auth.uid()`/`auth.tenant_id()` from the session claims. **Implemented in 0001:** `audit.log` is
  **INSERT-only to the app role** (no UPDATE/DELETE grant — tamper-evident; CI-asserted), with the
  generic `audit.record()` trigger + `audit.attach()`; secrets (`key_hash`, `credentials_ref`) are
  redacted. 0001 attaches it to the sensitive auth tables; each later migration attaches its own
  (0002: accounts, transactions, applications, contact_users, client_advisers, compliance.\*).
- **C3 — Lakehouse archive.** `events.events` / `audit.log` / `workflow.transitions` stream into
  per-tenant DuckLake (sink 2) as immutable, time-travelable parquet.
- **C4 — FCA 6-year retention.** Object-lock/WORM on regulated files + the lake archive;
  time-partition the hot OLTP `events`/`audit`/`transactions` window once mirrored; GDPR erasure as
  PII-redaction-within-retained-records, not row deletion.

---

## 8. Build sequence (phased, minimal migrations)

The validated north-star DDL stays as a **reference** (`proposed-schema.sql`); the applied
migrations are built **forward and minimal** — each defines only what its step needs and ships its
RLS in the same file.

1. **`0001_auth_core.sql` — login + RLS + audit spine. ✅ BUILT & TESTED.** Roles
   (`authenticated`/`service_role`/`anon`), `auth.users`, `auth.tenants` (+ the `lineage[]` trigger
   **with a cycle guard and descendant recompute on re-parent**), the `auth.*` claims/RLS helpers
   (incl. `can_read_all`/`contact_scope`/`account_scope` and the `client` empty-lineage lever),
   **`auth.resolve_claims()`** (the `SECURITY DEFINER` request-claim minter), the WorkOS RBAC
   mirror (`roles`/`permissions`/`role_permissions`/`organisation_memberships`) + the environment
   role/permission seed, `auth.api_keys`, `set_updated_at`, and the append-only **`audit.log`** +
   `audit.record()`/`audit.attach()`. **Improvement over the original draft:** `auth.users` read is
   scoped to same-tenant + self (not `USING(true)`) so a client cannot read the cross-tenant user
   directory; `auth.tenants` stays `USING(true)` to avoid RLS recursion in `tenant_id()`. The
   request layer is wired (`db.py::tenant_session` + `auth.py::build_jwt_claims`) and isolation is
   proven by `tests/test_rls.py` (real Postgres via testcontainers).
2. **`0002_tenant_storage.sql` — the per-tenant object-storage registry. ✅ BUILT & TESTED.**
   The `storage` schema (the object-storage plane — `integrations` stays connectivity-only,
   Decision 7) + `storage.tenants` (bucket registry, firm-readable via lineage RLS)
   and the service_role-only `tenant_storage_secrets` (Fernet ciphertext), with
   `audit.record()`'s redaction list extended. Bucket-per-tenant provisioning + layouts:
   `docs/storage.md`; the physical file registry (`storage.files`) lands here with the
   documents work.
3. **`0003_platform_crm.sql` — entities + transactions + inbound sync. (designed; pending)**
   Reference (countries/currencies); `platform.platforms`/`products`/`product_providers`/`models` +`model_platforms`/`accounts`(+`primary_custodian`)/`platform_links`/**`transactions`**/
   **`advisers`** (the adviser regulatory profile keyed by `auth.users` — `irn` moved off the
   identity row); `crm.contacts` + **`contact_users`** (the login↔party bridge, replacing
   `contacts.user_id`) + **`client_advisers`** (staff assignment) + `account_contacts` (**with a
   key**); a first-class **`service_type`** (advised/execution_only) on the client;
   `integrations.providers`+`connections`; **client-scoped two-layer RLS** on
   contacts/accounts/transactions; the DML audit triggers; and `auth.resolve_claims` re-defined to
   fill `contact_ids`/`account_ids`. Plus the thin read-only SEI/Seccl inbound sync.
4. **The pensions ledger — first slice shipped as `0051_contributions_ledger.sql`. ✅ BUILT &
   TESTED.** `integrations` keeps connectivity only. The money **ledger**
   (`platform.transaction_expectations`) joins `platform.transactions`, and the
   `platform.transactions.expectation_id` FK that 0002 declared but never wired is finally
   attached. The SIPP domain lands in **`platform`, not `pensions`** — see the revision note on
   Decision 7 — as `platform.contributions` + `platform.transfers`, with the two-layer RLS and
   `crm.contacts.relevant_uk_earnings`/`pension_protection` (the HMRC eligibility inputs nothing
   held). Design + the provider gap analysis: `docs/contributions.md`.
   Still to come on this line: `reconciliation_breaks`, expectation↔transaction matching,
   `beneficiaries`/`policy_valuations`/`benefit_applications`, the pgmq queues and the outbox
   relay (C1).
5. **`0005_natural_income.sql` — the natural-income workflow.** `platform.income_instructions`
   (the standing per-account mandate), `platform.payment_runs` + `payment_run_items` (the
   processing layer), with their enums + RLS. Built forward once `platform.transactions` is
   populated (§4.3).

### Decision 6 — Morgan Lloyd: full bidirectional mirror + CRUD

ML (Dynamics/OData) is integrated as a **full mirror with create/update/delete write-through**.
A **delta-cursored** pull (`$filter` on `modifiedon`) keeps the local mirror a complete copy for
active accounts; the SIPP processes live in **`pensions.*`** and money movements in
**`platform.transactions`**, with `platform_links` mapping the ML ids. **Delete is the ML
cancel/void semantics**, never a hard destroy (SIPP records are FCA-retained), permission-gated
(`transfers:admin`) and audited. Multi-step flows remain **idempotent sagas** over the CRUD
primitives, executed through `integrations.sync_jobs`. The adapter is **contract-tested** against
recorded fixtures (no live calls in CI).

### Decision 7 — `integrations` is connectivity only; domain vs ledger split out

`integrations` is **connectivity only** — `providers`, `connections`, `sync_jobs`. The **money
ledger** (forecast `transaction_expectations` → realised `platform.transactions` → reconcile
`reconciliation_breaks`) lives in **`platform`**, and `integrations` only owns _how_ we reach
the provider. A pensions process is the join of: a ledger expectation (forecast) → a provider
saga (execution) → a transaction (realised) → reconciliation.

> **Revised July 2026 — the domain lands in `platform`, not `pensions`.** This decision
> originally routed the SIPP domain (a transfer-in, a contribution, a beneficiary nomination, a
> drawdown) to a separate **`pensions.*`** schema. When the first two of those were actually
> built (`0051_contributions_ledger`: `platform.contributions` + `platform.transfers`), the
> split earned nothing. The domain rows reference `platform.accounts`, `crm.contacts` and the
> ledger's own `platform.transaction_expectations` in every query, so every read crossed the
> boundary; and a second schema is another set of `GRANT USAGE`, another RLS surface and another
> thing to remember in a migration. Keeping the whole money story in one schema is the cheaper
> arrangement, and the connectivity/domain separation that motivated the decision is preserved
> — `integrations` still holds none of it. See `docs/contributions.md`.

Deferred until needed: onboarding (forms/illustrations), the documents pipeline, the generic
`workflow.*` engine, notifications fan-out, the full DuckLake analytics wiring, and
natural-income.

### Decision 8 — the contact _is_ the client; there is no separate `client` entity

Three layers, deliberately three (not four): a **login** (`auth.users`, identity only), a **party**
(`crm.contacts` — "the client as data": KYC, biographical, the thing that owns accounts), and the
**bridges** between them. There is **no `crm.clients` table** — "client" is the domain word for a
`crm.contacts` row, not a distinct object (see `client_advisers`, "client-scoped RLS" — all resolve
to `crm.contacts`).

- **A login is not a contact, and a contact is not a login.** The relationship is M:N in both
  directions (`crm.contact_users`, with a `capacity`: self / authorised / power_of_attorney /
  delegate). One login can act for several contacts (a couple, an attorney for a parent); one
  contact can be reached by several logins; and a contact can exist with **no login at all** (an
  organisation, a trust, a not-yet-onboarded prospect). This is why `contacts.user_id` was dropped
  in favour of the bridge.
- **Accounts associate with the contact, never the login.** `platform.accounts.contact_id →
crm.contacts(id)` is the owner; multi-party accounts (joint, trust) hang off `crm.account_contacts`
  with a `role` (`owner`/`decision_maker`/`trustee`/`beneficiary`). RLS scope (`contact_ids`/
  `account_ids`) is derived from `contact_users` + `client_advisers`, not from the user row.
- **"Client" is a state a contact occupies, expressed as columns, not a table.** `type`
  (`individual`/`organisation`), `service_type` (`advised`/`execution_only` — the FCA-relevant
  distinction), and having accounts distinguish a fee-paying client from a prospect or a merely
  referenced party (a beneficiary, a related company via `crm.relationships`). Whenever one side is
  a strict subset of the other like this, it is a column/flag, not a second entity.

Consequence for `0002`: build `crm.contacts` + the two bridges + `account_contacts`; point
`platform.accounts.contact_id` and `platform.transactions.contact_id` at `crm.contacts`; and have
`auth.resolve_claims` mint `contact_ids`/`account_ids` from `contact_users`/`client_advisers`. No
`client` table is introduced now or later.

### Decision 9 — a public id is a bare 12-character nanoid

Every table carries two identifiers and they have different jobs. **`id`** is internal — `INT`, or
`BIGINT` where the row count could plausibly pass 2^31 (audit/event streams) — lightweight and
sortable, ideal for joins, and it **never appears in a URL or an API response**. **`uid`** is the
public one: `text NOT NULL UNIQUE`, and it is what a route, an API path and a client see.

A `uid` is a **nanoid of length 12** over the 62 alphanumeric symbols (`A-Za-z0-9`) — nanoid's
default set minus `_` and `-`, which are URL-safe but not safe in the SQL identifier a tenant uid
becomes (71.5 bits rather than 72; `app/lake/holdings.py`'s `^[A-Za-z0-9_]+$` guard refuses `-`) — minted
by `app/ids.py::new_id` and **nothing else**. UUIDv4 is deliberately not used: it is long and ugly in
a URL, and its only real advantage — collision-freedom across independent writers with no shared
database — is not a situation this platform will ever be in. 12 symbols of 64 is 72 bits, and every
`uid` column is `UNIQUE`, so a collision is a caught integrity error rather than silent corruption.

**No type prefix.** `doc_…`/`con_…` was the previous convention and is gone. It did not pay for
itself: `con_` meant both `crm.contacts` (a person) and `platform.contributions` (money in), so it
failed at exactly the distinction it existed to make, and nothing in the codebase ever resolved a
uid without already knowing its table.

**One kind is held back: the tenant uid**, which stays `ten_<hex>`. It is the only uid that lands
somewhere other than a URL — `tenant_storage._bucket_name` strips the prefix and puts the rest in the
firm's S3 bucket name, where `_`, `-` and uppercase are all illegal, and `app/lake` and
`app/platform_entities` put `tenant_model_key(uid)` in a SQL identifier (`tenant_<hex>.positions`,
`book_<hex>.merge_ledger`) behind an `^[A-Za-z0-9_]+$` guard that refuses
`-`. A bucket cannot be renamed once it holds a firm's files, so moving this kind is a migration, not
an edit, and it is not needed yet. It is minted by `app/ids.py::tenant_uid` so the kind has one
shape; `tests/test_public_ids.py` pins that, and pins that no third module names the prefix.

Two lesser exceptions are allowlisted in the same test with their reasons: an object-store service
user's **access key** (a credential the store names, not a row's id), and the **demo seeders'**
`demo_`/`cdemo_` uids, whose prefix is a working `uid LIKE` delete-selector for their own re-run.

**Dev fixtures keep readable, stable ids on purpose** — `seed_clients` writes
`usr_seedclient_j_mitchell`, `ingest_seccl` derives `seccl_<external_id>` and purges by that prefix.
These are deterministic so a re-run is idempotent and so a human can tell a fixture from a real
record; they are not random mints and are not in scope. Production paths mint through `new_id()`.

**There is no SQL-side minter, and that is enforced.** Three statements built a uid with
`gen_random_uuid()` inside a SQL string (`auth.users` on a WorkOS event, and the two set-based
`INSERT ... SELECT`s that clone `notifications.rules`/`rule_versions` from the model tenant), which
left both kinds with two shapes — a Python-minted id from one path and a hex one from the other. A
set-based insert genuinely needs an id per row; the answer is to pin the source rows by key and pair
each with a `new_id()` through `unnest(...) WITH ORDINALITY`-style joins, as
`notifications/rules_service.py::clone_rules` does, rather than teach Postgres a second alphabet
that can drift from Python's. `tests/test_public_ids.py` scans string literals for this, because an
AST scan of Python expressions cannot see it.

**Existing rows are never rewritten.** A uid is matched whole, a document's bytes live at an
object-storage key containing it, and `documents.request_items.target_document_uid`, historic
`audit.actions.path_params` and notification links all reference it. Old rows keep their old shape;
a dev box gets one shape everywhere by reseeding. Minting is entirely application-side — no `uid`
column has a DB `DEFAULT` — so a Postgres `nanoid()` function is deliberately not added until there
is a table that would use it.

---

---

## 9. Open questions

- Transactions working-set boundary: full history in OLTP partitioned by tenant/date, or a hot
  window with the lake as archive? (Leaning hot-window + lake archive.)
- ML settlement-confirmation transport: poll (`cam_gettransfer`) vs webhook.
- Exact ML OData surface + Azure AD scope — capture real sandbox responses before wiring the live adapter.
- SEI credit ↔ expectation match tolerance (amount/date window) — ops input; config per tenant.
- FCA retention horizon for pension-transfer suitability (may exceed 6 years) — confirm with compliance.
- Per-tenant `lake_reader_<tenant>` roles for grant-level lake isolation, in addition to
  session-level attach isolation — recommended for true sovereignty, at the cost of more roles.
