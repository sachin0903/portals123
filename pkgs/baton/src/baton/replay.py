"""One replay pass over an action run.

The engine claims a run, then runs its body once against a :class:`ReplayPass`. A *composite*
body calls child actions; each call finds the child's memo row and returns its stored result,
or creates the child and either drains it inline (budget permitting) or suspends the parent. A
*leaf* body reaches for ``ctx.session`` and does its own work inside the attempt transaction.

The pass raises control-flow signals the engine catches: :class:`Suspend` (rest in a state),
:class:`BudgetExhausted` (a worker continues), :class:`LeaseLost` (another holder took over),
and the body-facing :class:`ChildFailed` / :class:`Rejected` / :class:`Conflicted`.
"""

from __future__ import annotations

import time
from collections.abc import Generator, Sequence
from typing import TYPE_CHECKING, Any

from baton import states
from baton.codec import identity
from baton.definitions import ActionDefinition
from baton.errors import (
    BudgetExhausted,
    ChildFailed,
    Conflicted,
    LeaseLost,
    NotReady,
    Permanent,
    ProgrammingError,
    Rejected,
    Retry,
    Skip,
    Suspend,
)
from baton.model import RunRow, RunView
from baton.policy import delay_for
from baton.registry import GATE_KEY
from baton.states import State

if TYPE_CHECKING:
    from datetime import datetime

    from baton.engine import Engine
    from baton.model import AttemptView


class _Evidence:
    __slots__ = ("method", "url", "request", "response", "status_code")

    def __init__(self) -> None:
        self.method = self.url = None
        self.request = self.response = None
        self.status_code = None


