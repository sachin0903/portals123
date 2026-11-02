# The API (`apps/api`)

FastAPI backend. It owns business logic, authorisation, audit and database access; WorkOS only answers "who is this user" (see [`auth.md`](auth.md)). The auth/RLS/audit foundation (`0001_auth_core`) is in, plus the cross-tenant admin surface and tenant-scoped portal reads. Cross-domain platform search, ranking and index design are documented in [`search.md`](search.md).

## Layout

```
apps/api/
  app/
    main.py        FastAPI app, CORS, router wiring, OpenAPI operation-id scheme
    config.py      Settings (pydantic-settings) — env-driven config
    auth.py        WorkOS JWT verification (issuer-keyed, multi-env), Principal, get_current_principal
    tenant_registry.py  issuer -> environment cache (project-per-tenant token verification)
    db.py          async engine, get_session, tenant_session (RLS context)
    provisioning.py WorkOS write-through + mirror (tenant/member)
    routers/       health.py, me.py, admin.py
  migrations/      Alembic + SQL-first DDL in migrations/sql/ + bootstrap/01-roles.sql
                   (the one-time superuser role/grant bootstrap that precedes migration 0001)
  scripts/         dev tooling (generate_permissions_ts.py)
  tests/           pytest (+ conftest.py spins PG18 via testcontainers; test_rls/test_admin)
  pyproject.toml   deps, ruff, mypy, pytest config
```

## Endpoints

