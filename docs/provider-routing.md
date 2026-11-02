# Provider routing and the outgoing-call audit

Two related problems, deliberately kept apart:

1. **Routing** — a domain action ("create this transfer") has to become the right calls to the
   right custodians, and which custodian depends on the tenant's configuration, not on the code
   path the adviser happened to take.
2. **Evidence** — every one of those calls has to leave a durable record. An FCA-regulated firm
   asked "what did you send Seccl on 3 March, and what came back" must be able to answer without
   reading application logs.

Nothing here is speculative plumbing: transfers needs it now, and onboarding (clients, accounts,
models, fees) and payments/withdrawals need the same shape immediately after. Building it once
per feature is how three incompatible versions happen.

## 1. Where the seam goes

The three vendor clients already have exactly one HTTP choke point each, which is what makes
this tractable:

| Provider     | Choke point                                                      |
| ------------ | ---------------------------------------------------------------- |
| Morgan Lloyd | `pkgs/morgan-lloyd/.../_base.py::_request`                       |
| Seccl        | `services/seccl/.../client.py::_request`                         |
| SEI          | `pkgs/sei/.../_base.py::request`                                 |
| CTC          | `apps/api/app/integrations/ctc/client.py` — its own `httpx` call |

**CTC records under Morgan Lloyd.** It is reached as part of the ML illustration flow and a firm
thinks of it as "Morgan Lloyd produced my illustration", not as a fourth counterparty. It gets
`provider = 'morgan_lloyd'` with `service = 'ctc'` so it is still separable.

Instrumenting the choke point rather than each endpoint method means a new endpoint is audited
the day it is written, with no chance of someone forgetting. It also means the recorder sees the
request and response verbatim, before any envelope unwrapping.

## 2. `audit.api_log`

A separate table from `audit.log`, not a row type within it. `audit.log` records _what a user
did_; this records _what we said to a third party_. They differ in volume (a sync run is
thousands of calls), in retention, and in who reads them — and `audit.log`'s `detail jsonb`
would become an untyped dumping ground holding request bodies.

It follows `audit.log`'s two hard rules, which exist for good reasons and are not negotiable:

- **Append-only.** `GRANT SELECT, INSERT` only. Evidence that can be edited is not evidence.
- **The INSERT policy is `WITH CHECK (true)`.** Writing evidence must never fail because of
  tenant scoping. Reads stay tenant-scoped.

One addition `audit.log` does not need: **a call is recorded even when it fails**, including
when it never reached the network. A timeout with no response is precisely the case someone
will need to reconstruct later, so `status_code` and `response` are nullable and `error` carries
the reason.

### Redaction is a table-level concern, not a caller's

Request and response bodies carry client data, and the ML payloads carry NI numbers. Bodies are
stored, because a redacted audit trail answers no questions — but the **recorder** strips
credentials (`Authorization`, `apikey`, `client_secret`) before writing, so a token can never
reach the table by a caller forgetting. That belongs in one place, not in twelve call sites.

## 3. Routing: resolve, then dispatch

The mistake to avoid is `if tenant.uses_seccl: ... elif tenant.uses_ml: ...` scattered through
routers. Two layers instead:

**Resolve** — given a tenant and an account, which platform is this action going to?
`platform.tenant_platforms` already records which providers a tenant has enabled, and
`platform.accounts` records which platform an account lives on. So:

- an action **against an existing account** resolves from the account. No ambiguity, no picker.
- an action **creating something** resolves from the tenant's enabled providers: one enabled
  provider means no choice to offer; more than one means the adviser picks, and the picker is
  hidden when it would have a single option.

**Dispatch** — a small per-provider adapter turns the canonical request into that provider's
call. `app/integrations/<provider>/` already exists for ML and CTC; Seccl and SEI join it.

The canonical shape is the union of what the providers need, and the adapter is where the
provider-specific rules live — including the ones that constrain the _form_, because they are
provider facts, not UI preferences.

### Transfers: the rules differ per provider, and the form must follow

