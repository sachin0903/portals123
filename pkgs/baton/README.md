# baton

A small durable-execution runtime for domain actions, backed by Postgres. It is the engine
behind `apps/api/app/actions` (see `docs/baton.md` and `docs/actions.md`).

## The model: one type

There is one unit of work, the **action**, declared with `@action`. A run of an action replays
its body each time the engine advances it.

- An action that does its own work — reaches for `ctx.session` — is a **leaf**. It runs inside an
  attempt transaction, records one **attempt** per try, and retries or waits durably.
- An action that calls other actions is a **composite**. Each `await ctx.run(other, ...)` is a
  **child run**: on replay a completed child returns its stored result without re-executing, a
  child that is not ready suspends the parent, and the rest are created and drained.

Leaf vs composite is behaviour, not a declaration. A body may not do both (touch `ctx.session`
_and_ call a child); that raises `ProgrammingError` on the first pass, before any side effect.

A run carries a `tenant_id`, a public `uid`, an `idempotency_key` unique per tenant (a second
`start` with the same key returns the same run), typed `params`, a `result`, an optional
`subject` (the key entity — client, account, transaction), and firm-facing fields the host app
fills: a case number, approver roles, a justification and a decision.

## States

`state_type` drives orchestration; `state_name` is the label a screen shows.

| type        | names                                        | meaning                                        |
| ----------- | -------------------------------------------- | ---------------------------------------------- |
| `scheduled` | Scheduled, AwaitingRetry, Waiting, Resumed   | claimable once `scheduled_at` passes           |
| `running`   | Running                                      | a holder has the lease                         |
| `paused`    | AwaitingApproval, AwaitingChildren           | waiting on a decision or on children           |
| `completed` | Completed, Partial, Skipped, Approved        | terminal, succeeded                            |
| `failed`    | Failed, NeedsAttention, Conflicted, TimedOut | terminal; NeedsAttention is revived by `retry` |
| `cancelled` | Cancelled, Rejected                          | terminal, stopped                              |
| `crashed`   | Crashed                                      | a leaf whose attempt lease expired             |

## Retries, waits, gates

An action declares leaf options: `retries`, `retry_delay` (number, list, or
`exponential_backoff(...)`), `retry_jitter`, `retry_on`, `attempt_timeout_seconds`,
`wait_timeout_seconds`, `idempotent`, a `guard`, and `on_failure` (`fail` | `needs_attention` |
`continue`). A leaf body signals its outcome by raising `Retry`, `NotReady` (a durable wait that
does not consume retries), `Permanent`, or `Skip`. A **gate** — `await ctx.gate(key, roles=...)`,
or `start(approval=Approval(roles))` — pauses a run for a human decision resolved by
`engine.decide(...)`.

## Execution

`engine.start(session, action, ...)` creates a run in the caller's transaction so it commits
with the booked row. `engine.advance(run_uid, budget_seconds=...)` runs it: a door advances
inline for a short budget (fast provider calls still return their status), and the `Worker`
continues anything longer. Both share one path and claim with `FOR UPDATE SKIP LOCKED` under a
lease; every write by a holder is fenced on its lease token, so a reclaimed run's old holder
writes nothing.

## Determinism

A composite body must be deterministic given its params and its children's results: no clock, no
randomness, no I/O, no `ctx.session`. Every book read that steers control flow is a leaf, so the
read is a stored, inspectable child. A pass that diverges from the stored run (a different child
at a position, a key issued twice, a child never reached) ends the run `Conflicted`.

## DDL contract for a host app

`baton.ddl("<schema>")` returns the `CREATE TABLE` statements for `runs`, `attempts`, `events`
and `subjects`, rendered into a schema. The package ships no RLS, grants, FKs into app schemas,
uid defaults or triggers: the host migration adds those around the tables, and renames any
pre-existing tables of the same name out of the way first (the DDL does not use `IF NOT EXISTS`,
so a clash fails loudly). The host also provides `Hooks` (`begin_session` to set the database
role, `before_child` for firm policy on a child, `on_event` to mirror to an audit stream,
`on_run_terminal` to react when a root settles).

## Tests

`uv run pytest` runs the in-memory suite (the replay algorithm, against `MemoryStore` +
`FakeClock`). `tests/pg/` runs the same engine against a real Postgres via testcontainers
(claims, fencing, sweep, subjects, concurrency); it skips cleanly when Docker is absent.
