# baton — the durable action runtime

`pkgs/baton` is the Prefect-like runtime that the domain-actions engine
(`apps/api/app/actions`, `docs/actions.md`) is being rebuilt on. The authoritative reference is
the package's own `pkgs/baton/README.md`; this file records why it exists and the decisions behind
it.

## Why

The hand-rolled table queue in `apps/api/app/actions` ran vendor calls inside the HTTP request's
transaction (a raised handler rolled back the attempt record after the call had happened), had no
lease (a crash re-sent a `manual` step), no backoff or scheduled retry (long polls held a
`FOR UPDATE` row lock for minutes), and no dependency edges between a plan's children. Reviews were
folded into `actions.runs` at the storage level but not in code. baton replaces the engine with a
single, tested package.

## The model (one type)

There is one unit of work, the **action** (`@action`). An action that uses `ctx.session` is a
_leaf_ and does its own work; an action that calls other actions (`await ctx.run(...)`) is a
_composite_, and each call is a **child run**. A run replays its body on each advance: completed
children return stored results, a not-ready child suspends the parent, the rest execute. This
keeps the domain's own shape — `account.pay_to` is both an action an adviser can run and a child
of `account.create` — without a second "task" concept. The word **action** still means a domain
action; `app/flows` effects and `audit.actions` are unrelated.

## Decisions (16 Oct 2026)

- **One type, not action+step.** A leaf is an action that did its own work; a composite is one
  that called children. Every node is addressable, governable (firm policy via a hook), and
  distributable (a child is a claimable row). The leaf/composite boundary is enforced at run time
  (a `ProgrammingError` on the first pass), not by a type.
- **Commit, then advance with a budget.** A door creates the run in its own transaction, commits
  with the booked row, then advances inline for a few seconds so fast provider calls still return
  4xx/5xx; the worker continues anything longer and the door returns 202.
- **Fold reviews fully** (planned Phase 3): a book action becomes a composite whose body is a gate
  then an apply leaf.
- **Start empty** at cutover: the old `actions.*` tables are renamed `actions.legacy_*`.
- Package name **baton**.

## Coexistence during the migration

The runtime tables live in their own schema, **`baton`** (migration `0261_baton`), beside the
legacy `actions.*` engine rather than replacing it in place. Firm policy is not duplicated: the
new service reads the existing `actions.policies` / `actions.policy_roles`. This lets the old and
new engines run side by side while actions move over one at a time. The written plan's cutover
(rename `actions.*` → `legacy_*`) becomes the final step once every door runs on baton; until
then nothing is renamed and the old engine keeps working.

The app wiring is in `apps/api/app/actions/`: `baton_engine.py` (the process engine, bound to the
app database and schema `baton`), `hooks.py` (`GrapheneHooks`: `SET LOCAL ROLE service_role`, firm
policy on a child, the journey settle callback), `redact.py`, `service.py` (the policy-aware
façade the doors call — start / advance_inline / decide / retry / resume / cancel), and `defs/`
(the action catalogue, filled as each domain is migrated).

## Status

Phase 1 (the package) and the bulk of Phase 2 are done and gate-green.

**Migrated and tested (coexisting with the legacy engine):**

- The package: `MemoryStore` + a real `PostgresStore`, 32 tests.
- The API foundation: migration `0261` (schema `baton`), the engine, hooks and the policy-aware
  service.
- Every provider action: clients (create, the four updates, sync), accounts (create, details,
  model.attach), money (`transaction.payment`/`transaction.withdrawal`/`transaction.fee` and
  `transaction.transfer`) and `client.setup` — on Seccl, SEI and Morgan Lloyd. Provider dispatch is
  proven across all three; SEI's account open is a durable `NotReady` wait that resumes by its
  business key. (SEI money leaves wired Oct 2026 — see "The money namespace" below.)
- The worker advances baton runs in the background, beside the legacy loop.
- The doors, all behind the `baton_actions` flag, which is **off by default** — production still
  runs the legacy engine until the cutover:
  - the generic `POST /actions/runs` run button;
  - the money instruction door (`lodge_instruction`) and the transfer door (`lodge_transfer`,
    both legs — the raise and the later SEI receipt of a Morgan Lloyd SIPP);
  - both Morgan Lloyd sync doors (contact and account), where the replay button becomes a fresh
    run guarded by `one_open_per_subject`;
  - the contribution lodge retry door (`/lodge-expectation`), which starts a `transaction.payment`
    run for the contribution source (it calls the internal `contribution.lodge` leaf) or resumes a
    parked one;
  - the journey `setup_account` step (`settle_account_setup`), keyed on the application so the
    other account's step attaches to the same run; the `on_run_terminal` hook releases every step
    that recorded the run uid;
  - the decide and step-retry endpoints, which route on `service.owns_run` so a baton run and a
    legacy `actions.runs` row are handled the same way.