|                     | Seccl                                   | Morgan Lloyd               | SEI             |
| ------------------- | --------------------------------------- | -------------------------- | --------------- |
| Scope               | **Full only**                           | Full or partial            | Full or partial |
| Previous address    | **Required** (`transferAccountAddress`) | Not sent                   | Not sent        |
| Crystallised amount | —                                       | **Required for a pension** | —               |
| Pension type        | —                                       | **Required for a pension** | —               |

So the composer is not one form with optional fields; it is one form whose _required set_ is a
function of the resolved platform. Getting this from the adapter rather than hard-coding it in
the Svelte component is what stops the two drifting: the API validates against the same table
the form renders from.

Concretely, the form asks the API "what does a transfer need on platform X" and renders that,
rather than the component knowing that Seccl is full-only. That endpoint is cheap and it is the
difference between adding a fourth custodian being a config change and being a UI rewrite.

## 4. Why this generalises

The same resolve-then-dispatch applies to everything queued behind transfers:

| Action         | Seccl                             | Morgan Lloyd             | SEI                      |
| -------------- | --------------------------------- | ------------------------ | ------------------------ |
| Create client  | `POST /client`                    | `cam_createcontact`      | SWP REST                 |
| Create account | `POST /account`                   | `cam_createscheme`       | SWP REST                 |
| Set model      | `POST /portfoliomodel`            | n/a                      | SWP REST                 |
| Contribution   | `POST /portfoliotransactiongroup` | `cam_createcontribution` | Anticipated Cash Receipt |
| Transfer       | `POST /portfoliotransactiongroup` | `cam_createtransfer`     | SWP REST                 |
| Withdrawal     | `POST /portfoliotransactiongroup` | `cam_drawdownsetup`      | SWP REST                 |

The SEI column needs filling in against the merged spec rather than guessed at — 200 specs is
too many to assume from. `pkgs/sei` already covers cash receipts; the rest is a reading job.

The rows are the domain vocabulary the portal already speaks. The columns are adapters. Nothing
in a router should know which column it is in.

**SEI is a REST custodian, not a file-drop one.** An earlier draft of this document said
otherwise and was wrong: SEI publishes ~200 OpenAPI specs (vendored and merged in the monitoring
repo, `apps/api/app/resources/sei/openapi`), and `pkgs/sei` is already an mTLS + OAuth client
against them. The nightly SFTP drop described in `docs/sei.md` is the **inbound** data feed —
how we receive their reporting — and has nothing to do with how we send instructions. SEI's
adapter is an HTTP adapter like the other two.

`transport` still exists on `audit.api_log` because the inbound SFTP pull is worth recording
in the same place, but no outbound instruction should be using it.

## 5. The emit/persist split

The vendor clients do NOT write to the database. `pkgs/api-log` defines an `ApiCall`
record and a `Recorder` protocol, and each client takes an optional `recorder=` it calls once
per request. The API passes one that writes `audit.api_log`; a script or a test passes nothing.

That split is deliberate. Giving `pkgs/morgan-lloyd` a Postgres dependency so it could write its
own rows would make it unusable outside the API and couple three SDKs to one application's
schema. It also keeps the packages testable: the recorder in a test is `list.append`.

### The process default — and the bug of leaving it out

**The split above was only half a design, and the missing half meant the table recorded
nothing at all for as long as it existed.** Every client accepted a `recorder=` and **not one
production call site ever passed one**: the table, the drain, the endpoints and the admin page
were all built and the log stayed empty. Nothing failed, because an audit trail with no
producer is indistinguishable from a quiet week.

The reason is worth keeping. There are ~35 client construction sites — five in the API, thirty
across the three services' interlace models — so "pass a recorder" was never one change, and
whichever ones got done, the next client added would silently not be. **Whether a process
records its calls is a property of the deployment, not of the call site.** So it is configured
once, per process:

- `api_log.set_default_recorder()` installs it; each client resolves `recorder or
default_recorder()` **per call** (not in `__init__` — model discovery imports every module
  under `models/` before a daemon has installed anything, so a client built at import time
  would capture the absence for ever);
- the API's lifespan installs `ApiLog.recorder(source="app")`;
- each service's `models/tenants.py` calls `book_sink.install_api_log()`, which installs a
  `source="sync"` recorder. Model discovery is the hook because interlace is a CLI — there is
  no service-owned entrypoint, and importing `models/` is the one thing every invocation does.