class ReplayPass:
    def __init__(
        self,
        engine: Engine,
        run: RunRow,
        children: Sequence[RunRow],
        *,
        token: str,
        deadline: float | None,
        worker_id: str,
    ) -> None:
        self.engine = engine
        self.run = run
        self.token = token
        self.deadline = deadline
        self.worker_id = worker_id
        self.run_view: RunView = engine.view(run)
        self._by_key: dict[str, RunRow] = {r.key: r for r in children if r.key}
        self._by_pos: dict[int, RunRow] = {
            r.position: r for r in children if r.position is not None
        }
        self._ordinal: dict[str, int] = {}
        self._seen: set[str] = set()
        self._next_pos = 1 if run.approver_role_ids or self._has_gate_zero(children) else 0
        self._frontier: list[str] = []
        self.composed = False
        self.session_used = False
        self.lost = False
        self._leaf_cm: Any = None
        self._leaf_unit: Any = None
        self._evidence = _Evidence()
        self._previous: AttemptView | None = None

    @staticmethod
    def _has_gate_zero(children: Sequence[RunRow]) -> bool:
        return any(c.position == 0 and c.kind == "gate" for c in children)

    # ---- leaf support ----
    @property
    def is_leaf(self) -> bool:
        return not self.composed

    def leaf_session(self) -> Any:
        if self.composed:
            raise ProgrammingError(
                f"{self.run.action_key}: a run that calls child actions cannot also use ctx.session"
            )
        self.session_used = True
        if self._leaf_unit is None:
            raise ProgrammingError("ctx.session is only available while a leaf attempt is running")
        return self._leaf_unit.raw

    def set_evidence(self, **kw: Any) -> None:
        for name, value in kw.items():
            if value is not None:
                setattr(self._evidence, name, value)

    def previous_attempt(self) -> AttemptView | None:
        return self._previous

    async def heartbeat(self) -> None:
        ok = await self.engine.store.renew_lease(
            self._leaf_unit, self.run.id, self.token, self.engine.lease_seconds, self.engine.now()
        )
        if not ok:
            self.lost = True

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        unit = self._leaf_unit
        if unit is None:
            raise ProgrammingError("ctx.emit is only available inside a leaf attempt")
        await self.engine.store.insert_event(
            unit, run=self.run, event_type=event_type, payload=payload, now=self.engine.now()
        )

    # ---- composite support ----
    def resolve(self, action: ActionDefinition[Any] | str) -> ActionDefinition[Any]:
        if isinstance(action, str):
            return self.engine.registry.require(action)
        return action

    def state_of(self, key: str) -> State | None:
        row = self._by_key.get(key)
        return row.state if row else None

    def result_of(self, key: str) -> Any:
        row = self._by_key.get(key)
        return self.engine.codec_load(row.result) if row and row.result is not None else None

    def _assign_key(self, defn: ActionDefinition[Any], key: str | None) -> tuple[str, int]:
        if key is None:
            n = self._ordinal.get(defn.key, 0)
            self._ordinal[defn.key] = n + 1
            key = f"{defn.key}#{n}"
        if key in self._seen:
            raise Conflicted(f"child key {key!r} was issued twice in one pass")
        self._seen.add(key)
        position = self._next_pos
        self._next_pos += 1
        return key, position

    def make_call(
        self,
        defn: ActionDefinition[Any],
        params: dict[str, Any],
        *,
        key: str | None,
        wait_for: tuple[str, ...],
        kind: str = "action",
        gate_roles: tuple[int, ...] | None = None,
    ) -> Call:
        if self.session_used:
            raise ProgrammingError(
                f"{self.run.action_key}: a leaf run (uses ctx.session) cannot call child actions"
            )
        self.composed = True
        return Call(self, defn, params, key, wait_for, kind, gate_roles)

    async def call(
        self,
        defn: ActionDefinition[Any],
        params: dict[str, Any],
        *,
        key: str | None,
        wait_for: tuple[str, ...],
        kind: str = "action",
        gate_roles: tuple[int, ...] | None = None,
    ) -> Any:
        return await self.make_call(
            defn, params, key=key, wait_for=wait_for, kind=kind, gate_roles=gate_roles
        )

    def _assign_single(self, call: Call) -> None:
        key, position = self._assign_key(call.defn, call.req_key)
        call.key = key
        call.position = position
        call.after = list(self._frontier) + list(call.wait_for)
        self._frontier = [key]
        call.assigned = True

    def _assign_gathered(self, calls: Sequence[Call]) -> None:
        snapshot = list(self._frontier)
        keys: list[str] = []
        for call in calls:
            key, position = self._assign_key(call.defn, call.req_key)
            call.key = key
            call.position = position
            call.after = snapshot + list(call.wait_for)
            call.assigned = True
            keys.append(key)
        if keys:
            self._frontier = keys

    async def _run_call(self, call: Call) -> Any:
        # This run has called a child, so it is a composite: free its unused attempt connection
        # before draining the subtree (see release_idle_leaf_unit).
        await self.release_idle_leaf_unit()
        if not call.assigned:
            self._assign_single(call)
        key = call.key
        position = call.position
        if key is None or position is None:  # pragma: no cover - set by assignment above
            raise ProgrammingError("call was not assigned a key")
        row = self._by_key.get(key)
        if row is None:
            row = await self._create_child(
                call.defn,
                call.params,
                key=key,
                position=position,
                after=call.after,
                kind=call.kind,
                gate_roles=call.gate_roles,
            )
        elif row.action_key != call.defn.key and call.kind != "gate":
            raise Conflicted(
                f"{call.key!r}: store holds {row.action_key!r}, replay called {call.defn.key!r}"
            )
        return await self._dispatch_child(call.defn, row)

    async def _create_child(
        self,
        defn: ActionDefinition[Any],
        params: dict[str, Any],
        *,
        key: str,
        position: int,
        after: list[str],
        kind: str,
        gate_roles: tuple[int, ...] | None,
    ) -> RunRow:
        if self.lost:
            raise LeaseLost
        if self._budget_spent():
            raise BudgetExhausted
        at_pos = self._by_pos.get(position)
        if at_pos is not None:
            raise Conflicted(
                f"position {position}: store holds {at_pos.key!r}, replay issued {key!r}"
            )
        row = await self.engine.create_child(
            parent=self.run,
            defn=defn,
            params=params,
            key=key,
            position=position,
            after=after,
            kind=kind,
            gate_roles=gate_roles,
            on_failure=defn.options.on_failure,
        )
        self._by_key[key] = row
        self._by_pos[position] = row
        return row

    async def _dispatch_child(self, defn: ActionDefinition[Any], row: RunRow) -> Any:
        st = row.state
        t = st.type
        if t is states.StateType.COMPLETED:
            if st.name == "Skipped":
                return None
            if row.kind == "gate":
                return self.engine.gate_decision(row)
            return self.engine.codec_load(row.result)
        if t is states.StateType.CANCELLED:
            # Rejected or plainly cancelled, the child will not complete; stop the parent rather
            # than fall through and re-suspend it forever on AWAITING_CHILDREN.
            raise Rejected(row.key or defn.key, row.decision_note)
        if t is states.StateType.FAILED:
            return self._on_child_failed(defn, row)
        if t is states.StateType.PAUSED:
            raise Suspend(states.AWAITING_CHILDREN)
        if t is states.StateType.SCHEDULED:
            row = await self._maybe_run_inline(row)
            if row.state.type is not states.StateType.SCHEDULED:
                return await self._dispatch_child(defn, row)
            raise Suspend(states.AWAITING_CHILDREN, wake_at=row.scheduled_at)
        if t is states.StateType.CRASHED:
            if defn.options.idempotent:
                # claim_run only takes 'scheduled', so a crashed child must be revived first or the
                # force-advance below just reloads the same crashed row and recurses to no effect.
                await self.engine.revive_run(row)
                refreshed = await self.engine.reload(row.id)
                row = await self._maybe_run_inline(refreshed or row, force=True)
                return await self._dispatch_child(defn, row)
            await self.engine.mark_needs_attention(row, "crashed mid-attempt; not idempotent")
            raise Suspend(states.AWAITING_CHILDREN)
        raise Suspend(states.AWAITING_CHILDREN)

    def _on_child_failed(self, defn: ActionDefinition[Any], row: RunRow) -> Any:
        mode = row.on_failure or defn.options.on_failure
        if mode == "continue":
            return None
        # A resumable child (NeedsAttention), or a call site asking for it, makes the parent
        # resumable too. A hard failure under the default mode fails the parent outright.
        if mode == "needs_attention" or row.state.name == "NeedsAttention":
            raise Suspend(states.NEEDS_ATTENTION)
        raise ChildFailed(row.key or defn.key, row.uid, row.error or row.state.message)

    async def _maybe_run_inline(self, row: RunRow, *, force: bool = False) -> RunRow:
        now = self.engine.now()
        due = row.scheduled_at is not None and row.scheduled_at <= now
        free = row.lease_until is None or row.lease_until < now
        if not force and (not due or not free):
            return row
        if self._budget_spent():
            raise BudgetExhausted
        budget = None if self.deadline is None else max(0.0, self.deadline - time.monotonic())
        await self.engine.advance_child(row.uid, budget_seconds=budget, worker_id=self.worker_id)
        refreshed = await self.engine.reload(row.id)
        return refreshed if refreshed is not None else row

    def gate(self, key: str, *, roles: tuple[int, ...], message: str | None) -> Call:
        gate_def = self.engine.registry.require(GATE_KEY)
        return self.make_call(
            gate_def, {"message": message}, key=key, wait_for=(), kind="gate", gate_roles=roles
        )

    async def gather(self, calls: Sequence[Any]) -> list[Any]:
        items = list(calls)
        if not all(isinstance(c, Call) for c in items):
            raise ProgrammingError("ctx.gather takes ctx.run(...) / ctx.gate(...) calls")
        self._assign_gathered(items)
        results: list[Any] = [None] * len(items)
        errors: list[tuple[int, BaseException]] = []
        for i, c in enumerate(items):
            try:
                results[i] = await c
            except (LeaseLost, BudgetExhausted):
                raise
            except (Suspend, ChildFailed, Rejected, Conflicted) as err:
                errors.append((i, err))
        if not errors:
            return results
        for _, caught in errors:
            if isinstance(caught, ChildFailed | Rejected | Conflicted):
                raise caught
        waits = [w for _, w in errors if isinstance(w, Suspend)]
        wake_times = [w.wake_at for w in waits if w.wake_at is not None]
        wake = min(wake_times) if wake_times else None
        raise Suspend(states.AWAITING_CHILDREN, wake_at=wake)

    # ---- leaf attempt lifecycle, driven by the engine ----
    async def open_leaf_attempt(self, attempt_no: int, previous: AttemptView | None) -> None:
        self._previous = previous
        self._evidence = _Evidence()
        self._leaf_cm = self.engine.store.unit(self.run)
        self._leaf_unit = await self._leaf_cm.__aenter__()

    @property
    def leaf_unit(self) -> Any:
        return self._leaf_unit

    @property
    def evidence(self) -> _Evidence:
        return self._evidence

    async def commit_leaf(self) -> None:
        cm, self._leaf_cm, self._leaf_unit = self._leaf_cm, None, None
        if cm is not None:
            await cm.__aexit__(None, None, None)

    async def close_leaf_empty(self) -> None:
        """A composite never wrote to the pre-opened attempt unit; commit it empty so any
        children it created in their own units survive."""
        cm, self._leaf_cm, self._leaf_unit = self._leaf_cm, None, None
        if cm is not None:
            await cm.__aexit__(None, None, None)

    async def release_idle_leaf_unit(self) -> None:
        """Drop the pre-opened attempt unit the moment this run goes composite, before it drains
        any child. The unit is opened eagerly so a leaf's ``ctx.session`` is ready synchronously,
        but a composite never touches it — and holding its connection idle-in-transaction through
        the whole recursive child drain is how a deep or fanned-out tree exhausts the pool. A child
        call can never follow ``ctx.session`` (``make_call`` forbids it), so the unit is always
        still unused here; closing it with no error commits nothing and frees the connection."""
        if self._leaf_cm is None:
            return
        cm, self._leaf_cm, self._leaf_unit = self._leaf_cm, None, None
        await cm.__aexit__(None, None, None)

    async def rollback_leaf(self, error: BaseException) -> None:
        cm, self._leaf_cm, self._leaf_unit = self._leaf_cm, None, None
        if cm is not None:
            try:
                await cm.__aexit__(type(error), error, error.__traceback__)
            except BaseException:  # noqa: BLE001, S110 - the original error is what we re-raise
                pass

    def check_unseen(self) -> None:
        for key, row in self._by_key.items():
            if row.kind == "gate":
                continue  # start-level gates are engine-managed, not issued by the body
            if key not in self._seen and row.state.type is not states.StateType.CANCELLED:
                raise Conflicted(
                    f"child {key!r} exists in the store but this pass never reached it"
                )

    def classify_leaf(self, exc: BaseException, *, retries_used: int = 0) -> _LeafOutcome:
        return classify_leaf(exc, self.run, self.engine, retries_used=retries_used)

    def _budget_spent(self) -> bool:
        return self.deadline is not None and time.monotonic() > self.deadline


