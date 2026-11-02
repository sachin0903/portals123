"""A dict-backed store and a fake clock, for unit-testing the replay algorithm with no database.

It mirrors ``PostgresStore``'s observable behaviour: idempotency collapse, lease fencing (a
write with the wrong token writes nothing), the one-open-subject rule, and the parent wake-up
that only fires when no child is still open. A ``Unit`` snapshots state on entry and restores it
on an exception, so a rolled-back transaction leaves nothing behind.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from baton.context import Subject
from baton.errors import SubjectBusy
from baton.hooks import Hooks, NoopHooks
from baton.model import AttemptRow, EventRow, RunRow, SubjectRow
from baton.states import State, StateType
from baton.store import NewAttempt, NewRun, SettleFields, SweepReport


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


_RUN_FIELDS: tuple[str, ...] = tuple(RunRow.__slots__)


class _MemUnit:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    @property
    def raw(self) -> Any:  # a leaf body would use ctx.session; memory tests use .direct or spies
        return self


class MemoryStore:
    schema = "memory"

    def __init__(self, *, clock: FakeClock | None = None, hooks: Hooks | None = None) -> None:
        self.clock = clock or FakeClock()
        self.hooks: Hooks = hooks or NoopHooks()
        self._runs: dict[int, dict[str, Any]] = {}
        self._attempts: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._subjects: list[dict[str, Any]] = []
        self._seq = 0
        self._case: dict[int, int] = {}
        self.notified: list[int] = []

    # ---- unit of work ----
    @asynccontextmanager
    async def unit(self, run: RunRow | None = None) -> Any:
        snap = (
            copy.deepcopy(self._runs),
            copy.deepcopy(self._attempts),
            copy.deepcopy(self._events),
            copy.deepcopy(self._subjects),
            dict(self._case),
            self._seq,
        )
        u = _MemUnit(self)
        await self.hooks.begin_session(u, run)  # type: ignore[arg-type]
        try:
            yield u
        except BaseException:
            (
                self._runs,
                self._attempts,
                self._events,
                self._subjects,
                self._case,
                self._seq,
            ) = snap
            raise

    def bind(self, session: Any) -> _MemUnit:
        return _MemUnit(self)

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def _row(self, rec: dict[str, Any]) -> RunRow:
        data = {k: rec.get(k) for k in _RUN_FIELDS}
        data["state"] = State(StateType(rec["state_type"]), rec["state_name"], rec["state_message"])
        return RunRow(**data)  # type: ignore[arg-type]

    # ---- run lifecycle ----
    async def insert_run(self, u: Any, new: NewRun) -> RunRow | None:
        for rec in self._runs.values():
            if rec["tenant_id"] == new.tenant_id and rec["idempotency_key"] == new.idempotency_key:
                return self._row(rec)
        rid = self._next()
        root = new.root_run_id if new.root_run_id is not None else rid
        rec = {
            "id": rid,
            "uid": new.uid,
            "tenant_id": new.tenant_id,
            "action_key": new.action_key,
            "idempotency_key": new.idempotency_key,
            "kind": new.kind,
            "leaf": None,
            "params": new.params,
            "result": None,
            "meta": dict(new.meta),
            "parent_run_id": new.parent_run_id,
            "root_run_id": root,
            "depth": new.depth,
            "key": new.key,
            "position": new.position,
            "after_keys": list(new.after_keys),
            "requested_by": new.requested_by,
            "requested_at": self.clock.now(),
            "contact_id": new.contact_id,
            "subject_kind": new.subject_kind,
            "subject_uid": new.subject_uid,
            "state_type": new.state.type.value,
            "state_name": new.state.name,
            "state_message": new.state.message,
            "scheduled_at": new.scheduled_at,
            "lease_token": None,
            "lease_owner": None,
            "lease_until": None,
            "timeout_at": None,
            "started_at": None,
            "ended_at": None,
            "passes": 0,
            "attempt_count": 0,
            "wait_started_at": None,
            "error": None,
            "case_no": new.case_no,
            "justification": new.justification,
            "approver_role_ids": list(new.approver_role_ids),
            "gate_roles": new.gate_roles,
            "gate_message": new.gate_message,
            "decision": None,
            "decided_by": None,
            "decided_at": None,
            "decision_note": None,
            "on_failure": new.on_failure,
        }
        self._runs[rid] = rec
        return self._row(rec)

    async def run_by_key(self, u: Any, tenant_id: int, idempotency_key: str) -> RunRow | None:
        for rec in self._runs.values():
            if rec["tenant_id"] == tenant_id and rec["idempotency_key"] == idempotency_key:
                return self._row(rec)
        return None

    async def run_by_uid(
        self, u: Any, uid: str, *, tenant_id: int | None = None, for_update: bool = False
    ) -> RunRow | None:
        for rec in self._runs.values():
            if rec["uid"] == uid and (tenant_id is None or rec["tenant_id"] == tenant_id):
                return self._row(rec)
        return None

    async def run_by_id(self, u: Any, run_id: int) -> RunRow | None:
        rec = self._runs.get(run_id)
        return self._row(rec) if rec else None

    async def claim_run(
        self,
        u: Any,
        *,
        worker_id: str,
        token: str,
        lease_seconds: float,
        now: datetime,
        run_id: int | None = None,
        timeout_seconds: float | None = None,
    ) -> RunRow | None:
        candidates = sorted(
            (
                rec
                for rec in self._runs.values()
                if rec["state_type"] == "scheduled"
                and rec["scheduled_at"] is not None
                and rec["scheduled_at"] <= now
                and (rec["lease_until"] is None or rec["lease_until"] < now)
                and (run_id is None or rec["id"] == run_id)
            ),
            key=lambda r: (r["scheduled_at"], r["id"]),
        )
        if not candidates:
            return None
        rec = candidates[0]
        rec["state_type"] = "running"
        rec["state_name"] = "Running"
        rec["lease_token"] = token
        rec["lease_owner"] = worker_id
        rec["lease_until"] = now + timedelta(seconds=lease_seconds)
        rec["started_at"] = rec["started_at"] or now
        if rec["timeout_at"] is None and timeout_seconds is not None:
            rec["timeout_at"] = now + timedelta(seconds=timeout_seconds)
        rec["passes"] += 1
        return self._row(rec)

    async def renew_lease(
        self, u: Any, run_id: int, token: str, lease_seconds: float, now: datetime
    ) -> bool:
        rec = self._runs.get(run_id)
        if rec is None or rec["lease_token"] != token:
            return False
        rec["lease_until"] = now + timedelta(seconds=lease_seconds)
        return True

    async def bump_attempt(self, u: Any, run_id: int, token: str, now: datetime) -> int:
        rec = self._runs[run_id]
        if rec["lease_token"] != token:
            raise RuntimeError("lease lost")
        rec["attempt_count"] += 1
        return int(rec["attempt_count"])

    async def mark_passes(self, u: Any, run_id: int, leaf: bool) -> None:
        self._runs[run_id]["leaf"] = leaf

    async def set_attempt_count(self, u: Any, run_id: int, count: int) -> None:
        self._runs[run_id]["attempt_count"] = count

    def _set_scheduled(self, rec: dict[str, Any], now: datetime) -> None:
        rec["state_type"], rec["state_name"] = "scheduled", "Resumed"
        rec["scheduled_at"] = now
        rec["lease_token"] = rec["lease_until"] = rec["lease_owner"] = None
        rec["ended_at"] = None

    def _is_descendant(self, rec: dict[str, Any], ancestor_id: int) -> bool:
        pid = rec["parent_run_id"]
        while pid is not None:
            if pid == ancestor_id:
                return True
            parent = self._runs.get(pid)
            pid = parent["parent_run_id"] if parent else None
        return False

    async def revive(self, u: Any, run_id: int, *, now: datetime) -> None:
        target = self._runs.get(run_id)
        if target is None:
            return
        self._set_scheduled(target, now)
        for rec in self._runs.values():
            if self._is_descendant(rec, run_id) and rec["state_type"] in ("failed", "crashed"):
                self._set_scheduled(rec, now)
        pid = target["parent_run_id"]
        while pid is not None:
            anc = self._runs.get(pid)
            if anc is None:
                break
            if anc["state_type"] in ("paused", "failed", "crashed"):
                self._set_scheduled(anc, now)
            pid = anc["parent_run_id"]

    async def settle_run(
        self,
        u: Any,
        run_id: int,
        token: str | None,
        *,
        state: State,
        scheduled_at: datetime | None = None,
        ended: bool = False,
        fields: SettleFields | None = None,
        now: datetime,
    ) -> RunRow | None:
        rec = self._runs.get(run_id)
        if rec is None:
            return None
        if token is not None and rec["lease_token"] != token:
            return None
        rec["state_type"] = state.type.value
        rec["state_name"] = state.name
        rec["state_message"] = state.message
        rec["state_at"] = now
        rec["scheduled_at"] = scheduled_at
        if state.type != StateType.RUNNING:
            rec["lease_token"] = None
            rec["lease_owner"] = None
            rec["lease_until"] = None
        if ended:
            rec["ended_at"] = now
        f = fields or SettleFields()
        if f.write_result:
            rec["result"] = f.result
        if f.write_wait_started_at:
            rec["wait_started_at"] = f.wait_started_at
        if f.leaf is not None:
            rec["leaf"] = f.leaf
        if f.error is not None:
            rec["error"] = f.error
        for attr in ("decision", "decided_by", "decided_at", "decision_note", "case_no"):
            val = getattr(f, attr)
            if val is not None:
                rec[attr] = val
        if f.approver_role_ids is not None:
            rec["approver_role_ids"] = list(f.approver_role_ids)
        return self._row(rec)

    # ---- children ----
    async def children(self, u: Any, parent_run_id: int) -> list[RunRow]:
        kids = [r for r in self._runs.values() if r["parent_run_id"] == parent_run_id]
        kids.sort(key=lambda r: r["position"] if r["position"] is not None else 0)
        return [self._row(r) for r in kids]

    async def open_children_exist(self, u: Any, parent_run_id: int) -> bool:
        return any(
            r["parent_run_id"] == parent_run_id
            and r["state_type"] not in ("completed", "failed", "cancelled")
            for r in self._runs.values()
        )

    async def wake_parent(
        self, u: Any, parent_run_id: int, *, state: State, now: datetime
    ) -> RunRow | None:
        rec = self._runs.get(parent_run_id)
        if rec is None or rec["state_type"] != "paused":
            return None
        if await self.open_children_exist(u, parent_run_id):
            return None
        rec["state_type"] = state.type.value
        rec["state_name"] = state.name
        rec["scheduled_at"] = now
        rec["state_at"] = now
        return self._row(rec)

    async def next_case_no(self, u: Any, tenant_id: int) -> int:
        nxt = self._case.get(tenant_id, 0) + 1
        self._case[tenant_id] = nxt
        return nxt

    # ---- subjects / events / attempts ----
    async def insert_subjects(
        self, u: Any, run: RunRow, subjects: Sequence[Any], *, blocking: bool
    ) -> None:
        for s in subjects:
            kind, uid, entity_id = _subject_tuple(s)
            if blocking:
                for ex in self._subjects:
                    if (
                        ex["blocking"]
                        and ex["tenant_id"] == run.tenant_id
                        and ex["action_key"] == run.action_key
                        and ex["kind"] == kind
                        and ex["uid"] == uid
                    ):
                        raise SubjectBusy(f"{run.action_key} is already open for {kind}:{uid}")
            self._subjects.append(
                {
                    "id": self._next(),
                    "run_id": run.id,
                    "tenant_id": run.tenant_id,
                    "action_key": run.action_key,
                    "kind": kind,
                    "uid": uid,
                    "entity_id": entity_id,
                    "blocking": blocking,
                }
            )

    async def release_subjects(self, u: Any, run_id: int) -> None:
        for s in self._subjects:
            if s["run_id"] == run_id:
                s["blocking"] = False

    async def subjects_of(self, u: Any, run_id: int) -> list[SubjectRow]:
        return [
            SubjectRow(
                kind=s["kind"], uid=s["uid"], entity_id=s["entity_id"], blocking=s["blocking"]
            )
            for s in self._subjects
            if s["run_id"] == run_id
        ]

    async def insert_event(
        self,
        u: Any,
        *,
        run: RunRow,
        event_type: str,
        actor_id: str | None = None,
        payload: dict[str, Any] | None = None,
        now: datetime,
    ) -> EventRow:
        rec = {
            "id": self._next(),
            "tenant_id": run.tenant_id,
            "run_id": run.id,
            "root_run_id": run.root_run_id or run.id,
            "event_type": event_type,
            "actor_id": actor_id,
            "payload": payload or {},
            "created_at": now,
        }
        self._events.append(rec)
        return EventRow(**rec)  # type: ignore[arg-type]

    async def events_of(self, u: Any, root_run_id: int) -> list[EventRow]:
        return [EventRow(**e) for e in self._events if e["root_run_id"] == root_run_id]

    async def insert_attempt(self, u: Any, new: NewAttempt) -> AttemptRow:
        rec = {"id": self._next(), **asdict(new)}
        self._attempts.append(rec)
        return AttemptRow(**rec)

    async def last_attempt(self, u: Any, run_id: int) -> AttemptRow | None:
        rows = [a for a in self._attempts if a["run_id"] == run_id]
        return AttemptRow(**rows[-1]) if rows else None

    async def attempts_of(self, u: Any, run_ids: Sequence[int]) -> list[AttemptRow]:
        ids = set(run_ids)
        return [AttemptRow(**a) for a in self._attempts if a["run_id"] in ids]

    async def retries_used(self, u: Any, run_id: int) -> int:
        return sum(1 for a in self._attempts if a["run_id"] == run_id and a["outcome"] == "retry")

    # ---- worker ----
    async def sweep(self, u: Any, *, now: datetime, registry_idempotent: Any) -> SweepReport:
        report = SweepReport()
        for rec in self._runs.values():
            if rec["state_type"] == "running" and rec["lease_until"] and rec["lease_until"] < now:
                # A known leaf, OR any child (its leaf flag is still NULL): treat as crashed so the
                # parent's replay decides whether a re-run is safe — a non-idempotent leaf must not
                # be silently rescheduled and re-sent. Only roots resume. Mirrors PostgresStore.
                if rec["leaf"] or rec["parent_run_id"] is not None:
                    rec["state_type"], rec["state_name"] = "crashed", "Crashed"
                    rec["error"] = "attempt lease expired"
                    rec["lease_token"] = rec["lease_until"] = rec["lease_owner"] = None
                    # Numbered after the last row recorded for this run, and attempt_count moved up
                    # to match — see PostgresStore.sweep for why attempt_count alone drifts.
                    no = (
                        max(
                            (a["attempt_no"] for a in self._attempts if a["run_id"] == rec["id"]),
                            default=0,
                        )
                        + 1
                    )
                    rec["attempt_count"] = no
                    self._attempts.append(
                        {
                            "id": self._next(),
                            "run_id": rec["id"],
                            "root_run_id": rec["root_run_id"],
                            "tenant_id": rec["tenant_id"],
                            "attempt_no": no,
                            "worker": rec["lease_owner"],
                            "started_at": now,
                            "ended_at": now,
                            "duration_ms": None,
                            "outcome": "crashed",
                            "error": "attempt lease expired",
                            "error_type": None,
                            "method": None,
                            "url": None,
                            "status_code": None,
                            "request": None,
                            "response": None,
                        }
                    )
                    report.crashed_leaves += 1
                else:
                    rec["state_type"], rec["state_name"] = "scheduled", "Resumed"
                    rec["scheduled_at"] = now
                    rec["lease_token"] = rec["lease_until"] = rec["lease_owner"] = None
                    report.resumed_runs += 1
        for rec in self._runs.values():
            # Match PostgresStore: a paused run is exempt (paused time does not count toward the
            # timeout — an approval may wait for a person), and a still-leased running run is left
            # for its lease to expire first.
            if (
                rec["timeout_at"]
                and rec["timeout_at"] < now
                and rec["state_type"] in ("scheduled", "running")
                and (rec["lease_until"] is None or rec["lease_until"] < now)
            ):
                rec["state_type"], rec["state_name"] = "failed", "TimedOut"
                rec["ended_at"] = now
                rec["lease_token"] = rec["lease_until"] = None
                report.timed_out += 1
        return report

    async def notify(self, u: Any, run_id: int) -> None:
        self.notified.append(run_id)

    async def due_run_ids(self, u: Any, *, now: datetime, limit: int) -> list[int]:
        due = sorted(
            (
                r
                for r in self._runs.values()
                if r["state_type"] == "scheduled"
                and r["scheduled_at"] is not None
                and r["scheduled_at"] <= now
            ),
            key=lambda r: (r["scheduled_at"], r["id"]),
        )
        return [r["id"] for r in due[:limit]]


def _subject_tuple(s: Any) -> tuple[str, str, int | None]:
    if isinstance(s, Subject):
        return s.kind, s.uid, s.entity_id
    if isinstance(s, dict):
        return s["kind"], s["uid"], s.get("entity_id")
    return s.kind, s.uid, getattr(s, "entity_id", None)