An explicit `recorder=` still wins: the API's call sites know the acting tenant and user, and
the default cannot. `pkgs/morgan-lloyd/tests/test_audit_recorder.py` pins all of it, including
the tripwire that a bare client still records.

### `source`: app vs sync (0073)

The first question anyone asks of this log is "was that us or the cron", and it could not be
answered — both halves call the same operations, and a null `user_id` means "a sync" and "a
token refresh on the request path" equally. `source` is `app` or `sync`, a CHECK kept in step
with `api_log.Source`, and the summary groups by it: pooled, the sync's volume buries the app's
while the app's failures vanish into the sync's p95.

The sync services write their own rows as **`book_writer`** (`book_sink/api_log.py`, a bounded
queue and one drain thread), not through the API's queue — they are separate processes. Two
things `0073` had to get right, both found by running the INSERT as the role rather than by
reading the grant:

- `0062` gave `book_writer` USAGE on `platform`, `crm` and `auth` and **not `audit`**, so the
  table grant alone left every insert failing with "permission denied for schema audit" — into
  a handler that swallows. The same shape as the `service_role` bug below;
- **`RETURNING` needs SELECT**, which `book_writer` deliberately does not hold. A recorder
  written with `INSERT … RETURNING id` would fail every row, silently.

### Responses are kept only when they answer something

A successful response is **not stored**; a failure's is. A success body is usually an echo of
the request, and at sync volumes keeping them all costs a great deal to answer nothing, while a
failure's message is the whole point of the log.

The exception is a call that **minted an identifier**: `cam_createcontact` answers with
`contactid` and `cam_createscheme` with `schemeReference`, and once `platform.links` is written
those live in one place — if the link write then fails, a discarded body makes the id
unrecoverable and the retry creates the record twice, the exact duplication
`sync-to-morgan-lloyd` was rebuilt to stop. The client says so with `ApiCall.keep_response`.

An explicit flag, because the two obvious inferences are both wrong: `entity_type` means "what
this was ABOUT" and a read sets it too (and the ML client sets it nowhere), while matching
`cam_create*` in the shared layer would be string-matching three vendors' conventions. The ML
client does apply that prefix test — but in `pkgs/morgan-lloyd`, where the convention is a fact
about Dynamics rather than a guess.

Requests are kept regardless: being able to say what we sent is what the table is for.

Three properties the hook has to have, all covered by tests in
`pkgs/morgan-lloyd/tests/test_audit_recorder.py`:

- **A call that never reached the network is still recorded.** The record is taken in a
  `finally`, so a timeout or a DNS failure produces a row with a null status and a populated
  `error` — the row an incident is actually reconstructed from, and the one a naive
  "record after the response" hook misses.
- **One row per logical call, not per retry.** The hook wraps the retry loop rather than sitting
  inside it, so a 503 retried five times is one row saying we called once and it failed — not
  five calls the adviser never made.
- **Recording never breaks the caller.** If the recorder raises, it is swallowed. Failing an
  adviser's transfer because an audit insert deadlocked is worse than a missing row.

## 6. The persist side

`app/api_log.py` holds `ApiLog`: a bounded queue plus a drain task, started and stopped
by the app's lifespan.

**Queued, not written inline.** The clients' hook is synchronous by contract — they cannot await
inside it — and a sync run makes thousands of calls, so a Postgres round trip per call would
roughly double its wall time for evidence nobody reads synchronously. The queue is bounded
(10,000) and an overflow is _logged_: silently losing rows would make the table's completeness
unknowable, which is worse than a known gap.

The session factory is injectable. The default is `db.system_transaction` (service_role, no
request principal — the same primitive the WorkOS startup sync uses), but it binds to the
module-level engine built from settings at import, which a test cannot repoint.

Two bugs this shape produced, both worth not repeating:

- **`service_role` was missing from `0047`'s grants.** Every INSERT failed and the drain's
  deliberately-broad error handler swallowed it — an audit trail that logged its own failure and
  stayed empty. Caught only by exercising the real path against a real database; the unit tests
  used the superuser engine, which bypasses grants entirely. The fixture now does
  `SET LOCAL ROLE service_role`, exactly as production does.