class _LeafOutcome:
    __slots__ = ("kind", "state", "outcome", "scheduled_at", "wait_started", "error")

    def __init__(
        self,
        kind: str,
        *,
        state: State | None = None,
        outcome: str = "failed",
        scheduled_at: datetime | None = None,
        wait_started: bool = False,
        error: str | None = None,
    ) -> None:
        self.kind = kind  # retry_now | retry_later | wait | needs_attention | continue | fail
        self.state = state
        self.outcome = outcome
        self.scheduled_at = scheduled_at
        self.wait_started = wait_started
        self.error = error


def classify_leaf(
    exc: BaseException, run: RunRow, engine: Engine, *, retries_used: int = 0
) -> _LeafOutcome:
    from baton.definitions import ActionDefinition  # noqa: F401 - typing only

    defn = engine.registry.require(run.action_key)
    opts = defn.options
    now = engine.now()
    # The retry budget counts only prior RETRY attempts, never durable waits — a NotReady wait is
    # bounded by wait_timeout_seconds, not by `retries` (errors.py). `attempt_count` includes waits,
    # so the budget uses retries_used (committed 'retry' attempt rows), and the backoff uses the
    # retry number, not the total attempt number.
    retry_number = retries_used + 1
    msg = str(exc) or type(exc).__name__

    if isinstance(exc, Skip):
        return _LeafOutcome(
            "skip", state=states.SKIPPED.with_message(exc.reason or None), outcome="skipped"
        )
    if isinstance(exc, NotReady):
        within = _within_wait_budget(run, opts.wait_timeout_seconds, now)
        if within:
            return _LeafOutcome(
                "wait",
                state=states.WAITING.with_message(msg),
                outcome="wait",
                scheduled_at=engine.after(exc.delay),
                wait_started=run.wait_started_at is None,
                error=msg,
            )
        return _fail(opts, states.TIMED_OUT.with_message(msg), "timed_out", msg)
    if isinstance(exc, Permanent):
        return _fail(opts, states.FAILED.with_message(msg), "failed", msg)

    retryable = _retryable(exc, opts)
    if retryable and retries_used < opts.retries:
        delay = (
            exc.delay
            if isinstance(exc, Retry) and exc.delay is not None
            else delay_for(opts.retry_delay, retry_number, jitter=opts.retry_jitter)
        )
        if delay <= 0:
            return _LeafOutcome("retry_now", outcome="retry", error=msg)
        return _LeafOutcome(
            "retry_later",
            state=states.AWAITING_RETRY.with_message(msg),
            outcome="retry",
            scheduled_at=engine.after(delay),
            error=msg,
        )
    return _fail(opts, states.FAILED.with_message(msg), "failed", msg)