| Method    | Path                                                                     | Auth            | Returns                                                                                                                                                                                                                                                                                                         |
| --------- | ------------------------------------------------------------------------ | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET       | `/health`                                                                | none            | `{"status": "ok"}`                                                                                                                                                                                                                                                                                              |
| GET       | `/me`                                                                    | bearer          | the verified `Principal` (permissions + `portal`; shells use this instead of a second `/me/portal` round trip)                                                                                                                                                                                                  |
| GET       | `/me/portal`                                                             | bearer          | `{ portal }` — kept for login-time `validatePortalSession`; navigations read `portal` off `/me`                                                                                                                                                                                                                 |
| GET       | `/me/tenant`                                                             | bearer (tenant) | the caller's active firm (RLS-scoped)                                                                                                                                                                                                                                                                           |
| GET       | `/me/memberships`                                                        | bearer (tenant) | the caller's memberships — the org switcher                                                                                                                                                                                                                                                                     |
| POST      | `/me/page-view`                                                          | bearer (tenant) | record the caller's pathname (`audit.page_views`). Not an action event. 204.                                                                                                                                                                                                                                    |
| GET       | `/me/notes`                                                              | bearer (tenant) | the caller's own notes — the dashboard checklist (`platform.staff_notes`, 0249). RLS admits the owner alone.                                                                                                                                                                                                    |
| POST      | `/me/notes`                                                              | bearer (tenant) | add a note `{body}` (trimmed, ≤500 chars); appended to the owner's order. 201 with the row. Not an action event: private.                                                                                                                                                                                       |
| PATCH     | `/me/notes/{uid}`                                                        | bearer (tenant) | re-word (`body`) or tick (`done`) one of the caller's notes; somebody else's uid is a 404. Not an action event.                                                                                                                                                                                                 |
| DELETE    | `/me/notes/{uid}`                                                        | bearer (tenant) | remove one of the caller's notes. 204, also when there was nothing (or nothing of theirs) to remove. Not an action event.                                                                                                                                                                                       |
| POST      | `/me/rum`                                                                | bearer          | join a browser page view (route id, settle time, web vitals) onto the trace the portal minted. Not an action event. A bad trace id emits nothing. 204.                                                                                                                                                          |
| GET       | `/me/sessions`                                                           | bearer (tenant) | the caller's live sessions from `auth.sessions`. WorkOS is listed after the response.                                                                                                                                                                                                                           |
| POST      | `/me/sessions/refresh`                                                   | bearer          | copy the caller's live sessions from WorkOS into `auth.sessions`. Not an action event. 204.                                                                                                                                                                                                                     |
| POST      | `/admin/tenants`                                                         | bearer (system) | create a firm (local tenant row; the WorkOS project is registered separately)                                                                                                                                                                                                                                   |
| GET       | `/admin/tenants`                                                         | bearer (system) | list all firms (cross-tenant)                                                                                                                                                                                                                                                                                   |
| POST      | `/admin/tenants/{id}/members`                                            | bearer (system) | invite a person into the firm (first portal = the role's portal; WorkOS + mirror)                                                                                                                                                                                                                               |
| GET       | `/admin/tenants/{id}/members`                                            | bearer (system) | list a firm's people, one row per person with the portals each holds                                                                                                                                                                                                                                            |
| PUT       | `/admin/tenants/{id}/members/{uid}/portals/{portal}`                     | bearer (system) | grant a person a portal + role (reactivates if revoked)                                                                                                                                                                                                                                                         |
| DELETE    | `/admin/tenants/{id}/members/{uid}/portals/{portal}`                     | bearer (system) | revoke a person's access to one portal                                                                                                                                                                                                                                                                          |
| PATCH     | `/admin/tenants/{id}/members/{uid}/portals/{portal}`                     | bearer (system) | change a person's role within one portal                                                                                                                                                                                                                                                                        |
| GET       | `/admin/tenants/{id}/environment`                                        | bearer (system) | the tenant's single WorkOS config for this instance (or null)                                                                                                                                                                                                                                                   |
| PUT       | `/admin/tenants/{id}/environment`                                        | bearer (system) | register/update the tenant's WorkOS config (no stage — the instance stamps it)                                                                                                                                                                                                                                  |
| GET · PUT | `/admin/tenants/{uid}/currency-settings`                                 | bearer (system) | read/replace the firm's allowed currencies and base/default (`tenants:read` / `tenants:write`; model tenant uses `model:*`)                                                                                                                                                                                     |
| PUT       | `/admin/environments/{id}/secrets`                                       | bearer (system) | set/rotate an environment's api key + cookie password (encrypted at rest)                                                                                                                                                                                                                                       |
| POST      | `/admin/environments/refresh`                                            | bearer (system) | reload the issuer→environment verification registry                                                                                                                                                                                                                                                             |
| GET       | `/admin/audit/actions`                                                   | bearer (system) | the action-event stream, newest first (`audit:read`; `before`/`after` cursors + filters)                                                                                                                                                                                                                        |
| \*        | `/admin/tenants/{id}/email-templates…`                                   | bearer (system) | author a tenant's emails — the model preset (`model:*`) or a firm's (`tenants:*`)                                                                                                                                                                                                                               |
| GET       | `/firm/members`, `/firm/roles`, `/firm/permissions`                      | bearer (firm)   | a firm managing ITSELF from the platform portal (see below)                                                                                                                                                                                                                                                     |
| POST      | `/firm/members`; `PUT/DELETE/PATCH /firm/members/{uid}/portals/{portal}` | bearer (firm)   | invite / grant / revoke / set-role (own firm)                                                                                                                                                                                                                                                                   |
| POST      | `/firm/members/{uid}/deactivate\|reactivate\|resend-invite`              | bearer (firm)   | whole-person suspend / resume / re-invite (own firm)                                                                                                                                                                                                                                                            |
| POST      | `/firm/roles`; `PUT /firm/roles/{slug}/permissions`                      | bearer (firm)   | create/edit the firm's own roles + permissions                                                                                                                                                                                                                                                                  |
| GET       | `/firm/audit/actions`                                                    | bearer (firm)   | the firm's OWN action trail (`activity:read`; both sides, AML family excluded)                                                                                                                                                                                                                                  |
| GET       | `/firm/comms/messages[?facets=true]`                                     | bearer (firm)   | the firm's outbound-email ledger — one row per send attempt, with the provider's delivery state (`activity:read`; tenant from the session, never a param; AML family excluded). `?contact=<uid>` narrows to one client, as the action stream's does — an unresolvable one narrows to nothing, never to the firm |
| GET       | `/firm/comms/messages/{uid}`                                             | bearer (firm)   | one message: the envelope, its provider callbacks, the stored body and wire request (`activity:read`)                                                                                                                                                                                                           |
| GET       | `/firm/event-registry`                                                   | bearer (firm)   | every declared event with what it can notify — four states (`assignable`/`ready`/`declarable`/`actor_only`), the firm's rules, automation + 30-day email counts (`notifications:read`)                                                                                                                          |
| GET · PUT | `/reviews/policies[/{action_key}]`                                       | bearer (firm)   | four-eye policy per wired action (`actions:write` to change; any staff may read so a save form can ask for a justification). See [`docs/reviews.md`](reviews.md)                                                                                                                                                |
| GET       | `/reviews`, `/reviews/pending`, `/reviews/{uid}`                         | bearer (firm)   | the review workqueue (any staff member; pending strip is the same read). Scopes `inbox` / `mine` / `all`                                                                                                                                                                                                        |
| POST      | `/reviews/{uid}/approve\|reject\|cancel`                                 | bearer (firm)   | decide a held command (the policy role, and not the proposer; cancel is the proposer's). Approve applies once and then emits the action's happened-event                                                                                                                                                        |
| POST      | `/webhooks/resend`                                                       | Svix signature  | Resend's delivery callbacks — advances the ledger. Always answers 200; blank `RESEND_WEBHOOK_SECRET` disables it (`docs/comms.md`)                                                                                                                                                                              |
| GET       | `/internal/env-config?host=…`                                            | internal token  | decrypted per-host WorkOS config; loopback compose ports alias the vite ports                                                                                                                                                                                                                                   |
| GET       | `/internal/admin-branding`                                               | internal token  | Graphene tenant logo set for the admin shell (unparseable theme → null logos)                                                                                                                                                                                                                                   |

**CRM + platform reads** (`app/routers/crm.py`). All `bearer (tenant)` on `tenant_session`, so **row visibility is RLS, not code** — a `contacts:read_all` staff member sees the whole firm, a scoped adviser only their book, a client only their own party, all from the same query. There is deliberately no `require_permission` on the reads. Cash and unrealised growth on the client and account listings come from the latest `platform.valuations` row. The account record and the client record's accounts use that same custodian cash (an administrator balance is added on the account record, not summed into `cash` twice). The holdings summary still reads the lake through `_lake_metrics`. The OLTP `value` stays authoritative for AUM. The staff-side GETs here (and across documents/tasks/flows/applications) actually ride `book_session`/`firm_book_session` (`app/db.py`) — identical to `tenant_session`/`firm_session` with no param, while an operations principal holding `book:read` may add `?tenant=<uid>` to read the TARGET firm's book read-only (403 before 404, audit keeps naming the operator); the admin portal mounts this at `/tenants/[uid]/book` (clients · accounts · transactions, the shared `@graphene/ui` book surfaces with `actions: null` — except the client record, which mounts the approved proxy middle-office actions for an operator holding `book:write`, every write riding the same staff endpoints with `?tenant=`).

| Method | Path                                                          | Returns / purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------ | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/crm/contacts`                                               | the clients the caller may see, each with `portfolio_value`, owned `account_count`, `represented_account_count` (JISA registered-contact roles; status only, never AUM), `account_types[]` (the distinct wrappers held — the listing shows these as tags in place of the count), `client_type` (the kind, at its most specific: `individual`, or the organisation's `sub_type` — `trust`/`company`/`charity` — falling back to `organisation`), `advisers[]` (the one adviser, as a list so the facet stays array-shaped), `cash`, `growth`. Search, filters (`status`, `adviser`, `account_type`, `type`), sort, `COUNT(*)` and `LIMIT`/`OFFSET` run in SQL so a 25-row screen does not ship the book; `cash` and `growth` are the latest `platform.valuations` of each valued account, summed onto the client (cash across source systems; growth the custodian's stated unrealised gain, or the administrator's when the custodian stated none). A NULL is a figure nobody stated; zero is a measured flat position. Facets lift their own filter so Active still offers Lead. `type` matches EITHER level of the kind so `organisation` still finds a trust. No `limit` keeps the bare list every existing caller parses                |
| GET    | `/crm/contacts/{id}`                                          | one client with accounts, advisers, notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| GET    | `/crm/contacts/{id}/shell`                                    | lean chrome for the client-record layout: identity (incl. `dob`, `retirement_age`, `marital_status` — the illustration composer always asks for the retirement age, and asks for marital status when it is missing), accounts, advisers, users, KYC kind/status, and `chats_unread`. The portals' record layout loads this instead of the fat row; Overview and Details still call `GET /crm/contacts/{id}` themselves                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| GET    | `/crm/contacts/by-uid/{uid}`                                  | the same client, addressed by their **uid** — what a portal URL carries. Delegates to the row above, so the two cannot answer with two record shapes. See "Addressing" below                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| GET    | `/crm/accounts`                                               | the accounts listing: product, `model_id`/`model_name`, `discretionary` (= carries a model), `service_type` (the service the firm provides: the captured `investment_capacity`, else `discretionary` for a model-run account, else **null** — advisory and execution-only cannot be told apart from what we hold, so neither is guessed), `adviser`, nested `client`, `cash`, `growth`; paged filters include product `type`, `service` and stable model id via `model`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| GET    | `/crm/accounts/{id}`                                          | one account + ordered equal `fee_packages[]` (each carrying its own **charge** — `fee_type`, `rule`, `frequency`, `charge_basis`, `payer`, `currency`, `vatable`, `minimum_amount`/`maximum_amount`, `description` — because the record's Fees card projected the firm's pricing LABEL alone, so the question it is read to answer could only be answered from the fee catalogue; the reader formats it, `book/journeys/fee-summary.ts`), the product's `bank_details_allowed` flag, `bank_accounts[]` (this account's own; empty → the card falls back to the nested client's only when bank details apply), legacy `fees`/`payments`/`withdrawals` jsonb, custodian, the client's address/bank/pension inputs (person data stays nested), and `withdrawal_fees` (`{faster_payment, swift}` for an SEI-custodied account; null for another custodian). Also `performance` (`[{label, value}]`, the account's own daily value series from the lake, last 120 points) and `performance_period` (`{from, to}` or null) — the client portal's account record wears the portfolio header scoped to one account, and these are what its trace and period change read; `gain`/`gain_pct` beside them come from the same series (`_series_return`) |
| GET    | `/crm/accounts/by-uid/{uid}`                                  | the same account, addressed by its **uid**. Delegates, like the contacts pair                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| GET    | `/crm/holdings`, `/crm/holdings/summary`, `/crm/transactions` | current holdings / first-paint allocation+cash summary / recent transactions; `?account_id` or `?contact_id`, plus exact transaction `?isin`. Holdings follow each account's primary custodian link; RLS resolves ids before any lake read. `GET /crm/transactions` pushes status/type/adviser/account-name/`q` into SQL `WHERE`. `paged=true` is a real page of the match set (no 2000-row pre-fetch); a bare list (no `paged`) stays a list. `needs_action=true` (alias `needs_categorisation=true`) so the client-record action strip does not pull settled rows. The record layout never lists unpaged holdings for first paint — it uses `/crm/holdings/summary`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| GET    | `/crm/look-through/{fund_uid}`                                | dated IFSL composition for a fund the caller demonstrably holds; optional `?as_of` and account-uid `?account`. Unknown and not-held funds both return 404                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| GET    | `/crm/content` · `/{uid}` · `/{uid}/file`                     | published, eligible news/articles/guides for the signed-in contact (`kind` / `category` / `q` / paging). Detail 404s if unpublished or ineligible. Guides stream the PDF. See [`content.md`](content.md)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| PUT    | `/crm/accounts/{id}/fee-packages`                             | atomically replace all enabled fee packages compatible with the account's tenant/product/model (`instructions:write`; no primary package)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| PUT    | `/crm/accounts/{id}/regular-movements`                        | replace the account's scheduled payments-in and withdrawals (`instructions:write`). Empty lists clear. GET projects the same register, including the object older onboarding rows hold                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| PUT    | `/crm/accounts/{id}/bank-accounts`                            | replace this account's own bank accounts (`instructions:write`). A list, always (`[0]` is the default); `null` clears. A non-empty value is refused when the product has `bank_details_allowed = false`, so a JISA cannot acquire a withdrawal destination. Account first, client second — SEI holds pay-to profiles per account, Seccl per client                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| POST   | `/crm/money/preview`, `/crm/money/instructions`               | a CLIENT's own payment in / withdrawal (`{kind, account_uid, amount}`): preview answers the review screen without recording anything; the raise re-decides from the account's position and writes an ordinary `platform.instructions` row (held pending, not dispatched, when the safety check trips). `GET /crm/money/instructions[?contact_id=]` lists their pending ones; `POST …/{uid}/cancel` withdraws one they raised. [`contributions.md` §10](contributions.md#10-client-raised-money-oct-2026)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| GET    | `/crm/overview`, `/crm/home`                                  | firm KPIs + AUM trend (dashboard)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| POST   | `/crm/accounts/{id}/drawdown-session`                         | mint a one-time DCube drawdown SSO link via Morgan Lloyd (`instructions:write` — money movement, not a read). Sends a CTC1 `illustrationTemplate` when the book has one (needed after SSO; the mint succeeds without it). Sex and date of birth are omitted when unknown — they are not invented. 400 non-pension · 409 account or client not ML-linked (the rows Sync to Morgan Lloyd writes) · 503 credentials unset                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| POST   | `/crm/contacts/{id}/sync-to-morgan-lloyd`                     | push the client, their SIPP schemes, policies and beneficiaries to Morgan Lloyd (`contacts:sync_morgan_lloyd`). Idempotent via `platform.links`. 422 missing required details · 502/503 vendor                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| POST   | `/crm/accounts/{id}/sync-to-morgan-lloyd`                     | the same push, scoped to one SIPP (`contacts:sync_morgan_lloyd`). The account record's **Sync to Morgan Lloyd** button hides without this grant                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| POST   | `/crm/contacts/{id}/illustrations`                            | generate a pension illustration through the CTC engine and file the PDF as a client document (`illustrations:write`). See below                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| GET    | `/crm/illustrations/onboarding`                               | list unused successful SIPP illustrations available for journey kickoff (`cases:read`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| GET    | `/crm/contacts/{id}/screening`                                | the firm's read-only window onto Graphene's AML screening of one of its clients (`screening:read`) — reviewed hits only, never the vendor dossier. See below                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |

The account holdings surface is a **current-position** view. It shows the feed's real `as_of`,
book cost, unrealised gain, ISIN/SEDOL and ISO currency, but there is no historical position
series to answer a fixed-point date picker. Daily account valuations are not per-instrument
holdings and must not be presented as if they were.

**IFSL fund look-through** (`app/routers/lookthrough.py`, design record:
[`lookthrough.md`](lookthrough.md)). Operations imports one or more XLSX reports through
`POST /admin/look-through/import` (`lookthrough:write`) and lists the global history at
`GET /admin/look-through` (`lookthrough:read`). The register returns every matching share-class
ISIN, and `GET /admin/look-through/{fund_uid}` opens the composition and dated history without a
holding proof — this is an operations reference view, not a client entitlement. A matching
share-class ISIN annotates a holdings row with the fund uid/name/latest date, but `/crm/` composition
reads additionally prove a matching current holding inside the caller's RLS-visible book. Snapshot
corrections supersede rather than overwrite. The detail payload includes share-class NAV history,
signed currency cash, a derived maturity profile, share-class/fund-level currency contracts and
position book-cost gain/loss. An identical file hash is normally a no-op; re-uploading a
pre-analytics snapshot once reports `enriched` and backfills those facts from the same source bytes.

**Client portal content** (`app/routers/content.py`, design record: [`content.md`](content.md)).
Firm-authored news, articles and PDF guides. Staff CRUD is `content:read`/`content:write` on
`tenant_session`; clients read only published, eligible items on `book_session` (`GET /crm/content`).
Bodies are sanitised at publish (`nh3`); clients never receive the GrapesJS schema.

**Contributions + transfers** (`app/routers/contributions.py`, design record: `docs/contributions.md`). `bearer (firm)` on **`firm_session`** — every statement is scoped to the caller's own resolved `tenant_id` — and gated on `contributions:*` / `transfers:*`. Booking a contribution writes three things: the domain record, a `platform.transaction_expectations` forecast, and (best-effort) an **Anticipated Cash Receipt** at SEI. The custodian call is deliberately non-fatal on create: the local records are Graphene's truth and must not be lost because SEI is unreachable or a firm's reference codes are unset, so the outcome is reported in the response's `sei` block and retried explicitly. Relief and gross are recomputed **server-side** on every classification — the portal's preview is a preview.

| Method     | Path                                                | Returns / purpose                                                                                                                        |
| ---------- | --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| GET        | `/contributions`                                    | the firm's contributions with account/client/expectation context + HMRC inputs; `?account_id` or `?contact_id` narrows                   |
| GET        | `/contributions/summary`                            | gross contributed this tax year for one client (`contact_id`, required) and optionally one account (`account_id`) (`contributions:read`) |
| POST       | `/contributions`                                    | book one: contribution + expectation + SEI lodge. 400 bad request · 404 not the firm's account (`contributions:write`)                   |
| GET        | `/contributions/{uid}`                              | one contribution                                                                                                                         |
| PATCH      | `/contributions/{uid}`                              | set/amend the HMRC classification, recomputing gross + relief. 409 past `confirmed` (`contributions:write`)                              |
| POST       | `/contributions/{uid}/status`                       | move it along the pipeline (hold, flag for manual, reconcile) (`contributions:write`)                                                    |
| POST       | `/contributions/{uid}/lodge-expectation`            | retry the SEI ACR. 422 names the missing reference data · 409 already lodged · 502 SEI refused · 503 credentials unset                   |
| POST       | `/contributions/{uid}/record-to-morgan-lloyd`       | create the `cam_contribution` and link its GUID. 409 already recorded · 422 missing details or an unpushable category · 502/503 as above |
| GET · POST | `/transfers`                                        | list · raise a transfer in from a ceding scheme, with its expectation (`transfers:read` / `transfers:write`)                             |
| GET        | `/transfers/{uid}` · POST `/transfers/{uid}/status` | one transfer · move it along (`transfers:read` / `transfers:write`)                                                                      |

Three SEI values are external unknowns and are **never guessed**: the account's SEI account number + portfolio id (`platform_links`, provider `sei`) and the firm's description/treatment reference codes (`platform.tenant_platforms.config`). `app/integrations/sei/missing_fields` names them and the endpoint answers 422 — the full gap table is in `docs/contributions.md` §4.

**Illustrations** (`app/routers/illustrations.py` + `app/integrations/ctc/`). Pension wrappers only. The client's name, date of birth and the account's fund value are read from the record — the adviser supplies only assumptions. `xml_builder.py` emits CTC Bespoke XML v1.6 (`message_version` CTC1) and validates against the spec's closed vocabularies before any network call, because the engine's only diagnostic is "Error reading XML message". **There are no CTC credentials**: the endpoint is reached with `nodb=true` and the request identifies itself through `<agent_name>` (`CTC_AGENT_NAME`). A CTC rejection returns 201 with `errors[]` rather than an HTTP error — the engine's own descriptions are the useful output. `document_location` is documented only as a _name_, so it is fetched when it is a URL and otherwise handed back with `stored: false`, rather than guessing a base path and filing a 404 body as an illustration.

SIPP onboarding accepts only an unused, non-placeholder, error-free run belonging to one selected
owner. A new applicant can first be persisted and illustrated from kickoff. The application binds
the run to the account in the same transaction and retains the provenance
on `platform.accounts.onboarding_illustration_id`. Matching assumptions become reviewed defaults;
they never overwrite the live client record, reinterpret fund value as cash, or infer fee packages.

**AML / KYC screening** (`app/routers/aml.py`; decision record [`aml.md`](aml.md)). ComplyCube is the only screening provider. The operations console reads `compliance.*` (migrations `0186_compliance` + `0221_complycube_core`), which the worker keeps in step with ComplyCube, plus `crm.kyc_checks` for clients still waiting on a first screen. Writes go to ComplyCube first and are then stored, so a refused provider call leaves nothing half-recorded. The router is gated on `aml:read` / `aml:write` / `aml:adjudicate`, which exist only in the operations portal.

| Method | Path                                             | Returns / purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------ | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/aml/clients`                                   | every client Graphene screens, one row each, paginated (fixed 25/page): the latest identity check and AML screen, the ComplyCube risk level and `needs_attention`, which sorts first. `?tenant=<uid>` narrows to one firm and `?contact=` to one client, plus `q`/`sort`/`dir`                                                                                                                                                                                                                  |
| GET    | `/aml/clients/{key}`                             | one client by a list key (the CRM contact uid, or the `compliance.clients` uid for a partner client with no contact): facts, risk profile, the latest identity check and AML screen in the `/aml/checks/{uid}` shape, earlier checks and internal notes (`?images=false` lists no identity images and records no view), and what needs a person: `reasons`, `steps`, `waiting_since` and `age` (`app/complycube/attention.py`)                                                                  |
| GET    | `/aml/work`                                      | every client that needs a person, the longest wait first (`app/complycube/attention.py`): `key`, `name`, `type`, `firm` (`uid`, `name`, `icon_url`), `origin`, `waiting_since`, `age` (`working_days`, `band`: `fresh` / `ageing` over 2 / `overdue` over 5), `reasons` and `steps`. `?tenant=<uid>` narrows to one firm, `q` searches the name, `reason` keeps one of `hits`, `identity`, `screens`, `monitoring`, `held` (anything else is ignored). At most 500: `{items, total, truncated}` |
| GET    | `/aml/work/summary`                              | how much work there is, narrowed as `/aml/work`: `total`, `by_reason` (a client counts under every key it falls under) and `overdue`                                                                                                                                                                                                                                                                                                                                                            |
| GET    | `/aml/clients/{key}/report`                      | Graphene's client report as a PDF: details, risk, every identity check and AML screen. Made on request, never stored; the download is recorded (`aml.report_downloaded`). 404 when the client was never checked                                                                                                                                                                                                                                                                                 |
| GET    | `/aml/checks/{uid}/client`                       | the client key a check belongs to, for the console's one-check links                                                                                                                                                                                                                                                                                                                                                                                                                            |
| GET    | `/aml/checks/{uid}`                              | one check: who it is about, its firm, its matches, its identity result and its history. For an identity check it lists which images ComplyCube holds ("ID front", "ID back", "Selfie": `part`, `label`, `content_type`; a side may be a PDF), and records an `aml.identity_images_viewed` event                                                                                                                                                                                                 |
| GET    | `/aml/checks/{uid}/images`                       | one identity check's images, as the check lists them (`images`, `images_missing`), for a page that read the client without them; records the view, unless `?record=false` (a page reading them ahead). 404 for an AML screen                                                                                                                                                                                                                                                                    |
| POST   | `/aml/checks/{uid}/images/viewed`                | `aml:read` — record that a check's identity images were shown (`image_count`, 1–3), by a page that read them ahead. Event `aml.identity_images_viewed`. 404 for an AML screen                                                                                                                                                                                                                                                                                                                   |
| GET    | `/aml/checks/{uid}/images/{part}`                | one identity image (`front`, `back` or `selfie`), read from ComplyCube and never cached; 404 when ComplyCube holds none                                                                                                                                                                                                                                                                                                                                                                         |
| GET    | `/aml/checks/{uid}/report`                       | Graphene's report of one check as a PDF, with its decision once decided. Made on request, never stored; the download is recorded (`aml.report_downloaded`)                                                                                                                                                                                                                                                                                                                                      |
| POST   | `/aml/screen`                                    | `aml:write` — run a ComplyCube AML screen for a portal client waiting on one, or whose details changed after a clear. Idempotent: an open AML check is returned instead of a new one                                                                                                                                                                                                                                                                                                            |
| POST   | `/aml/checks/{uid}/matches/{match_uid}/decision` | `aml:adjudicate` — confirm or dismiss one match on an AML check in `refer` (comment optional), then settle the check when no match is undecided. 409 once the check is settled                                                                                                                                                                                                                                                                                                                  |
| POST   | `/aml/checks/{uid}/allow`                        | `aml:adjudicate` — let a portal AML screen held on a confirmed match continue: decides the check under the operator (comment optional), closes its tasks and passes the kyc row. 409 unless the check is held                                                                                                                                                                                                                                                                                   |
| POST   | `/aml/checks/{uid}/decision`                     | `aml:adjudicate` — clear or reject an identity check (comment required). A `failed` check can only be rejected; an AML check settles from its matches (409)                                                                                                                                                                                                                                                                                                                                     |
| POST   | `/aml/checks/{uid}/request-photos`               | `aml:adjudicate` — ask a portal client for new photos of an undecided identity `refer` (`photos`, `reason`, `comment`). Marks the check decided, keeps it `refer`, and gives the client a new try. Nothing goes to ComplyCube. 409 when no try waits on it                                                                                                                                                                                                                                      |
| PUT    | `/aml/checks/{uid}/monitoring`                   | `aml:write` — turn ComplyCube's ongoing monitoring on or off for an AML check                                                                                                                                                                                                                                                                                                                                                                                                                   |
| POST   | `/aml/checks/{uid}/rerun`                        | `aml:write` — run an AML screen ComplyCube could not run (`failed`) again, for the same ComplyCube client                                                                                                                                                                                                                                                                                                                                                                                       |
| POST   | `/aml/clients/{key}/notes`                       | `aml:write` — add an internal note on a client (`body`, 1–5000 characters once trimmed; 422 otherwise). Kept on the contact, else the compliance client. Graphene only and append-only; event `aml.client_note_added`                                                                                                                                                                                                                                                                           |
| POST   | `/aml/clients/{key}/identity-photos`             | `aml:adjudicate` — upload a portal client's identity photos for them and start an identity check (multipart: `comment`, `document_type`, the document's sides, `idv_selfie`, `issuing_country`). The photos go to ComplyCube and are not kept here                                                                                                                                                                                                                                              |

A missing ComplyCube key is **503** on every write; a ComplyCube refusal or no answer is 502; a check in the wrong state is 409.

`aml_session` mirrors `storage_session`: runs as `service_role`, resolves the caller's own tenant via `auth.resolve_claims`, and takes an optional `?tenant=<uid>` to target a firm. Unlike `storage_session`, there is no "own tenant" branch that matters — ops staff always resolve to the operations tenant, so any firm's AML data is _always_ the foreign branch, and `AML_READ` is required **unconditionally** (not only when foreign), because the queue is cross-tenant by default and only narrows when `?tenant=` is given. There is no RLS backstop under `service_role` (BYPASSRLS): every predicate is hand-written, exactly as `storage.py` does.

The tipping-off control is on `compliance.*`. Every compliance read policy is `tenant_id = ANY (auth.tenant_lineage())` and **never** `auth.contact_scope()` (contrast `crm.kyc_checks`) — showing a client their own sanctions/PEP/adverse-media matches, or even that a check exists against them, is tipping-off, an offence under POCA 2002 s.333A; see [`aml.md`](aml.md) for the full rationale. `compliance.checks` grants `authenticated` its status columns only (0221), and `compliance.matches`, `compliance.webhook_deliveries` and `compliance.events` hold no grant to `authenticated`. `tests/test_compliance_rls.py` and `tests/test_complycube_grants.py` pin both.

A firm's own window is `GET /crm/contacts/{id}/screening` (`screening:read`, RLS-scoped). It returns one status and one date per kind — never a result, outcome, match, match count, monitoring alert or decision comment — and reads an undecided `refer` as pending. There is no companion write endpoint on the platform surface at all.

The worker runs `_complycube_events_loop`, which applies the ComplyCube webhooks stored in `compliance.events` (the firm API process receives them at `/internal/webhooks/screening`), and `_firm_webhooks_loop`, which sends firms their `check.updated` deliveries (needs `SECRETS_MASTER_KEY`). An `aml_check` flow step queues its screen and the worker runs it by itself; only a screen that cannot start raises a task for Graphene's compliance role, and an operator screens from this console — see [`flows.md`](flows.md).

Settings (`app/config.py`): `COMPLYCUBE_API_KEY` (alias `SCREENING_API_KEY`), `COMPLYCUBE_WEBHOOK_SECRET` (alias `SCREENING_WEBHOOK_SECRET`; verifies `ComplyCube-Signature`, and blank refuses every delivery) and `COMPLYCUBE_BASE_URL`. A blank key means `complycube_configured()` is false: the worker's event loop no-ops, the console's writes answer 503 and the seeded onboarding publishes a manual identity step (its AML step still queues the screen, which goes to operations as a task).

**Document management** (the first domain slice — `docs/document-management.md`). All routes are `bearer (tenant)` (RLS-scoped like `crm`) except the client fulfilment paths, which run on `firm_session` (elevated, ownership-checked). Reads are scoped by RLS to the caller's tenant; writes are gated with `require_permission` against a permission from the registry. The client a document/request binds to is the **`crm.contacts`** id (the true client model, since `0015_documents_crm_contacts`): request/response bodies key on `contact_id` (int), and a logged-in client sees a document/request when its contact is in their resolved `contact_scope`.

| Method               | Path                                                         | Auth                             | Returns / purpose                                                                                                                                                                                   |
| -------------------- | ------------------------------------------------------------ | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET                  | `/documents`                                                 | bearer (tenant)                  | list the firm's documents (RLS-scoped), newest first, excl. superseded/archived; `?contact_id` scopes to one client (a `crm.contacts` id)                                                           |
| POST                 | `/documents/upload`                                          | bearer (tenant)                  | multipart upload: magic-byte sniff + size check, store in the private bucket, record it (`documents:write`)                                                                                         |
| POST                 | `/documents/generate`                                        | bearer (tenant)                  | queue a document from a published template + inputs (optional `client_contact_id`); enqueues a render job (`documents:write`)                                                                       |
| POST                 | `/documents/generate/batch`                                  | bearer (tenant)                  | queue ONE document per client from one published template (`contact_ids`, shared `answers`); partial success — `{documents, rejected}` (`documents:write`)                                          |
| GET                  | `/documents/clients`                                         | bearer (tenant)                  | the tenant's clients (`crm.contacts`) — the recipient/assignee picker, `[{contact_id, name, email}]` (`documents:write`)                                                                            |
| GET                  | `/documents/activity`                                        | bearer (tenant)                  | recent document events across the tenant (overview feed)                                                                                                                                            |
| GET · PATCH          | `/documents/{uid}`                                           | bearer (tenant)                  | one document's metadata · rename/reclassify (`documents:write`)                                                                                                                                     |
| GET                  | `/documents/{uid}/url` · `/content`                          | bearer (tenant)                  | 15-min presigned download URL · proxied bytes (PDFs/images inline)                                                                                                                                  |
| POST                 | `/documents/{uid}/send` · `/archive`                         | bearer (tenant)                  | deliver to clients (`contact_ids: int[]` recipients, `documents:send` — the client-facing release act) · soft-archive (`documents:write`)                                                           |
| GET                  | `/documents/{uid}/activity` · `/requests`                    | bearer (tenant)                  | the doc's product-event trail · requests touching this document                                                                                                                                     |
| GET · POST           | `/documents/requests`                                        | bearer (tenant)                  | list requests (staff see the tenant's, a client sees own via `contact_scope`) · create a multi-item request for a `contact_id` (`documents:send`); `RequestOut` carries `contact_id`/`contact_name` |
| PATCH · POST         | `/documents/requests/{uid}` · `/{uid}/cancel`                | bearer (tenant)                  | update a pending request's due date · cancel it (`documents:write`)                                                                                                                                 |
| POST                 | `/documents/requests/{req}/items/{item}/fulfill`             | **`firm_session`** (client owns) | fulfil a file-backed item — stores the blob, creates the doc, links the client                                                                                                                      |
| POST                 | `/documents/requests/{req}/items/{item}/form-response`       | **`firm_session`** (client owns) | submit a completed form → `documents.form_responses` (jsonb; no document created)                                                                                                                   |
| GET                  | `/documents/requests/{req}/items/{item}/form`                | **`firm_session`** (client owns) | the builder form + fields to fill a `complete_form` item                                                                                                                                            |
| POST                 | `/documents/requests/{req}/items/{item}/reassign`            | bearer (tenant)                  | re-point an agree/acknowledge item at the correct document (`documents:send` — it changes what the client is asked to consent to)                                                                   |
| GET · POST           | `/templates`                                                 | bearer (tenant)                  | list · create a template with an initial draft version (`templates:read` / `templates:write`)                                                                                                       |
| GET · PATCH · DELETE | `/templates/{uid}`                                           | bearer (tenant)                  | metadata · update · archive (soft) (`templates:read` / `templates:write`)                                                                                                                           |
| GET                  | `/templates/{uid}/preview` · `/variables` · `/versions`      | bearer (tenant)                  | on-screen HTML preview · published variables · version history (`templates:read`)                                                                                                                   |
| GET · POST           | `/templates/{uid}/versions[/{vid}]` · `/{vid}/publish`       | bearer (tenant)                  | one version (incl. `schema`) · save the editor content · publish a draft (`templates:write`)                                                                                                        |
| GET · POST           | `/forms`                                                     | bearer (tenant)                  | list forms (excl. archived unless `?include_archived`) · create a draft (`forms:read` / `forms:write`)                                                                                              |
| GET · PATCH · DELETE | `/forms/{uid}`                                               | bearer (tenant)                  | metadata · update · archive (in-use guard) (`forms:read` / `forms:write`)                                                                                                                           |
| POST                 | `/forms/{uid}/publish` · `/unarchive`                        | bearer (tenant)                  | validated publish (≥1 field) · restore as draft (`forms:write`)                                                                                                                                     |
| GET · PUT            | `/forms/{uid}/fields`                                        | bearer (tenant)                  | ordered field list · replace it wholesale (`forms:read` / `forms:write`)                                                                                                                            |
| GET · POST           | `/forms/{uid}/responses`                                     | bearer (tenant)                  | list submissions (`forms:submit` **or** `documents:read` — responses are client data, so `forms:read` deliberately no longer reads them) · record a standalone submission (`forms:submit`)          |
| GET · POST           | `/datasets`                                                  | bearer (tenant)                  | the firm's datasets **plus the ones Graphene provides** (code-side, no row of their own) · configure one (`datasets:read` / `datasets:write`; a firm-wide one also needs `contacts:read_all`)       |
| GET                  | `/datasets/catalogue`                                        | bearer (tenant)                  | what Graphene provides and what each entry lets a firm set — declared columns and typed settings (`datasets:read`)                                                                                  |
| GET · PATCH · DELETE | `/datasets/{uid}`                                            | bearer (tenant)                  | one dataset — a **catalogue key** is accepted here as the uid, since a provided entry has no row · edit metadata · delete (refused while a live template uses its code)                             |
| GET · POST           | `/datasets/{uid}/versions`                                   | bearer (tenant)                  | the authoring history · publish a new version (the table's rows, or a provided entry's settings — stored **verbatim**, so `-12m` stays rolling)                                                     |
| GET                  | `/datasets/{uid}/data`                                       | bearer (tenant)                  | resolve it now and return the rows, under the **caller's own** claims — a dataset is not a way around one's own scope (`datasets:read`)                                                             |
| GET · POST           | `/content/categories`                                        | bearer (tenant)                  | list · create a content category (`content:read` / `content:write`)                                                                                                                                 |
| GET · POST           | `/content/items`                                             | bearer (tenant)                  | list · create a news item, article or PDF guide (`content:read` / `content:write`)                                                                                                                  |
| GET · PATCH          | `/content/items/{uid}`                                       | bearer (tenant)                  | one item (staff see `schema`) · update metadata and draft body (`content:read` / `content:write`)                                                                                                   |
| POST                 | `/content/items/{uid}/publish` · `/archive`                  | bearer (tenant)                  | snapshot + sanitise + go live · archive (`content:write`)                                                                                                                                           |
| GET · POST           | `/content/items/{uid}/file` · `/guide` · `/hero` · `/images` | bearer (tenant)                  | staff PDF stream · upload guide PDF / hero / article image (capped multipart)                                                                                                                       |

`bearer (tenant)` routes go through `tenant_session` (fail closed without an org); `bearer (system)` routes go through `admin_session` — the coarse gate (caller must be a Graphene **operations** principal: `is_system`, or resolved to the operations tenant), runs as `service_role`, audited. **Granular operations RBAC** is then enforced per-endpoint with `require_permission` against the registered operations permissions (`tenants:read`/`:write`, `operations:read`/`:write`, `model:read`/`:write`, `audit:read`; write implies read, `system` bypasses). The endpoints that are shared across targets — members, roles and now **email templates** — choose their gate by the **target tenant** (`_require_tenant_scope`: ops tenant → `operations:*`, model → `model:*`, a firm → `tenants:*`), and the admin portal mirrors these in its nav/page gating. `bearer (firm)` routes go through **`firm_session`**: a firm-scoped, self-service path for the platform portal — it resolves the caller's OWN `tenant_id` from their verified identity (`auth.resolve_claims`, never a client-supplied id), runs as `service_role` (writes to `auth.*` need it) but forces every query to that tenant, and gates each action with `auth.require_permission` against a token permission (`members:read` / `members:write` / `roles:write`, seeded on the model tenant). Only the **client + platform** portals are exposed to firms; operations is Graphene-internal. `/internal/*` is service-to-service (guarded by `INTERNAL_API_TOKEN`, `include_in_schema=False`, network-internal) — the shared portal fetches its per-host WorkOS config there since the API is the sole holder of `SECRETS_MASTER_KEY`. Operation ids are `"{tag}-{route_name}"` (`custom_generate_unique_id` in `main.py`) — keep route `name`s and `tags` stable, since they shape the [client](packages.md).

## Addressing — a URL names a record by its uid

A portal URL never carries a serial id. `crm.contacts.id`, `platform.accounts.id` and
`auth.tenants.id` are the database's own keys — they say how many rows preceded them, and they are
an implementation detail a firm's URLs should not publish. Every record route in every portal is
addressed by the record's `uid`: `/clients/{uid}`, `/accounts/{uid}`, `/tenants/{uid}`,
`/users/{uid}`, `/documents/{uid}` and so on.

**A firm's PEOPLE are at `/firm/members`** — `GET`/`POST /firm/members`,
`PUT /firm/members/{user_id}` (the Edit drawer's save),
`{PUT,DELETE,PATCH} /firm/members/{user_id}/portals/{portal}`,
`POST /firm/members/{user_id}/{deactivate,reactivate,resend-invite}`, plus
`/firm/members/custodians`, `/firm/members/provider-staff/{provider}` and
`/firm/members/{user_id}/provider-ref/{provider}`. `GET /firm/members?kind=staff|profile|unclassified`
is the partition the platform portal's `/team` and `/profiles` pages read.

**A rename to `/users` was attempted and reverted (Sept 2026).** It is recorded because the
argument stands and is worth making again deliberately — a login is not a sub-resource of the firm;
it is the thing a firm's staff, its clients and a custodian's synced employees (0123) are all rows
of, and "member" names the MEMBERSHIP rather than the person. It was reverted because `main` had
independently built the same `?kind=` partition on the path it already had, together with
multi-role memberships and the `/profiles` page, and reconciling the two branches took main's
shape. **There is no `users_router`.** Four platform loaders were left pointing at `/users` after
the revert and all four 404'd silently — an empty picker reads as "your firm has nobody to
assign" — so if this paragraph and the endpoint table above ever disagree again, the table is
right.

**The record paths take the uid too, and the resolve is gone.** `/crm/contacts/{contact_uid}`,
`/crm/accounts/{account_uid}`, their `/firm/` counterparts and `/admin/tenants/{tenant_uid}` name
the record the way a URL does; a dependency in `app/addressing.py` turns the uid into the serial at
the door, so every service below keeps its integer contract unchanged. The `by-uid` resolvers and
the portals' `contactIdFor` / `accountIdFor` / `bookContactIdFor` wrappers are **deleted** — they
existed to buy one integer with a whole extra round trip.

Three rules the resolvers carry, each of which has already been got wrong once:

- **A uid-typed route is declared BEFORE an id-typed sibling.** FastAPI matches in declaration
  order, and `/admin/tenants/{tenant_id}` types its param `int` — a later `by-uid`-shaped
  declaration would answer with a 422 rather than reaching the resolve.
- **Only the RECORD identifier moved.** A nested param (`/firm/contacts/{contact_uid}/users/{user_id}`,
  `…/advisers/{adviser_id}`) is still id-typed, and so is a query FILTER named for what it takes
  (`?contact_id=`, `?account_id=`) and an id in a request BODY. A body is not a URL.
- **They resolve inside the caller's own session.** The lookup is a `SELECT` under the same
  RLS-bound session as the endpoint, so a uid outside the caller's book is a 404 exactly as an
  out-of-scope id is — found-and-refused and never-existed are deliberately indistinguishable.
  The corollary bit: a **serial** sent into one of these paths matches no uid, so it 404s at the
  resolver — and on a record SECTION that reads as "this client has none of that" rather than as a
  defect. Eight call sites shipped that way (the Screening section, the client portal's own Account
  page, every proxy write on the admin book, the archived-client restore, the journey's portal
  invite); the scan below is what now refuses them.

**What the audit stream records is different, and stays different.** `audit.actions` stores the
path param the route was actually called with — a serial, for a contact or an account. That is
history and must not be rewritten. The read-time entity resolver (`_resolve_entities`) therefore
sends `uid` alongside the `label` it was already reading the row for, and a portal links the chip
from that; an entity it cannot resolve renders unlinked rather than pointing at an address built
from the serial.

**Enforcement is a static scan**, because no half of the rule is visible to a type system: a route
directory is a filename, an href is a string template that type-checks perfectly against a row that
has an `id`, and so does a fetch URL — both identifiers are on the row, so `detail.contact.id` reads
as valid against a path that wanted `detail.contact.uid`.
`apps/admin/src/route-addressing.{ts,test.ts}` walks all three portals and fails on three things: a
route segment named for a database key, a link interpolating one, and a **call into a uid-typed API
path whose interpolation is named for one**. The collections for that third rule come from the
generated OpenAPI client (`{uid}` / `{*_uid}` on every path, not a hand-maintained `/crm/contacts`
list) plus the two-step `${base}/${id}` shape the catalogue form actions use. Matching walks the
full path, so a `{kind}` sitting between the collection and the uid cannot hide a serial, and
`encodeURIComponent(id)` unwraps before the name test. A file that already calls `apiUrl()` /
`bookUrl(` is in-scope even when the uid is interpolated into a neighbouring `/contributions/${id}`
template and concatenated (`${apiUrl()}${path}`) — the shape the contributions workbench uses.
`isSerialApiExpression` flags `id`, `*_id` / `*Id`, and `str(data, 'id')`. Nested still-id params
(`{adviser_id}`, `{user_id}`) and query filters (`?contact_id=`) stay integer-typed and are not in
that set. Portal hrefs that share a collection name with the API (`/documents/${id}`) are ignored
unless the file already calls `apiUrl()` / `bookUrl(` — a SvelteKit `+server.ts` that concatenates
`${apiUrl()}${path}` is in-scope; a page that only builds an href is not. The third rule exists
because the API moved: the link rule deliberately never reaches an API path, on a reason written
down when those paths really were id-typed. There is no documented exception:
`/platforms/api/[call_uid]` is uid-addressed
(`0114_api_log_uid`).

## Permissions — one registry, three consumers

`app/permissions.py` is the **single source of truth** for RBAC permissions — declare each one once
(as a `Permission` constant). From there:

- **DB catalogue**: `permissions.sync_permissions()` idempotently upserts the registry into
  `auth.permissions` on app startup (and the test fixture) — there are **no hand-written permission
  seed/delta INSERTs** to keep in step. RLS policies still reference specific slugs by literal
  (e.g. `auth.is_reserved()` → `contacts:read_all`); the row is guaranteed by the sync.
- **Python enforcement**: `auth.require_permission` / `require_any_permission` accept the
  `Permission` constants (or raw slugs); the `/firm` and `/admin` routers gate with them.
- **Frontends**: `moon run api:generate-permissions` emits a typed module to
  `@graphene/api-client/permissions` (`PermissionSlug` union, `PERMISSIONS`, and `can`/`satisfies`)
  — the portals import it so slugs never drift. CI regenerates + fails on drift, like the client.

**Taxonomy**: each resource has `read` + `write`; **`write` implies `read`** at check time (both in
`satisfies` and the TS `can`). Bigger features add extra actions (e.g. `listings:export`). `system`
is the global superuser and bypasses every check. Adding a permission = one line in the registry.

**Tenant workspace permissions.** `tenant_portal:read` opens
`/tenants/{uid}/operate` in read-only mode; `tenant_portal:write` implies it and permits mutations.
They do not imply `tenants:*`, `book:*`, `platforms:*` or `audit:read`. Configure routes remain
under their existing operations permissions. Every API call made by an Operate loader, action or
same-origin proxy carries `tenant=<uid>` from the route through the single target URL helper; the
API accepts that selector only from an Operations-application session. A mutation is refused in
the admin server before the request body is forwarded and is independently refused by the target
session unless the principal holds `tenant_portal:write`.

**The six permission splits (July 2026, migration `0042_permission_splits`).** Six finer-grained
permissions took surfaces off coarser parents, and the re-gated endpoints no longer accept the
parent slug:

- `instructions:read` / `instructions:write` ← `contacts:write` — money movement: the
  instructions list/create/cancel (`/firm/…`), the bank-account + account-model PUTs and
  drawdown (`/crm/…`). The read also fixes the old read-behind-write: LISTING a client's
  instructions used to demand `contacts:write`, so oversight roles could not see the pending
  queue without being able to add to it. The book overlay (`db._BOOK_FIRM_VISIBILITY`) carries
  `instructions:*` explicitly so the ops proxy middle-office keeps working.
- `documents:send` ← `documents:write` — the client-facing release act (send, request create,
  request-item reassign); upload/generate/rename/archive stay `write` (drafting).
- `illustrations:write` ← `documents:write` — both generators (paid external engine, files
  client-facing advice output). Deliberately no `illustrations:read`: the listing is RLS-scoped
  and the PDFs are documents.
- `forms:submit` ← `forms:write` — the response plane. Response LISTING also leaves
  `forms:read` (now `forms:submit` **or** `documents:read`): responses are client data, and a
  form-definition viewer must not thereby read every fact-find answer in the firm.
- `pages:write` ← `branding:write` — portal composition. A **diff** gate on both branding PUTs
  (`branding.pages_changed`): changes to `Branding.pages`, named client design sets, their page
  compositions or role assignments demand it; colour-only edits inside an existing design do not.
  A cosmetic save that echoes composition untouched still needs only `branding:write`.
- `platforms:query` ← `platforms:read` — the lake SQL console (operations portal, so
  `rbac.OPERATIONS_ONLY` keeps it off firm roles by declaration).

`0042` is a pure grant **backfill** (expand-only, idempotent): each new slug is granted to every
existing role holding its parent — plus `platforms:write` holders for the console, which the
write⇒read implication used to admit — so nobody who could act yesterday 403s today; narrowing is
a deliberate role-editor act afterwards. The model preset gains the survey mapping (owner: the
full set incl. `pages:write`; adviser: the working set without `pages:write`; compliance:
`instructions:read` only). Grants propagate to WorkOS on each tenant's next role push.

**`contacts:create` (Sept 2026, migration `0159_contacts_create`).** Minting a client is a
different act from editing one. `POST /firm/contacts` and the listing's New client button gate on
`contacts:create`; PATCH / archive / restore / advisers / notes stay `contacts:write`. `write`
does **not** imply `create` (the implication only ever covers the matching `:read`). `0159` grants
the new slug to every existing `contacts:write` holder, and to the model owner and adviser, so
nobody who could create yesterday 403s today; taking it off a role is a later editor act.
Compliance stays without it.

**`listings:export` (Sept 2026, migration `0163_listings_export`).** Dumping a book listing to
CSV, Excel or PDF is a different act from reading a page of it. The Export button on clients,
accounts, transactions and holdings is hidden without this grant, and the SvelteKit export
routes refuse a missing one. The PDF is drawn in the browser (`pkgs/ui`'s `table-pdf.ts`, via
jsPDF) — there is no API endpoint to gate. Every export — CSV, Excel or PDF — is still recorded:
the portal calls `POST /exports/record` after building the file, and that fire-and-record route
(gated the same way) is what the action-event middleware turns into an `export.listing_exported`
row carrying the format. `contacts:read` / `contacts:write`
do **not** imply it. `0163` grants the new slug to every existing `contacts:read`,
`contacts:write` or `contacts:read_all` holder (and to the model owner, adviser and
compliance), so nobody who could export yesterday loses the button today; taking it off a
role is a later editor act.

**`contacts:sync_morgan_lloyd` (Sept 2026, migration `0198_contacts_sync_morgan_lloyd`).**
Pushing a client and their SIPP to Morgan Lloyd creates records in a live CRM that has no
delete — it is not editing a client record. The account record's **Sync to Morgan Lloyd**
button hides without this grant, and `POST /crm/contacts/{id}/sync-to-morgan-lloyd` /
`POST /crm/accounts/{id}/sync-to-morgan-lloyd` (and the older contact-only
`POST /crm/contacts/{id}/morgan-lloyd`) refuse a missing one. `contacts:write` does **not**
imply it. `0198` grants the new slug to every existing `contacts:write` holder, and to the
model owner and adviser, so nobody who could sync yesterday 403s today; taking it off a role
is a later editor act. Compliance stays without it.

**`documents:show` (Oct 2026, no migration).** The firm-wide Documents page is split off
`documents:read`. The sidebar's Documents link and the `/documents` page need it **on top of**
`documents:read`; a role with `documents:read` alone still reads documents on a client's record
(the Documents tab, the document record, downloads) but has no firm-wide library. Portal-layer
only, like `firm_settings:show`: no API endpoint checks it, because `GET /documents` serves both
the library and the client tab (`?contact_id=`). The `/documents` form actions stay on their own
`write` / `send` gates — the client tab and the document record post to them too. **Off by
default, deliberately:** there is no grant backfill and the model preset does not carry it, so
on deploy no firm role holds it — owners included — until it is ticked in the role editor.
`sync_permissions()` registers the slug at startup; nothing else needed a migration.

**New (AML):** `aml:read` / `aml:write` / `aml:adjudicate` are registered against the **operations**
portal, so `rbac.OPERATIONS_ONLY` keeps all three off every firm-scoped role — the permission
declaration itself is the tenant boundary for AML, not a check somewhere that has to remember to
enforce it. All three are enforced: `aml:read` on every `/aml/*` read, `aml:write` on screening,
monitoring and re-runs, `aml:adjudicate` on the two `…/decision` routes alone — only Graphene
clears a match or decides an identity check. `screening:read` is the firm's own, much
narrower window (platform portal) onto its clients' screening status — deliberately read-only, with
no `screening:write`.

## WorkOS write-through + mirror

WorkOS is the system of record for users / memberships / RBAC; the `auth.*` tables are a mirror
(runtime authz reads JWT claims, never these tables). There is **no global reconcile** — the old
org-per-tenant `sync.py` (it pulled every WorkOS _organisation_ into `auth.tenants`) was removed:
under project-per-tenant, orgs are **portals**, not tenants, so a boot-time pull corrupted the
tenant list. There are **no global role templates**: every role is tenant-scoped. The `0001`
migration seeds only the reserved `contacts:read_all` permission plus a single blank **model
tenant** (`auth.tenants.is_model`). New tenants clone the model's roles on creation (unless
created blank); the model is configured in the admin UI and, having no WorkOS project, is edited
**mirror-only**.

Provisioning **dual-writes** in the admin handler: it resolves the tenant's live environment,
invites/updates the person through that environment's WorkOS client, and mirrors the result into
`auth.*`. **Identity kind is a hard boundary** (`0119`): `staff` may hold only Platform/Operations
access, `profile` may hold only Client access, and an `unclassified` WorkOS import holds none until
a deliberate first grant claims it. Database triggers enforce the boundary in both directions.
`provisioning` exposes `grant_portal_access` (invite-or-grant, idempotent; an unclassified WorkOS
import already has a User so Invitation is refused and the grant falls back to a password-reset
token), `revoke_portal_access`,
`set_member_roles`, and whole-person `deactivate`/`reactivate`. A membership carries one or more
roles through `auth.membership_roles`; the first stays the cosmetic WorkOS role, while
authorisation unions the whole set. Roles are created in WorkOS the same way;
`org-`-prefixed in WorkOS, clean slug in the mirror. When an environment's keys are set,
`environments.push_roles_to_workos` creates the tenant's (cloned) roles in its WorkOS project so
members can be invited with them — members become invitable only once that's done (a clear 409
otherwise). A member's role must be one of the tenant's own roles. A webhook-driven reconciler for
the project-per-tenant model is deferred (project-context §12.4).

## White-label branding

The client-portal composition system (pages, blocks, variants/looks, theme axes, the studio) has
its own definitive reference: [`portal-customisation.md`](portal-customisation.md).

Per-tenant branding is a structured, versioned object in `auth.tenants.theme` (jsonb):
**`Theme { v, client: Branding, platform: Branding }`** — the **client and adviser portals are
branded independently** (a legacy single-`Branding` theme migrates to _both_ portals on read). Each
`Branding` has **separate `colors.light` / `colors.dark` palettes**. On the **client** portal those
are the fourteen-token basic palette (neutrals, primary, brand, the four status bases — one field
per CSS var, 1:1 via `COLOUR_VARS`; every other token aliases them in `theme.css`, see
[`portal-customisation.md`](portal-customisation.md) §4), plus `card_designs` (product type →
card finish or artwork). On the **platform** portal a `surface: 'fixed'` inject emits only
`--primary` / `--brand` (and their foregrounds): logos, icon and two accents, not a re-tint of the paper. Shared `radius`/`font`/
`mode` and a **`glass` material block** still exist on the stored object (expand/contract); the
staff tool does not apply them. Logo/favicon URLs, login copy, support info, and (client only)
**`pages`** — the composable portal: up to 12 `PageDef`s (`id`/`slug`/`label`/`icon`/`nav` + per-audience
`layouts.default|individual|organisation`), each layout a block list (`id`/`type`/`visible`/`w`/`h`/
`settings`, per-type discriminated settings models that clamp rather than 422). The cross-language
contract (block types, variants, settings keys, reserved/shadowable slugs, icons, caps, default
pages) is pinned in `pkgs/ui/src/lib/components/branding/block-contract.json`, asserted by BOTH
`apps/api/tests/test_branding.py` and the pkgs/ui registry tests. A legacy `layout` (home-only)
migrates into `pages` on read and home's default layout is mirrored back into `layout` on dump for
one release of deploy skew; reserved-slug pages are dropped on read, never rejected.
`env_config_by_host` returns the **matched portal's** branding
(client_portal_host → `theme.client`, platform_host → `theme.platform`) and, since Aug 2026, the
matched **`portal`** itself — the SQL always derived it to pick that branding, it just dropped the
answer, so a caller holding only a host had nothing to resolve an audience from (see the
password-reset note under Emails). The admin/firm branding
endpoints take a `?portal=client|platform`. It's **structured tokens only** (no raw CSS); every value that reaches an SSR `<style>`
is validated against a strict allowlist on write (`app/branding.py`) and re-validated/escaped on
inject (`@graphene/auth` `brandingHead`), so a tenant can't inject CSS/HTML. The design system keys
off a handful of CSS custom properties, so overriding them re-themes everything.

- **Served on the hot path**: `environments.env_config_by_host` JOINs `auth.tenants` and returns
  `branding` + `app_name` alongside the WorkOS config, so the portal applies it on every request
  (incl. the logged-out login page) — `hooks.server.ts` injects `%tenant.head%` before
  `%sveltekit.head%` via `transformPageChunk` (no flash) — the light palette is scoped to
  `:root:root:not(.dark)` and dark to `.dark:root:root` (so an unset dark palette keeps the design
  system's own dark theme), and the shells take a `brand` prop. The editor previews both portals
  live via `@graphene/auth` `brandingPreviewStyle`.
- **Editing**: a studio (`pkgs/ui` `BrandingEditor`) whose **live preview is the real app shell**
  (`ShellPreview` mounts the actual `ClientShell`/`SaaSShell` + the shared `ClientHome` grid with demo
  fixtures, bounded in a `transform` frame — a true representation, not a mock). On the **client**
  portal, controls are the seed colours, style and presets (no per-token override), a Card designs
  panel, a Material panel (glass on/off + sliders), identity/style/login/support, and a **Pages view**. On
  the **adviser** portal, Design is logos, icon and two accents — the generated palette, Material
  and card designs are withheld. Client Pages view: a pages rail
  (add/rename/slug/icon/nav/reorder/delete, dispatching through the pure `page-ops.ts`, which
  mirrors the API's caps and refusals), per-page audience tabs (Default | Individuals | Trusts &
  companies — copy-on-write overrides via `page-audience.ts`, with a Customised badge and revert),
  the visual grid `LayoutBuilder` (drag to move, corner-drag or ± to resize, toggle, every type
  re-addable with per-instance ids), a `BlockInspector` rendered from the registry's
  `SettingDescriptor[]` (the variant control captions its live auto resolution), and a persona
  selector (Individual·2 / Family·6 / Trust·300, deterministic fixtures in
  `pkgs/ui/src/lib/fixtures/personas.ts`) driving one page-aware `ShellPreview`. Saving an
  untouched studio keeps `pages` null, so an un-customised tenant keeps tracking platform
  defaults. Composed
  pages are rendered by the one shared `@graphene/ui` `PortalPage` grid (`ClientHome` is a thin
  wrapper over it) used by both the app and the preview; demo gating is a single decision
  (`resolveSlices`): `data: null` ⇒ the studio preview's fixtures wholesale, non-null ⇒ a real
  client's slices with empty states — never fixtures (a leak-regression suite pins this per slice).
  Client-side, the portal derives its nav from the page set (`derivePortalNav`, ≤6 tabs), routes
  custom slugs through a `[slug=portalPage]` param matcher (reserved slugs 404 at the router; a
  route-consistency test keeps the reserved list honest), lets a firm page shadow the default
  listing routes, and composes each page in `apps/client/src/lib/page-data.ts` — audience from
  **`GET /crm/context`** (`contact_id`/`type`/`service_type`/`account_count`/
  `valued_account_count`/`adviser`, the caller's own party via `contact_users` + `contact_scope()`),
  then the union of the visible blocks' data sources fetched best-effort and per-block variants
  resolved via the pure `resolveVariant`. **Scale (Phase 4):** `GET /crm/home?wrappers=full|auto|summary`
  — full/absent is byte-identical to the classic payload; auto summarises above 25 valued accounts
  (the UI's `accountsCardsMax + 1` boundary); summary returns the top-10 wrappers WITHOUT `trend`
  plus `wrapper_groups`/`wrappers_total`/`wrappers_summarised`, so a 300-account trust's home stays
  small (a byte-size tripwire test guards against sparklines creeping back in). `GET /crm/accounts`
  and `GET /crm/holdings` take optional `limit` (1..200) + `offset` and then return `{items, total}`
  (param absent ⇒ the old bare list, so the platform portal and legacy pages are untouched). The
  loader picks the wrappers mode pre-fetch from `/crm/context`'s account count (an explicit
  stack/cards/table choice is never summarised out from under the firm), fetches holdings/activity
  in full for table variants (paginated via `?page=`, rendered as SSR links by the repo
  `Pagination`), and sizes variant resolution off the returned totals. The legacy listing-page
  branches in apps/client survive pending a visual parity QA; deleting them is the last Phase 4
  step.
  Client branding can additionally carry named `templates[]`. Each template owns its page set,
  light/dark colours, fonts, density, finish and material; firm identity assets and support/login
  copy stay shared. `group_slugs[]` assigns a design to [client groups](client-groups.md) and is
  tried first; `classification_slugs[]` is a many-book assignment with
  one-template-per-classification canonicalisation ([`client-classifications.md`](client-classifications.md));
  leftover `role_slugs[]` stay as a fallback. `default_template_id` handles unmatched clients.
  The client hook selects the effective template from `GET /crm/groups/mine` then
  `GET /crm/classifications/mine` (then the WorkOS role) before generating the SSR head and
  before page loaders read `locals.branding`.
  Book catalogue CRUD is `GET/POST /crm/classifications`, `PATCH/DELETE /crm/classifications/{uid}`
  (`branding:read`/`contacts:read` to list; `branding:write` to mutate). Pin is
  `PUT /crm/contacts/{uid}/classification` (`contacts:write`). Group catalogue CRUD is the same
  shape on `/crm/groups`; membership is `PUT /crm/contacts/{uid}/groups`.
  Graphene admin per tenant — `GET/PUT /admin/tenants/{id}/branding?portal=…` (gated
  `tenants:read`/`tenants:write`); firm self-service — `GET/PUT /firm/branding?portal=…` (gated new
  `branding:read`/`branding:write`). The client GET also returns the firm's client-role options for
  the Studio assignment picker. On BOTH PUTs a change to pages or design-set assignment (portal
  composition) additionally requires **`pages:write`** — a validator-level diff
  (`branding.pages_changed` on the canonical parsed models), so a cosmetic save echoing the
  stored pages untouched never trips it. Logo/favicon uploads go to S3-compatible object storage
  (`app/storage.py`, `POST …/branding/asset?kind=logo|logo-dark|favicon`) which returns a public
  URL. Uploads are **tenant-bucket-first** ([`storage.md`](storage.md) §8): the tenant's own
  `t-{uid}` bucket under its public `files/tenant/` prefix once provisioned, else the legacy
  shared `S3_BUCKET`, else an inline `data:` URI when no storage is configured.
- **Object storage (bucket-per-tenant, [`storage.md`](storage.md)).** Tenant creation mints the
  tenant's bucket + a registry row in `storage.tenants` (best-effort — never blocks
  creation); `GET /admin/tenants/{id}/storage` shows it, `POST /admin/tenants/{id}/storage/provision`
  retries/backfills a single tenant (returning a per-step summary) and `POST /admin/storage/provision-all`
  sweeps every tenant without an active row (all gated `tenants:read`/`tenants:write`). The tenant
  service-user secret is Fernet-encrypted in the service_role-only `storage.tenant_secrets`.
- **Provider enablement ([`storage.md`](storage.md) §9, [`provider-pipeline.md`](provider-pipeline.md)
  §6a).** `GET/PUT /admin/tenants/{id}/providers(/{provider})` enables a platform for a tenant with
  its slice filter (validated per provider in `app/providers.py`) and immediately asks that
  provider's interlace daemon to run the firm's chain — filter, stage, merge. Re-run it with
  `POST /admin/tenants/{id}/providers/{provider}/refresh` for one firm or
  `POST /admin/platforms/{provider}/refresh` for all of them (gated `platforms:write`). Registry:
  `platform.tenant_platforms` (0004). Both answers carry `restart_required`: a firm enabled since
  the daemon last started has no models until it reloads, and an unmatched tag selects the empty set
  while still answering 200, so "ran fine" and "did nothing" are otherwise the same response.
  (`app/fanout.py`, `app/fanout_listener.py` and `POST /admin/{fanout,materialise}/run` are gone —
  the projection and the book write are one DAG step now.)
- **Platform cockpit (`/admin/platforms/*`, gated `platforms:read`/`platforms:write`).**
  Drives the admin /platforms pages (`app/platforms.py`): `GET /admin/platforms` (per-provider
  daemon health + enabled-firm count), `GET /admin/platforms/{provider}/overview` (the
  provider's interlace daemon — model list, promotion plan, runs, event log
  (`INTERLACE_MORGAN_LLOYD_URL`) — plus headline warehouse figures and firm enablements;
  partial when the daemon/lake is down). Per-model SQL, column lineage and row counts are
  `GET /admin/platforms/{provider}/catalogue`, loaded after the overview is on screen so
  opening a platform does not wait on a request and a scan per model,
  `POST /admin/platforms/{provider}/query`
  (read-only SQL console: lake_reader READ_ONLY attach on the core catalogue, single
  SELECT/WITH only — gated **`platforms:query`** since the 0042 split: arbitrary SQL over every
  firm's raw book is a different trust from watching dashboards) and
  `POST /admin/platforms/{provider}/sync` (enqueue a pipeline run on
  the daemon). `PUT /admin/providers/{provider}/sources` (register/rename a named upstream
  source) is `platforms:write`; the GET stays `tenants:read` for the tenant-page dropdown.
- **Per-user storage credentials (`/storage/*`, [`storage.md`](storage.md) §4).** A standalone,
  tenant-target-aware router (`storage_session`, mirroring `firm_session` with an optional
  `?tenant=<uid>` — foreign tenants require operations scope): `GET /storage` (connection info +
  own credential), `POST /storage/credentials` (issue/rotate own, `storage:credentials_own`),
  `POST /storage/credentials/{user_id}` (issue for a member, scope from THEIR role grants,
  `storage:credentials`), `GET /storage/credentials` (list), `DELETE /storage/credentials/{id}`
  (revoke). The secret appears once in the issue response; registry rows live in
  `storage.user_credentials` (+ service_role-only `storage.user_credential_secrets`).
- Only the **client + platform** portals are branded; admin/operations stays Graphene-branded.
- Forward-compatible: the `theme` object is versioned; new keys (the full palette, `glass`, the sized
  `layout`) were added without a migration — legacy/partial themes keep validating (unset ⇒ default).

## Email — shipped defaults, per-firm overrides, and the editor

Every transactional email resolves at send time to one of two renderers: the firm's **published**
template for that `code`, else the **shipped default**. The fallback is the point — a firm that has
never opened the editor keeps getting the default, so wiring a send site to the store can never
regress delivery.

A template has **two** authoring doors onto one store: the firm itself, from the platform portal
(`/templates/emails` — the Emails tab of its one Templates surface, `email_templates:*`), and
Graphene operations, from the admin portal
(`/tenants/{uid}/email-templates`, the operations permissions). They share one service, one editor and
one publish gate; what distinguishes them is recorded on the version (`authored_by_scope`), not
inferred.

- **Send + brand (`app/email/transport.py`).** One sender — `send_email` via Resend — best-effort by contract:
  it returns `False` and logs rather than raising, so an email outage never fails provisioning or a
  document send. A blank `RESEND_API_KEY` means every send logs a warning and returns `False` — an
  email-less deployment is silently email-less; `EMAIL_FROM` is the DNS-verified envelope address
  (both in `.env.example`, neither in the settings table below).
  `brand_for(session, tenant_id, portal=…)` resolves the firm's email identity from
  `auth.tenants.theme`: name, `logo_url` (**https only** — mail clients block `data:` images, so an
  inlined logo degrades to the text wordmark), a **hex** `accent` (email clients don't understand the
  design system's `oklch()` tokens) and the support address — per audience, so client-facing mail
  wears the `client` branding and staff mail the `platform` one, cached 300s. `resolve_sender` turns
  that into the envelope. A firm with a verified row in `email.sender_domains` and an address chosen
  on that domain sends from it (`Ridley Wealth <notifications@mail.ridleywealth.co.uk>`), and the
  firm's support address stays `Reply-To`. Every other firm keeps the platform sender: a support
  address becomes the `From` only when it sits on the same DNS-verified domain as `EMAIL_FROM`
  (Resend refuses an unverified domain, so an arbitrary firm address would silently kill delivery);
  otherwise we keep the verified address, wear the firm's display name, and put theirs in `Reply-To`.
  Graphene operations registers the domain from the tenant's Settings → Sending page
  (`/admin/tenants/{uid}/sender-domain`); the firm publishes the DNS records Resend returns, and
  sending stays on `EMAIL_FROM` until Resend reports the sending records verified. The same page
  can mail the acting operator a test of that sender (`POST …/sender-domain/test`); it takes no
  recipient. An adviser
  address on the active sending domain can still be the literal `From`. The cache is cleared when
  the domain's state changes, including from `domain.updated` on `POST /webhooks/resend`.
  **Every send site must resolve that `portal`, and one did not.** `notifications/email_consumer.py`
  takes it from the rule's own audience and `provisioning.py` from the portal being granted, but
  `routers/internal.py`'s **password reset** called `brand_for(session, tenant_id)` with no `portal`
  at all — so it took the `client` default for every caller, and a member of a firm's STAFF resetting
  their password from the platform portal received the client-portal branding _and_ the client-portal
  sender identity, in the one seam where "which of the firm's two faces is this" is the whole
  question. It resolves the portal from `body.host` now, via `env_config_by_host`'s newly-returned
  `portal` (a tenant environment registers its client-portal and platform hosts separately, so the
  host names the audience), and passes it to `brand_for` **and** `resolve_sender` — the support
  address the envelope is built from is per-portal too.
  **This does not touch the no-account-existence-oracle contract** (`tests/test_forgot_password_brand.py`
  pins both halves): the portal is decided from the caller's own host, before and independently of
  anything WorkOS says, and it changes only the **email**, which only the account owner receives. The
  **response** stays a fixed `{ok: true}` on every branch — existing address, unknown address, staff
  host, client host, suspended firm, throttled — so nothing an unauthenticated caller can observe
  varies with whether the address exists or which side of the firm it belongs to; `first_name` is
  still hard-coded `None` for the same reason. Branding the email differently is safe; branding the
  response, or varying its shape, would not be. An unregistered host is not a tenant host (ops/admin
  or local dev) and still takes the Graphene-branded `render_default` branch, unchanged.
- **The non-production send guard — ON by default (`EMAIL_NONPROD_GUARD`).** A key alone cannot
  express "never email a real person from staging", and a staging deploy inherits a working
  `RESEND_API_KEY` from the deploy config far more readily than it inherits a curated recipient list —
  at which point every invite, task notice and client document notice a test run produces goes to the
  real address on the record. So when `APP_STAGE` is exactly `dev` or `staging` a recipient is mailed
  only if it matches `EMAIL_ALLOWLIST` (full address or `@domain`; comma-separated or a JSON array;
  case- and whitespace-insensitive; **exact** domain match, because `@example.com` admitting
  `@clients.example.com` is how the generosity arrives). **An unset list is `@grapheneplatforms.com`**,
  so those two stages mail Graphene addresses and nobody else without a Coolify entry. **The admin
  tenant page can replace that list** for the whole environment (one row in
  `auth.outbound_email_allowlist`); reset puts this default back. **An explicit blank, or a saved
  empty list, suppresses everything there** — a cleared value means "mail nobody", never "mail
  everybody".
  `EMAIL_NONPROD_GUARD=false` is the break-glass: suppression stops and a send is best-effort, gated
  only by `RESEND_API_KEY`. Every other stage, including production, a blank, and a misspelling,
  consults none of it — `stage_is_guarded()` matches those two names only. The admin tenant page
  reads the portal's own `APP_STAGE` and shows the editor only on dev and staging.
  Suppression is an **outcome, not an error**: `send_email` returns `False` (the same answer an
  unconfigured Resend gives, so no caller changed) and logs at WARNING with the recipient's **domain
  only** — never the address, never the token. `sending_disabled()` answers "is every send a no-op,
  and why", which is what `/health/ready` reports (`email: disabled` names the reason — "ok" on a box
  that mails nobody is the harder failure to diagnose) and what the worker's notifier loop skips on
  (`notified_at` is stamped only on a delivered send, so scanning under blanket suppression would
  re-read the same unstamped rows every minute for ever). `graphene_email_suppressed` on `/metrics`
  separates "configured" from "suppressed".
  **Callers must not treat that `False` as a fault**, and two now don't. The flow tier's `send_email`
  action turned any `False` into an `ActionRefused`, which latches the step `failed` and parks the
  case at `blocked` — so on a non-production deploy whose allowlist was empty, every journey
  carrying an email action parked, blaming "the mail provider rejected
  it or is not configured". It now asks `suppression_reason`/`sending_disabled` and separates the
  two: a suppressed send completes the step with a detail naming the setting and `changed=False` (no
  action event for an email nobody received — the stream is a trigger source); only a real provider
  rejection refuses. The outbox's `notify.*` rows gained a third state, **held**
  (`dispatcher._drain`): under blanket suppression the row moves its `next_retry` with `attempts`
  untouched, because counting suppression as a delivery failure walked it to a DEAD LETTER — a
  reported, tracked "will NEVER be delivered" incident manufactured by a dev box. That check is on
  the ROW and not on the worker's outbox loop, because that loop also drains `task.requested`,
  `identity.check.requested` and `webhook_out`, none of which sends mail.
- **The value CHECKED is the value SENT (`email._single_address`).** The guard validated a
  _normalised_ address (`_address` then `rpartition('@')`) while the payload posted the **raw
  original string** — so the two could differ, and did: `rpartition` examines only what follows the
  LAST `@`, and `_address` takes the first `<…>` pair and discards the remainder. A `to` of
  `victim@evil.com, ok@grapheneplatforms.com` passed a staging allowlist of `@grapheneplatforms.com`
  and was then posted whole, to both. Reachable, not theoretical: `to` comes from
  `crm.contacts.email`, which has no shape validation at all. `_single_address` refuses anything that
  is not exactly ONE mailbox — a comma, semicolon, newline or a second `@` — and `send_email` posts
  what it returns. The refusal is **stage-independent** (production reads no allowlist, but a `to`
  naming two people is malformed data everywhere) and logs **no part** of the string, not even a
  "domain", because we could not decide what the string meant.
  **It is a chokepoint, not a convention:** `tests/test_email_send_guard.py` scans `app/` and fails
  if any module other than `app/email/transport.py` reads `settings.resend_api_key`, imports a Resend SDK or
  names `api.resend.com`, and asserts on `send_email`'s own AST that it calls `suppression_reason`
  before it POSTs. WorkOS's own emails stay **disabled in the dashboard** (docs/auth.md) — that is
  the one mail path outside this guard, and it is switched off at the vendor, not in code.
- **`sender_for` is a cache read, and a cold read is silently wrong — so no send path may depend on
  it.** It answers without a DB round trip by looking in the same 300s-TTL dict `resolve_sender`
  fills, and a miss returns the Graphene `EMAIL_FROM`; it also cannot yield a `Reply-To` at all. That
  made the white-label sender **cosmetic for months**: the invite paths (`provisioning.py`) warmed
  the _brand_ cache and then read the _sender_ one, which nothing had filled, and the worker-side
  paths (the outbox dispatcher, the notification email subscriber) run in a **separate process** whose
  `email._sender_cache` is a different, permanently empty dict — so the miss there was total, not
  occasional. Every send site now either calls `resolve_sender` (it has a session:
  `provisioning.py`'s two invite paths, `routers/internal.py`'s reset,
  `notifications/email_consumer.py`, `flows/effects.py`, and the send-test endpoint in
  `routers/email_templates.py`) or reads the identity the producer resolved and wrote into the
  payload (`flows/notifications.py::_send`, which has neither a session nor a warm cache).
  `sender_for` survives only as the no-tenant default and the fallback for
  outbox rows queued before the payload carried `sender`/`reply_to`; those must still dispatch, so an
  absent value degrades rather than raising. `tests/test_email_sender_identity.py` pins all of this
  with the caches held explicitly cold. **Do not reintroduce `sender_for(tenant_id)` on a send
  path** — its failure mode is a firm-branded email arriving from someone else's address, which
  reads as phishing and is invisible in every log.
- **The shipped default is authored ONCE, as a template (Aug 2026).** `app/email/defaults.py` holds
  one token authoring per code — `{{ brand.name }}`, `{% if expires_at %}`, `{{ message | paragraphs }}`
  — and `email_render.render_default(code, context)` merges it through the **same** Jinja pass, with
  the same filters and the same autoescape rules, that a firm's own published template goes through.
  So the editor's "Load default" canvas and the email an un-overridden send delivers are one string
  rendered twice, not two texts that agree by vigilance. The **chrome** is `app/email/shell.py`,
  composed from parts that are already HTML-safe: `shell_html` (the wrapper — card, accent
  edge, masthead, heading, body, optional CTA, optional footer), `logo_wordmark`/`text_wordmark`/
  `masthead`, `support_link`/`footer_row`/`footer_for`, and `cta_block` (the accent button AND its
  paste-the-link fallback paragraph, as one unit). It sits under `app/render/` rather than in
  `app/email/transport.py` because it holds no credential, opens no connection and knows nothing about
  delivery — pure text in, text out, importing `app.email` only for the `Brand` shape; the envelope
  identity, the recipient guard and the transport stay in `app/email/transport.py`.
  **The button is a one-cell presentation table, not a padded anchor**
  (Aug 2026): Outlook's Word engine implements neither `display:inline-block` nor
  margins on an `<a>`, so the padding collapsed to the label's text box and Word then asserted its own
  link ink over the anchor's — observed in a real inbox, with `to_email_html` returning the anchor
  intact, so purely a markup-shape defect. The `<td>` carries the padding, `background-color` (never
  the `background` shorthand, which the editor's Style rail cannot edit) and radius; the `<a>` carries
  typography and `color:#ffffff` only, and the block's spacing sits on the wrapping table. **The INK is
  a second, independent defect and took three real renders to close.** With the pill full-size the
  label was still dark on dark, and then black rather than blue — black is the _inherited_ colour, i.e.
  no declaration at all, and `#0f172a` is both `shell_html`'s wrapper ink and the default accent. Two
  mechanisms, in order: Word applies its own Hyperlink character style over a plain inline `color`, and
  it **invalidates an inline declaration carrying `!important`** (hteumeuleu/email-bugs#31) — so
  flagging the anchor deleted the ink instead of defending it. The flag is therefore **off every
  element**, and the ink is three layers: an inner `<span>` on the label carrying `color:#ffffff`
  (the layer Word's Hyperlink style does not reach — the one doing the work), the anchor's own unflagged
  `color:#ffffff` behind it, and `app/email/document.py::MSO_CTA_INK` —
  `<!--[if mso]><style>a.cta{color:#ffffff !important}` in the head of **every** email, which is why the
  anchor carries `class="cta"` and the only place a flag works, Word honouring `!important` in a
  stylesheet. It deliberately does not target the span (`color` is inherited, and an inherited value
  never competes in the cascade, so a firm's own label colour still wins). Still no VML: it would need
  `xmlns:v`/`xmlns:o` plus `<o:OfficeDocumentSettings>`, which a conditional `<style>` does not.
  Identical shape in the editor-side copy,
  `pkgs/ui/src/lib/components/email-editor/create.ts` → THE BULLETPROOF BUTTON (whose Style rail also
  mirrors a `color` edit between the two levels, `syncButtonLabelInk`, so neither control is dead and
  the two inks cannot drift); pinned per code, and across the language boundary, by
  `tests/test_email_send_parity.py`. That "already-safe parts" rule is what lets one
  authoring serve two callers with opposite escaping needs: `email_defaults` feeds it Jinja SOURCE
  (nothing escapes a template — the merge escapes the VALUE later), while `email_shell.shell` renders
  it NOW around an already-merged body for the flow tier's email action, escaping the heading and CTA
  there.
  The six per-code builders (`invite_email`, `password_reset_email`, `document_sent_email`,
  `document_share_email`, `request_raised_email`, `task_notice_email`) are **gone**; so is
  `verify_email`, which had no caller at all (verification folded into set-password and `/verify` was
  removed — a builder for an email nothing sends, with no catalogue code to make it overridable).
  One notice is still deliberately **absent** (request-due): the scan that would send
  them doesn't exist, and `app/email/shell.py`'s closing comment carries the handoff (per-row
  idempotency stamps first, copy written with the producer, as an `email_defaults` default with a
  catalogue code).
  Task notices are **two codes**, `task_assigned` and `task_reminder`, and **the notifier sends
  neither** — `app/tasks/notifier.py` is a scan that emits one `audit.actions` row per task
  (`task.assigned` from the first pass, `task.due_soon` from the reminder pass) and nothing else. The
  code is chosen by the notification catalogue, one `email_code` per trigger's audience, and rendered
  by the rule-driven subscriber; so the brand is the RULE's, resolved from its audience, and both task
  triggers ship the `assignee` audience, which maps to the **platform** portal for every kind of
  assignee — a contact included. A staff-branded notice to a client assignee is therefore the shipped
  behaviour, not a defect: the audience is what carries the portal, and the catalogue defines no
  client-keyed task audience to carry the other one. They were ONE
  (`task_notice`), whose heading and lead sentence each chose between "New task"/"Reminder" wordings on
  an `is_reminder` merge value — first as raw `{% if %}`, then as the editor's conditional-text node.
  Both were the wrong shape: a shipped default may SUPPORT conditions and must not be built out of
  them, because the canvas an author opens has to be the email rather than the email plus the other
  email plus the rule that picks. So the scan's own pass picks the EVENT, the catalogue maps that
  event's trigger to a code, and each template states its own words; nothing derives `notice_kind` any
  more and neither template branches on the context at all. `task_notice` is retired by absence from
  `email_catalog.KNOWN_CODES` — a firm that published one keeps the row, listed and editable, and no
  send site resolves it.
  Two consequences of there being one authoring. The **masthead is chosen at merge time**
  (`{% if brand.logo_url %}`) rather than baked: the prefill used to hard-code the text wordmark — to
  avoid an `<img src="">` for a logo-less firm — so a firm that loaded the default and published it
  lost its logo for good, while the send path had been showing it. And the nested `brand` merge value
  has one definition, `email.brand_tokens` — a five-key dict (`name`, `logo_url`, `accent`,
  `support_email`, `icon_url`) — used by every send site that puts a brand in a merge context
  (`provisioning.py`'s two invite paths, both branches of `routers/internal.py`'s reset, and
  `notifications/email_consumer.py`) and by `email_preview.sample_context`, so preview and send agree
  by construction. There were seven sites spelling it out by hand, which is a per-site chance to omit
  a key — and a key that renders blank in an email is invisible until a client asks who wrote to them.
- **The chrome is a card, and the palette is the editor's (Aug 2026).** The shell was a bare
  `<div style="max-width:480px;margin:0 auto">`: Word implements neither half of that centring, so the
  email a client actually received was full-bleed in the client most advisers use while every preview
  of it looked right. It is now a centring table, an `align="center"` cell and a 600px card (both the
  legacy `width`/`align` ATTRIBUTES Word reads and the CSS everyone else does), with an accent edge,
  and it closes with a **footer** — the firm's name plus a `mailto:` support address when it has one,
  both guarded, the row omitted entirely when there is neither, so nothing can leave an empty bordered
  strip. One accent beyond the button, one hairline, `docs/design-language.md`'s discipline transposed
  to a medium with no tokens. The type face is **Arial first**, which is an Outlook fix rather than
  taste: Word resolves the FIRST family in a stack or falls back to Times New Roman — it never reads
  the rest — so `system-ui,-apple-system,…` had been setting every shipped email in a serif there. The
  palette (`email_shell.FONT_STACK`/`INK`/`BODY_INK`/`MUTED_INK`/`FAINT_INK`/`RULE`, plus `CARD_BG` and
  the 600px `CARD_WIDTH`) is now literally the set `create.ts` holds, which that file's header had
  claimed all along — **neither side carries a page background**, so a palette block dropped into a
  default cannot land Arial 15/24 beside system-ui 14/22. There is no `PAGE_BG`: the band drew a grey
  stripe between the editor's white sheet and the card, and the white fill, 10px radius and accent bar
  are what read as a card anyway. The centring table and its cell stay — Outlook ignores
  `margin:0 auto` — they simply paint nothing.
  **The words did not change**: the golden (`tests/test_email_shipped_copy_golden.py`) moved only to
  take the footer line, and every hierarchy change is markup — a `<p>` where a `<br><br>` was, the
  reset email's reassurance and the share's expiry set as quieter asides, a due date on its own line.
- **A subject is plain text, not HTML (`jinja_engine.render_text`).** All three paths merge the
  subject with autoescape **off**, because it is a header value: a firm called "Ridley & Co" sending
  "Bonds & Gilts" must not arrive as "Ridley &amp; Co: Bonds &amp; Gilts". Two of the three used to
  get this wrong (the published override and the preview merged it through the body's pass; the old
  Python builder escaped nothing, so the paths disagreed) and no sampled value contained a character
  worth escaping, so nothing caught it. Bodies keep autoescape, obviously — that is what stops a
  client's own name injecting markup.
- **`paragraphs` is a filter, not a builder (`app/render/jinja_engine.py`).** An adviser's covering
  note is free text they TYPED: blank lines become `<p>` blocks and soft newlines `<br>`, with the
  content escaped inside `Markup` so the tags survive and nothing else does. It was Python-side
  (`email._paragraphs`) while the prefill emitted the bare `{{ message }}`, so a firm that published
  the default mailed a carefully-broken note as one run-on paragraph — the seventh divergence, latent
  because every sample note in the suite was a single line and a visible-text comparison collapses
  whitespace anyway. As a filter it serves both readings and a firm's own template can use it. It also
  answers `""` for whitespace-only input, which is why the note block guards on
  `{% if message | paragraphs %}`: `{% if message %}` calls a box of spaces a note and ships an empty
  bordered box.
- **A `code` is only real if a send site resolves it.** The column is free text, so
  `app/email/catalog.py::KNOWN_CODES` is the curated set the authoring UI offers as "email types" —
  and the list page marks a template whose code matches none of them "not linked", because such a
  template is inert. `tests/test_email/catalog.py` fails if a code in the catalogue is never resolved
  or a resolved code is missing from it, so that test — not this doc — is the authority on the set.
  Anything that does not go through `render_or_default` is not firm-overridable at all: today that is
  the generic `notify.*` outbox events, which build their HTML inline at enqueue (the dispatcher's
  `_send` just posts `payload['html']` under the `sender`/`reply_to` the producer resolved into the
  same payload) — and which still have no producer.
- **The store (`0042_email_templates`).** A dedicated `email` schema mirroring 0012's document
  templates: `email.templates` (`UNIQUE(tenant_id, code)`, `category` transactional | notification,
  `current_published_version_id`) + `email.template_versions` (`version_number`,
  draft/published/archived, `subject`, `preheader`, and the editor content as an opaque `schema`
  jsonb). RLS is **staff-only** — `tenant_id = ANY (auth.tenant_lineage())` with no client branch,
  because templates are firm assets and a client principal's lineage is empty.
- **One service, two doors (`app/email/templates_service.py`).** The authoring SQL — the version
  roll, the publish gates, restore, the seed, the clone — lives in exactly one HTTP-free module, and
  both the firm router (`tenant_session`, RLS `authenticated`) and the admin door (`admin_session`,
  `service_role`) call it. Every function takes an **explicit** `tenant_id` + `actor_id` rather than
  the claim helpers (`auth.tenant_id()`/`auth.uid()`), because `service_role` has no claim-derived
  identity: the firm router passes its RLS-derived values, the admin door the path tenant and the
  operator's uuid. Every statement is tenant-scoped by that id, so a `service_role` caller cannot
  cross tenants by accident and the RLS caller's `WITH CHECK` still enforces isolation on top.
- **The firm router** (`app/routers/email_templates.py`, `tenant_session`, gated
  `email_templates:read`/`:write`) mirrors `templates.py`: list/create/get/patch, archive +
  unarchive, `GET /catalog`, `GET /{uid}/default`, `POST /{uid}/preview`,
  `POST /{uid}/send-test` (below), version list/get,
  `POST /{uid}/versions` (the editor's save — updates the head draft **in place** while it is fresh,
  rolls `v(n+1)` once the head is published or its 30-minute bucket elapses, so a save doesn't spawn
  a version per keystroke but yesterday's draft stays a restore point), `…/publish` (archives the
  prior published version, one active at a time) and `…/restore` (copies an old version into a NEW
  draft — never overwrites, never auto-publishes, so a mis-click is itself reversible).
- **The admin door — the same endpoints per tenant** (`/admin/tenants/{id}/email-templates…`,
  `admin_session`, one-for-one with the firm router — including `…/{uid}/send-test`, which mails the
  ACTING OPERATOR and is the only way to inbox-test the model preset — plus `…/load-defaults` and
  `…/provision`).
  Its permission is chosen by the **target** tenant, exactly as the shared member/role endpoints
  choose theirs (`admin.py::_require_tenant_scope`): `model:read`/`model:write` for the model tenant,
  `tenants:read`/`tenants:write` for a firm. Two jobs, one surface: curating the **model preset**
  (which has no logins, so the firm router cannot reach it at all) and setting a firm's own emails up
  **on its behalf** after creation — the firm keeps its own `/email-templates` surface throughout,
  and a code neither side touches still falls back to the shipped default.
- **The admin surface** is `apps/admin/src/routes/tenants/[uid]/email-templates/` (list ·
  `[templateId]/edit` · `[templateId]/preview` and `…/versions` token-safe proxies), sitting in the
  tenant section beside Branding and Terminology rather than in a page of its own; `/model` deep-links
  to it and the old `/model/email-templates` **308s** there (to `/model` if the model tenant cannot be
  resolved, which is a `/model` problem rather than a redirect one). When the target is a firm rather
  than the preset, and the operator may write, the list carries an **"Acting on behalf of {firm}"**
  banner — the same posture the book's proxy surfaces take: the changes go to that firm's clients under
  that firm's brand, and the firm can see who made them. The app mirrors the API's target-based gate in `$lib/email-templates/scope.ts`
  (`model:*` vs `tenants:*`), in one place because four surfaces need the same answer and a fifth copy
  is how they drift; a failed model lookup degrades to the firm scope, which is the stricter of the two
  and never widens access since the API re-checks regardless. **Mind the id/uid split:** the app's
  tenant routes are **uid**-addressed while the API's own routes take the numeric **id**, and nothing
  in the type system can tell — `href="/tenants/{t.id}"` type-checks perfectly and 404s. That is what
  `apps/admin/src/routes/tenants/tenant-links.test.ts` scans the source for, and it caught this
  section's redirect doing exactly that.
- **Seeding is operator-attributed, never automatic.** `load_defaults` writes the built-in default
  for every catalogue code as a published `v1` (idempotent — it skips codes the tenant already has),
  and `clone_published` copies one tenant's published templates into another. There is no startup
  auto-seed because `created_by` is a NOT-NULL FK to `auth.users` and there is no system user: an
  operator presses **Load defaults** on the model, and `provisioning.clone_model_email_templates`
  then hands that preset to each new firm at creation (best-effort savepoint) or, for an existing
  firm, on demand via `POST …/email-templates/provision`.
- **Publish refuses a blank template, and the rule is substance rather than bytes**
  (`app/email/content.py::has_renderable_content`, called from the service's `_has_content` — it
  moved out of the router when the admin door landed, so the two doors cannot enforce different
  rules). Publish needs a subject **and** a body that would show a recipient something: a
  non-whitespace text node outside `<script>`/`<style>`/comments (a Jinja token counts — it is text
  the merge fills), an element that renders on its own (`img`/`svg`/`video`/…), or a background
  **image**. An empty wrapper at any depth, a nest of empty table cells, `&nbsp;` and zero-width
  padding, a comment, a lone rule or spacer, and a background **colour** with nothing inside it are
  all blank. Judged on `schema['html']` alone, because that is the only thing the send path renders.
  The gate matters more here than in the document pipeline: publishing an empty version doesn't
  merely ship an empty email, it switches the fail-safe fallback OFF for every future send of that
  code.
  **The subject half of that gate is on `send-test` too, and was not** — a test send of a
  subjectless canvas went out as the literal `"[TEST] "` and nothing else. Easy to reach: the create
  form offers the subject as a _placeholder_, not a value, so a template arrives in the editor with
  no subject at all. It is a **422** now, in the publish gates' voice, refused before the DB read
  and before the throttle so a fumbled subject costs the author none of their hourly budget. The
  test reads the **authored** subject (stripped), matching publish exactly rather than asking
  whether it _renders_ non-empty: a canvas publish would accept and mail to real clients has to stay
  testable, or the tool for verifying is stricter than the tool for shipping. A subject that merges
  to nothing (a token with no value behind it) therefore still sends, and comes back in the
  response's existing `missing` list — a diagnostic, not a refusal, and one the author could not act
  on from a subject field they can see is not empty. **`preview` deliberately has no such gate:** it
  sends nothing, so there is no inbox for a blank subject to be wrong about, and an author checking
  layout before writing copy is a normal way to work.
- **Render (`app/email/document.py` + `app/email/render.py`).** The document pipeline's Jinja merge is
  reused verbatim and only the OUTPUT differs: `to_email_html` folds the template CSS (plus a tiny
  email base reset) into inline `style` attributes with **`css-inline`**, prepends the preheader as a
  hidden preview-text span, and wraps the fragment in a minimal client-safe document. Remote
  stylesheet loading is off — an email render must never make an outbound request.
  `render_or_default(session, tenant_id=…, code=…, context=…)` resolves the tenant's published version
  by `code`, merges subject/preheader/body identically, and falls through to `render_default` when
  there is none. A send site passes **only the merge context** — the flat keys that site provides plus
  a nested `brand` from `email.brand_tokens` (see `provisioning.py`, `routers/internal.py`,
  `notifications/email_consumer.py`). It no longer hands in a pre-built
  `default_subject`/`default_html`, and that removed the last way for the two branches to disagree:
  the site used to build the default from its own arguments while the override was offered a
  separately-assembled context, so the same send could put a different value in each. `expires_at`
  really did differ — the builder printed `expires_at.date()` while the context carried the full
  `timestamptz`, so a firm publishing the prefill started telling recipients their secure link lapses
  at a UTC-offset timestamp. `routers/shares.py` now decides once, and passes a date.
  **An override that merges to nothing counts as absent.** `render_published_email` re-runs
  `has_renderable_content` on the body **after** the merge and returns `None` if it is blank, so the
  caller falls back to the shipped default rather than sending a subject with an empty body. The
  publish gate cannot cover this on its own: a version published before that gate existed carries no
  such promise, and a body that is legitimately publishable can still merge to nothing for one
  particular send — the obvious case being copy whose only content is `{{ message }}` when the
  adviser wrote no covering note. Checking at render time is what makes "fail-safe" true per send
  rather than per publish.
- **Who authored a version (`0051_email_version_authorship`).** `email.template_versions` carries
  `authored_by_scope` — `'firm'` (the tenant's own staff) or `'operations'` (Graphene, through the
  admin door, including the seeding paths). `created_by` already resolved to a person's **name**,
  which cannot tell a firm that the person was one of ours; this can, and the platform portal's
  version history badges it. Four deliberate choices: it is stamped at **write** time and never
  derived later by joining the author to their tenant (authorship is a property of the event, and a
  membership is mutable — an operator holds none in the firm's tenant at all, so a derivation would
  relabel history the moment the directory moved); the column is **nullable with no default**, so
  pre-0051 rows read as honestly unknown instead of being back-dated to `'firm'` — the false claim
  would land on exactly the rows the feature exists to expose — and a future write path that forgets
  to declare its side writes NULL rather than the flattering lie (the service parameter is mandatory,
  so mypy is the first line of defence and the absent default the second); the `CHECK` constrains the
  vocabulary while admitting NULL, since a CHECK passes on NULL; and the stamp is **sticky at
  `'operations'`** — a firm save landing on a draft Graphene already wrote into, or a firm publish of
  a version an operator authored, must not launder that fact away, because being able to tell is the
  whole point. Neither 0042 policy nor its grants are column-scoped, so the new column needs no RLS
  change: a version operations authored carries the **firm's** `tenant_id` and stays readable by that
  firm, which is the requirement — visible and labelled, never hidden. (Chain note: `0051` revises
  `0042_email_templates` on the email lineage, which forked; `0061_merge_email_authorship` is the
  no-DDL merge that gives alembic one head again.)
- **Preview (`app/email/preview.py`).** Renders the editor's current, possibly unsaved content
  against representative sample values on a fixed reference date, dressed in the firm's **real**
  brand, with no send path in the module at all. Sample values are scoped to the code's real
  send-site keys (`_CODE_KEYS`), so a template referencing a token its send site won't supply comes
  back in `missing` rather than being helpfully filled — it would render blank in reality. The
  returned HTML is author-controlled and unsanitised: render it only in a `sandbox=""` iframe.
- **Preview↔send parity is asserted, not assumed (`tests/test_email_send_parity.py`).** The
  authoring surface promises the Preview pane IS the email a client receives; three separately
  written paths have to agree for that to be true — preview (`render_preview`), a real send with no
  override (the shipped default the send site built), and a real send resolving a published override
  (`render_published_email`). For every code in `KNOWN_CODES` the suite pins that preview and the
  published send produce **byte-identical** subject, preheader and HTML for the same canvas and the
  same context; that the authored preheader is actually **delivered**, inside the hidden
  preview-text span and ahead of the body (it regressed once, authored and previewed but never
  sent); that the firm's brand — never Graphene's — reaches every code on both send paths; and that
  the CSS is inlined with no surviving `<style>` block on both. Two structural pins hold the
  comparison up, both AST scans over `app/`: `_CODE_KEYS` is checked against the literal `context={…}`
  keys at every `render_or_default` call site (drift there means preview and send are merging
  different things, which is exactly the bug the file exists to catch), and the `"brand"` entry of
  each of those contexts must be an `email_mod.brand_tokens(<resolved brand>)` call — not a
  hand-rolled dict (the per-site duplication that let a key go missing) and not `GRAPHENE_BRAND` (the
  white-label leak spelled out). **That second pin was rewritten, because the failure mode it used to
  describe no longer exists:** it read the `default_subject, default_html = <builder>(…)` assignment
  and demanded a `brand=` keyword, since every builder defaulted the parameter to `GRAPHENE_BRAND` and
  omitting it silently mailed a firm's client in Graphene's name (omitted twice — for `invite`, then
  again for its sibling `password_reset`). There are no builders and no such assignments now, and a
  missing `brand` key already fails the first pin; what is still forgettable is the brand's
  _provenance_, so that is what is asserted. The SQL half of override resolution — published
  wins, a **draft never sends**, an archived template stops overriding, no row falls back — needs
  real Postgres and lives in `tests/test_email_templates_api.py`, alongside the canvas round trip
  (the saved `schema.html`/`schema.css` is what the send path renders; `gjs` editor state is not).
- **All FOUR paths deliver the same document, via `email_render.sendable_default`/`sendable`.** The
  no-override path used to be the odd one out: `render_or_default` handed the builder's body
  FRAGMENT straight to `send_email`, so the path most firms are actually on delivered no doctype, no
  charset meta, none of the email base reset that normalises tables and linked images across
  clients, and **no preheader span at all** — while the preview the author had just looked at and
  the published-override send both delivered a full document. The preheader half was the decisive
  one: it is the same bug that had already been fixed once for the override path (copy authored,
  saved, previewed, then not delivered). `sendable_default(code, html)` is now the single seam both
  the fallback branch and `routers/internal.py`'s no-tenant-host password reset go through; it takes
  the **code**, not a preheader string, so no send site can forget it — the same failure mode as the
  `brand=` omission that reached production twice. The wrapping is **additive, and proved so**
  (`test_wrapping_the_shipped_default_changes_nothing_visual`): same elements in the same order,
  same attributes, same visible text, and no declaration changed or removed — the only additions
  come from `EMAIL_BASE_CSS`. Byte equality is deliberately NOT the assertion, because `css-inline`
  re-serialises the declaration list of any element a rule matched (`color:#fff` → `color: #fff`)
  and normalises `style='…'` to double quotes; neither is visual, and asserting bytes would have
  been asserting something false.
  The FOURTH path is the flow tier's `send_email` action, and the parity suite was structurally
  unable to see it: site discovery scanned for `render_or_default` CALLS, which can only find the
  sites already on the seam — so an action that built its own `email_shell.shell` fragment and posted it
  unwrapped was not a failure, it was absent from the population. It goes through
  `email_render.sendable` (the seam without a `code`, since a firm's own template has no shipped
  preheader to look up; `email_shell.shell` is the chrome rendered NOW, around a body the action has already
  merged and escaped, which is why it must not go through a template engine a second time), and
  discovery is now off **`email.send_email` itself**
  (`test_every_send_site_wraps_its_html_in_the_document_envelope`): every send must hand `html=`
  something that came through a wrapping seam, with one documented relay exemption
  (`flows/notifications.py`, whose payload the producer already wrapped).
- **Every shipped default now carries a preheader (`email_defaults.PREHEADERS`).** One short line
  per code, authored rather than derived, that earns its place beside the subject instead of
  repeating it, and deliberately **token-free** so it reads correctly on the sparsest send. All
  three consumers read it: the un-overridden send (through `render_default` → `sendable_default`,
  which takes the CODE rather than a preheader string so no caller can forget it), the editor prefill
  (`GET /{uid}/default` now returns `preheader` — without it "Load default" then publish LOST the
  preview line), and `load_defaults`, which seeded the model preset with `preheader=""` and so gave
  every firm cloning the preset an empty inbox preview line. `default_for` returns a
  `ShippedDefault(subject, preheader, html, css)` NamedTuple rather than a 3-tuple. **The authoring
  half has landed too:** "Load default" now fills BOTH compose fields, via
  `@graphene/ui/email-editor`'s pure `defaultComposeFields` (`default-content.ts`) — it overwrites
  subject and preheader alike (the affordance is only offered on an empty canvas) and turns an absent
  `preheader` into a blank line rather than the string "undefined". Both loaders had to be widened:
  each declares its own `ApiEmailDefault`, and a local shape that omits a field the API returns drops
  the value before the page mounts, which is invisible at runtime.
- **Send a test to yourself (`POST /email-templates/{uid}/send-test`, and the admin twin
  `POST /admin/tenants/{id}/email-templates/{uid}/send-test`).** A browser preview shows what a
  browser makes of the HTML; it says nothing about the things that actually break an email — Outlook's
  table handling, dark-mode inversion, a client that blocks images, whether the preheader surfaces,
  whether the CTA is tappable. So the author can put the live canvas in a real inbox. It takes the
  **same body as `preview`** (subject/preheader/html/css/audience) and renders it through the **same**
  `email_preview.render_preview`, so a test and a preview can never disagree; the mail then goes out
  through `send_email`, the one chokepoint, so the non-production guard applies unchanged.
  **`audience` has to be forwarded by the proxies, and was not.** All four app-side proxies (both
  apps × `preview`/`send-test`) build the request body field by field, and each omitted `audience` —
  so the API's default of `"client"` was the only value that could ever arrive, and the two task codes
  — which really render under `portal="platform"`, because their rules ship the `assignee` audience
  (`notifications/email_consumer.py`) — were rendered and sent in the wrong brand from the wrong sender
  identity. They normalise it to the two values `Audience` accepts, so an unrecognised one is a client
  render rather than a 422. The editor's own starting position for each code is
  `@graphene/ui/email-editor`'s `defaultAudienceFor` (`preview.ts`), read off these same send sites.
  **There is NO recipient parameter — deliberately, and this must not be relaxed.** It mails the
  authenticated caller's own address, resolved server-side (`auth.uid()` → `auth.users.email`); an
  address in the body is ignored, and a caller with no address on record is refused with a message
  saying so rather than falling back to anyone. An endpoint that accepted an address would be an
  authenticated open relay: a member of a firm's staff could aim the firm's own verified sending
  identity at a client with arbitrary HTML on it, and the audit trail would read "tested a template".
  The envelope is the firm's, via **`resolve_sender`** (never `sender_for` — see the cold-cache
  warning above). The subject is prefixed **`[TEST] `** so a test reaching a real inbox cannot be
  mistaken for a live send: on the SUBJECT, not in the body, because the body is the thing under test
  and an injected banner would change the very layout the send exists to reveal — and a subject shows
  in an inbox list before anything is opened. The response is three-valued —
  `delivered` / `suppressed` / `failed` — because `send_email`'s single bool would tell an author on a
  staging box that a suppressed send succeeded; `suppressed` carries `suppression_reason`'s
  (or `sending_disabled`'s) text, which names the **setting** and never a recipient, and an absent
  `RESEND_API_KEY` is reported as suppressed rather than failed (a configuration state, not an
  incident). Before any of that it **422s a blank subject** — the publish gate applied here too, in
  the same voice, since a `"[TEST] "` with nothing after it teaches nothing about the inbox this
  endpoint exists to show you (see the publish-gate bullet above for the authored-vs-rendered
  reasoning, and why `preview` keeps no such gate). Gated on `email_templates:write` (the admin twin
  on the target tenant's write scope) and
  throttled through the shared limiter — `ratelimit.EMAIL_TEST`, keyed on the acting user
  (`RATE_LIMIT_EMAIL_TEST_PER_HOUR`, default 20) with an advisory per-firm budget
  (`RATE_LIMIT_EMAIL_TEST_TENANT_PER_HOUR`, default 200): the only authenticated capped surface,
  because what is bounded is Resend spend, and a held-down button costs the same as an anonymous
  flood. The caller's own address is echoed back so the UI can say where to look; no other address
  appears in the response or in a log line (`tests/test_email_send_test.py`).
- **The default copy WAS authored twice. It is one source now (Aug 2026), and the collapse is the
  fix for the divergence class above.** The two copies were `app/email/transport.py`'s per-code builders (what
  actually sent) and `app/email/defaults.py`'s token prefill (`GET /{uid}/default`, the "Load default"
  button) — the same copy and the same chrome, written twice, in two dialects. `email_defaults` is now
  the only authoring and `email_render.render_default` renders it; the builders are deleted (~150
  lines).
  The stated reason for the split does not survive contact: the builders escaped their arguments,
  which would have mangled a quoted expression like `{{ first_name or 'there' }}`. Under
  single-sourcing nothing passes Jinja SOURCE through an escaper — the template is never an argument —
  and Jinja's autoescape escapes the merged VALUE, which is exactly what `_esc` was doing by hand one
  step earlier. The other reason (`request_raised` needs a `{% for %}` over `item_titles` that no
  builder can express) argues FOR the template being the source, not against it.
  **Seven divergences came out of that gap, none of them a wording preference**, and all are fixed:
  the `document_share` prefill referenced `expires_at` nowhere, so publishing it unchanged stopped
  telling recipients when their secure link — which IS the credential — would lapse; the
  `request_raised` prefill flattened the item-count pluralisation ("…: 3 things need your attention")
  to the one-item wording; `document_sent`/`document_share` lacked the `document_name or 'A document'`
  fallback, so a nameless document gave an empty `<strong>` and the subject "Acme Wealth: is ready
  for you"; `request_raised` printed "Please complete this by ." on a request with no deadline;
  `document_sent` and `request_raised` emitted an unguarded CTA, so a deployment with no client-portal
  host published a dead `href=""` button above a "paste this link into your browser:" line with
  nothing after it; `task_notice`'s hand-rolled button omitted the fallback-link paragraph; and the
  **seventh, latent one** — the covering note's paragraphs — is the `paragraphs` filter above. Three
  straight copy splits were closed earlier by moving the builder onto the prefill's wording: "Hi
  there," for a nameless recipient, `password_reset` says "safely ignore", `document_sent` says "by
  your adviser" when no sender is named.
  **What guards the copy now.** `test_the_prefilled_default_and_the_shipped_default_read_identically`
  compared the two authorings' rendered subject and visible copy, on a full context and a sparse one,
  and it earned its keep — but with one source it compares a string to itself, so it is **deleted**: a
  test that cannot fail reads as coverage and is worse than none. In its place,
  **`tests/test_email_shipped_copy_golden.py`** states the copy as LITERALS — each code's delivered
  subject (verbatim, escaping included) and the delivered document's visible text, full and sparse,
  captured from the shipped defaults as they stood BEFORE the collapse and unchanged by it. A
  two-derivation comparison can never catch both sides moving together, which is precisely what a
  collapse does; a literal can. Its frozen context is deliberately NOT `sample_context` — a golden
  whose inputs can move is not a golden — and its brand and document names carry ampersands, which is
  what pins the subject as plain text. `tests/test_email/defaults.py` still pins that every catalogue
  code HAS a default, that each merges cleanly under its own send-site context (no stray token, no
  blank), and that each **uses every token its send site supplies** (an unused key is the default
  dropping content the send site went to the trouble of providing).

  There used to be a THIRD authoring — `pkgs/email`, Svelte mirrors of three codes behind a dev-only
  `/dev/emails` preview — and it was **deleted (Aug 2026)** along with that route. It was never on the
  send path, nothing pinned it against either copy, and it covered only three of the six codes:
  a preview of templates that no longer send is worse than no preview, because a developer can open it
  and reasonably conclude that is what clients receive. What replaced it is better on its own terms:
  the editor's Preview dialog renders through `render_preview` (the same function the send path's
  override branch merges with), and Send test puts the live canvas in a real inbox.

- **A stored body has two style shapes, and the difference only shows when a block is duplicated.**
  `schema.html` is a body fragment and `schema.css` a stylesheet, and GrapesJS decides which one
  carries the styling. **Rule-shaped** (every body saved before commit `0f1e1ac`): the
  parse lifted each component's inline `style` into a generated **id-keyed rule**, so `html` holds
  bare `<div id="i3f4">` and `css` holds `#i3f4{…}`. **Inline-shaped** (anything parsed since):
  `avoidInlineStyle: false` **and** `forceClass: false` in
  `pkgs/ui/src/lib/components/email-editor/create.ts` keep the styles on the elements and `css`
  is largely empty. Both **send** correctly — `to_email_html` folds `css` back into `style`
  attributes, so a rule-shaped body arrives inlined anyway. Both also **load** correctly, which is a
  separate promise and a newer one: `loadEmailContent` installs the stylesheet **before** parsing the
  components, so a rule-shaped body's id-keyed rules are in place and still match the ids the parse
  then assigns, while an inline-shaped body carries its declarations where `cssComposer.clear()`
  cannot reach them (see the editor bullet — the order is load-bearing). The asymmetry is authoring:
  **duplicating a block in a rule-shaped body yields an unstyled clone**, because the copy gets no id
  and no rule of its own, so it renders naked (serif, bare link) in the preview and the real send
  while the canvas still looks right — the rules are live in the canvas document. `avoidInlineStyle`
  alone is the trap that reads as a fix: with `forceClass` left at its default `true`, GrapesJS still
  lifts inline styles into generated class rules. A rule-shaped draft **heals by re-parsing fresh** —
  clear the body, then **Load default** (offered only on an empty canvas), which re-seeds from the
  built-in default's inline-styled HTML and saves in the new shape.
- **The editor is shared** — `@graphene/ui/email-editor`
  (`pkgs/ui/src/lib/components/email-editor/`, GrapesJS + `grapesjs-preset-newsletter`,
  dynamic-imported in `onMount` because both touch `window`; a **subpath** export so grapesjs stays
  off the package root and `apps/client` never pulls it). The platform firm surface and the admin
  per-tenant surface mount the SAME editor, so there is one of it and not two: the canvas emits
  content via `onChange` while save/publish/navigation stay app-side, and the dialogs take injected
  props (`previewUrl` + `senderName`; `versionsUrl`/`updateAction` plus the app's own `enhance` and
  `onRestored`/`onSaved` callbacks, because pkgs/ui cannot import SvelteKit's `$app/*` virtuals).
  `VersionHistoryDialog` and `TemplateSettingsDialog` were app-local and are now here too, on the
  same contract, and the admin surface mounts **both** — a mistyped name or code is a correction
  rather than an archive-and-recreate, which matters most on the model preset, whose codes every new
  firm clones. Its permission is the target tenant's own (`model:write` for the preset,
  `tenants:write` for a firm — `$lib/email-templates/scope`, mirroring `_require_tenant_scope`), not
  the section's blanket write. `senderName` is the tenant being edited (`auth.tenants.name`, which is
  what the firm portal's `appName` resolves to), and stays the generic placeholder on the preset,
  which has no logins and sends nothing. **The settings dialog asks for no `category`** — nothing
  reads it at send time (the preview's brand choice is an explicit `audience` param) so the create
  form derives it, and because the API's PATCH coalesces, a control there would make both surfaces
  post a defaulted `notification` and silently reset a transactional template's category on every
  rename. Both actions therefore omit the key rather than defaulting it.
  **`description` is the mirror image and needs the opposite treatment:** it is the one field a user
  can legitimately EMPTY, and both actions posted `trim() || null` into the same `COALESCE(:desc, …)`
  — so deleting a description restored the old one and reported success. The PATCH now decides on
  PRESENCE (`'description' in model_fields_set`), an explicit `""` (or null) clears the column to
  NULL, and only an ABSENT key preserves it; the actions post the trimmed string, blank included. The
  two rules hold together — omit `category`, always send `description`.
  **"Send test" sits beside Preview** on the same live-canvas read (one `liveContent()` both use, so a
  tweak made inside the canvas's ~250ms sync debounce is in the thing the author clicked to inspect),
  behind an optional `sendTestUrl` — absent ⇒ no button, and it never renders for a viewer, since the
  API gates the send on write and offering it would only produce a 403. Each app proxies it
  (`email-templates/[id]/send-test`, `tenants/[uid]/email-templates/[templateId]/send-test`, gated the
  same way as its `/preview` sibling — the admin one on the target tenant's WRITE scope) so the access
  token stays server-side and no recipient can cross the boundary; a **429 is passed back as a
  response, not thrown**, because SvelteKit's `error()` cannot carry `Retry-After` and the wait is what
  lets the toast name a number. The three outcomes stay three in the UI
  (`email-editor/send-test.ts::testSendFeedback` → success/warning/error toast, one test per branch):
  **`suppressed` is a warning naming the setting**, never a success it hasn't earned and never a
  failure that sends an author hunting a delivery bug that does not exist.
  See [`packages.md`](packages.md).
- **A block may be dropped anywhere over the email** (`create.ts` → "WHERE A DROP LANDS"), which needed
  two halves because the canvas is almost entirely a table: GrapesJS's built-in `table` accepts only
  `tbody`/`thead`/`tfoot` and its `row` only cells, and a refused target is **not** a dead drop — the
  sorter re-runs itself against the parent element and the walk stops only at the wrapper, so a pointer
  over the card's edge silently promoted the block to BODY level and drew its indicator at the email's
  top or bottom edge, hundreds of pixels away. So the table types' `droppable` is widened **for this
  editor's own component types** (the sorter stops where the pointer is), and the landing is then
  corrected inside the same `component:add` callstack so the block ends up in a `<td>` — a body-level
  drop wrapped in the `Full Width` section's own markup, one constant shared with the block. The
  widening alone would be the bug it looks like a fix: `<p>` as a direct child of `<table>` is hoisted
  out by every parser and mangled by Outlook, so the canvas would look right and the inbox would not.
  Same callstack ⇒ **one undo step** (backbone-undo groups by callstack, not timestamp), and the gate is
  the Sorter's own two `action` labels, so a parse of a stored template is never rewritten on open.
  A read-only principal gets neither half — they are installed in the `else` of `applyReadOnly`, not
  neutralised after it.
- **The editor's chrome.** Three surfaces: a **Blocks** rail, the canvas, and a right rail carrying a
  **curated inspector** above the contextual **Style** rail. GrapesJS's own trait ("Settings") and
  Layers rails stay dropped — its default traits for an email block are the raw `id`/`title`
  attributes, plumbing no firm author should be asked about, and a layer tree adds little to a
  single-column 640px email. What replaced Settings is the inspector: only the attributes that matter
  in an email **and** are otherwise unreachable, shown only when the selection has them — a link's
  destination (which accepts a merge field, since every block ships the `#` placeholder and emptying
  the field clears the attribute rather than restoring a dead `#`), an open-in-new-tab toggle that
  writes `rel="noopener noreferrer"` alongside `target` and **removes** both rather than blanking
  them, and an image's alt text (most clients block images, so it is often all the recipient sees).
  For a Button — a table wrapping a single `<a>` so the fill can sit on the cell — the inspector edits
  that one descendant anchor, and deliberately offers **nothing** when a selection contains more than
  one link, because then "the link" is ambiguous and silently editing the first is worse. The Style
  rail names the selected component and shows "Select a component on the canvas to style or move it."
  when there is no selection — its mount is hidden, never removed, because GrapesJS appended its panel
  into it at init. **The right rail is ONE section with ONE sticky header** (the selection's name);
  everything under it scrolls as a sector — Position, the inspector, then GrapesJS's own
  Dimension/Typography/Decorations. It used to be two sections, each with a `position:sticky` label in
  the same `overflow-y:auto` scroller, and two headers pinned to the same edge **overlap** as soon as
  both their sections are on screen — which is what expanding a style sector did. The inspector is
  therefore a **collapsible sector** on `@graphene/ui`'s `Collapsible` (caret that rotates when closed,
  the theme's brand dot when open, hairline underneath — GrapesJS's own affordance, since the complaint
  was that it behaved unlike the sections beneath it), and it **opens itself on a newly selected link or
  image** because it is the only route to a destination and an alt text; re-syncing the same selection
  leaves an author's own collapse alone. The section is `flex: none` for the same reason: as an ordinary
  shrinkable flex item it collapsed to the RAIL's height while its content overflowed it, and a sticky
  child is confined to its containing block, so the header unpinned partway down a long sector list
  (measured live: a 712px box around 2,409px of content). `rail.test.ts` derives the sticky classes from
  the component's own stylesheet and counts them per scroller, so a second sticky header fails however
  it is spelled.
  **Position** (`position.ts`) is explicit reordering of the selection within its parent — up / down /
  to top / to bottom, disabled at the ends of the range rather than hidden, and the only keyboard route
  to reordering (GrapesJS offers dragging alone, which is all cost in a single-column email). The
  arithmetic is a pure function because one index is a trap: `Component.move` treats `at === index + 1`
  as "already there" and silently does nothing, so moving DOWN asks for `index + 2` (and to the bottom
  for `count`). One move is **one undo step** with no grouping code — backbone-undo fuses everything
  raised in a single synchronous callstack, which is why the handler must never `await` — and
  `describeUndoGroup` already reports the add+remove pair as "Moved …". The selection is re-applied
  after the move, since `move` removes the component and GrapesJS drops a removed component from the
  selection. The page header is **one** row (back · name · status · unsaved · Preview · Save ·
  Publish — deliberately not `PageHeader`, which stacks three), and the canvas owns the **only**
  scrollbar, flush against the style rail's edge rather than on the email card. The white page is
  sized to the email's own content by `syncFrameHeight` with a **600px floor** (`MIN_FRAME_HEIGHT`):
  an iframe never sizes to its own document, and the floor is load-bearing — the wrapper is
  `overflow:hidden`, so a sync that hasn't run yet would collapse it and clip the canvas blank. Merge
  fields come from a per-code **Insert field** menu (`merge-fields.ts`, mirroring the API's
  `_CODE_KEYS` — keep the two in step); a blank canvas additionally offers **Load default** and
  **Starter layout**. A principal with `email_templates:read` but not `:write` gets a document-level
  read-only lock (GrapesJS ships no read-only mode, so every component is made non-editable and the
  mutating keymaps are stripped).
- **Loading content: styles first, components second** (`loadEmailContent` — the one seam every seed
  path goes through, including Load default and Starter layout, so no call site can reintroduce the
  bug). `EditorModel.setStyle` is `cssComposer.clear()` followed by an add: it wipes the WHOLE rule
  collection, not just the selectors handed to it, so running it AFTER the parse deleted the rules the
  parse had just derived from the markup's own styling. That was the real cause of "Load default lands
  unstyled" — `email_defaults.default_for` always returns an EMPTY css (the shipped emails are
  entirely inline-styled), so the old trailing `setStyle(css)` was a bare `clear()`.
- **Preview fetching lives in `preview.ts`** — the request contract, the error derivation and a
  monotonic **stale-response guard** that reports a no-longer-newest response as `stale` instead of
  writing it over fresher content — extracted with its own tests so a second preview surface cannot
  reimplement that guard slightly differently. The dialog renders the result as a **mail-client
  envelope** rather than a bare iframe: sender name and initials from the app's branding (injected,
  **not** scraped from the rendered body — that picked up the first text node and showed the email's
  own heading as the sender), then recipient, subject and the preheader as the inbox preview line, in
  the recipient's own reading order, with skeletons while the render is in flight. The recipient and
  time are **fixed samples**, because the real To and timestamp are decided at send time and a
  live-looking value would imply a precision the preview does not have. The body itself renders in
  `@graphene/ui/email`'s frame, so the `sandbox=""` that makes author HTML safe to render at all
  cannot be re-derived more laxly by a second surface.

## Configuration

`config.py` reads `.env` then `.env.local` (local values win, Vite-style). Settings actually consumed:

| Var                        | Default                      | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| -------------------------- | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`             | `…app:app@localhost/portals` | App role: **DML only, subject to RLS**. Also the firm API's connection (`python -m external`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `EXTERNAL_HOST`            | `127.0.0.1`                  | Bind address for the firm API. Containers set `0.0.0.0`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `EXTERNAL_PORT`            | `8010`                       | Port for the firm API (`python -m external`), beside the portal API on `8000`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `EXTERNAL_STUB`            | `true`                       | In-memory stub when true (local default). Deployed compose sets `false` unless the environment already sets the variable                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `SCREENING_API_KEY`        | `""`                         | ComplyCube key (also read as `COMPLYCUBE_API_KEY`). The API (AML console screens and decisions), the worker (event consumer) and the firm API all need it. Blank fails a firm-API check closed, makes the AML console answer 503 and leaves the worker idle                                                                                                                                                                                                                                                                                                                                                                              |
| `SCREENING_WEBHOOK_SECRET` | `""`                         | ComplyCube webhook secret (also read as `COMPLYCUBE_WEBHOOK_SECRET`), checked by the firm API's inbound webhook. Blank refuses every delivery                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `MIGRATION_DATABASE_URL`   | `…migrator:migrator@…`       | Elevated role: used **only** by Alembic                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `WORKOS_CLIENT_ID`         | `""`                         | Required to verify tokens (fetches the JWKS); blank → `/me` 500s                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `WORKOS_API_KEY`           | `""`                         | Only needed for user-management API calls                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `WORKOS_ISSUER`            | `""`                         | Optional: pin the access-token `iss` (off by default — see [`auth.md`](auth.md))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `ALLOWED_ORIGINS`          | localhost 5173/5174/5175     | CORS allowlist (credentials are allowed, so this is **not** `*`); comma-separated or a JSON array                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `SYSTEM_USER_IDS`          | `` (empty)                   | Bootstrap break-glass: WorkOS user ids treated as Graphene **system** admins even without a WorkOS role — grants the first admin so they can set up orgs/roles. `is_system()` honours it alongside the token role. Comma-separated or JSON array                                                                                                                                                                                                                                                                                                                                                                                         |
| `SECRETS_MASTER_KEY`       | `""`                         | Fernet key that encrypts per-tenant WorkOS environment secrets in `auth.environment_secrets`. The API is the sole holder; blank → secret handling fails closed. Generate with `Fernet.generate_key()`                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `INTERNAL_API_TOKEN`       | `""`                         | Shared secret for the internal service-to-service endpoints (portal → `/internal/env-config`); blank disables them (404)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `S3_*`                     | `""` (unconfigured)          | Object storage ([`storage.md`](storage.md)): the platform root credential (`S3_ENDPOINT_URL`/`S3_ACCESS_KEY_ID`/`S3_SECRET_ACCESS_KEY`) provisions per-tenant `t-{uid}` buckets (`S3_TENANT_BUCKET_PREFIX`) and writes assets; `S3_BUCKET`/`S3_PUBLIC_BASE_URL` are the legacy shared branding bucket fallback; `S3_CORE_BUCKET` is the Graphene-internal raw/staged zone (ETL plane). All blank ⇒ storage disabled (branding falls back to `data:` URIs)                                                                                                                                                                                |
| `S3_DOCUMENTS_BUCKET`      | `""`                         | **Private** document object storage (uploaded + generated docs) — a **separate** bucket from `S3_BUCKET`, reusing the same endpoint/credentials/addressing. No public URL: bytes reach the browser only via a presigned URL or a proxied stream. Blank disables document storage (`app/storage.py` raises `StorageUnavailable`; the document routes 503 with a clear error). A row whose object the store no longer holds raises `storage.ObjectMissing` (botocore `NoSuchKey`/`NoSuchBucket`/`404`) and the content routes answer **404**, not the 500 it used to be — an outage and a gone file are different facts. Local dev: RustFS |

**Rate limiting** (`app/ratelimit.py`, per-process in-memory — see the known gap in
[`auth.md`](auth.md)): `RATE_LIMIT_ENABLED` switches the lot; `0` disables one limit. The
unauthenticated surfaces are reached by the portals through `POST /internal/rate-limit` — `login`
(`RATE_LIMIT_LOGIN_PER_15M`), `forgot-password` (`…_PER_HOUR`, plus the advisory per-host budget
`…_HOST_PER_HOUR` that alarms and never refuses) and `mfa` (`RATE_LIMIT_MFA_PER_CHALLENGE`, keyed on
a hash of the pending challenge, and `RATE_LIMIT_MFA_IP_PER_15M`). The authenticated surfaces are
enforced in their own routers, keyed per acting user: `email-test` (see the email section),
`postcoder` (`RATE_LIMIT_POSTCODER_PER_MINUTE`, checked before the upstream key is even looked at,
because each lookup is a metered call a form can fire per keystroke — the same limit covers the
UK bank check, `POST /ref/postcoder/bank`) and `search`
(`RATE_LIMIT_SEARCH_PER_MINUTE`, after the minimum-length check so a short query costs nothing).
`tests/test_ratelimit.py`, `test_postcoder.py`.

> `apps/api/.env.example` also lists `API_URL` and `WORKOS_COOKIE_PASSWORD` — those belong to the **frontend portals** and are ignored here (`extra="ignore"`); they ride along only so one `.env` can serve local dev. Never commit real `.env`; only `.env.example` is tracked.

## Database roles & RLS context

Two roles, deliberately separate (rationale in [`stack-and-workflows.md`](stack-and-workflows.md) §10):

- **app role** (`DATABASE_URL`) — non-owner, DML-only, subject to `FORCE ROW LEVEL SECURITY`. Everything a request does runs as this role.
- **migration role** (`MIGRATION_DATABASE_URL`) — owns the schema, runs DDL, used only by Alembic. Owners bypass RLS, so the app must never use it.

`db.py` exposes the base session dependencies plus target-aware Operations counterparts:

- `tenant_session` — opens a transaction and establishes the RLS context: `SET LOCAL ROLE authenticated` (the `app` login role is a member, so policies `TO authenticated` apply), then calls `auth.resolve_claims(workos_user, workos_org)` — the `SECURITY DEFINER` minter that turns the verified WorkOS identity into the pre-resolved internal claims (`user_uuid`, `tenant_id`, `tenant_ids`, `contact_ids`, `account_ids`) — merges them with the JWT-derived half (`build_jwt_claims` in `auth.py`: `sub`/`role`/`permissions`/`sid`/`org_id`), and publishes the whole bundle as `request.jwt.claims` via `set_config(..., is_local => true)` (transaction-scoped, correct under PgBouncer transaction pooling). The `auth.*` RLS helpers read only this GUC, so policy evaluation does **zero** per-row auth queries. It **fails closed**: no `org_id`, or a WorkOS user/org that doesn't resolve to a known `auth.users` row + tenant, gives a 403 rather than an unscoped session. Apply it to every tenant-scoped route.
- `firm_session` — the **firm self-service** path (platform portal): resolves the caller's OWN `tenant_id` from `auth.resolve_claims`, runs as `service_role` but forces every query to that tenant, and publishes claims for audit. Endpoints gate actions with `auth.require_permission(principal, "…")` against a token permission. Non-system, tenant-locked — a firm can only ever touch its own data. Yields a `FirmContext` (`session`, `tenant_id`, `principal`).
- `target_tenant_session` / `target_firm_session` — no `tenant` param delegates to the base path;
  `?tenant=<uid>` requires an Operations-portal principal and `tenant_portal:read` for safe methods
  or `tenant_portal:write` for mutations. The selected firm's tenant scopes replace the actor's
  scopes, but `sub`/`user_uuid` remain the operator's. `target_tenant_read_session` is the narrow
  POST-as-read variant used only to mint a target firm's realtime ticket. Target paths deliberately
  do not apply the firm's suspension check. The borrowed permission vocabulary is the target firm's
  reads on a safe method and its whole platform set on a mutating one — it is republished into
  `request.jwt.claims`, so a GET must not carry claims that satisfy the firm's write policies. The
  three book-proxy sessions defer to these for an operator holding `tenant_portal:*`, since both
  families front the same handlers. And a uid resolver in `app/addressing.py` declares the SAME
  session as its endpoints: a mismatch opens a second session on the operator's own tenant, so the
  firm's record 404s (pinned route-wide by `test_target_tenant_context.py`).
- `get_session` — a plain transactional session with **no** tenant context (runs as the `app` role, no `SET ROLE`, no claims). For system/non-tenant work only; do not use it on tenant-scoped tables. Prefer `tenant_session`.

**The claims contract** (`request.jwt.claims`) is the seam between the app and RLS — see `docs/identity-and-access.md` §5–6. `role`/`permissions` come straight from the verified WorkOS token (never read back from the DB); the internal ids are resolved once per request by `auth.resolve_claims`. A `client` principal resolves to an **empty** `tenant_lineage()`, so it is locked out of every staff/tenant table and reachable only via the explicit contact/account-scoped policies (those scope claims arrive in 0002).

**Project-per-tenant (`identity-and-access.md` §0).** The tenant boundary is a WorkOS **project per tenant** (staging/production environments within it; the WorkOS org denotes the **portal** — Client Portal vs Platform — not the tenant). So `auth.resolve_claims` takes the tenant from the **login** (`auth.users.tenant_id`), not from `org_id`; the `p_workos_org_id` argument is retained only as a transitional fallback for logins not yet backfilled. Email is unique **per tenant** (`UNIQUE(tenant_id, email)`) — the same address is a separate login in another firm. The non-secret routing config the API needs to verify a token against the right environment (client_id / issuer / auth domain / host) lives in **`auth.tenant_environments`**; the environment's **api key + cookie password are set via the admin UI and stored encrypted at rest** in a service_role-only secrets table, decrypted at runtime with a master key held in env/KMS (the one bootstrap secret).

**Shared-API token verification is issuer-keyed (built).** `auth.py` reads the token's `iss` (unverified) to pick the matching environment from an in-memory registry (`tenant_registry`, loaded from `auth.tenant_environments` at startup), then verifies against **that** environment's JWKS (`.../sso/jwks/<client_id>`) and enforces its issuer. An issuer not in the registry (or an empty registry) falls back to the configured environment (`WORKOS_CLIENT_ID` — the Ops/control-plane env, or local dev); the JWKS signature check is the real gate, so registering tenant environments never locks out the fallback env. When a new tenant environment is registered, call `tenant_registry.refresh()` so the shared API picks it up without a restart (the register-environment admin action is the staged next step — project-context §12.4).

## Action events — every action emits an event

**The guarantee:** every authenticated mutating request (POST/PUT/PATCH/DELETE) that succeeds
(2xx, except `202 Accepted`) appends one row to **`audit.actions`** (0041), except a handler
marked `writes_no_row` — the two realtime ticket mints (transport for the SSE stream),
`POST /me/page-view`, `POST /me/rum` (a trace span, not a row), and the caller's own notes
(`/me/notes`, private to them — a trail of someone's "call Joan" is nobody's business). Page views are navigations, not actions: they
land in **`audit.page_views`** (0213), path only. A document load is posted by the portal
`handle` hooks; a committed client navigation is posted by the layout. A member
profile lists the latest of those views in Recorded activity, beside the action rows. A designated door
that holds a change for four-eye review answers **202** and writes `review.requested` instead —
the happened-event fires only after approve. See [`docs/reviews.md`](reviews.md). All three portals act through this API, so
this is the product-wide "every action taken emits an event" floor. It sits **under** the richer
domain mechanisms — the `audit.record()` DML triggers (per-table effects into `audit.log`),
`documents.audit_log` (product events), the flows `domain_events`/`outbox` — which stay
authoritative for their domains; the request-level event is the uniform floor, not a replacement,
and the two deliberately live in **separate tables** so stream consumers never have to
disambiguate.

**Mechanism** (`app/audit_actions.py`): `ActionEventsMiddleware` is a pure-ASGI middleware installed
innermost (main.py). During routing FastAPI stamps `scope["route"]`/`scope["path_params"]`, and
`get_current_principal` stashes the verified principal in `request.state` (`_issue_principal`) —
so after the response has gone out the middleware has the full attribution without touching a
single handler. It then writes the event in its own short `service_role` transaction, resolving
the actor's tenant context through the same `auth.resolve_claims` the session dependencies use.

**Event shape** (`audit.actions`): `id` (monotonic bigint — the consumer cursor), `event_type`
(**a declared `resource.verb` name** — `document.sent`, `client.created`,
`email_template.version_published`; the map is `app/audit_actions.py::EVENT_TYPES`, keyed on the route's
operation id, and `tests/test_actions_tripwire.py` fails a mutating route with no entry so the
vocabulary is closed),
`tenant_id` (the actor's resolved tenant), `target_tenant_id` (**the firm the action was done TO**
— from the `?tenant=<uid>` book/ops convention or a `{tenant_id}` path param, so a proxied
middle-office action and every ops route under `/admin/tenants/{tenant_id}/…` name **both** the
operator's tenant and the firm acted on), `actor_id` (resolved `auth.users` uuid) + `actor_sub` (WorkOS subject, always present),
`method`, `route` (the **template**, never the raw path), `status`, `path_params` (the target
ids), `metadata` (correlation id, redacted query params, `break_glass` flag).
**The event vocabulary is `resource.verb`, one name per ACT.** A singular domain noun, then what
became of it, past tense (an event has already happened). A compound verb puts its qualifier first
(`email_template.version_published`), so the last word is always the verb.

The target-workspace contract depends on both sides remaining distinct: the query's
`tenant=<uid>` resolves `target_tenant_id`, while the principal resolves the actor tenant and
`actor_id`. Do not rewrite actor claims to look like a firm user. Support actions then appear in
the firm's event log as Graphene support by the real operator's name, and Operations retains the
same row as cross-tenant evidence.

Two rules follow from "per act", and both were defects before:

- **Two doors emit ONE type.** Graphene publishing a firm's email template and the firm publishing
  its own are the same act; who did it is on the row (`actor_id`, `target_tenant_id`, `route`), not
  in the name. 25 pairs are collapsed this way — members, roles, portals, branding, email templates,
  notification rules, and both ends of a message thread. A second name for one act splits every
  subscriber: a rule written against one silently missed half the occurrences.
- **The name does not follow the code.** It used to be `action.` + the operation id
  (`{router tag}-{handler name}`), so the router leaked in — twice where a handler repeated its tag
  (`action.admin-admin-create-email-template`) — and renaming a handler renamed a public contract.

`0096_event_vocabulary` rewrote the historic rows, so the stream carries one spelling. The readers
that must not lose history anyway — the AML deny list, the feed's hidden types, the document-family
predicate, the `{uid}` resolution and the UI's humanising — still accept the legacy `action.…` shape:
a rolling deploy has old and new instances writing at the same time, and for the AML deny list that
window is a disclosure rather than a cosmetic gap.

**Never request
bodies.** Path/query values are filtered through the same redact list as `audit.record()`
(`key_hash`/`credentials_ref`/`workos_api_key_ct`/`cookie_password_ct`/`password_ct`) plus
generic secret markers (`password`/`secret`/`token`/`authorization`/`credential`/`api_key`) —
keep the SQL list, `app/audit_actions.py::REDACTED_KEYS` and the pin in `tests/test_actions.py` in step.

**Failure semantics — documented choices, don't relitigate:**

- _An audit of effects, not attempts._ 4xx/5xx emits nothing — matching the house precedent (the
  DML triggers only ever see committed writes; refused attempts live in structured logs). The
  middleware observes the status only after the handler's transaction committed, so a recorded
  2xx means a durable effect.
- _Best-effort, post-commit._ The event write runs after the response is sent; a failure is
  swallowed into `report_error` (error tracker + `graphene_errors_total`), never breaks the
  action, and never 500s a response that is already on the wire.

**Two explicit writers cover what the middleware cannot see.** The middleware is the _request-path_
floor; it never fires on a GET, an anonymous request, or work with no request at all. So
`record_automation_action` writes the events an **automation** produces in the worker loop (it names
the automation as actor and carries the self-trigger chain marker), and `record_document_action`
folds the two document-activity events the middleware misses — a document **view** (authenticated
GET) and the worker **render** completing — into the same stream as `document.viewed` /
`document.rendered` (distinct names from the mutating document routes the middleware already
covers, so a reader never double-counts; `describe.ts` gives them "Viewed / Rendered" headlines and
the documents glyph). A THIRD — a **share access** (anonymous GET) — went with the document
share-links feature; the READ side deliberately keeps the share vocabulary in both spellings
(`describe.ts`, `audit_stream.py`, `admin._UID_KIND_BY_RESOURCE`) so the rows already written still
humanise. The document writer opens its **own `service_role` transaction, best-effort** (the
view/render must never break, and `audit.actions` is service_role-only) — the view path via a
`BackgroundTask` so it is off the request's critical path;
the automation writer instead writes atomically on its already-`service_role` caller session. A
third, `record_auth_action`, records **authentication** events the request floor cannot see —
login / MFA / logout run in the portals' own SvelteKit servers via WorkOS, and set-password
activation lands on the exempt `/internal` path. **Emitted:** `authentication.account_activated` (from
`/internal/onboarding-complete` on a real pending→active promotion; actor resolved from
`auth.users.workos_user_id`), and `authentication.succeeded` / `authentication.signed_out` /
`authentication.failed` — each portal reports the latter three from its login/logout **and**
mfa/mfa-enroll server actions via `reportAuthEvent` → `POST /internal/auth-event` (a failed login is
attributed to `ANONYMOUS_LOGIN_SUB` with the attempted email; `login`/`login_failed` carry the
WorkOS-aligned `method` — `password` or `mfa` — so the trail reads "Signed in with a password"). The
only **reserved** types are `authentication.mfa_enrolled` / `-removed`, the second-factor
enrol/remove events: plumbed end to end (constants, `/internal/auth-event` kinds, `describe.ts`
labels) but no caller yet (an MFA _sign-in_ is already recorded as a login). `describe.ts` gives the
family the `security` glyph. Like the others each is a best-effort `service_role` write and not a
new route, so none touch `test_actions_tripwire`.

**The trigger path — now built.** The subscriber this table was shaped for exists: the Flows v3
automation consumer (`app/flows/automation.py`, worker loop `_automations_loop`) tails this stream
by cursor and fires firms' armed automations off it — see `docs/flows.md` → "v3 automations". It
follows the contract below to the letter, including the read lag, and it deliberately does **not**
route on raw `event_type`: automation authors pick from a curated trigger registry
(`app/flows/triggers.py`), so a renamed route re-points one registry entry instead of silently
disarming every firm. Its cursor has one other writer: the estate-wide kill switch parks the
cursor at the head on resume so a pause drops the backlog rather than releasing it in a burst
(`app/flows/platform_pause.py`, `PUT /admin/automations/pause` — `automations:global_pause`). The
paragraph that follows remains the contract for any further subscriber.

`audit.actions` is designed as
a **stream**, not just a trail: tail it by cursor — `SELECT … WHERE id > :cursor ORDER BY id`
(indexes exist for the whole stream, one tenant's slice, and one event type's slice), persist
your cursor after processing, and you get at-least-once delivery; route on `event_type` exactly
as the outbox dispatcher routes on its own. The natural home for such a loop is the worker,
beside the outbox drain. Two honest caveats: (1) ids commit slightly out of order (each event is
its own transaction), so a strict tailer reads with a small lag (e.g. `created_at < now() -
interval '5 seconds'`) or periodically re-scans; (2) the floor is **post-commit best-effort** —
an event can in rare failure cases be missing. When a subscriber needs a transactional guarantee
("this event exists iff the action committed"), use a **domain event on the flows outbox**,
enqueued in the same transaction as the domain write (`task.requested` / `identity.check.requested`
set the pattern) — that is also why action events do NOT ride `flows.outbox`: it is a delivery
queue whose dispatcher dead-letters handler-less event types, and its transactional semantics
would be a lie for a post-response write.

**Reading it:** `audit.actions` is `service_role`-only and append-only even to it (`SELECT,
INSERT` — the `flows.domain_events` precedent); there is deliberately **no** `authenticated`
grant (withholding the privilege beats an always-false policy). **The read surface is
`GET /admin/audit/actions`** (`admin_session` + `audit:read`; RLS is deliberately not the control
here — the stream is cross-tenant evidence for Graphene operations, scoped by permission):
newest-first with a `before` keyset cursor, an `after` one for the live poll (below), server-side
filters (`tenant` uid matches the acting
OR the proxied-target side, `actor` substring, `event_type` prefix, `method`, `proxied`,
`since`/`until`), rows verbatim (redacted at write) with the actor + both tenant names joined —
each tenant side also carrying its branding icon (`tenant_icon_url`/`target_tenant_icon_url`,
extracted from `auth.tenants.theme` `client.icon_url` with the legacy flat-shape fallback;
inline `data:` icons over 64KiB are withheld whole, never truncated, so a page can't balloon) —
plus a `tenants` uid/name lookup for the filter UI. The admin portal renders it at `/audit`
(Actions tab) via `@graphene/ui`'s `ActionLog` (`describeActionEvent` humanises the event types;
proxied actions get the book-banner warning treatment, wearing the firm's icon on the acting
chip and the target firm's icon on the "Acting on {firm}" badge when branding sets one).

**The firm-facing view is `GET /firm/audit/actions`** (`firm_session` + the platform-portal
`activity:read` permission, grantable to firm roles): the same stream, query and filters
(`app/audit_stream.py` is the one implementation, minus `tenant` — the scope is hard-coded from
the caller's session to `tenant_id = firm OR target_tenant_id = firm`, so a firm sees its own
staff's actions AND ops actions taken ON it, with the operator NAMED). Not RLS: `firm_session`
provides the service_role read and the endpoint carries the tenant predicate — a lineage-scoped
policy is unnecessary because the table has no `authenticated` grant to scope. **The AML event
family (`aml.*` — the whole screening resource, plus the legacy `action.aml-` spelling) is
excluded server-side
and no filter can un-hide it** (`routers/firm.py::FIRM_EXCLUDED_EVENT_PREFIXES`, pinned by
`tests/test_firm_audit_actions.py`): a firm sees screening outcomes via `screening:read`, never
Graphene's review activity (docs/aml.md), and the audit stream must not become a side-channel
around that. The platform portal renders it at `/activity` (the same `EventStream` in its firm
posture: no firm filter, proxied rows read "Graphene support · {operator}"). The outbound-email
ledger is `/emails` and the event registry is `/activity/notifications`. `/events` and
`/event-logs` redirect to `/activity`, and `/notification-rules` to `/activity/notifications`.

**The feed is LIVE, and it offers rather than applies.** `after` is the opposite keyset end from
`before`: "what has happened since the newest row I hold", newest first. `@graphene/ui`'s
`EventStream` polls it every 20s through the app's own `more` proxy while the tab is visible
(`createPollController`, the notification bell's timer — hidden ⇒ paused, visible ⇒ an immediate
tick), holds what comes back and shows a **"N new events · Show"** pill. It never moves the table on
its own: the feed is a master-detail reader, so a background prepend would re-rank the rows under the
cursor and — with nothing selected, where the detail pane tracks the newest row — swap the event
someone is mid-way through reading. Clicking Show calls `prependNew`, which puts the new rows on top
and **keeps every "Load more" page already fetched** (the cheap alternative, re-seeding page one,
throws a reader's four loaded pages away every time anything happens in the firm). Held rows are
discarded whenever the question changes (an applied filter, a loader page landing), because they
answered the previous one. `after` composes with every filter AND with the deny list, so the count
offered is a count of rows that will actually appear — and the firm view can never _count_ an
`aml.*` event it refuses to display, which a filter-blind "how many since?" would leak as a number.
A mount that passes no `onPollNewer` does not poll at all, which is how the embedded rails (a
client's record, a document's Events tab) stay as they were: they are read in passing, not watched.

**Feed vs log — some events are recorded but not led with.** `app/audit_stream.py::ROUTINE_EVENT_TYPES`
is a set of event types kept in the raw `audit.actions` table (forensic, queryable, and read by
the trigger consumer) but left out of BOTH the admin and firm Activity logs, their filter options,
and a member's Recorded activity — the routine: a clock firing, a copy of somebody else's data
being refreshed, own notification preferences. It is a DISPLAY filter (exact event-type match,
always applied). Distinct from the firm-only AML `deny` list and from the contact gate, which are
security controls that withhold a row from a reader; here the rows are never dropped.
Business message SENDS stay in the feed (a chat send names its recipient); only the read-state does
not. Pinned to live routes by `tests/test_actions_tripwire.py` so a renamed op cannot silently
re-surface in the feed.

A realtime ticket mint (`POST /notifications/ticket`, `POST /messaging/ticket`) is not an action at
all. The handlers are marked `writes_no_row`, so the middleware writes no row — they are not on
`EXEMPT_MUTATING_ROUTES` and they are not new entries in the hide list. Rows already written
(`notification.stream_opened`, `message.stream_opened`, and the pre-rename `action.*-ws-ticket`
spellings) stay in the table and stay in the hide list, because the stream is append-only. The SSE
streams themselves (`GET /notifications/events`, `GET /messaging/events`) were never recorded: the
middleware only sees POST/PUT/PATCH/DELETE.

**Entity enrichment (write-time subjects + read-time labels).** Events are entity-rich in two
halves. At **write** time, `app/audit_actions.py::SUBJECT_FIELDS` is an explicit per-event-type
**allowlist** of id-shaped body/query fields captured into `metadata.subject` — the ONE narrow
exception to "bodies never land": ids and closed enums only (which login was linked, which
account was instructed, the instruction `kind`/`amount` — amounts are included for the
money-movement events as a deliberate judgement call), never free text and never anything
secret-shaped (`extract_subject` re-checks the redact list at runtime, and the tripwire suite
refuses an allowlist entry that names a redacted field, a stale event type or a field the route
never receives). Capture is best-effort: non-JSON/oversized bodies and missing keys just yield
less subject. A create names its subject only in the RESPONSE (the new row's id), and a multipart
upload's fields never reach `extract_subject` at all, so a second equally-narrow allowlist
`app/audit_actions.py::RESPONSE_SUBJECT_FIELDS` lifts a named id OUT of the 2xx JSON response and maps it
to the read side's own subject key — `firm-create-contact`'s response `id` → `contact_id`, so
"Created a client" names the person; `files-upload-file`'s `contact_id` likewise; and the two invite
paths (`firm-invite-member`, `admin-create-tenant-member`) lift the response `user_id` so an invite
names the login it created rather than only the role. The response is
TEE'd (every chunk still flows to the client) and bounded, so it never swallows a stream or holds a
large body, and the tripwire pins that the mapped subject key is one the reader actually resolves.
At **read** time, `GET /admin/audit/actions` batch-resolves the ids on the
returned page — from `path_params`, `metadata.subject` AND `metadata.previous`, the bare `{uid}`
param disambiguated by the event type's router tag — into `entities: [{kind, id, label}]` per
item, ONE query per entity kind per page (`_resolve_entities`; kinds: contact/account/user/
tenant/model/template/document/task/flow/form/instruction/transaction/conversation — a chat send
names the client on the channel via the 1:1 conversations→contacts join, never the message text). Resolution is read-time **on
purpose**: labels follow renames (support work wants the client's _current_ name, not a stale
snapshot), while the raw ids remain the durable record — a deleted entity resolves to a `null`
label and the UI shows the bare id. `ActionLog` (the Discord-audit-log treatment: actor-led
sentence behind a category glyph + initials disc) weaves the labels into the summary line
("Linked m.cole@example.com to Margaret Cole as authorised"), renders the expansion
CHANGES-first — one row per changed property ("Role: Adviser → Compliance"), the entity rows
(client/account rows link via the page-injected `entityHref` prop, the before-state id wearing a
quiet "previous" pill) — with the engineer plumbing (operation/query/correlation/raw) below a
divider.

**Before-values (`metadata.previous`).** `metadata.subject` answers "to what";
`app/audit_actions.py::PREVIOUS_FIELDS` answers "FROM what": an explicit registry of update-shaped
event types whose target row gets ONE cheap primary-key pre-read (own short `service_role`
transaction) **before the handler runs** — the read rides the request-body `receive` hook, which
FastAPI drains before solving dependencies, so registered routes must carry a body
(tripwire-pinned, `tests/test_actions_tripwire.py`). Registered: member portal-role changes
(firm + admin — prior `role_slug`), role-permission sets (firm + admin — prior slug list, which
is what lets the UI diff **added/removed** and show ± chips instead of a 30-slug dump; rows
older than the pre-read collapse the captured list to a count) and account-model changes (prior
`model_id`). The firm routes don't know their tenant until claims resolve, so those specs return
per-tenant candidates and `pick_previous` chooses at write time (proxied `?tenant=` target
first). Deliberate NON-entries: creations (contact-user link — no prior state) and
`set-contact-bank-account`, whose before-values come from the row diff (`CHANGED_COLUMNS`) instead,
so a named-field pre-read would be a second and narrower copy of the same fact. The
`_secret_shaped` belt filters previous columns at runtime and the tripwire refuses a secret-shaped
spec — a CREDENTIAL belt, which is not the same thing as a personal-data one. Capture is fail-quiet by contract: a
junk param, ambiguous rows or a dead DB just means no `previous`, never a blocked action. Old
rows keep their generic sentences, and that is now all they do. The UI used to caption such a row
"recorded before event enrichment", from a `SUBJECT_BEARING_EVENT_TYPES` list in pkgs/ui that had
to track which routes **require** a captured field. Removed (October 2026): it explained our own
rollout to a reader who cannot act on it, and the list was a second copy of `SUBJECT_FIELDS` kept
in another language, drifting whenever a field turned optional.

**Changed fields (`metadata.changed`) — the September 2026 event-content review.** A measurement
across the live route table found 54 event types carrying an id in their path that nothing could
resolve (the row showed a bare uid where a name belongs) and 32 more that named the record and
said nothing about the change: "Set a transfer's status" was the whole sentence, for every
transfer, to every status. Three mechanisms answer it, and which one an event gets is a judgement
about the act rather than a default.

_One:_ **more resolvers.** `admin._ENTITY_RESOLVERS` gained 23 kinds — automation, content,
contribution, transfer, dataset, document request, document type, email template, journey, case,
message group, file, storage credential, environment, beneficiary, product, fee package, catalogue
provider, notification, notification rule and the three AML ones — each with the path params
(`req_uid`, `exec_uid`, `group_uid`, `credential_uid`…) and `{uid}` resources that name them, mirrored into
`audit_stream.TAG_FIELDS` / `TAG_UID_RESOURCES` so the tag FILTER and the chips agree about the
same words. A record with no name of its own takes the two facts that identify it (a contribution
is a client and an amount). `item_uid` is deliberately absent: it names a document-request item on
one route and a journey identity item on another, and a field that means two things resolves the
wrong record half the time — which is worse than a bare uid, because a wrong name is believed.
Every resolver is EXECUTED against a real database by `tests/test_event_content.py`, because a
resolver naming a column wrong does not raise where anyone sees it — it silently matches nothing,
which is indistinguishable from the record having been deleted.

_Two:_ **more captured values.** The subject registry grew from 36 events to 67 — the closed value
each act turns on (a status, a category, a portal, a decision, a wrapper code). Free text stays the
guarded exception and is now bounded by a stated rule (`registry._text`): admitted ONLY where the
text IS the act — a document send's covering note, the reason a contribution or a journey was
cancelled, the SQL an operator ran against a firm's warehouse. A field that merely HAPPENS to be
prose (a name, a note, a description) is not admitted: the record itself holds it, and an audit row
is read by more people than the record is.

**Body capture — the doctrine changed, deliberately (`EventType.capture_body`,
`actions.BODY_CAPTURE_EVENTS`).** "Bodies are never captured" was the primary control, with
`SUBJECT_FIELDS` its one narrow exception. That held while the trail's job was "say what was done,
to which record"; it does not meet the requirement that a row carry enough to say exactly WHAT was
done. For 86 events the deciding content is free text, a form's answers or a nested object, and no
allowlist of VALUES reaches those — there are ~300 such fields, which is a list nobody maintains.
So an event may now DECLARE that its body is kept, redacted, under `metadata.body`. Three
properties keep that affordable rather than reckless: it is **declared, never blanket** (every
other route still streams its body through untouched, and the projection is frozen by
`test_events_registry` so switching it on for an event shows up in a diff a reviewer reads); it is
**redacted, always**, by `redact_deep` — the flat `redact` is right for path and query params and
wrong for a body, since a credential does not become safe by sitting one level in, and a
secret-shaped KEY masks its whole subtree rather than being descended into; and it is **bounded**,
with a body past `MAX_STORED_BODY_BYTES` stored as a marker naming its size rather than as
truncated JSON (half a document read back as though it were whole is a wrong answer told
confidently). **The cost, stated rather than left to be discovered:** a client's own data — a date
of birth, an address, a tax position — now lands in `audit.actions`, which every holder of
`activity:read` can read. That is bounded by EXISTING access rather than new (the same staff can
open the client record; an operator reaching another firm's rows already does so through
`?tenant=`), but it is wider than the rule it replaces and it was a deliberate call. An event whose
body is only credentials (`environment.secrets_updated`) is deliberately NOT declared: keeping it
would keep `[redacted]`, so the catalogue says the row cannot reproduce the call rather than
implying it can.

**What the save MOVED — before AND after (`actions.CHANGED_COLUMNS`, `metadata.changes`).** The
three captures above answer "which fields did this touch" and "what did the request ask for".
Neither answers the question a reader of an update actually has — WHAT DID IT USED TO BE — and
they cannot be made to answer it between them: a body key is a WIRE name where the target is a
COLUMN (`notes` against `vulnerability_notes`), so pairing them by name would pair the wrong
things silently, and a body states an INTENTION where the row states the OUTCOME. So 23
update-shaped events declare the ROW they address (a table, the column their path param names),
and the middleware reads that row twice — once on the body hook, which is the last moment it still
holds its pre-action state and is the timing `PREVIOUS_FIELDS` already depends on, and once after
a 2xx. What lands is the DIFFERENCE, `metadata.changes = {column: {"from": …, "to": …}}`, which
the detail panel renders as `was → now`. Four properties keep it from becoming a second copy of
the table: it is **the diff**, so a PATCH of one field records one field however wide the table is
and a save that moved nothing records nothing; it costs **two primary-key SELECTs, both
fail-quiet**, the second taken after the response is already on the wire, so the caller waits for
neither and an unreachable row simply yields no `changes`; the **redact belt runs on the column
NAME and BEFORE the comparison**, so a secret-shaped column is never even compared; and it is
**bounded**, an oversized value recorded as its shape and an overflowing tail dropped WITH A COUNT
(`_omitted`). The disclosure it adds is the OLD value of a field whose NEW value the row already
stores — every event here is already on `BODY_CAPTURE_EVENTS` — so it is the same class of data
bounded by the same access. Eligibility is exactly "addresses ONE row by ONE path param", which is
why bank details (never captured, either side), the session-scoped settings pages
(`branding.updated`, the notification and automation settings) and the two events whose row is not
pinned by one column (`firm.provider_updated`, keyed on a pair; `provider.updated`, whose `{kind}`
segment decides which table) are deliberately absent, each named in the registry's own note.

**The replay question (`tests/test_event_replayability.py`).** The gate asks, per event, whether the
row carries enough to reproduce the request: every field a caller could have sent must either be
captured or be one we have decided not to keep. 205 of 221 pass; the 16 that do not are structural
rather than policy — a `multipart` upload the JSON capture can never see, or a body that is only a
credential. The rule deciding which is which is `app/event_schema/classify.py`, read by the gate
itself so a second opinion cannot drift from it.

**An entity label names the RECORD, never its type (`routers/admin.py::_ENTITY_RESOLVERS`).**
`describe.ts::namedSubject` prints "{verb} {label}", so a label naming a type produces "Started
Onboarding" on a firm running fifty onboardings and "Created Individual Savings Account" on a book
holding four hundred — sentences that are true of the template and say nothing about what
happened. A record that BELONGS TO A CLIENT is therefore labelled with what it is AND whose it is,
in whichever of three orders reads best as a sentence: "{name} - {client}" where it has its own
name, "{definition} - {client}" where it is an instance of one (a journey, a case), and
"{client} - {what distinguishes it}" where it has no name at all (a contribution, an instruction,
a transaction, a document request). A record the FIRM authored — a model, a product, a fee
package, a template, a flow, a form, a dataset, an automation, a notification rule — is exempt,
because its name IS its identity. The two sets are declared in `tests/test_event_content.py` and
their union is asserted against the resolver map, so a resolver added later cannot skip the
decision; every client-scoped one must read `crm.contacts` or `auth.users`, and a seeded test
proves the join reaches the right row rather than merely running.

_Three:_ **`metadata.changed` — the field NAMES an update carried, never their values**
(`actions.extract_changed_keys`, declared per event as `EventType(changed_keys=True)`).
`SUBJECT_FIELDS` is an allowlist of VALUES, so it can only ever cover fields somebody thought
through one at a time — affordable for the twenty acts that turn on a closed enum, unaffordable
for the forty update routes whose body IS the change. A client PATCH declares thirty optional
fields and sends the two that moved, so "Updated a client" was the same sentence whether an email
or a date of birth changed. Recording names and never values is what makes it safe to turn on
broadly, and it is what keeps "bodies are never captured" true: no free text, no client data and
no credential can land, whatever a body holds. Two bounds guard against a CRAFTED key rather than
a realistic one — an identifier-shaped-names-only rule (every field any model here declares) and a
cap that REFUSES rather than truncates a body with more top-level keys than any form has. The
sentence names up to three ("Updated Margaret Cole: email, dob and 2 more"); the expanded detail
states them all. `client.bank_account_updated` is the shape of the judgement: WHICH fields were
rewritten is the audit fact, and the values are exactly what an audit row must not carry.

**Exemptions + the tripwire.** Exempt mutating routes fall into exactly two categories, listed
with reasons in `app/audit_actions.py::EXEMPT_MUTATING_ROUTES`. The first is the token-gated
service-to-service `/internal/*` endpoints (no bearer token, no human principal — the acting
human, where there is one, is pre-auth by definition). The second is a **POST that performs
nothing**: `POST /flows/{uid}/validate` asks "would this draft publish?" and issues only SELECTs,
borrowing the verb because a flow definition is too large for a query string. There is no effect
to record, and the flow builder calls it on a debounce while an author types, so emitting an event
per call would fill a firm's activity log with keystrokes. Such a route still authenticates
normally (this one runs on `tenant_session`) — the exemption suppresses the _event_, never the
gate — and pins its own read-only-ness by test (`tests/test_flows_validate_api.py` drives it
against a session that raises on any non-SELECT). **Known boundary:** auth-plane actions that never
reach this API (WorkOS-hosted login, MFA challenges, token refresh) emit nothing here — they
live in WorkOS's own audit surface. The guarantee is kept durable by
`tests/test_actions_tripwire.py`: it walks the live route table and fails on any mutating route
that neither carries `get_current_principal` in its dependency tree (what the middleware needs
for attribution) nor appears on the exemption list — with instructions in the failure message —
and also fails on stale exemptions and event-type collisions. A new mutating endpoint therefore
cannot land outside the guarantee without a reviewed, commented exemption.

## Migrations

The foundation migration `0001_auth_core` is in place (`auth` + `audit` schemas, RLS
helpers + `auth.resolve_claims`, the append-only audit spine), followed by
`0027_tenant_storage` (the `storage` schema + the per-tenant object-storage registry,
[`storage.md`](storage.md) §6), `0028_storage_credentials` (per-user credential
registry + secrets), `0037_aml_creditsafe` + `0038_aml_operator_credentials` (schema `aml`,
which `0225_drop_aml_schema` drops) and `0041_action_events` (`audit.actions` — the request-level action-event
stream, see "Action events" above). `migrations/env.py` connects with
`MIGRATION_DATABASE_URL` (the elevated `migrator` role).

**SQL-first layout.** The DDL lives in `migrations/sql/00NN_*.sql` (the reviewable artifact);
the matching `migrations/versions/00NN_*.py` is a thin wrapper that `exec_driver_sql`s the file
(`%` doubled for psycopg's pyformat parser). Hand-written, not autogenerated.

**Role bootstrap.** `migrations/bootstrap/01-roles.sql` (run once by a superuser, before the first
migration) creates the RLS principals (`anon`/`authenticated`/`service_role`) and the `app`/
`migrator` login roles, and grants `authenticated`/`service_role` to `app` (so the request path
can `SET ROLE`) and `service_role` to `migrator` (so 0001 can reassign the cross-RLS
`SECURITY DEFINER` helpers to the `BYPASSRLS` `service_role` — `FORCE RLS` binds the migrator
otherwise). In managed environments these are provisioned out of band.

Conventions:

- Expand/contract — every change is backwards-compatible with the running app; schema ships before the code that needs it.
- Long ops on **populated** tables use `CREATE INDEX CONCURRENTLY` + a sane `lock_timeout` (the greenfield foundation migrations use plain `CREATE INDEX` — empty tables, inside the Alembic txn).
- A migration that creates a tenant-scoped table **ships its RLS policy in the same migration**.

Create/run locally:

```
pnpm db:up                     # local Postgres 18 + RustFS in Docker; runs bootstrap/01-roles.sql on first init
moon run api:migrate              # alembic upgrade head (uses MIGRATION_DATABASE_URL)
uv run --project apps/api alembic revision -m "…"   # author a migration
```

`pnpm db:up` (`docker-compose.local.yaml` — a non-default name, so plain `docker compose` still
resolves to the prod `docker-compose.yaml`) is local dev only — production uses an external
managed Postgres, where a DBA provisions the roles (run `migrations/bootstrap/01-roles.sql` once as
a superuser) and grants. If a Postgres already holds `5432`, use `DB_PORT=5433 pnpm db:up` and
point `DATABASE_URL`/`MIGRATION_DATABASE_URL` at it.

Migrations are applied automatically in both environments: locally, `moon run api:dev` (and
`moon run :dev`) depends on `api:db-migrate`, which brings the stack up healthy (`--wait`)
and runs `alembic upgrade head` before uvicorn starts; in deployment the pipeline runs
`api:migrate` against the managed Postgres before the app build — see
[`../infrastructure/README.md`](../infrastructure/README.md). A standalone
`moon run api:migrate` still works any time the DB is up.

## Run, test, gates

```
moon run api:dev        # uvicorn --reload on :8000 (1s graceful timeout so SSE streams cannot pin a reload)
moon run api:test       # pytest
moon run api:lint api:typecheck api:format
```

`mypy` checks `app/` only (strict); `ruff` lints everything including `tests/`. Security-critical logic (`auth.py`, `db.py`) is unit-tested in `tests/` — keep that going (see the testing standard in `CLAUDE.md`). RLS isolation is proven against a **real Postgres 18** spun via `testcontainers` (`tests/conftest.py` bootstraps the roles + runs the migration; `tests/test_rls.py` asserts cross-tenant reads are blocked, a client sees only itself, empty claims see nothing, and `audit.log` is append-only). These tests **skip** (not fail) when Docker is unavailable.

## Outgoing provider calls (`audit.api_log`)

`0047_api_log` adds the append-only record of what we send custodians and what comes back —
separate from `audit.log`, which records what a _user_ did. Written from each vendor client's
single HTTP choke point so a new endpoint is audited the day it is written; CTC records under
`provider = 'morgan_lloyd'`, `service = 'ctc'`. Design and the routing it feeds:
[`provider-routing.md`](provider-routing.md).

## The `integrations` schema is gone (`0048`)

`0029` had created it as a connectivity-only schema, on the reasoning that provider enablement
was not domain data. The split earned nothing — both tables were read alongside
`platform.accounts`/`platform.platforms` on every path that used them, and two schemas each
answering "which platform" made the boundary a coin toss. Folded back:

| Was                             | Is                          |
| ------------------------------- | --------------------------- |
| `integrations.tenant_providers` | `platform.tenant_platforms` |
| `integrations.provider_sources` | `platform.providers`        |
| `provider_sources.source_id`    | `providers.external_id`     |

`external_id` is the name the same concept already carries in this schema
(`platform_links.external_id`, the conformed provider models): the id the _upstream_ knows the
thing by. The rename runs through the API contract and the admin UI too — leaving the API saying
`source_id` while the column said `external_id` would have recreated the problem it fixes.

Pure rename, no column added or retyped, and the downgrade reverses it exactly (round-tripped
against a real database).

## Non-production tenant data tools

Internal Operations can queue `POST /admin/tenants/{uid}/data-job` with
`kind = reset | reset_and_seed | seed_book` and the firm's typed alias.
`reset` and `reset_and_seed` require the tenant to already be suspended;
`seed_book` may run while it is active. `APP_STAGE=production` always refuses.
`GET .../data-job` reports the latest job; `POST .../data-job/resume-providers`
removes the operational pipeline pause after the operator has reviewed the result.

A reset removes operational CRM, book, journey, document, task, messaging, notification,
screening and tenant-lake data. It preserves the tenant, WorkOS/environment configuration,
staff, RBAC, branding, storage credentials, provider enablements/configuration, product/model
catalogues, authored flows/forms/templates/automations, the firm's sending domain and all audit
ledgers. `seed_book`
deletes only the previous `seeded_demo` clients and their accounts, transactions, valuations
and lake holdings, then writes the sample book again — real clients, documents and staff stay.
Provider refreshes remain paused deliberately: resuming them replaces demo data with the
custodian's current book. The worker executes the multi-store job and records each phase in
`platform.tenant_data_jobs`; the CLI `scripts/reset_tenant_data.py` invokes the same service
(`--seed-demo` for a full wipe-and-seed, `--seed-book` for the book only).