- **The append-only assertion could not be made at runtime.** The `app` login is a member of both
  `authenticated` and `service_role`, so once `service_role` correctly held ALL, an `UPDATE`
  attempted after `SET ROLE authenticated` stopped being refused. The test now asserts the GRANT
  in `information_schema` — unambiguous, and not confounded by inheritance.

**Known gap:** the hook wraps the request, not the token fetch, so a failure to _authenticate_
to a provider is not recorded. "We could not get a Seccl token at 3am" is exactly what an ops
log should show, so this is worth closing.

## 6a. Delivery: the outbound instruction queue

`audit.api_log` answers "what did we send". It does not make sending _reliable_: a call that
fails at 3am is recorded and then forgotten, and the operator finds out when a client asks. So
outbound work needs a durable queue with retries — but the retry is the dangerous part, not the
queue, and the two must be designed in that order.

### It is a table-queue, not PGMQ, and that is already settled

`0013_render_jobs.sql` made this call and recorded why: a job table drained with
`SELECT … FOR UPDATE SKIP LOCKED` gives "the same claim / ack / retry / dead-letter contract PGMQ
gives, without the extension", keeps the plain `postgres:18` image and its data volume, and
carries no PG18 extension-availability risk on managed Postgres. It is swappable behind the same
enqueue/claim interface if that ever changes.

Since then the idiom has spread to roughly ten queues — `documents.render_jobs`, the flows outbox
and its dispatcher, `flows.timers` (which already has the fourth `dead_letter` state), the
notification and email consumers, the tasks notifier, automations. Adopting PGMQ for outbound
calls now would mean either running two queue technologies or migrating ten. PGMQ appears in
`docs/document-generation.md` as deferred and in `docs/morgan-lloyd/README.md` only as a
description of the **graphene-platform** repo, which is where the instinct to reach for it comes
from.

The outbound queue is `actions.steps`, claimed with `FOR UPDATE SKIP LOCKED` by the worker. Each
attempt lands on `actions.http_requests` (column-compatible with `audit.api_log`, so the sync
firehose can move later). The retry class — `safe`, `guarded`, `manual` — is unchanged. See
[`docs/actions.md`](actions.md).

### The queue holds INTENTS; the log holds ATTEMPTS

One row per thing we mean to happen, against many rows per HTTP call made trying to make it
happen. Keeping them apart is not tidiness:

- folding the log into the queue loses the failed attempts, because a queue row is mutable and
  ends up holding only the last outcome — and the failed attempts are the evidence;
- folding the queue into the log makes evidence mutable, and `audit.api_log` is append-only **by
  grant** precisely so it cannot be edited.

They join with no new column: `api_log.entity_type` / `entity_id` are deliberately loose (no FK,
"what it was about, when the caller knows"), and `entity_id` is already `bigint`. An instruction
stamps `entity_type = 'provider_instruction'` and its own id, so "this instruction was tried four
times, and here is what came back each time" is one query.

### Retrying is not safe by default

Two facts about this domain decide the whole design:

- **Morgan Lloyd has no delete.** The scar is on record: `sync-to-morgan-lloyd` created a contact,
  failed to read the id out of the action envelope, recorded nothing — so every retry _created the
  person again_. A blind retry of `cam_createcontact` puts a second human being in the CRM.
- **Money double-instructs.** `cam_createcontribution` and SEI's Anticipated Cash Receipt delivered
  twice reclaim relief from HMRC twice.

So the worker never retries "the HTTP call". It retries **the intent**, and each attempt first
asks whether it already landed. Every operation declares a retry class on its `ActionDef`:

| Class     | Meaning                                                                                    | Examples                                      |
| --------- | ------------------------------------------------------------------------------------------ | --------------------------------------------- |
| `safe`    | Idempotent by nature — a read, or a PATCH setting an absolute value. Retry freely.         | `cam_updatecontact`, `cam_updatebeneficiary`  |
| `guarded` | A create we can check first, because it mints an identifier we record on `platform.links`. | `cam_createcontact`, `cam_createscheme`       |
| `manual`  | No check exists and the effect is not reversible. One attempt, then park for a human.      | `cam_createcontribution`, `cam_drawdownsetup` |