## Cutover — DONE

Baton is the **sole domain-actions engine**. The legacy table-queue engine is gone:
`app/actions/runs.py` keeps only the shared helpers (firm policy, `_load_account`,
`account_subjects`, `caller_ids`, uid resolvers, `Held`/`ActionError`/`pending_response`,
`list_settings`/`set_policy`); `operations.py`, `plan.py`, the integration `install()` functions,
`household.plan_children` and the worker `_actions_loop` are deleted. `OperationError` lives in
`app/actions/attempt.py`. Migration `0262` drops `actions.runs/steps/http_requests/events/subjects/
run_approvers`; `actions.policies` / `policy_roles` stay. The external readers
(`notifications/*`, `routers/applications.py::_annotate_setup_runs`, `routers/search.py`,
`tenant_data.py`, the SEI enrol resume) read `baton.*`. The `baton_actions` flag is gone from live
code (a dead field remains in `config.py`; the classifier blocked editing it).

**All 33 catalogue actions run on baton**, including the ones the migration had missed:
`contribution.record` / `transfer.record` (the record-to-Morgan-Lloyd doors), the Seccl
`payment.regular` / `withdrawal.regular` / `fee.attach`, the SEI `account.pay_to` /
`account.currencies`, `account.bank` (Seccl account bank — the send is a Graphene gap, not a Seccl
limitation; SEI sets the pay-to profile), and `model.remove` (SEI-scoped, implementation to
follow). The four handler-less entries (`beneficiary.replace`, `drawdown.start`, `trade.place`,
`illustration.generate`) are baton placeholders that refuse "not implemented yet".

## The money namespace (Oct 2026)

The money-movement actions moved into one `transaction.*` namespace and the money-in paths were
unified (migration `0263`):

- **Rename.** `payment.one_off`→`transaction.payment`, `withdrawal.one_off`→`transaction.withdrawal`,
  `fee.adhoc`→`transaction.fee`, `transfer.create`→`transaction.transfer`. The key is the firm-policy
  identity (`actions.policies`) and the `baton.runs`/`subjects` join key; `0263` moves the rows (it
  `SET LOCAL ROLE service_role` to clear FORCE RLS, then `RESET ROLE` so alembic can stamp — the 0161
  trap). Event-type strings are unchanged (the engine does not emit them yet; the doors write the
  audit events), so the event registry is untouched.
- **One money-in action.** `transaction.payment` is the single firm-facing money-in action — a plain
  payment and an ISA/SIPP contribution are the same custodian call. It takes two sources: a booked
  instruction (`account_id` + `instruction_uid`) runs the custodian payment leaf; a contribution
  expectation (`contribution_uid`, from the contributions workbench) runs the internal
  `contribution.lodge` leaf (now `_UNLISTED` in the catalogue — kept for run history, firms configure
  the policy on `transaction.payment`). The two share SEI's `create_anticipated_receipt`; the
  booking tables stay separate (`platform.instructions` vs `platform.transaction_expectations`).
- **SEI wired.** The one-off money path (`_send` in `defs/money.py`) only ever dispatched Seccl, so a
  SEI payment raised a run and made no call. SEI leaves `sei.payment`/`sei.withdrawal`/`sei.fee` now
  wrap the existing (complete, idempotency-aware) handlers, and `_send` dispatches `sei.{kind}` too.
- **Provider 4xx/5xx handled.** A definite refusal (HTTP 4xx) parks as `NeedsAttention`; a transient
  error (5xx/429/lost answer) is retryable. Seccl now carries the status on `SecclApiError` and the
  instruct seams classify via `_refused` (a 4xx validation refusal no longer retries forever). SEI
  money-in derives `ambiguous` from the status in `_lodge` and `sei_payment` honours it
  (`permanent = not ambiguous`), matching the money-out disbursement/fee paths. Money-out and the
  Seccl money rail still park every error (never auto-retry money); with the money leaves at
  `retries=0`, both 4xx and 5xx park operationally regardless.