def _retryable(exc: BaseException, opts: Any) -> bool:
    if isinstance(exc, Permanent):
        return False
    if opts.retry_on is not None:
        return bool(opts.retry_on(exc))
    return True


def _fail(opts: Any, state: State, outcome: str, msg: str) -> _LeafOutcome:
    if opts.on_failure == "continue":
        return _LeafOutcome(
            "continue", state=states.SKIPPED.with_message(msg), outcome=outcome, error=msg
        )
    if opts.on_failure == "needs_attention":
        return _LeafOutcome(
            "needs_attention",
            state=states.NEEDS_ATTENTION.with_message(msg),
            outcome=outcome,
            error=msg,
        )
    return _LeafOutcome("fail", state=state, outcome=outcome, error=msg)


def _within_wait_budget(run: RunRow, budget: float | None, now: datetime) -> bool:
    if budget is None:
        return True
    if run.wait_started_at is None:
        return True
    return (now - run.wait_started_at).total_seconds() <= budget


class Call:
    """A deferred child call. Awaiting it finds or creates the child run and returns its result.
    ``ctx.gather`` assigns a batch of these their keys and edges before any of them runs."""

    __slots__ = (
        "pass_",
        "defn",
        "params",
        "req_key",
        "wait_for",
        "kind",
        "gate_roles",
        "key",
        "position",
        "after",
        "assigned",
    )

    def __init__(
        self,
        pass_: ReplayPass,
        defn: ActionDefinition[Any],
        params: dict[str, Any],
        req_key: str | None,
        wait_for: tuple[str, ...],
        kind: str,
        gate_roles: tuple[int, ...] | None,
    ) -> None:
        self.pass_ = pass_
        self.defn = defn
        self.params = params
        self.req_key = req_key
        self.wait_for = wait_for
        self.kind = kind
        self.gate_roles = gate_roles
        self.key: str | None = None
        self.position: int | None = None
        self.after: list[str] = []
        self.assigned = False

    def __await__(self) -> Generator[Any, None, Any]:
        return self.pass_._run_call(self).__await__()


__all__ = ["Call", "ReplayPass", "classify_leaf", "identity"]
