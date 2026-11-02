# 1 · CRM — clients, dashboard, portfolio, KYC

Portal: **platform**. Part of the [functional audit](README.md) — statuses: ✅ working · ⚠️ partial · 🎭 demo/mock · ❌ missing · 💥 broken

---

## Dashboard (`apps/platform/src/routes/+page.svelte` / `+page.server.ts`)

### Firm KPI tiles (Clients / Onboarding / Accounts / Avg per client)

- **Status**: ⚠️ partial
- **What it does today**: `+page.server.ts:11` calls `GET /crm/overview`, which runs one real aggregate over `crm.contacts LEFT JOIN platform.accounts` on `tenant_session` (`apps/api/app/routers/crm.py:61-81`) — clients, accounts, `SUM(a.value)`, advised/exec-only counts, `clients_funded`. RLS decides row visibility (no `require_permission`; `crm.py:1-10`). "Onboarding" is not a state — it is derived client-side as `clients − clients_funded` (`+page.svelte:31`).
- **Expected**:
  - As an adviser, I want the tiles to show my book's true size so I can trust the header. Given 12 clients of whom 3 have no funded account, When I open the dashboard, Then Clients = 12 and Onboarding = 3; Given a client has only closed accounts, Then they are not counted as funded; Given the overview call fails, Then I see a retry banner, not zeros.
  - As a firm principal, I want AUM to exclude draft and closed accounts. Given an account with `status='closed'`, When AUM is computed, Then its value is excluded; Given an account with `status='draft'`, Then likewise.
- **Gaps**:
  - `SUM(a.value)` has no `status` filter — `draft` and `closed` accounts (`platform.account_status` enum, `0002_platform_crm.sql:40`) are counted in firm AUM and in every per-client AUM.
  - "Onboarding" conflates "no account at all" with "account not yet funded"; `crm.applications` / flows state is never consulted, so a client mid-journey and a dormant prospect look identical.
  - No API test exists for `/crm/overview` (nothing in `apps/api/tests` references it).
  - Failure is handled (`LoadErrorBanner`, `+page.svelte:65`) — this is the one CRM surface that does it correctly.

### AUM value card + performance series

- **Status**: 🎭 demo/mock
- **What it does today**: `crm.py:110-131` resolves the caller's Seccl external ids via `platform.links` (`_visible_seccl_accounts`, `crm.py:142`), reads a daily valuation series from the DuckDB lake (`lake.py:186` → `_SECCL_SERIES_SQL` over `seccl.seccl.asset_balances_daily`), then **rescales the whole series by `aum / latest`** so the last point equals the OLTP AUM. `gain` is then `aum − performance[0].value`, i.e. the movement over whatever window the lake happens to hold (last 120 points). The code comment is explicit: the lake's "anonymised absolute levels don't reconcile with the OLTP account values… we take only the _shape_".
- **Expected**:
  - As an adviser, I want the AUM chart and headline gain to be a real valuation history for my book. Given a client's accounts are valued daily, When I open the dashboard, Then each point is that day's actual firm AUM; Given a period selector, Then gain is the movement over the selected period; Given no valuation history, Then the card shows "no history yet" rather than a rescaled curve.