**Known follow-ups:**

- Two onboarding-walkthrough integration tests are **skipped**: `setup_account` is async on baton
  (the worker opens the accounts), and the walkthrough drives the journey in a single uncommitted
  transaction the worker cannot see. The clean fix is a baton **in-session advance** so the setup
  door drains its run synchronously (as the legacy best-effort settle did); that also lets reviews
  approve apply at the decision. In production the per-request commit + worker already do this.
- ~~The runs listing is a bridge in `queries.py`; the typed listing + DAG UI are Phase 4.~~ Done,
  21 Oct 2026 — see "The runs screens" below.
- Still outside a run: the inline `contributions.create_contribution(lodge=True)` (synchronous for
  its own response) and `lodge_instruction`'s `outbound.dispatch` fallback for an unmapped kind.
- The design for the bank/pay domain (unify `client.bank`/`account.bank`, add SEI `account.pay_from`
  and the UI to set pay-to/pay-from individually) is a feature on top of this foundation.

**Phase 3 (fold the reviews engine into gates) — done behind the flag:**

- Each wired review key (`app/reviews/catalogue.py`) is a baton apply leaf in
  `app/actions/defs/book.py`, reusing the live capture/apply/conflict logic from
  `app/reviews/apply.py`. A held review is a baton run with a run-level approval gate.
- `app/reviews/baton.py` is the isolated fold, which `reviews/service.py` delegates to when the
  flag is on (`maybe_hold`/`approve`/`reject`/`cancel`) or when a uid is a baton review run
  (`get_request`/`list_requests` merge legacy and baton rows). It holds via `engine.start` with the
  reviewer roles as the `Approval`, decides via `engine.decide`, and projects runs back into the
  shape `/reviews` already reads, so the book UI is unchanged. The legacy `actions.runs` path is
  untouched when the flag is off; old held rows there are still listed and decidable.
- The apply runs on the worker (the engine claims the approved run), so approve returns at once and
  the change lands a moment later, as the money and journey doors do. The happened-event the
  automations tail is written by the terminal hook (`GrapheneHooks._settle_review`) when the apply
  commits, not at decision.

  **Behaviour change to confirm before cutover:** approve no longer applies synchronously, so a
  stale-record conflict is not raised at approve time — the run parks and the inbox shows it
  `conflicted` on the next read, rather than a 409 on the Approve click.

- Deferred refinements (not blockers): unify the bank keys into gate → apply → custodian-send
  composites; surface the apply conflict at approve time if the synchronous feel is wanted (needs
  commit-then-advance in the approve door).

## The runs screens (Oct 2026)

The Phase 4 work: the actions screens read baton's own shape instead of a flattening bridge.
Design record: the canvas at https://claude.ai/artifact/52YWAuqbYGdA2Ddd1wHbCc (run detail, the
runs workbench, every node state, the review findings).

**What the API returns (`app/actions/queries.py`).**

