"""The engine: start a run, advance it, decide a gate, retry, cancel.

``advance`` and the worker share one path, :meth:`_advance_claimed`. It claims the run (lease +
fencing token), runs the body once against a :class:`ReplayPass`, and settles the run from
whatever the body did or raised. A leaf's attempt and its completion commit together; a leaf's
failure rolls its domain writes back but still records the attempt. A composite's children drain
inline while there is budget, and the parent rests ``paused`` when one is not ready.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from baton import states
from baton.codec import JsonCodec, Redactor, identity
from baton.context import Approval, GateDecision, RunContext, Subject
from baton.definitions import ActionDefinition, _active_pass
from baton.errors import (
    ApprovalError,
    BudgetExhausted,
    ChildFailed,
    Conflicted,
    LeaseLost,
    NotFound,
    ProgrammingError,
    Rejected,
    Skip,
    Suspend,
)
from baton.hooks import Hooks, NoopHooks
from baton.model import (
    AttemptRow,
    AttemptView,
    RunDetail,
    RunRow,
    RunView,
    StartResult,
)
from baton.registry import GATE_KEY, Registry, default_registry
from baton.replay import ReplayPass
from baton.states import TERMINAL, State, StateType
from baton.store import NewAttempt, NewRun, SettleFields, Store

if TYPE_CHECKING:
    pass

from baton.model import AdvanceResult

_RETRY_NOW = object()


def _new_uid() -> str:
    import secrets
    import string

    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))


def _gate_body(ctx: RunContext) -> Any:  # pragma: no cover - a gate never runs its body
    raise ProgrammingError("a gate run is resolved by decide(), not executed")


class Engine:
    def __init__(
        self,
        store: Store,
        registry: Registry = default_registry,
        *,
        hooks: Hooks | None = None,
        codec: JsonCodec | None = None,
        redact: Redactor | None = None,
        uid_factory: Any = _new_uid,
        lease_seconds: float = 60.0,
        default_budget_seconds: float = 25.0,
        strict_params: bool = False,
        max_depth: int = 8,
        worker_id: str = "engine",
    ) -> None:
        self.store = store
        self.registry = registry
        self.hooks = hooks or NoopHooks()
        self.codec = codec or JsonCodec()
        self.redact: Redactor = redact or identity
        self.uid_factory = uid_factory
        self.lease_seconds = lease_seconds
        self.default_budget_seconds = default_budget_seconds
        self.strict_params = strict_params
        self.max_depth = max_depth
        self.worker_id = worker_id
        self._clock = getattr(store, "clock", None)
        if not registry.known(GATE_KEY):
            from baton.definitions import ActionDefinition as _AD
            from baton.definitions import ActionOptions as _AO

            registry.register(_AD(_gate_body, _AO(key=GATE_KEY, name="Approval")))

    # ---- clock helpers ----
    def now(self) -> datetime:
        if self._clock is not None:
            return cast(datetime, self._clock.now())

        return datetime.now(tz=UTC)

    def after(self, seconds: float) -> datetime:
        return self.now() + timedelta(seconds=max(0.0, seconds))

    # ---- view / codec ----
    def view(self, run: RunRow) -> RunView:
        return RunView(
            uid=run.uid,
            tenant_id=run.tenant_id,
            action_key=run.action_key,
            kind=run.kind,
            params=run.params,
            result=self.codec_load(run.result),
            meta=run.meta,
            state=run.state,
            key=run.key,
            position=run.position,
            parent_uid=None,
            root_uid=None,
            depth=run.depth,
            requested_by=run.requested_by,
            requested_at=run.requested_at,
            contact_id=run.contact_id,
            subject_kind=run.subject_kind,
            subject_uid=run.subject_uid,
            case_no=run.case_no,
            justification=run.justification,
            approver_role_ids=run.approver_role_ids,
            decision=run.decision,
            decided_by=run.decided_by,
            decided_at=run.decided_at,
            decision_note=run.decision_note,
            attempt_count=run.attempt_count,
            started_at=run.started_at,
            ended_at=run.ended_at,
            scheduled_at=run.scheduled_at,
            wait_started_at=run.wait_started_at,
            passes=run.passes,
            leaf=run.leaf,
            on_failure=run.on_failure,
            after_keys=list(run.after_keys),
            gate_roles=list(run.gate_roles) if run.gate_roles is not None else None,
            gate_message=run.gate_message,
            error=run.error,
        )

    def codec_load(self, stored: Any) -> Any:
        return stored

    def gate_decision(self, row: RunRow) -> GateDecision:
        return GateDecision(
            decided_by=row.decided_by,
            decided_at=row.decided_at or self.now(),
            note=row.decision_note,
        )

    def _attempt_view(self, a: AttemptRow) -> AttemptView:
        return AttemptView(
            attempt_no=a.attempt_no,
            outcome=a.outcome,
            started_at=a.started_at,
            ended_at=a.ended_at,
            duration_ms=a.duration_ms,
            error=a.error,
            error_type=a.error_type,
            method=a.method,
            url=a.url,
            status_code=a.status_code,
            request=a.request,
            response=a.response,
        )

    # ---- start (caller's unit) ----
    async def start(
        self,
        u: Any,
        action: ActionDefinition[Any] | str,
        *,
        tenant_id: int,
        params: dict[str, Any],
        idempotency_key: str,
        requested_by: str | None = None,
        contact_id: int | None = None,
        subjects: Sequence[Subject] = (),
        subject: Subject | None = None,
        approval: Approval | None = None,
        justification: str | None = None,
        meta: dict[str, Any] | None = None,
        scheduled_at: datetime | None = None,
    ) -> StartResult:
        defn = action if isinstance(action, ActionDefinition) else self.registry.require(action)
        # Dedup by (kind, uid): the same entity is often passed both as `subject` and inside
        # `subjects`, and the subjects table is UNIQUE (run_id, kind, uid) — a duplicate would
        # fail the insert. Order is preserved so the primary stays the first one named.
        subs: list[Subject] = []
        _seen: set[tuple[str, str]] = set()
        for s in list(subjects) + ([subject] if subject else []):
            if (s.kind, s.uid) in _seen:
                continue
            _seen.add((s.kind, s.uid))
            subs.append(s)
        primary = subs[0] if subs else None
        existing = await self.store.run_by_key(u, tenant_id, idempotency_key)
        if existing is not None:
            return StartResult(self.view(existing), created=False)
        held = approval is not None
        case_no = (
            await self.store.next_case_no(u, tenant_id)
            if approval is not None and approval.case_number
            else None
        )
        full_meta = dict(defn.options.meta) | (meta or {})
        new = NewRun(
            uid=self.uid_factory(),
            tenant_id=tenant_id,
            action_key=defn.key,
            idempotency_key=idempotency_key,
            params=self.codec.params(params),
            params_hash=self.codec.digest(params),
            meta=full_meta,
            state=states.AWAITING_APPROVAL if held else states.SCHEDULED,
            scheduled_at=None if held else (scheduled_at or self.now()),
            requested_by=requested_by,
            contact_id=contact_id,
            subject_kind=primary.kind if primary else None,
            subject_uid=primary.uid if primary else None,
            case_no=case_no,
            justification=(justification or "").strip() or None,
            approver_role_ids=list(approval.roles) if approval is not None else [],
        )
        run = await self.store.insert_run(u, new)
        if run is None:
            again = await self.store.run_by_key(u, tenant_id, idempotency_key)
            if again is None:
                raise InternalError("insert returned no row and none is present")
            return StartResult(self.view(again), created=False)
        blocking = defn.options.one_open_per_subject
        if subs:
            await self.store.insert_subjects(u, run, subs, blocking=blocking)
        if approval is not None:
            await self._insert_gate(
                u, parent=run, roles=tuple(approval.roles), message=approval.message, position=0
            )
            ev = await self.store.insert_event(
                u, run=run, event_type="run.held", actor_id=requested_by, now=self.now()
            )
        else:
            ev = await self.store.insert_event(
                u, run=run, event_type="run.created", actor_id=requested_by, now=self.now()
            )
        await self.hooks.on_event(u, ev, self.view(run))
        return StartResult(self.view(run), created=True)

    async def _insert_gate(
        self,
        u: Any,
        *,
        parent: RunRow,
        roles: tuple[int, ...],
        message: str | None,
        position: int,
        key: str = "approval",
    ) -> RunRow:
        new = NewRun(
            uid=self.uid_factory(),
            tenant_id=parent.tenant_id,
            action_key=GATE_KEY,
            idempotency_key=f"{parent.idempotency_key}/{key}",
            params={},
            params_hash=None,
            meta={},
            kind="gate",
            state=states.AWAITING_APPROVAL.with_message(message),
            parent_run_id=parent.id,
            root_run_id=parent.root_run_id or parent.id,
            depth=parent.depth + 1,
            key=key,
            position=position,
            gate_roles=list(roles),
            gate_message=message,
            on_failure="fail",
        )
        row = await self.store.insert_run(u, new)
        if row is None:
            raise InternalError(f"gate insert collided for {new.idempotency_key}")
        return row

    # ---- advance ----
    async def advance(
        self,
        run_uid: str,
        *,
        budget_seconds: float | None = None,
        worker_id: str | None = None,
    ) -> AdvanceResult:
        wid = worker_id or self.worker_id
        budget = self.default_budget_seconds if budget_seconds is None else budget_seconds
        token = uuid.uuid4().hex
        async with self.store.unit() as u:
            run = await self.store.run_by_uid(u, run_uid)
            if run is None:
                raise NotFound(run_uid)
            if run.state.type in TERMINAL or run.state.type is StateType.PAUSED:
                return AdvanceResult(self.view(run), "noop")
            if (
                run.state.type is StateType.SCHEDULED
                and run.scheduled_at is not None
                and run.scheduled_at > self.now()
            ):
                return AdvanceResult(self.view(run), "noop")
            claimed = await self.store.claim_run(
                u,
                worker_id=wid,
                token=token,
                lease_seconds=self.lease_seconds,
                now=self.now(),
                run_id=run.id,
                timeout_seconds=self._timeout(run),
            )
        if claimed is None:
            return AdvanceResult(self.view(run), "busy")
        deadline = None if budget is None else time.monotonic() + budget
        return await self._advance_claimed(claimed, token=token, deadline=deadline, worker_id=wid)

    async def advance_child(
        self, run_uid: str, *, budget_seconds: float | None, worker_id: str
    ) -> AdvanceResult:
        return await self.advance(run_uid, budget_seconds=budget_seconds, worker_id=worker_id)

    async def claim_and_advance(
        self, *, budget_seconds: float | None = None, worker_id: str | None = None
    ) -> AdvanceResult | None:
        """Claim ANY one due run with ``FOR UPDATE SKIP LOCKED`` and advance it; ``None`` if nothing
        is due.

        The worker's single claim path: no read-then-claim window where concurrent slots all read
        the same id and then contend on it (all but one wasting a 'busy' round trip). The whole-run
        ``timeout_seconds`` is not applied on a worker's first claim (only the inline ``advance``
        knows it before claiming), which is moot while no action sets it."""
        wid = worker_id or self.worker_id
        budget = self.default_budget_seconds if budget_seconds is None else budget_seconds
        token = uuid.uuid4().hex
        async with self.store.unit() as u:
            claimed = await self.store.claim_run(
                u,
                worker_id=wid,
                token=token,
                lease_seconds=self.lease_seconds,
                now=self.now(),
                run_id=None,
                timeout_seconds=None,
            )
        if claimed is None:
            return None
        deadline = None if budget is None else time.monotonic() + budget
        return await self._advance_claimed(claimed, token=token, deadline=deadline, worker_id=wid)

    def _timeout(self, run: RunRow) -> float | None:
        defn = self.registry.get(run.action_key)
        return defn.options.timeout_seconds if defn else None

    async def _advance_claimed(
        self, run: RunRow, *, token: str, deadline: float | None, worker_id: str
    ) -> AdvanceResult:
        orphaned = await self._cancel_if_orphaned(run, token=token)
        if orphaned is not None:
            return orphaned
        children = await self._children(run.id)
        pass_ = ReplayPass(self, run, children, token=token, deadline=deadline, worker_id=worker_id)
        marker = _active_pass.set(pass_)
        heartbeat = asyncio.create_task(self._heartbeat(pass_))
        try:
            return await self._run_pass(run, pass_)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            _active_pass.reset(marker)

    async def _cancel_if_orphaned(self, run: RunRow, *, token: str) -> AdvanceResult | None:
        """The orphan guard. A child claimed after its parent was CANCELLED must not run: a leaf
        that was mid-wait when the parent went (polling a custodian every 15 s) would otherwise
        keep polling, and eventually act, for a run nobody wants any more. ``cancel`` already
        cancels the open children it can see; this catches the one that was leased at the time and
        rescheduled itself afterwards. Only a cancelled parent counts — a FAILED parent's open
        children are left alone, because ``retry`` revives the parent and expects them."""
        if run.parent_run_id is None:
            return None
        parent = await self.reload(run.parent_run_id)
        if parent is None or parent.state.type is not StateType.CANCELLED:
            return None
        now = self.now()
        message = "The parent run was cancelled."
        async with self.store.unit(run) as u:
            settled = await self.store.settle_run(
                u,
                run.id,
                token,
                state=states.CANCELLED.with_message(message),
                ended=True,
                fields=SettleFields(error=message),
                now=now,
            )
            if settled is None:
                raise LeaseLost
            await self.store.release_subjects(u, run.id)
            ev = await self.store.insert_event(u, run=run, event_type="run.cancelled", now=now)
            await self.hooks.on_event(u, ev, self.view(settled))
        return AdvanceResult(self.view(settled), "noop")

    async def _heartbeat(self, pass_: ReplayPass) -> None:
        try:
            while True:
                await asyncio.sleep(self.lease_seconds / 3)
                async with self.store.unit() as u:
                    ok = await self.store.renew_lease(
                        u, pass_.run.id, pass_.token, self.lease_seconds, self.now()
                    )
                if not ok:
                    pass_.lost = True
                    return
        except asyncio.CancelledError:
            return

    async def _run_pass(self, run: RunRow, pass_: ReplayPass) -> AdvanceResult:
        defn = self.registry.require(run.action_key)
        while True:
            attempt_no = run.attempt_count + 1
            previous = await self._last_attempt(run.id)
            await pass_.open_leaf_attempt(
                attempt_no, self._attempt_view(previous) if previous else None
            )
            started = time.perf_counter()
            ctx = RunContext(pass_)
            try:
                value = await self._invoke(defn, ctx, run, pass_)
            except (Suspend, BudgetExhausted) as sig:
                await self._discard(pass_, sig)
                return await self._on_signal(run, pass_, sig)
            except LeaseLost:
                await self._discard(pass_, LeaseLost())
                return AdvanceResult(self.view(run), "busy")
            except (ChildFailed, Rejected, Conflicted, ProgrammingError) as exc:
                await self._discard(pass_, exc)
                return await self._fail_run(run, pass_, exc)
            except asyncio.CancelledError:
                # Worker shutdown cancels the slot task; unwind cleanly rather than recording a
                # spurious failed/crashed attempt. The run stays leased 'running' and the sweep
                # recovers it when the lease lapses.
                raise
            except BaseException as exc:  # noqa: BLE001 - leaf failure or composite body bug
                if pass_.composed:
                    await self._discard(pass_, exc)
                    return await self._fail_run(run, pass_, exc)
                duration = int((time.perf_counter() - started) * 1000)
                try:
                    result = await self._on_leaf_failure(run, pass_, exc, attempt_no, duration)
                except (LeaseLost, asyncio.CancelledError):
                    raise
                except BaseException as recording:  # noqa: BLE001 - the failure's own failure
                    return await self._park_unrecorded(run, exc, recording)
                if result is _RETRY_NOW:
                    reloaded = await self.reload(run.id)
                    if reloaded is not None:
                        run = reloaded
                        pass_.run = run
                    continue
                return cast(AdvanceResult, result)
            else:
                if pass_.composed:
                    await pass_.close_leaf_empty()
                    pass_.check_unseen()
                    return await self._complete_composite(run, pass_, value)
                duration = int((time.perf_counter() - started) * 1000)
                return await self._complete_leaf(run, pass_, value, attempt_no, duration)

    async def _discard(self, pass_: ReplayPass, exc: BaseException) -> None:
        if pass_.session_used:
            await pass_.rollback_leaf(exc)
        else:
            await pass_.close_leaf_empty()

    async def _invoke(
        self, defn: ActionDefinition[Any], ctx: RunContext, run: RunRow, pass_: ReplayPass
    ) -> Any:
        opts = defn.options
        if opts.guard is not None:
            reason = await opts.guard(ctx, **run.params)
            if reason:
                raise Skip(reason)
        if opts.attempt_timeout_seconds is not None:
            return await asyncio.wait_for(defn.fn(ctx, **run.params), opts.attempt_timeout_seconds)
        return await defn.fn(ctx, **run.params)

    # ---- leaf outcomes ----
    async def _complete_leaf(
        self, run: RunRow, pass_: ReplayPass, value: Any, attempt_no: int, duration_ms: int
    ) -> AdvanceResult:
        unit = pass_.leaf_unit
        now = self.now()
        ev = pass_.evidence
        result = self.codec.normalise(value)
        # Record what went in and what came back for EVERY leaf, not only one that made an HTTP
        # call. A plain function-call leaf records no ctx.evidence, so default the attempt's
        # request/response to the run's params and result — the run detail then shows the input and
        # output instead of "nothing recorded". An HTTP leaf that called ctx.evidence keeps its own
        # request/response (they take precedence).
        await self.store.insert_attempt(
            unit,
            NewAttempt(
                run_id=run.id,
                root_run_id=run.root_run_id or run.id,
                tenant_id=run.tenant_id,
                attempt_no=attempt_no,
                worker=pass_.worker_id,
                outcome="ok",
                started_at=now,
                ended_at=now,
                duration_ms=duration_ms,
                method=ev.method,
                url=ev.url,
                status_code=ev.status_code,
                request=self._body(ev.request if ev.request is not None else run.params),
                response=self._body(ev.response if ev.response is not None else result),
            ),
        )
        # `ended=True`: a completed leaf has finished, and a screen reads its duration off
        # ended_at. Without it a leaf that succeeded nine hours ago read "9h 28m" on the tree.
        await self._finalise(
            unit,
            run,
            states.COMPLETED,
            result=result,
            write_result=True,
            attempt_count=attempt_no,
            leaf=True,
            ended=True,
        )
        await pass_.commit_leaf()
        return AdvanceResult(self.view(await self._must_reload(run.id)), "completed")

    def _body(self, value: Any) -> Any:
        """An attempt body as the row stores it: JSON-normalised (a Decimal is text, a date is
        ISO), then redacted. Normalising here, not only in the store, means the memory store and
        the Postgres store hand a body back in the same shape."""
        return self.redact(self.codec.normalise(value))

    async def _park_unrecorded(
        self, run: RunRow, exc: BaseException, recording: BaseException
    ) -> AdvanceResult:
        """The attempt failed AND the failure could not be written — a body the store could not
        take, a constraint, a lost connection. Park the run for a person with both messages. Before
        this the recording error escaped: the run stayed leased 'running' until the sweep crashed
        it a minute later, and its parent failed with the serialiser's message instead."""
        message = f"{exc or type(exc).__name__}; the attempt could not be recorded: {recording}"
        async with self.store.unit(run) as u:
            await self._finalise(
                u,
                run,
                states.NEEDS_ATTENTION.with_message(message),
                leaf=True,
                error=message,
                ended=True,
            )
        return AdvanceResult(self.view(await self._must_reload(run.id)), "failed", error=exc)

    async def _on_leaf_failure(
        self, run: RunRow, pass_: ReplayPass, exc: BaseException, attempt_no: int, duration_ms: int
    ) -> Any:
        await pass_.rollback_leaf(exc)
        now = self.now()
        ev = pass_.evidence
        status = getattr(exc, "status_code", None)
        async with self.store.unit(run) as u:
            retries_used = await self.store.retries_used(u, run.id)
            outcome = pass_.classify_leaf(exc, retries_used=retries_used)
            await self.store.insert_attempt(
                u,
                NewAttempt(
                    run_id=run.id,
                    root_run_id=run.root_run_id or run.id,
                    tenant_id=run.tenant_id,
                    attempt_no=attempt_no,
                    worker=pass_.worker_id,
                    outcome=outcome.outcome,
                    started_at=now,
                    ended_at=now,
                    duration_ms=duration_ms,
                    error=outcome.error,
                    error_type=type(exc).__name__,
                    method=ev.method,
                    url=ev.url,
                    status_code=status if status is not None else ev.status_code,
                    request=self._body(getattr(exc, "request", None) or ev.request or run.params),
                    response=self._body(getattr(exc, "response", None) or ev.response),
                ),
            )
            if outcome.kind == "retry_now":
                await self.store.bump_attempt(u, run.id, pass_.token, now)
                reloaded = await self.store.run_by_id(u, run.id)
                if reloaded is None or reloaded.lease_token != pass_.token:
                    return AdvanceResult(self.view(run), "busy")
                return _RETRY_NOW
            if outcome.kind in ("retry_later", "wait"):
                await self._finalise(
                    u,
                    run,
                    outcome.state or states.AWAITING_RETRY,
                    scheduled_at=outcome.scheduled_at,
                    attempt_count=attempt_no,
                    leaf=True,
                    wait_started=outcome.wait_started,
                    error=outcome.error,
                )
                await self.store.notify(u, run.id)
                refreshed = await self.store.run_by_id(u, run.id)
                return AdvanceResult(self.view(refreshed or run), "suspended")
            # continue / needs_attention / fail -> terminal
            terminal = outcome.state or states.FAILED
            await self._finalise(
                u,
                run,
                terminal,
                attempt_count=attempt_no,
                leaf=True,
                error=outcome.error,
                ended=True,
            )
            kind = "completed" if terminal.type is StateType.COMPLETED else "failed"
            refreshed = await self.store.run_by_id(u, run.id)
            return AdvanceResult(
                self.view(refreshed or run), kind, error=exc if kind == "failed" else None
            )

    # ---- composite outcomes ----
    async def _complete_composite(
        self, run: RunRow, pass_: ReplayPass, value: Any
    ) -> AdvanceResult:
        children = await self._children(run.id)
        final = _derive_final(children)
        async with self.store.unit(run) as u:
            await self._finalise(
                u,
                run,
                final,
                result=self.codec.normalise(value),
                write_result=True,
                leaf=False,
                ended=True,
            )
        kind = "completed" if final.type is StateType.COMPLETED else "failed"
        return AdvanceResult(self.view(await self._must_reload(run.id)), kind)

    async def _on_signal(self, run: RunRow, pass_: ReplayPass, sig: BaseException) -> AdvanceResult:
        now = self.now()
        if isinstance(sig, BudgetExhausted):
            async with self.store.unit(run) as u:
                await self.store.settle_run(
                    u, run.id, pass_.token, state=states.SCHEDULED, scheduled_at=now, now=now
                )
                await self.store.notify(u, run.id)
            return AdvanceResult(self.view(await self._must_reload(run.id)), "budget")
        if not isinstance(sig, Suspend):
            raise InternalError("_on_signal received a non-Suspend signal")
        state = sig.state
        async with self.store.unit(run) as u:
            if state.type in TERMINAL:
                await self._finalise(
                    u, run, state, leaf=pass_.is_leaf, ended=True, error=state.message
                )
                kind = "completed" if state.type is StateType.COMPLETED else "failed"
                refreshed = await self.store.run_by_id(u, run.id)
                return AdvanceResult(self.view(refreshed or run), kind)
            await self.store.settle_run(
                u, run.id, pass_.token, state=state, scheduled_at=sig.wake_at, now=now
            )
            if state.type is StateType.SCHEDULED:
                await self.store.notify(u, run.id)
            elif state.type is StateType.PAUSED and not await self.store.open_children_exist(
                u, run.id
            ):
                # Lost-wakeup guard. The awaited child can settle — and fire its own wake_parent,
                # a no-op because we were still 'running' — between this pass reading the child
                # snapshot and this settle writing 'paused'. The settle above took this run's row
                # lock and `unit(run)` holds it to commit, so a concurrent wake_parent is blocked
                # until then; re-checking here under that lock catches the child that is already
                # terminal and reschedules instead of sleeping forever. A still-open child (an
                # approval gate, or one still waiting) keeps us paused as intended. token=None:
                # settle cleared the lease, and the held row lock makes fencing unnecessary.
                await self.store.settle_run(
                    u, run.id, None, state=states.RESUMED, scheduled_at=now, now=now
                )
                await self.store.notify(u, run.id)
        return AdvanceResult(self.view(await self._must_reload(run.id)), "suspended")

    async def _fail_run(self, run: RunRow, pass_: ReplayPass, exc: BaseException) -> AdvanceResult:
        if isinstance(exc, Rejected):
            state = states.REJECTED.with_message(exc.note)
        elif isinstance(exc, Conflicted):
            state = states.CONFLICTED.with_message(str(exc))
        else:
            state = states.FAILED.with_message(str(exc) or type(exc).__name__)
        async with self.store.unit(run) as u:
            await self._finalise(u, run, state, leaf=pass_.is_leaf, ended=True, error=str(exc))
        return AdvanceResult(self.view(await self._must_reload(run.id)), "failed", error=exc)

    # ---- the shared settle ----
    async def _finalise(
        self,
        u: Any,
        run: RunRow,
        state: State,
        *,
        result: Any = None,
        write_result: bool = False,
        scheduled_at: datetime | None = None,
        attempt_count: int | None = None,
        leaf: bool | None = None,
        wait_started: bool = False,
        error: str | None = None,
        ended: bool = False,
    ) -> None:
        now = self.now()
        fields = SettleFields(
            result=result,
            write_result=write_result,
            leaf=leaf,
            error=error,
            write_wait_started_at=wait_started,
            wait_started_at=now if wait_started else None,
        )
        token = run.lease_token
        settled = await self.store.settle_run(
            u,
            run.id,
            token,
            state=state,
            scheduled_at=scheduled_at,
            ended=ended,
            fields=fields,
            now=now,
        )
        if settled is None:
            raise LeaseLost
        if attempt_count is not None:
            await self.store.set_attempt_count(u, run.id, attempt_count)
        if state.type in TERMINAL:
            await self.store.release_subjects(u, run.id)
            event = _terminal_event(state)
            row = await self.store.insert_event(u, run=run, event_type=event, now=now)
            await self.hooks.on_event(u, row, self.view(settled))
            if run.parent_run_id is not None:
                woken = await self.store.wake_parent(
                    u, run.parent_run_id, state=states.RESUMED, now=now
                )
                if woken is not None:
                    await self.store.notify(u, run.parent_run_id)
            else:
                await self.hooks.on_run_terminal(
                    u,
                    self.view(settled),
                    None if state.type is StateType.COMPLETED else (error or state.name),
                )

    # ---- child creation (own unit) ----
    async def create_child(
        self,
        *,
        parent: RunRow,
        defn: ActionDefinition[Any],
        params: dict[str, Any],
        key: str,
        position: int,
        after: list[str],
        kind: str,
        gate_roles: tuple[int, ...] | None,
        on_failure: str,
    ) -> RunRow:
        if parent.depth + 1 > self.max_depth:
            raise ProgrammingError(f"child depth exceeds max_depth={self.max_depth}")
        now = self.now()
        async with self.store.unit(parent) as u:
            if kind == "gate":
                row = await self._insert_gate(
                    u,
                    parent=parent,
                    roles=gate_roles or (),
                    message=params.get("message"),
                    position=position,
                    key=key,
                )
                await self.store.insert_event(u, run=parent, event_type="run.held", now=now)
                return row
            policy = await self.hooks.before_child(u, parent, defn, params)
            skip = policy if isinstance(policy, Skip) else None
            approval = policy if isinstance(policy, Approval) else None
            case_no = (
                await self.store.next_case_no(u, parent.tenant_id)
                if approval is not None and approval.case_number
                else None
            )
            if skip is not None:
                child_state = states.SKIPPED.with_message(skip.reason)
            elif approval is not None:
                child_state = states.AWAITING_APPROVAL
            else:
                child_state = states.SCHEDULED
            new = NewRun(
                uid=self.uid_factory(),
                tenant_id=parent.tenant_id,
                action_key=defn.key,
                idempotency_key=f"{parent.idempotency_key}/{key}",
                params=self.codec.params(params),
                params_hash=self.codec.digest(params),
                meta=dict(defn.options.meta),
                state=child_state,
                # a skipped or held child is not schedulable until (respectively) never / approval
                scheduled_at=None if (skip is not None or approval is not None) else now,
                requested_by=parent.requested_by,
                contact_id=parent.contact_id,
                parent_run_id=parent.id,
                root_run_id=parent.root_run_id or parent.id,
                depth=parent.depth + 1,
                key=key,
                position=position,
                after_keys=after,
                on_failure=on_failure,
                case_no=case_no,
                approver_role_ids=list(approval.roles) if approval is not None else [],
            )
            inserted = await self.store.insert_run(u, new)
            if inserted is None:
                raise InternalError(f"child insert collided for {new.idempotency_key}")
            if skip is not None:
                await self.store.insert_event(u, run=inserted, event_type="run.completed", now=now)
            elif approval is not None:
                # The firm gates this action: hold the child on its own approval gate (findable and
                # decidable in the approvals queue) rather than running it; the parent then suspends
                # on it like any other open child. Paused, so no NOTIFY.
                await self._insert_gate(
                    u,
                    parent=inserted,
                    roles=tuple(approval.roles),
                    message=approval.message,
                    position=0,
                )
                ev = await self.store.insert_event(u, run=inserted, event_type="run.held", now=now)
                # The app hears about a gated child the way it hears about a held root, so the
                # approvers are told either way.
                await self.hooks.on_event(u, ev, self.view(inserted))
            else:
                await self.store.insert_event(u, run=inserted, event_type="run.created", now=now)
                await self.store.notify(u, inserted.id)
            return inserted

    async def revive_run(self, row: RunRow) -> None:
        """Reschedule a run (and its failed descendants) in a committed unit — used to recover a
        crashed idempotent child so the next claim can pick it up (claim only takes 'scheduled')."""
        async with self.store.unit(row) as u:
            await self.store.revive(u, row.id, now=self.now())

    async def mark_needs_attention(self, row: RunRow, message: str) -> None:
        async with self.store.unit(row) as u:
            await self.store.settle_run(
                u,
                row.id,
                None,
                state=states.NEEDS_ATTENTION.with_message(message),
                ended=True,
                fields=SettleFields(error=message),
                now=self.now(),
            )

    # ---- decide / cancel / retry / resume ----
    async def decide(
        self,
        u: Any,
        run_uid: str,
        *,
        decision: str,
        actor_id: str | None,
        role_ids: Sequence[int],
        note: str | None = None,
    ) -> RunView:
        if decision not in ("approve", "reject"):
            raise ApprovalError("decision must be approve or reject", status=422)
        run = await self.store.run_by_uid(u, run_uid, for_update=True)
        if run is None:
            raise NotFound(run_uid)
        gate = await self._open_gate(u, run)
        if gate is None:
            raise ApprovalError("this run is not awaiting approval", status=409)
        if actor_id is not None and run.requested_by == actor_id:
            raise ApprovalError("you cannot decide a run you raised", status=403)
        allowed = set(gate.gate_roles or [])
        if allowed and not (allowed & set(role_ids)):
            raise ApprovalError("your role cannot decide this run", status=403)
        now = self.now()
        if decision == "reject":
            await self.store.settle_run(
                u,
                gate.id,
                None,
                state=states.REJECTED.with_message(note),
                ended=True,
                fields=SettleFields(
                    decision="rejected", decided_by=actor_id, decided_at=now, decision_note=note
                ),
                now=now,
            )
            settled = await self.store.settle_run(
                u,
                run.id,
                None,
                state=states.REJECTED.with_message(note),
                ended=True,
                fields=SettleFields(
                    decision="rejected", decided_by=actor_id, decided_at=now, decision_note=note
                ),
                now=now,
            )
            await self.store.release_subjects(u, run.id)
            ev = await self.store.insert_event(
                u, run=run, event_type="run.rejected", actor_id=actor_id, now=now
            )
            await self.hooks.on_event(u, ev, self.view(settled or run))
            if run.parent_run_id is None:
                await self.hooks.on_run_terminal(u, self.view(settled or run), "rejected")
            else:
                # A gated CHILD was rejected: wake the paused parent so it re-runs and propagates
                # the rejection, the way _finalise wakes a parent on a terminal child. Without this
                # the parent composite (and its subject lock) is stranded paused forever.
                woken = await self.store.wake_parent(
                    u, run.parent_run_id, state=states.RESUMED, now=now
                )
                if woken is not None:
                    await self.store.notify(u, run.parent_run_id)
            return self.view(settled or run)
        # approve
        await self.store.settle_run(
            u,
            gate.id,
            None,
            state=states.APPROVED,
            ended=True,
            fields=SettleFields(
                decision="approved", decided_by=actor_id, decided_at=now, decision_note=note
            ),
            now=now,
        )
        if run.meta.get("manual"):
            # A MANUAL action has no provider call: accepting it completes the run by hand. It must
            # NOT schedule the body — that would run the real action (e.g. send a payment), the
            # opposite of "done manually". Completing it at the gate-release is generic across any
            # action a firm marks manual, not a per-body meta.manual check.
            settled = await self.store.settle_run(
                u,
                run.id,
                None,
                state=states.COMPLETED.with_message("Completed manually"),
                ended=True,
                fields=SettleFields(
                    result={"manual": True, "detail": "Completed manually."},
                    write_result=True,
                    decision="approved",
                    decided_by=actor_id,
                    decided_at=now,
                    decision_note=note,
                ),
                now=now,
            )
            await self.store.release_subjects(u, run.id)
            ev = await self.store.insert_event(
                u, run=run, event_type=_terminal_event(states.COMPLETED), actor_id=actor_id, now=now
            )
            await self.hooks.on_event(u, ev, self.view(settled or run))
            if run.parent_run_id is None:
                await self.hooks.on_run_terminal(u, self.view(settled or run), None)
            else:
                woken = await self.store.wake_parent(
                    u, run.parent_run_id, state=states.RESUMED, now=now
                )
                if woken is not None:
                    await self.store.notify(u, run.parent_run_id)
            return self.view(settled or run)
        settled = await self.store.settle_run(
            u,
            run.id,
            None,
            state=states.SCHEDULED,
            scheduled_at=now,
            fields=SettleFields(
                decision="approved", decided_by=actor_id, decided_at=now, decision_note=note
            ),
            now=now,
        )
        ev = await self.store.insert_event(
            u, run=run, event_type="run.approved", actor_id=actor_id, now=now
        )
        await self.hooks.on_event(u, ev, self.view(settled or run))
        await self.store.notify(u, run.id)
        return self.view(settled or run)

    async def _open_gate(self, u: Any, run: RunRow) -> RunRow | None:
        for child in await self.store.children(u, run.id):
            if child.kind == "gate" and child.state.type is StateType.PAUSED:
                return child
        return None

    async def cancel(
        self, u: Any, run_uid: str, *, actor_id: str | None = None, note: str | None = None
    ) -> RunView:
        run = await self.store.run_by_uid(u, run_uid, for_update=True)
        if run is None:
            raise NotFound(run_uid)
        if run.state.type in TERMINAL:
            return self.view(run)
        now = self.now()
        settled = await self.store.settle_run(
            u,
            run.id,
            None,
            state=states.CANCELLED.with_message(note),
            ended=True,
            fields=SettleFields(decision_note=note),
            now=now,
        )
        await self.store.release_subjects(u, run.id)
        ev = await self.store.insert_event(
            u, run=run, event_type="run.cancelled", actor_id=actor_id, now=now
        )
        await self.hooks.on_event(u, ev, self.view(settled or run))
        # Cancelling a composite cancels what it was waiting on: its open children, their open
        # children, and any gate still holding. Without this a cancelled parent left its subtree
        # live — a child Waiting on SEI kept polling and would have opened the account for a run
        # nobody wanted. A child that is RUNNING right now is left to finish its attempt (its
        # writes are fenced on a lease we must not pull from under it); if it reschedules itself
        # the orphan guard in ``_advance_claimed`` cancels it on its next claim.
        await self._cancel_open_descendants(u, run, actor_id=actor_id, now=now)
        if run.parent_run_id is None:
            await self.hooks.on_run_terminal(u, self.view(settled or run), "cancelled")
        else:
            # A cancelled CHILD must wake its paused parent too (same gap as reject), or the parent
            # composite and its subject lock hang forever.
            woken = await self.store.wake_parent(
                u, run.parent_run_id, state=states.RESUMED, now=now
            )
            if woken is not None:
                await self.store.notify(u, run.parent_run_id)
        return self.view(settled or run)

    async def _cancel_open_descendants(
        self, u: Any, parent: RunRow, *, actor_id: str | None, now: datetime
    ) -> None:
        message = "The parent run was cancelled."
        for child in await self.store.children(u, parent.id):
            if child.state.type in TERMINAL or child.state.type is StateType.RUNNING:
                # A running child keeps its lease; see ``cancel``. Its own subtree, if any, is
                # still visited below so a paused grandchild does not outlive a cancelled root.
                if child.state.type is StateType.RUNNING:
                    await self._cancel_open_descendants(u, child, actor_id=actor_id, now=now)
                continue
            settled = await self.store.settle_run(
                u,
                child.id,
                None,
                state=states.CANCELLED.with_message(message),
                ended=True,
                fields=SettleFields(error=message),
                now=now,
            )
            await self.store.release_subjects(u, child.id)
            ev = await self.store.insert_event(
                u, run=child, event_type="run.cancelled", actor_id=actor_id, now=now
            )
            await self.hooks.on_event(u, ev, self.view(settled or child))
            await self._cancel_open_descendants(u, child, actor_id=actor_id, now=now)

    async def retry(self, u: Any, run_uid: str) -> RunView:
        run = await self.store.run_by_uid(u, run_uid, for_update=True)
        if run is None:
            raise NotFound(run_uid)
        now = self.now()
        await self.store.revive(u, run.id, now=now)
        ev = await self.store.insert_event(u, run=run, event_type="run.retry_requested", now=now)
        reloaded = await self.store.run_by_id(u, run.id)
        await self.hooks.on_event(u, ev, self.view(reloaded or run))
        await self.store.notify(u, run.id)
        return self.view(reloaded or run)

    async def resume(self, u: Any, run_uid: str) -> RunView:
        return await self.retry(u, run_uid)

    # ---- reads ----
    async def _children(self, run_id: int) -> list[RunRow]:
        async with self.store.unit() as u:
            return await self.store.children(u, run_id)

    async def _last_attempt(self, run_id: int) -> AttemptRow | None:
        async with self.store.unit() as u:
            return await self.store.last_attempt(u, run_id)

    async def reload(self, run_id: int) -> RunRow | None:
        async with self.store.unit() as u:
            return await self.store.run_by_id(u, run_id)

    async def _must_reload(self, run_id: int) -> RunRow:
        row = await self.reload(run_id)
        if row is None:
            raise InternalError(f"run {run_id} vanished")
        return row

    async def get(self, u: Any, run_uid: str, *, tenant_id: int) -> RunDetail:
        run = await self.store.run_by_uid(u, run_uid, tenant_id=tenant_id)
        if run is None:
            raise NotFound(run_uid)
        return await self._detail(u, run)

    async def _detail(self, u: Any, run: RunRow) -> RunDetail:
        attempts = [self._attempt_view(a) for a in await self.store.attempts_of(u, [run.id])]
        attempts.sort(key=lambda a: a.attempt_no)
        children = await self.store.children(u, run.id)
        child_details = [await self._detail(u, c) for c in children]
        events = (
            await self.store.events_of(u, run.root_run_id or run.id)
            if run.parent_run_id is None
            else []
        )
        return RunDetail(
            run=self.view(run),
            after_keys=run.after_keys,
            attempts=attempts,
            children=child_details,
            events=events,
        )


class InternalError(Exception):
    pass


def _is_leaf_error(exc: BaseException) -> bool:
    from baton.errors import NotReady, Permanent, Retry

    return isinstance(exc, Retry | NotReady | Permanent | Skip)


def _derive_final(children: Sequence[RunRow]) -> State:
    failed = [c for c in children if c.state.type is StateType.FAILED]
    if any((c.on_failure or "fail") != "continue" for c in failed):
        return states.NEEDS_ATTENTION
    if failed:
        return states.PARTIAL
    return states.COMPLETED


def _terminal_event(state: State) -> str:
    if state.type is StateType.COMPLETED:
        return "run.completed"
    if state.type is StateType.CANCELLED:
        return "run.cancelled" if state.name != "Rejected" else "run.rejected"
    if state.name == "NeedsAttention":
        return "run.needs_attention"
    return "run.failed"


__all__ = ["Engine", "AdvanceResult", "InternalError"]