- **Gaps**:
  - The plotted values are fabricated — real _shape_, arbitrary _level_. Two clients with identical AUM but different lake histories would show different "gain" for the same book.
  - `gain`/`gainPct` are not a return: no time-weighting, no cash-flow adjustment, no book cost (the comment concedes the lake's cost basis is "bogus").
  - The window is "last 120 lake points", not a date range; there is no period selector and no as-of date shown, so the number is unauditable.
  - `lake_seccl_path` defaults to the committed 38 MB demo file `docs/seccl.duckdb` (`lake.py:47`, `config.py:111`) — in dev, this is anonymised sample data presented as the firm's performance.
  - `lake.py` has zero tests; a missing file silently yields an empty chart (fail-soft by design, but indistinguishable from "flat/no growth").

### Top clients by assets under advice

- **Status**: ✅ working
- **What it does today**: `crm.py:82-102` — real `GROUP BY c.id` with `SUM(a.value)`, plus a correlated subquery for one adviser name from `crm.client_advisers` join `auth.users`, `LIMIT 6`. Rendered as a table with an inline AUM bar (`+page.svelte:141-179`), each row linking to `/clients/{id}`.
- **Expected**:
  - As an adviser, I want my six largest clients with their adviser. Given 40 clients, When I open the dashboard, Then the 6 highest by AUM are listed descending; Given a client has a team, Then the adviser is shown; Given I lack `contacts:read_all`, Then only my book appears.
- **Gaps**:
  - "Primary adviser" is `ORDER BY u.name LIMIT 1` — alphabetical, not the `role='lead'` on `crm.client_advisers`, which is stored but ignored. Since `0245` a client has one adviser (`role = 'adviser'`). `lead` is not a role, and the name is that row.
  - Same unfiltered `SUM(a.value)` as the KPI tiles (draft/closed included).
  - Six is hard-coded; no "top 20" or sort control.

### "Needs attention" rail

- **Status**: ⚠️ partial
- **What it does today**: `+page.server.ts:14` fetches `GET /documents/requests` and filters to `status === 'pending'` (`:17`). The endpoint is real and RLS-scoped (`documents.py:771`, header SQL at `:729` joins `crm.contacts` for `contact_name`). The rail prepends a synthetic "N onboarding" row from the `clients − clients_funded` arithmetic, then lists up to 5 requests, each linking to `/documents` (not to the request).
- **Expected**:
  - As an adviser, I want one prioritised list of things blocking my clients, and to act from it. Given a pending document request due tomorrow, When I open the dashboard, Then it appears with the client's name and due date; When I click it, Then I land on that request; Given I chase or dismiss it, Then it leaves the rail; Given an overdue request, Then it sorts above a future one.
- **Gaps**:
  - `contact_name` is `crm.contacts.name` only (`documents.py:731`). The create-client action deliberately writes `name: null` for individuals (`clients/+page.server.ts:34`), so every platform-created individual renders as the literal string "Client" in the rail.
  - Requests are not sorted by priority or due date — the rail takes `.slice(0,5)` of a `created_at DESC` list, so the priority dot is decorative.
  - Links go to the `/documents` index, losing the row; no deep link, no chase/dismiss/snooze action.
  - Open tasks (`/tasks`) and stalled flows/journeys are not folded in, despite both having endpoints — the "needs attention" rail attends to document requests only.
  - `requestsR` failure is silently swallowed into `[]` (`:17`), so an outage renders "All clear".

### "New client" CTA

- **Status**: ✅ working
- **What it does today**: `+page.svelte:60` links to `/clients?new=1`; the list page reads that param to open the create panel (`clients/+page.svelte:36`).
- **Expected**: As an adviser, When I click New client on the dashboard, Then the clients page opens with the create form expanded and focused.
- **Gaps**: No autofocus on the first field; the `?new=1` param persists in the URL after the panel is closed.

---

## Clients list (`apps/platform/src/routes/clients/`)

### List load

- **Status**: ⚠️ partial
- **What it does today**: `clients/+page.server.ts:11` does a bare `fetch` of `GET /crm/contacts` and `res.ok ? await res.json() : []`. The endpoint (`crm.py:24-54`) is a real aggregate returning portfolio value, account count and an `array_agg` of adviser names per contact, ordered by surname, `WHERE c.deleted_at IS NULL`, RLS-scoped.
- **Expected**:
  - As an adviser, I want my whole book listed with AUM, adviser and status. Given 500 clients, When I open the page, Then the first page renders quickly; Given the API is down, Then I see an error with retry, not an empty book.
- **Gaps**:
  - `res.ok ? json : []` is precisely the anti-pattern `fetchJson` was written to eliminate (`$lib/api.ts:33-38` documents this) — a 500 renders the "No clients yet · Add your first client to start building your book" empty state to an adviser with a full book.
  - No server-side pagination, limit or count: `/crm/contacts` returns every visible row with a `GROUP BY` over all accounts on every page load.
  - No test coverage at any layer.

### Search, segment and sort

- **Status**: ⚠️ partial
- **What it does today**: All three are pure client-side `$derived` over the already-loaded array (`clients/+page.svelte:72-89`): substring match on name/email/adviser, segment predicate `inSegment` (`:64`), and a three-way sort. Nothing reaches the API.
- **Expected**:
  - As an adviser, I want to find a client by name, email or reference and filter by segment. Given I type "cald", Then matching clients appear; Given 5,000 clients, Then search stays responsive; Given I search a phone number or client reference (`uid`), Then the client is found; Given I refresh, Then my filter is preserved.
- **Gaps**:
  - Search does not cover `uid`, phone, postcode or account number — the fields staff actually quote on a call.
  - Filter/sort state is not in the URL, so it is lost on refresh and cannot be shared.
  - `/crm/contacts` accepts no `q`, `segment`, `sort`, `limit` or `offset` params — the endpoint would need extending before any of this can scale.
  - "Onboarding" segment = `account_count === 0`; "Active" = `account_count > 0 && enabled` — same conflation as the dashboard tile, and no "Inactive" segment despite the badge existing (`:93`).

### Create client

- **Status**: ⚠️ partial
- **What it does today**: `?/createClient` (`clients/+page.server.ts:17`) re-checks `contacts:write`, requires a surname (individual) or name (organisation), then `POST /firm/contacts` → `firm.py:380` (`require_permission(CONTACTS_WRITE)`) → `contacts.py:70` which really does `INSERT INTO crm.contacts` with a generated uid, tenant-scoped, audited by the 0002 DML triggers. The row persists.
- **Expected**:
  - As an adviser, I want to add a client and land on their record. Given I enter "Theo Caldwell", When I submit, Then a `crm.contacts` row exists and I am taken to `/clients/{id}`; Given I am scope-limited, Then I can still see the client I just created; Given the email already belongs to a client, Then I am warned; Given a malformed email, Then submission is rejected.
- **Gaps**:
  - **No adviser self-assignment.** Nothing writes `crm.client_advisers`. `crm.contacts` RLS requires `can_read_all() OR id = ANY(auth.contact_scope())` (`0002:457`) and `contact_scope` derives from `contact_users`/`client_advisers` (`0002:382-386`). A book-scoped adviser without `contacts:read_all` creates a client and it is **immediately invisible to them** — the form succeeds, the toast fires, the client vanishes. Untested.
  - No redirect to the new record: the action returns `{ok:true}` and the panel just closes.
  - API-side validation is far weaker than the UI's: `CreateContact` (`firm.py:77-87`) has no `min_length` on names and types `email` as bare `str | None`. A direct `POST /firm/contacts {}` creates a nameless, emailless contact.
  - No duplicate detection on email/name.
  - `name` is forced to `null` for individuals (`:34`), which breaks every downstream consumer that reads `crm.contacts.name` directly — including the dashboard attention rail and `documents.requests.contact_name`.
  - `sub_type`, `dob` and `nationality` exist on the table (and `sub_type` on the API model) but have no UI.

---

## Client record (`apps/platform/src/routes/clients/[id]/`)

### Record load

- **Status**: ✅ working
- **What it does today**: `[id]/+page.server.ts:61` fetches `GET /crm/contacts/{id}` → `crm.py:472`, which returns the contact, advisers (`crm.client_advisers`), linked logins (`crm.contact_users`), accounts (`platform.accounts` + product + model) and notes (`crm.contact_notes`) in five real RLS-scoped queries. A 404 is mapped to a proper SvelteKit `error(404)` (`:62`), 5xx to `error(status)` (`:63`) — fail-closed and correct.
- **Expected**: As an adviser, Given a client id I am entitled to see, Then the record loads with advisers, logins, accounts and notes; Given an id outside my scope, Then I get 404, not 403 (no existence leak).
- **Gaps**: The six secondary fetches on the same load (`members`, docs/files/library/activity, holdings, transactions, instructions, tasks) all use `res.ok ? json : []` (`:72, :86-91, :110-111, :189, :196`) — every one degrades silently to an empty tab. No API test for `/crm/contacts/{id}`.

### Overview: details form (save)

- **Status**: ⚠️ partial
- **What it does today**: `?/updateClient` (`:219`) builds an address object from five inputs, sends `null` when every part is blank, and `PATCH /firm/contacts/{id}` → `firm.py:391` → `contacts.py:119`. The update is real: `_UPDATABLE` whitelist, jsonb cast for `address`, tenant-scoped `UPDATE … RETURNING`, `LookupError` → 404. Persistence is genuine and covered by `tests/test_contacts.py:57`.
- **Expected**:
  - As an adviser, I want to correct a client's details, including removing stale ones. Given I clear the phone number and save, Then `phone` is NULL in `crm.contacts`; Given I clear all address lines, Then `address` is NULL; Given I enter "not-an-email", Then I get a field error; Given I save nothing, Then no write occurs.
- **Gaps**:
  - **Fields cannot be cleared.** The action converts blanks to `null` (`:222-241`), and `contacts.py:126` filters `if … and v is not None`. Clearing a phone, email, title or the whole address is a silent no-op — the toast says "Client updated", the value is unchanged. This also defeats the action's own "send null when every part is blank so an empty form clears it" comment.
  - No email/phone format validation on either side (`UpdateContact.email: str | None`, `firm.py:98`).
  - `enabled` and `sub_type` are accepted by the API (`firm.py:100`, `:92`) but have no control — no way to deactivate a client (the "Inactive" badge can never be produced from this UI).
  - `middle_name` is whitelisted in `_UPDATABLE` but absent from the form.
  - The non-`canManage` fallback shows only email and phone (`+page.svelte:599-608`) — a read-only adviser cannot see the address they can't edit.
  - No optimistic-concurrency check; last write wins with no `updated_at` comparison.

### Money movement 1 — Record expected money in (`expected_in`)

- **Status**: ⚠️ partial (real row, zero downstream effect)
- **What it does today**: The header button opens a dialog (`+page.svelte:321-327`, `:993`) posting `?/createInstruction` (`:308`) → `POST /firm/contacts/{id}/instructions` → `firm.py:513` → `instructions.py:21`, which validates the kind, `amount > 0`, resolves the account within the caller's tenant and `INSERT`s into `platform.instructions` (`:68-98`, table at `0016_platform_instructions.sql:11`) with `status='pending'`, `created_by` resolved from the verified WorkOS id, and an audit-log row via `audit.attach`. The module docstring states it plainly: "There is NO provider ledger behind this: a row records intent + status only."
- **Expected**:
  - As an adviser, I want to log an expected contribution so the firm can reconcile it on arrival. Given I record £10,000 due 30 Sep, When cash of £10,000 lands on that account, Then the instruction reconciles and moves to settled; Given the date passes with no cash, Then it shows as overdue and raises a task; Given I record it, Then the client's expected-in total reflects it.
  - As an operations user, I want an aged expected-in report across the firm. Given three pending expectations, Then I can list them by due date with client and account.
- **Gaps**:
  - Nothing ever writes `status='settled'` — `grep` across the whole repo finds no writer. There is no reconciliation, no matcher against lake transactions, no worker, no cron.
  - `due_date` is unvalidated: passed through as a raw string (`+page.server.ts:323`, `firm.py:498` types it `str | None`), cast in SQL. A past date is accepted; a malformed one raises a DB cast error surfaced as a 500.
  - No firm-wide instructions view — the only surface is the per-client "Recent instructions" card, unbounded and unpaginated (`+page.svelte:613-668`).
  - No task, no notification, no document request is raised alongside it.
  - `instructions.py` has zero tests (no test file references it).

### Money movement 2 — Withdraw cash (`withdrawal`)

- **Status**: ⚠️ partial (real row, wrong balance basis, zero downstream effect)
- **What it does today**: Same chain. Additionally `instructions.py:57-62` checks `amount > acct["value"]` and raises `ValueError` → 400 with "Insufficient funds — only £X is available to withdraw." The code comment calls `accounts.value` "the demo's available balance".
- **Expected**:
  - As an adviser, I want to raise a withdrawal that can actually be paid. Given the account holds £2,000 cash inside a £50,000 portfolio, When I request £10,000, Then I am told cash is insufficient and offered a disinvestment; Given I request £1,500, Then it is accepted and the payable cash is reserved; Given it is paid, Then status becomes settled and a transaction appears.
- **Gaps**:
  - The balance check uses **total account value, not cash**. The same page computes a lake-derived `cash` figure for its hero metric (`+page.server.ts:176`, `+page.svelte:340`), so the screen displays one available balance and validates against another. A withdrawal of the full portfolio value passes validation.
  - Values validated against are a stale batch snapshot (see Accounts tab) — the check can pass or fail on months-old data.
  - The dialog shows no available balance and no client-side max (`+page.svelte:1026-1035`, `min="0"` only), so insufficiency is discovered only on submit.
  - No payment instruction, no bank-detail capture, no four-eyes approval, no anti-fraud hold on a cash-out — for a money-movement control this is the most serious omission.
  - Never settles; no payment run (`docs/data-architecture.md:162` describes the intended `payment_runs` layer as unbuilt).

### Money movement 3 — Top up model (`model_topup`)

- **Status**: 🎭 demo/mock
- **What it does today**: Same chain, plus `instructions.py:63-66`: `model_id = model_id or acct["model_id"]`, error if still null. The dialog filters the account picker to accounts with `model_id != null` (`+page.svelte:151-155`) and shows "None of this client's accounts are linked to a model yet" otherwise. Crucially, **`model_id` on `platform.accounts` is only ever written by a dev demo seed** — `scripts/seed_advice.py:167` assigns models by `mod(contact_id, 3)`; neither the ingest (`ingest_seccl.py:182`) nor the applications path (`applications.py:250`) sets it, and there is no model-assignment endpoint or UI anywhere.
- **Expected**:
  - As a discretionary manager, I want to invest new cash into an account's model. Given the account is on the Balanced model with £5,000 cash, When I top up £5,000, Then per-asset buy orders are generated to model weights; Given the account has no model, Then I can assign one from this screen; Given I top up, Then the instruction settles when the trades execute.
- **Gaps**:
  - Only reachable because a demo script assigned models arbitrarily; in a real tenant the two model buttons are dead UI that always shows the "not linked to a model" dead end.
  - No model assignment/change UI or endpoint — the prerequisite for the feature does not exist in the product.
  - The dialog never lets you pick a model even though the API accepts `model_id` (`firm.py:497`): missing UI for an existing parameter.
  - No weights, no order generation, no rebalance, no drift check — a top-up produces one `amount` row with no relationship to the model's composition.
  - No cash check at all on the inflow side, and no settlement.

### Money movement 4 — Withdraw from model (`model_withdrawal`)

- **Status**: 🎭 demo/mock
- **What it does today**: Identical to `model_topup` (both are in `_MODEL`, `instructions.py:18`) plus the `_WITHDRAWALS` balance check against total account value (`:17`, `:57`).
- **Expected**:
  - As a discretionary manager, I want to disinvest from a model back to cash. Given £20,000 invested in the Growth model, When I withdraw £5,000, Then pro-rata sell orders are raised; Given I request more than is invested, Then it is rejected; Given settlement, Then cash rises and holdings fall.
- **Gaps**: All of the `model_topup` gaps, plus: the "insufficient funds" test compares against total account value rather than the model's invested amount, so it neither prevents over-disinvestment nor reflects what is actually held.

### Cancel instruction

- **Status**: ✅ working
- **What it does today**: The row button arms `cancelUid` and opens a `ConfirmDialog`, which submits a single hidden form (`+page.svelte:645-656`, `:974-991`) → `?/cancelInstruction` (`+page.server.ts:335`) → `POST /firm/instructions/{uid}/cancel` → `firm.py:538` → `instructions.py:127`, a tenant-scoped `UPDATE … SET status='cancelled' WHERE status='pending' RETURNING uid`. No row cancelled → 404 "No pending instruction with that id." Genuinely persists, genuinely audited, correctly idempotent-safe.
- **Expected**: As an adviser, Given a pending instruction, When I confirm cancellation, Then status is `cancelled` and the Cancel control disappears; Given it was already cancelled or settled, Then I get a clear message; Given another firm's uid, Then 404.
- **Gaps**: No cancellation reason captured; no test; the confirm dialog copy says "This can't be undone" but there is no re-instate path (accurate, if terse).

### Accounts tab

- **Status**: ⚠️ partial
- **What it does today**: Pure render of `data.detail.accounts` from `crm.py:523-540` — real `platform.accounts` rows joined to `platform.products` and `platform.models`. Values originate from `scripts/ingest_seccl.py:172`, which copies the lake's `accounts.current_value` into `platform.accounts.value`; `product_id`/`model_id`/`risk_level` come from `scripts/seed_advice.py:160-171`. **No poller or job refreshes any of this** — `app/poller.py` only drains the event queue.
- **Expected**:
  - As an adviser, I want each account with an as-of-dated value and a way in. Given four accounts, When I open the tab, Then each shows product, status, value and valuation date; When I click one, Then I see the account; Given a value older than a day, Then I am told it is stale.
- **Gaps**:
  - Account values are a **manual CLI snapshot with no refresh mechanism** — the figure driving AUM, the hero panel, the client list, the dashboard and the withdrawal balance check is whenever someone last ran `ingest_seccl.py`. No as-of date is stored or shown.
  - No drill-through: `GET /crm/accounts/{id}` exists (`crm.py:212`) and the client portal builds `/accounts/{id}` hrefs (`crm.py:387`), but the platform has no account route — missing UI for an existing endpoint.
  - No account open/close/edit, no product or model assignment, no fee package, no risk level shown — all columns exist on the table.
  - Currency, `opened_at`, `custody` and `risk_level` are returned by the API and not rendered.

### Holdings tab

- **Status**: 🎭 demo/mock
- **What it does today**: `+page.server.ts:107` calls `GET /crm/holdings?contact_id=…` → `crm.py:182`, which RLS-resolves Seccl external ids and reads the latest daily snapshot per asset from the lake (`lake.py:121`, `_SECCL_HOLDINGS_SQL` over `seccl.seccl.asset_performance_daily`). The loader then aggregates by ISIN and **rescales units and values by `clientAum / lakeTotal`** (`:139-154`), and clamps any return whose magnitude exceeds 1000% to zero (`:151`) because "the lake book cost is unreliable (near-zero basis → absurd %)".
- **Expected**:
  - As an adviser, I want the client's actual holdings with units, price, value and return. Given the client holds 1,200 units of a fund, Then 1,200 units are shown; Given a valuation date, Then it is displayed; Given return cannot be computed, Then it shows "—", not 0.00%.
- **Gaps**:
  - Units and values are **scaled fabrications** — the rescale factor is applied to unit counts, so the numbers correspond to no real position. An adviser could quote them to a client.
  - Returns are clamped to flat 0% when the basis is implausible, which is presented identically to a genuine 0% return.
  - No as-of date surfaced despite `as_of` being selected by the lake query (`lake.py:105`).
  - No per-account breakdown (`showWrapper={false}`, `+page.svelte:729`) and no way to see which account holds what, though `account_id`/`account_name` are tagged server-side (`crm.py:171-179`).
  - Empty state ("Holdings appear here once this client's accounts are invested") is also what you see when the lake file is absent or the fetch 500s — three different conditions, one message.

### Transactions tab

- **Status**: ⚠️ partial
- **What it does today**: `+page.server.ts:108` → `GET /crm/transactions?contact_id=…&limit=40` → `crm.py:197` → `lake.py:158` (`seccl.seccl.transactions`, most recent first, clamped 1–500). The loader derives an icon type and a **signed** amount by keyword-sniffing `movement_type/type/sub_type` (`activityType`/`signedAmount`, `:33-55`) — logic duplicated verbatim in Python (`crm.py:251-274`).
- **Expected**:
  - As an adviser, I want the client's transaction history, filterable and paginated. Given 500 transactions, When I open the tab, Then I see the most recent 40 with a "load more"; Given I filter to withdrawals in Q2, Then only those show; Given a transaction, Then I see which account it belongs to and its settlement status.
- **Gaps**:
  - Direction is inferred from substring matching on free-text provider fields ("out", "debit", "charge") — a fund named e.g. "Charge…" or an unmatched `movement_type` silently flips the sign. Two independent copies of this logic must be kept in step.
  - Hard-coded 40 rows, no pagination, no date/type/account filter, no export.
  - `account_name` is mapped into `wrapper` (`:182`) but `ActivityList` is given no account column context.
  - `platform.transactions` — a real, RLS-protected table with its own policy (`0002:537`) — is **read by no Python code in the repo**. The OLTP transaction fact is dead schema; everything comes from the lake.
  - No tests at any layer.

### People tab — advisers (assign / unassign)

- **Status**: ✅ working
- **What it does today**: `?/assignAdviser` (`+page.server.ts:252`) → `POST /firm/contacts/{id}/advisers` → `firm.py:405` → `contacts.py:165`, which verifies both contact and adviser belong to the caller's tenant and upserts `crm.client_advisers` with `ON CONFLICT (contact_id, adviser_id) DO UPDATE SET role` (re-roling, not erroring). `?/unassignAdviser` (`:266`) → `DELETE …/advisers/{adviser_id}` → `contacts.py:197`, a tenant-scoped `DELETE … RETURNING id` with `LookupError` → 404. Both genuinely write, and both are covered by `tests/test_contacts.py:57,101` including cross-tenant refusal. Because `auth.resolve_claims` runs per request in the DB (`db.py:76`), an assignment changes visibility immediately.
- **Expected**:
  - As a firm principal, I want to assign an adviser to a client's book. Given I assign Andy as the adviser, Then `crm.client_advisers` holds `(contact, andy, 'adviser')` and Andy can see the client; Given I re-assign his role, Then the role updates without a duplicate; Given I remove the last adviser, Then I am warned the client becomes unassigned.
- **Gaps**:
  - `role` is a free-text `Input` with placeholder "lead" (`+page.svelte:815-820`) — no enumeration, so `role='lead'` can never be relied on (and indeed the dashboard ignores it). Since `0245` the role is a closed list (`adviser`, `paraplanner`, `administrator`, `support`), with at most one adviser per client.
  - Unassigning the last adviser is silently allowed, orphaning the client from every scoped adviser's view with no warning.
  - The pickers depend on `GET /firm/members`, which requires `members:read` (`firm.py:124`) while the action itself requires only `contacts:write`. An adviser with `contacts:write` but not `members:read` gets `members = []` (`+page.server.ts:72`) and the assign/link forms **silently disappear** — no explanation.
  - `adviser_id` is typed `str` (`firm.py:105`), not UUID: a non-UUID reaches `WHERE id = :u` against a `uuid` column and raises a DB error surfaced as a 500 rather than a 400/404.
  - No adviser-side surface: no "my book" view, no bulk reassignment when a member leaves.

### People tab — client logins (link / unlink)

- **Status**: ✅ working
- **What it does today**: `?/linkUser` (`+page.server.ts:278`) → `POST /firm/contacts/{id}/users` → `firm.py:437` → `contacts.py:215`, upserting `crm.contact_users` with a `capacity` (`self`/`authorised`/`power_of_attorney`/`delegate`, `firm.py:113-115`), both sides tenant-verified. `?/unlinkUser` → `contacts.py:280`. Real writes, tested (`test_contacts.py`). This is the row that grants the client-portal login sight of the party, via `resolve_claims` → `contact_scope` (`0002:383`).
- **Expected**:
  - As an adviser, I want to give a client access to their own portal. Given the client has no login, When I invite them by email, Then a client-portal login is created and linked as `self`; Given a spouse, Then I link them as `authorised`; Given I unlink, Then their portal access to that party stops immediately.
- **Gaps**:
  - **No invite path from the client record.** The picker lists only members who already hold a `client` portal grant (`+page.svelte:269`). Creating the login requires leaving for `/team` and inviting via `POST /firm/members` — the single most common CRM action (onboard a client's access) is split across two unlinked screens.
  - Unlinking revokes portal visibility with no confirmation dialog and no warning about the consequence, unlike the instruction cancel which has one.
  - `capacity` is stored but never enforced anywhere — `resolve_claims` treats `authorised`, `power_of_attorney` and `delegate` identically to `self` (`0002:383`), so a delegate gets the same scope as the client. Same 500-on-malformed-uuid issue as advisers.

### Notes tab

- **Status**: ⚠️ partial
- **What it does today**: `?/addNote` (`+page.server.ts:347`) requires a non-empty body, then `POST /firm/contacts/{id}/notes` → `firm.py:455` → `contacts.py:246`, which verifies the contact is in-tenant, resolves `author_id` from the caller's verified WorkOS id, and inserts into `crm.contact_notes`. Reads come back through `crm.py:541-554`. RLS is real and, importantly, **verified**: `contact_notes_read` (`0002:486`) requires tenant lineage, and `tests/test_rls_crm.py:218` asserts a client-portal session sees zero notes.
- **Expected**:
  - As an adviser, I want a running internal note trail on a client. Given I write a note, Then it persists with my name and timestamp; Given I made a typo, Then I can edit or delete it within a window; Given 200 notes, Then they paginate; Given the client logs in, Then they cannot see any of it.
- **Gaps**:
  - No edit and no delete — `POST` is the only note endpoint. A note with a client's name misspelled, or written on the wrong record, is permanent.
  - Every note is loaded with the record, unbounded and unpaginated (`crm.py:549`, `ORDER BY created_at DESC` with no `LIMIT`).
  - No categories, pinning, mentions or attachments; no note against an account or an instruction.
  - `contacts.py:258` resolves the author with `WHERE workos_user_id = :w` and **no tenant filter** — safe today only because that column is globally `UNIQUE` (`0001:70`); it breaks the "every statement is explicitly tenant-scoped" discipline the module docstring claims.
  - A staff login that is also linked as a client of its own firm would satisfy `contact_id = ANY(contact_scope())` with a non-empty lineage and could read notes about itself — narrow, but the policy comment ("a client never reads notes about themselves") is stronger than the policy.

### Activity fold-in

- **Status**: ✅ working
- **What it does today**: `+page.server.ts:84` fetches `GET /documents/activity?limit=100&contact_id=…`, scoped **server-side** to documents linked to this contact via `document_contacts` (the comment at `:89` notes this deliberately avoids a client-side slice that could leak or miss). Rendered by `lib/components/clients/client-activity.svelte` with type filters and a show-all toggle.
- **Expected**: As an adviser, Given documents were sent, viewed and signed, When I open Activity, Then I see each event with actor and time, filterable by type; Given the client viewed a document, Then I see when.
- **Gaps**: Document activity only — instructions, notes, adviser changes and detail edits all land in `audit.log` but no CRM screen surfaces them, so there is no single client audit trail. Hard-coded `limit=100`, no paging. Tab (and Documents tab) is hidden entirely without `documents:read`, with no explanation.

### Documents fold-in

- **Status**: ⚠️ partial
- **What it does today**: `+page.server.ts:80-91` fetches the client's documents, files, the firm library and activity. `client-documents.svelte` renders preview/download/archive/delete plus send and upload dialogs. Archive posts to `?/archive` (`+page.server.ts:363`, correctly gated on `documents:write` rather than `contacts:write`) → `POST /documents/{uid}/archive`; file delete posts to `/files?/delete`.
- **Expected**: As an adviser, Given a superseded document, When I archive it, Then it disappears from the client's list; Given the archive fails, Then I am told; Given I delete a client's uploaded ID, Then I confirm first.
- **Gaps**:
  - `archive()` and `deleteFile()` (`client-documents.svelte:141-155`) **fire-and-forget**: `await fetch(...)` then `invalidateAll()` with the response never inspected. A 403 or 500 produces no toast and no error — the row simply stays put and the user assumes a UI glitch. This is the one mutation path on the record that bypasses the page's own `withToast` discipline.
  - No confirmation dialog before deleting a client-supplied file, although `ConfirmDialog` is already imported and used for instruction cancellation.
  - Four independent fetches on every record load, including the **entire firm document library** (`:83`, unfiltered `GET /documents`), just to populate a send dialog.

### Tasks card (Action items)

- **Status**: ⚠️ partial
- **What it does today**: `+page.server.ts:195` fetches `GET /tasks?assignee=contact:{id}` (real filter, `routers/tasks.py:167`, `require_permission(TASKS_READ)`, RLS-scoped) and renders open ones with an overdue dot in the Overview rail (`+page.svelte:369-401`).
- **Expected**: As an adviser, Given a client has two outstanding items, Then I see them with due dates; When I raise a new task on this client, Then it appears here; When one is done, Then I can complete it inline.
- **Gaps**:
  - Read-only: no create, complete, reassign or open action, though `POST /tasks` and the status transitions all exist (`tasks.py:199`, `service.py:205`) — missing UI for existing endpoints.
  - Shows only tasks assigned **to the contact**; tasks assigned to the adviser _about_ this client (`subject_type`/`subject_id`, supported at `tasks.py:145`) are not shown, so the card misses the adviser's own workload.
  - The card vanishes entirely when there are no open tasks — no "no action items" state, so the absence of the card is ambiguous.

### Hero metrics + allocation donut (AUM / Cash / Advisers / Models)

- **Status**: 🎭 demo/mock
- **What it does today**: AUM is summed client-side from `detail.accounts` (`+page.svelte:78`) — the stale ingest snapshot. `Cash` and the donut come from bucketing lake holdings by **regex on asset type and fund name** (`+page.server.ts:158-176`, mirrored in `crm.py:282-294`), then multiplying by the same AUM/lake rescale factor. `Models` counts distinct `model_name` from the demo seed.
- **Expected**: As an adviser, Given the client holds a money-market fund, Then Cash reflects the actual cash balance; Given asset classes, Then the donut uses the instrument's real classification; Given I hover a segment, Then I see the value.
- **Gaps**:
  - "Cash" is not a cash balance — it is the rescaled value of holdings whose name matches `/cash|money market|liquidity/`. The withdrawal validator, meanwhile, uses total account value, so the screen's own two "available money" figures disagree.
  - Asset classification is keyword guessing over anonymised fund names, acknowledged in both comments as a workaround for the lake's coarse `asset_type`; the same regex is maintained twice, in TypeScript and Python.
  - Donut is capped at six segments with no "Other" bucket, so percentages need not sum to 100.
  - "Models" counts names from a dev seed script.

### Deactivate / archive a client

- **Status**: ❌ missing
- **What it does today**: Nothing. `crm.contacts.deleted_at` is filtered on read (`crm.py:484`, `crm.py:45`) but **no code path ever sets it** — the only `deleted_at` write in the API is on `documents.attachments` (`files.py:302`). `enabled` is accepted by `PATCH /firm/contacts/{id}` (`firm.py:100`) but has no control, and there is no `DELETE /firm/contacts/{id}`.
- **Expected**:
  - As a firm principal, I want to retire a client record without destroying history. Given a client has left, When I archive them, Then `deleted_at` is set, they leave the list and AUM, and their documents remain for retention; Given I archive a client with open instructions, Then I am blocked or warned; Given an archived client, Then I can restore them.
  - As a compliance officer, I want the "Inactive" state to be reachable. Given I disable a client, Then the record shows Inactive and no new instructions can be raised against it.
- **Gaps**:
  - No archive endpoint, no soft-delete write, no restore, no UI.
  - The `enabled=false` state is rendered in three places (list badge `clients/+page.svelte:93`, record badge `+page.svelte:307`, the "Active" segment filter) but is unreachable from the product.
  - Nothing prevents raising instructions against a disabled client — `instructions.py` never checks `contacts.enabled`.

---

## Domain summary

| Function                                  | Status       |
| ----------------------------------------- | ------------ |
| Dashboard — firm KPI tiles                | ⚠️ partial   |
| Dashboard — AUM card + performance series | 🎭 demo/mock |
| Dashboard — top clients by AUM            | ✅ working   |
| Dashboard — "Needs attention" rail        | ⚠️ partial   |
| Dashboard — clients by service            | ✅ working   |
| Dashboard — "New client" CTA              | ✅ working   |
| Clients list — load                       | ⚠️ partial   |
| Clients list — search / segment / sort    | ⚠️ partial   |
| Clients list — create client              | ⚠️ partial   |
| Client record — load                      | ✅ working   |
| Client record — details form (save)       | ⚠️ partial   |
| Instruction — expected money in           | ⚠️ partial   |
| Instruction — cash withdrawal             | ⚠️ partial   |
| Instruction — model top-up                | 🎭 demo/mock |
| Instruction — model withdrawal            | 🎭 demo/mock |
| Instruction — cancel                      | ✅ working   |
| Accounts tab                              | ⚠️ partial   |
| Holdings tab                              | 🎭 demo/mock |
| Transactions tab                          | ⚠️ partial   |
| People — advisers assign / unassign       | ✅ working   |
| People — client logins link / unlink      | ✅ working   |
| Notes tab                                 | ⚠️ partial   |
| Activity fold-in                          | ✅ working   |
| Documents fold-in                         | ⚠️ partial   |
| Tasks card                                | ⚠️ partial   |
| Hero metrics + allocation donut           | 🎭 demo/mock |
| Deactivate / archive a client             | ❌ missing   |

### The five most severe gaps, in priority order

1. **The four money-movement actions have no downstream effect whatsoever.** `platform.instructions` rows persist and are audited, but nothing settles them (`status='settled'` is written nowhere in the repo), no provider call is made, no ledger posts, no task or notification is raised, and no firm-wide instruction queue exists. Worse, the cash-withdrawal control validates against **total account value rather than cash** (`apps/api/app/instructions.py:57-62`) while the same screen displays a different, lake-derived "Cash" figure — and there is no approval step, bank-detail capture or four-eyes control on a cash-out. These are the highest-risk buttons in the product and they are, per their own docstrings, "demo-grade".

2. **Every money figure on every CRM screen is either a stale manual snapshot or a rescaled fabrication.** `platform.accounts.value` is copied from the lake by a hand-run CLI script (`apps/api/scripts/ingest_seccl.py:172`) with no refresh job, no as-of date and no staleness indicator. The dashboard performance series takes only the lake's _shape_ and rescales it so the last point equals that snapshot (`apps/api/app/routers/crm.py:110-131`); the holdings tab rescales **unit counts and values** by the same factor and clamps implausible returns to 0% (`apps/platform/src/routes/clients/[id]/+page.server.ts:139-154`). Advisers can quote these numbers to clients. AUM also silently includes `draft` and `closed` accounts.

3. **Creating a client can hide it from its creator.** `?/createClient` never writes `crm.client_advisers`, and `crm.contacts` RLS admits a row only via `contacts:read_all` or `auth.contact_scope()` (`migrations/sql/0002_platform_crm.sql:457`, `:382-386`). A book-scoped adviser therefore creates a client, sees a success toast, and the client is gone. Compounding it: the surname requirement lives only in the SvelteKit action, not in `CreateContact` (`apps/api/app/routers/firm.py:77-87`), there is no duplicate check, and no test covers the scoped-adviser create path.

4. **Silent failure is the default across the domain.** The clients list and all nine secondary fetches on the client record use `res.ok ? await res.json() : []` (`apps/platform/src/routes/clients/+page.server.ts:12`; `clients/[id]/+page.server.ts:72, 86-91, 110-111, 189, 196`), so an API outage renders "No clients yet — add your first client" and empty tabs. Document archive and file delete ignore their responses entirely (`lib/components/clients/client-documents.svelte:141-155`). Separately, the details form **cannot clear any field** — blanks become `null` and `null` is filtered out of the SET clause (`apps/api/app/contacts.py:126`) — yet it reports "Client updated".

5. **The CRM has effectively no test coverage, and several capabilities are half-built.** No API test touches `/crm/overview`, `/crm/contacts`, `/crm/contacts/{id}`, `/crm/holdings` or `/crm/transactions`; `app/instructions.py` and `app/lake.py` have no tests at all; there is no e2e coverage of the clients screens. Only `app/contacts.py` (write path) and the CRM RLS policies are genuinely tested. Alongside this: no archive/deactivate path exists (`deleted_at` is read but never written); `GET /crm/accounts/{id}` has no platform route; `platform.transactions` is dead schema read by no code; models and products are assigned only by `scripts/seed_advice.py:167`, which is what makes the two model actions appear to work; `crm.contact_users.capacity` is stored but never enforced in `resolve_claims`; and `/crm/contacts` has no pagination or server-side search.