- `GET /actions/runs/{uid}` is the run as its **tree**: the root node with `children`, each node
  carrying baton's `state` (type + name + message), the folded `status` word, `provider`, `key`,
  `position`, `after_keys`, `on_failure`, the leaf policy (`retries`, `wait_budget_seconds`,
  `idempotent`), `scheduled_at` / `wait_started_at` / `passes`, `params`, `result`, the decision
  fields with roles and people **named**, its `attempts` (each with the engine's `outcome` word —
  `wait` and `retry` are not failures) and, on a failed node, `failure`: the provider's reason
  parsed into a headline (`summarise_failure` reads SEI's `errors[].reason`, Seccl's `message`,
  Dynamics' `error.message`, and trims the JSON tail off an engine message). The root adds
  `contact`, `account`, `journey` (the onboarding step that raised it), `input`, `approval`
  (`required`, `roles`, `gate_uid`, `can_decide` for the caller), `attention` (the deepest failed
  leaf, with its parsed reason), `timing`, `events` (from `baton.events`, actors named) and the
  advice `can_retry` / `can_cancel`.
- `GET /actions/runs` is server-paged in the book listings' envelope: `{items, total, limit,
offset, facets, facet_counts, facet_labels, stats}`. Filters `state`, `action`, `provider`,
  `requested_by`, `contact`, plus `q`, `sort` (`requested`, `name`, `state`, `requested_by`,
  `duration`), `dir`, `limit`, `offset`. A row carries its immediate `children` by name and status
  (the step bar), the `providers` it reached, and `can_decide`. `stats` adds `waiting` (resting on
  a provider — a Waiting/AwaitingRetry leaf, or a composite paused on one) and `failed_7d`.
- **One status fold.** `status_word(state_type, state_name)` in Python and `_STATUS_SQL` in the
  listing agree by test (`test_status_word_agrees_with_the_sql_fold` runs the CASE over every
  state baton defines). Words: running, waiting, awaiting_approval, succeeded, partial, skipped,
  needs_attention, conflicted, timed_out, failed, rejected, cancelled. The old fold sent a
  cancelled root to `failed` on the detail and `cancelled` on the listing.
- `POST /actions/runs/{uid}/retry` revives a run (or any node — a node is a run) and its failed
  descendants and ancestors; `POST /actions/runs/{uid}/cancel` stops an open run. The per-step
  retry route stays for compatibility; the UI posts retries to the run route with the uid in hand.

**Engine changes (`pkgs/baton`).** `RunView` carries `scheduled_at`, `wait_started_at`, `passes`,
`leaf`, `on_failure`, `after_keys`, `gate_roles`, `gate_message` and `error`. `Engine.cancel`
**cascades**: open children (scheduled, pending, paused, crashed) and their gates are cancelled
with the parent, so a leaf mid-wait stops polling the provider for a run nobody wants; a child
RUNNING at that moment keeps its lease and the **orphan guard** (`_cancel_if_orphaned`, on claim)
cancels it when it next reschedules. Only a CANCELLED parent orphans — a failed parent's open
children are left for `retry`. Tests: `test_compose.py` (cancel cascade, orphan guard, failed
parent keeps children).

**The UI (`pkgs/ui/components/actions`).** `RunNode` draws one node in every state (the name
first, the key beside it; a wait says when it polls again and how much budget is used; a skip
says why; a gate says who may decide and offers Approve/Reject to them; a refused leaf shows the
parsed reason, the call, and Retry). `RunDetailView` is the page: linked subject, the reason once
in an Alert, the one next action (`next-action.ts` — decide, or Retry from the node that stopped;
Cancel always secondary, behind a `ConfirmDialog`), the progress strip, the tree with Expand all /
Only problems, and the rail (timeline, approval, context, timing, input, output). `RunsListing`
is the Contributions/Transfers frame: six counters that name who has it and double as the state
filter, a server-driven `DataTable` with folding columns, `Pagination` as links, and `RunPanel`
pushed in beside the queue when a row is picked (`?run=` in the URL, so a reload keeps it). The
per-action route (`/actions/[key]`) redirects to the Runs tab filtered by action; the old
`ActionRuns` component — a copy of the inspector with its own status map — is gone.

**The approvals seat (22 Oct 2026).** Firm settings → Actions → Approvals is the same workbench
locked to the gate (`?tab=approvals` pins `state=awaiting_approval`, oldest case first), with the
gate's own counters — Awaiting approval · Older than a day · Nobody can decide · Money held ·
Decided · 7 days — and columns: Held, Run (with its case and figure), Subject, Provider,
Approvers (each role with how many people other than the requester hold it; red when none do),
Requested by, Waiting (amber past a day, red past three). The old card list (`ApprovalsQueue`,
`GET /actions/approvals`'s only reader) let an administrator approve a bank change it could not
see and posted a rejection with no reason, which the reviews route refuses; it is gone. The
panel (`RunPanel`) carries the change body the Reviews queue draws (`ReviewChange` over
`reviewRowFromRun`), who may decide and how many could, when the approvers were last reminded,
and Approve · Reject · Remind · Cancel run. Every Reject across the actions screens goes through
`DecideDialogs`, which asks for the reason; every Approve says what it does for that kind of hold
(a payment is SENT, a bank change moves future withdrawals) via `reviewApproveCopy`. A gate in
the tree is decided by posting the uid of the run that holds it (`parentUid` on `RunNode`), not
the gate's own — the engine looks for the open gate under the run it is given.

What the API adds for it (`queries.py`): `status_word(..., held_below=True)` and the matching
`_HELD_BELOW_SQL` fold a composite paused on a GATED CHILD (`before_child` returning an
`Approval`) to `awaiting_approval`, where it read "running" before; every root row carries
`held_uid` (the node to decide), `held_since`, `approver_roles`, `eligible_approvers` (NULL when
no role is named) and `held_amount` (from `meta.input`); `stats` gains `held_over_day`,
`held_undecidable`, `money_held`, `decided_7d`, `approved_7d`, `rejected_7d`; the `held` filter
takes `over_day` · `undecidable` · `money`; the detail's `approval` gains `held_uid`, `held_name`,
`held_since`, `eligible_approvers`, `reminded_at`, `reminders`, and a review run carries `change`
(`{payload, before}`). `POST /actions/runs/{uid}/remind` re-sends the approvers' notice (the
requester or anyone with `actions:read`; once an hour per run, refused once the run has moved
on). A rejection through `service.decide` needs a note. `action_name()` falls through to the
reviews catalogue, so a held book change is named on Runs. A held PROVIDER action now tells its
approvers and its requester — see "The kinds of hold" in `docs/reviews.md`.

**Model and fee reviews push after approval (22 Oct 2026).** "Change model" (`model.attach`) and
"Change fee packages" (`fee.attach`) are the settings rows; the doors hold on
`account.model_changed` / `account.fee_packages_changed` (paired in `_SAME_ACT`), and the book
rows are unlisted like the bank ones. An immediate save starts the push from the door; a held
save's apply runs on the worker, and `GrapheneHooks._push_after_review` then starts the same
provider run with `service.start(approved=True)`, which skips the approval gate (the second person
has agreed) but still honours the enabled switch and a manual halt. Four book reviews were
retired at the same time (`instruction.raised` / `cancelled`, `account.regular_movements_changed`,
`account.beneficiaries_changed`) — see `docs/reviews.md`.

**The recorded input (`meta.input`).** A run's `params` are the call — `{account_id,
instruction_uid}`, `{account_id, uid, leg}` — not what the adviser asked for. The door that opens a
run records the form beside it as `meta.input`, and the detail's Input panel (and the Reviews queue
body for a held run) prints that, with Copy input for the whole thing. The money doors snapshot the
instruction (`_instruction_input`: kind, amount, fee, due date, category, method). Since 22 Oct 2026
the transfer door does the same (`_transfer_input`): the client and account, ceding provider, policy
number, type, basis, extent, crystallisation and split values, pension type (code and label),
safeguarded benefits, expected date, note, the ceding scheme's address and FCA reference, and the
SEI lines (ceding account type, documents, assets). The point is a refused run: when the custodian
rejects the transfer, ops set it up by hand at SEI from the Input panel without opening the drawer
again. A door that records nothing shows raw params only. Provider outcomes (`provider_error`,
`seccl_placed`, `sei_accepted`) are not input and stay off the snapshot.

**Action definitions.** `morgan-lloyd.sync` and `morgan-lloyd.update_client` park as
`NeedsAttention` rather than failing outright, and classify a Dynamics failure by the recorded
call's status: a 5xx or a lost answer is `Retry` (so `update_client`'s `retries=2`, previously
unreachable, is live), a 4xx refusal is `Permanent`. The post-cutover "is not on baton yet"
messages read "is not available for {custodian}".

**Sweep and attempt-recording fixes (21 Oct 2026, from the dev worker's log).** Three faults in one
incident. (1) The sweep numbered a crashed attempt `attempt_count + 1` but never bumped
`attempt_count`, so a leaf that crashed, was revived by retry and crashed again collided with its
own first crashed row on `UNIQUE(run_id, attempt_no)` — and because the sweep is one transaction,
that single row aborted every sweep from then on (`baton sweep failed` on each cycle; nothing
crashed or timed out was recovered). The same drift numbered the revived run's next real attempt 1
again, so it could never settle. Both stores now number the crashed attempt after the last row in
the table and move `attempt_count` up to match. (2) Attempt bodies were written with a bare
`json.dumps`: a vendor request carrying a Decimal or a date raised `TypeError` in the middle of
recording the attempt. The engine now JSON-normalises every attempt body (`Engine._body`) and the
Postgres store dumps with the codec's `default`. (3) An exception while recording a failure
escaped `_run_pass`: the run stayed leased `running` until the sweep crashed it a minute later and
its parent failed with the serialiser's message. `_park_unrecorded` now settles the run
`NeedsAttention` with both messages instead. Tests: `test_leaf.py` (normalised bodies, a refusal
carrying a Decimal, a failure while recording the failure, a second crash after a revive) and
`tests/pg/test_postgres.py::test_sweep_numbers_a_second_crash_after_the_first`.