`guarded` is `integrations/morgan_lloyd/sync.py` generalised: it already checks `platform.links`
before each step and reports `skipped`, which is what makes that endpoint "idempotent and
resumable". `manual` is what `…/lodge-expectation` and `…/refresh-morgan-lloyd` already are — an
explicit door a human opens, having looked.

**The default is `manual`.** An operation added without thinking about its retry class must not
become auto-retried; the failure mode of guessing wrong runs one way only.

### Idempotency is a unique index, not a convention

`UNIQUE (tenant_id, provider, operation, idempotency_key)`, where the key is **derived from the
subject** — a contribution uid, a contact id — and never random. Enqueuing the same intent twice
then collapses to one row at the database rather than depending on every caller remembering to
check. A `guarded` retry additionally re-checks the vendor side before sending, because the unique
index protects against a double _enqueue_, not against an attempt that reached Dynamics and lost
the answer on the way back. That is the exact shape of the duplicate-contact bug.

### It goes behind the synchronous path, not in front of it

The console's action drawers submit directly and show the vendor's own refusal — a 409 naming the
status, a 400 naming the field. That is more useful to an operator who is sitting there than a job
id, and it is the posture chosen when the drawers were specified. So the queue takes the work
nobody is watching: flow- and automation-triggered pushes, scheduled ones, and anything that
failed. Validation still happens before enqueue, so a bad payload is refused at the door either
way.

## 7. Build order

1. ~~`audit.api_log` + the recorder, wired into all four choke points.~~ **Done** — Morgan
   Lloyd, Seccl, SEI and CTC, plus the persist side above.
   **This entry was wrong for as long as it stood, and the way it was wrong is the lesson.**
   All four choke points did EMIT; nothing ever supplied a recorder, so the table was empty.
   "Wired into the choke points" described half a pipeline and read as a whole one. Closed by
   the process default and `source` above — and note that no test caught it, because every test
   passed its own recorder. Only the end-to-end read of a real table could.
2. ~~Admin → Platforms → **API** endpoints~~ **Done** — `GET /admin/platforms/api-calls`
   (paged, filterable by provider/ok/tenant/operation/free text), `/summary` (24-hour volume,
   failures and p95 per provider) and `/{call_id}` (with the bodies the list omits). The admin
   _page_ is still to build. Note the route-order trap: the literal `api-calls` paths must be
   declared before `/{provider}/overview` or they resolve as a platform called "api-calls" —
   pinned by a test.
3. The requirements endpoint + transfer adapter for Seccl and ML, driving the composer.
4. Onboarding and payments adapters, once the shape has survived one real feature.

### The log became a read model, and that raised the bar on `operation`

`audit.api_log` is now the SOURCE for three tabs of the Morgan Lloyd firm console
(`app/platform_entities.py`, `source="outbound"`): investments, documents and illustrations are
things we SEND and the vendor publishes none of them back — valuations are 403 for our service
principal, and documents and illustrations have no collection form at all. So the log is not a
lesser answer to "what does this firm have here", it is the only complete one: it includes the
attempts that FAILED, which a projection of vendor data cannot.

Two consequences worth knowing:

- **`operation` is a domain verb and must not carry the call's arguments.** Observed live: the ML
  client derived it as `path.split("(")[0]`, so OData reads landed as
  `cam_schemes?$select=cam_name` — one distinct "operation" per projection. That fragments every
  count over it and makes the exact-match filter behind these tabs miss the call entirely. Fixed
  in `_base._operation_of` (strips the query string as well as the key predicate) and pinned.
  SEI and Seccl pass their query separately, so their paths never carried one.
- **An unattributed call belongs to no firm's tab.** The recorder stamps the TARGET tenant for a
  proxied action, but a process-default recorder has no tenant to stamp. Those rows are scoped
  OUT of every firm's tab (attributing one would assert something we did not record) and counted
  instead, so they stay visible somewhere — the posture the AML console takes for a profile it
  cannot attribute.

Step 1 is worth doing before the routing design is finished: it is how we find out what the
current syncs actually send, which is the best possible input to steps 3 and 4.
