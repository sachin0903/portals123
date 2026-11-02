"""The Postgres-backed store. All of baton's SQL lives here.

Claims use ``FOR UPDATE SKIP LOCKED``; every write by a lease holder is fenced on the lease
token, so a reclaimed run's old holder writes nothing. jsonb and array columns are cast
explicitly. The schema name is interpolated (a trusted identifier, validated by ``baton.ddl``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from baton.codec import json_default
from baton.context import Subject
from baton.errors import SubjectBusy
from baton.hooks import Hooks, NoopHooks
from baton.model import AttemptRow, EventRow, RunRow, SubjectRow
from baton.states import State, StateType
from baton.store import NewAttempt, NewRun, SettleFields, SweepReport

_RUN_COLUMNS = (
    "id, uid, tenant_id, action_key, idempotency_key, kind, leaf, params, params_hash, result, "
    "meta, parent_run_id, root_run_id, depth, key, position, after_keys, requested_by, "
    "requested_at, contact_id, subject_kind, subject_uid, state_type, state_name, state_message, "
    "scheduled_at, lease_token, lease_owner, lease_until, timeout_at, started_at, ended_at, "
    "passes, attempt_count, wait_started_at, error, case_no, justification, approver_role_ids, "
    "gate_roles, gate_message, decision, decided_by, decided_at, decision_note, on_failure"
)

_RUN_COLUMNS_R = ", ".join("r." + c.strip() for c in _RUN_COLUMNS.split(","))


def _j(value: Any) -> str:
    return json.dumps(value, default=json_default)


def _maybe_j(value: Any) -> str | None:
    # `default=`: a vendor request carrying a Decimal or a date used to raise TypeError here, in
    # the middle of recording the attempt — the run stayed leased 'running' until the sweep
    # crashed it, and its parent failed with the serialiser's message.
    return None if value is None else json.dumps(value, default=json_default)


class _PgUnit:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def raw(self) -> AsyncSession:
        return self.session


class PostgresStore:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        schema: str = "baton",
        hooks: Hooks | None = None,
        case_lock_key: int = 179,
        clock: Any = None,
    ) -> None:
        if not schema.isidentifier():
            raise ValueError(f"schema must be a bare identifier, got {schema!r}")
        self.engine = engine
        self.schema = schema
        self.hooks: Hooks = hooks or NoopHooks()
        self.case_lock_key = case_lock_key
        # An optional clock (with .now()) the Engine reads for deterministic tests. In production
        # it is absent and the Engine uses the wall clock; the store never calls SQL now() for
        # logic, only the engine-provided :now, so the two stay consistent either way.
        if clock is not None:
            self.clock = clock
        self._maker = async_sessionmaker(engine, expire_on_commit=False)
        self._advisory = abs(hash(f"{schema}.runs")) % (2**31)

    def _t(self, name: str) -> str:
        return f"{self.schema}.{name}"

    def bind(self, session: AsyncSession) -> _PgUnit:
        """Wrap a caller-owned session as a unit, so Engine.start/decide can run inside the
        transaction that also writes the booked row. The caller owns commit and rollback."""
        return _PgUnit(session)

    @asynccontextmanager
    async def unit(self, run: RunRow | None = None) -> AsyncIterator[_PgUnit]:
        async with self._maker() as session:
            async with session.begin():
                await self.hooks.begin_session(session, run)
                yield _PgUnit(session)

    # ---- row mapping ----
    def _run(self, m: Any) -> RunRow:
        return RunRow(
            id=m["id"],
            uid=m["uid"],
            tenant_id=m["tenant_id"],
            action_key=m["action_key"],
            idempotency_key=m["idempotency_key"],
            kind=m["kind"],
            leaf=m["leaf"],
            params=m["params"] or {},
            result=m["result"],
            meta=m["meta"] or {},
            parent_run_id=m["parent_run_id"],
            root_run_id=m["root_run_id"],
            depth=m["depth"],
            key=m["key"],
            position=m["position"],
            after_keys=list(m["after_keys"] or []),
            requested_by=str(m["requested_by"]) if m["requested_by"] else None,
            requested_at=m["requested_at"],
            contact_id=m["contact_id"],
            subject_kind=m["subject_kind"],
            subject_uid=m["subject_uid"],
            state=State(StateType(m["state_type"]), m["state_name"], m["state_message"]),
            scheduled_at=m["scheduled_at"],
            lease_token=str(m["lease_token"]) if m["lease_token"] else None,
            lease_owner=m["lease_owner"],
            lease_until=m["lease_until"],
            timeout_at=m["timeout_at"],
            started_at=m["started_at"],
            ended_at=m["ended_at"],
            passes=m["passes"],
            attempt_count=m["attempt_count"],
            wait_started_at=m["wait_started_at"],
            error=m["error"],
            case_no=m["case_no"],
            justification=m["justification"],
            approver_role_ids=list(m["approver_role_ids"] or []),
            gate_roles=list(m["gate_roles"]) if m["gate_roles"] is not None else None,
            gate_message=m["gate_message"],
            decision=m["decision"],
            decided_by=str(m["decided_by"]) if m["decided_by"] else None,
            decided_at=m["decided_at"],
            decision_note=m["decision_note"],
            on_failure=m["on_failure"],
        )

    # ---- run lifecycle ----
    async def insert_run(self, u: Any, new: NewRun) -> RunRow | None:
        sql = text(
            f"INSERT INTO {self._t('runs')} "
            "(uid, tenant_id, action_key, idempotency_key, kind, params, params_hash, meta, "
            " parent_run_id, root_run_id, depth, key, position, after_keys, requested_by, "
            " contact_id, subject_kind, subject_uid, state_type, state_name, state_message, "
            " scheduled_at, case_no, justification, approver_role_ids, gate_roles, gate_message, "
            " on_failure) "
            "VALUES (:uid, :tenant_id, :action_key, :idem, :kind, CAST(:params AS jsonb), "
            " :params_hash, CAST(:meta AS jsonb), :parent_run_id, :root_run_id, :depth, :key, "
            " :position, CAST(:after_keys AS text[]), CAST(:requested_by AS uuid), :contact_id, "
            " :subject_kind, :subject_uid, :state_type, :state_name, :state_message, "
            " :scheduled_at, :case_no, :justification, CAST(:approver_role_ids AS int[]), "
            " CAST(:gate_roles AS int[]), :gate_message, :on_failure) "
            "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING "
            f"RETURNING {_RUN_COLUMNS}"
        )
        params = {
            "uid": new.uid,
            "tenant_id": new.tenant_id,
            "action_key": new.action_key,
            "idem": new.idempotency_key,
            "kind": new.kind,
            "params": _j(new.params),
            "params_hash": new.params_hash,
            "meta": _j(new.meta),
            "parent_run_id": new.parent_run_id,
            "root_run_id": new.root_run_id,
            "depth": new.depth,
            "key": new.key,
            "position": new.position,
            "after_keys": list(new.after_keys),
            "requested_by": new.requested_by,
            "contact_id": new.contact_id,
            "subject_kind": new.subject_kind,
            "subject_uid": new.subject_uid,
            "state_type": new.state.type.value,
            "state_name": new.state.name,
            "state_message": new.state.message,
            "scheduled_at": new.scheduled_at,
            "case_no": new.case_no,
            "justification": new.justification,
            "approver_role_ids": list(new.approver_role_ids),
            "gate_roles": list(new.gate_roles) if new.gate_roles is not None else None,
            "gate_message": new.gate_message,
            "on_failure": new.on_failure,
        }
        row = (await u.session.execute(sql, params)).mappings().one_or_none()
        if row is None:
            return await self.run_by_key(u, new.tenant_id, new.idempotency_key)
        # A root keeps root_run_id NULL (the parent/root CHECK requires it); callers read the
        # effective root as ``row.root_run_id or row.id``.
        return self._run(row)

    def _select_by_id(self) -> Any:
        return text(f"SELECT {_RUN_COLUMNS} FROM {self._t('runs')} WHERE id = :id")

    async def run_by_key(self, u: Any, tenant_id: int, idempotency_key: str) -> RunRow | None:
        row = (
            (
                await u.session.execute(
                    text(
                        f"SELECT {_RUN_COLUMNS} FROM {self._t('runs')} "
                        "WHERE tenant_id = :t AND idempotency_key = :k"
                    ),
                    {"t": tenant_id, "k": idempotency_key},
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._run(row) if row else None

    async def run_by_uid(
        self, u: Any, uid: str, *, tenant_id: int | None = None, for_update: bool = False
    ) -> RunRow | None:
        clause = " AND tenant_id = :t" if tenant_id is not None else ""
        lock = " FOR UPDATE" if for_update else ""
        row = (
            (
                await u.session.execute(
                    text(
                        f"SELECT {_RUN_COLUMNS} FROM {self._t('runs')} WHERE uid = :u{clause}{lock}"
                    ),
                    {"u": uid, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._run(row) if row else None

    async def run_by_id(self, u: Any, run_id: int) -> RunRow | None:
        row = (
            (await u.session.execute(self._select_by_id(), {"id": run_id})).mappings().one_or_none()
        )
        return self._run(row) if row else None

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
        sql = text(
            f"WITH next AS ( "
            f"  SELECT id FROM {self._t('runs')} "
            "   WHERE state_type = 'scheduled' AND scheduled_at <= :now "
            "     AND (lease_until IS NULL OR lease_until < :now) "
            "     AND (CAST(:run_id AS bigint) IS NULL OR id = :run_id) "
            "   ORDER BY scheduled_at, id FOR UPDATE SKIP LOCKED LIMIT 1 "
            ") "
            f"UPDATE {self._t('runs')} r SET state_type = 'running', state_name = 'Running', "
            "  state_at = :now, lease_token = CAST(:token AS uuid), lease_owner = :worker, "
            "  lease_until = :now + make_interval(secs => CAST(:lease AS double precision)), "
            "  started_at = COALESCE(r.started_at, :now), "
            "  timeout_at = COALESCE(r.timeout_at, CASE WHEN CAST(:timeout AS double precision) IS NULL THEN NULL "
            "    ELSE :now + make_interval(secs => CAST(:timeout AS double precision)) END), "
            "  passes = r.passes + 1 "
            f"FROM next WHERE r.id = next.id RETURNING {_RUN_COLUMNS_R}"
        )
        row = (
            (
                await u.session.execute(
                    sql,
                    {
                        "now": now,
                        "run_id": run_id,
                        "token": token,
                        "worker": worker_id,
                        "lease": lease_seconds,
                        "timeout": timeout_seconds,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._run(row) if row else None

    async def renew_lease(
        self, u: Any, run_id: int, token: str, lease_seconds: float, now: datetime
    ) -> bool:
        res = await u.session.execute(
            text(
                f"UPDATE {self._t('runs')} SET lease_until = :now + make_interval(secs => CAST(:lease AS double precision)) "
                "WHERE id = :id AND lease_token = CAST(:token AS uuid)"
            ),
            {"id": run_id, "token": token, "lease": lease_seconds, "now": now},
        )
        return bool(res.rowcount)

    async def bump_attempt(self, u: Any, run_id: int, token: str, now: datetime) -> int:
        row = (
            await u.session.execute(
                text(
                    f"UPDATE {self._t('runs')} SET attempt_count = attempt_count + 1 "
                    "WHERE id = :id AND lease_token = CAST(:token AS uuid) RETURNING attempt_count"
                ),
                {"id": run_id, "token": token},
            )
        ).scalar_one_or_none()
        if row is None:
            raise RuntimeError("lease lost")
        return int(row)

    async def set_attempt_count(self, u: Any, run_id: int, count: int) -> None:
        await u.session.execute(
            text(f"UPDATE {self._t('runs')} SET attempt_count = :c WHERE id = :id"),
            {"c": count, "id": run_id},
        )

    async def mark_passes(self, u: Any, run_id: int, leaf: bool) -> None:
        await u.session.execute(
            text(f"UPDATE {self._t('runs')} SET leaf = :leaf WHERE id = :id"),
            {"leaf": leaf, "id": run_id},
        )

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
        f = fields or SettleFields()
        sets = [
            "state_type = :state_type",
            "state_name = :state_name",
            "state_message = :state_message",
            "state_at = :now",
            "scheduled_at = :scheduled_at",
        ]
        params: dict[str, Any] = {
            "id": run_id,
            "state_type": state.type.value,
            "state_name": state.name,
            "state_message": state.message,
            "scheduled_at": scheduled_at,
            "now": now,
        }
        if state.type is not StateType.RUNNING:
            sets += ["lease_token = NULL", "lease_owner = NULL", "lease_until = NULL"]
        if ended:
            sets.append("ended_at = :now")
        if f.write_result:
            sets.append("result = CAST(:result AS jsonb)")
            params["result"] = _maybe_j(f.result)
        if f.write_wait_started_at:
            sets.append("wait_started_at = :wait_started_at")
            params["wait_started_at"] = f.wait_started_at
        if f.leaf is not None:
            sets.append("leaf = :leaf")
            params["leaf"] = f.leaf
        if f.error is not None:
            sets.append("error = :error")
            params["error"] = f.error
        for attr in ("decision", "decided_by", "decided_at", "decision_note", "case_no"):
            val = getattr(f, attr)
            if val is not None:
                if attr == "decided_by":
                    sets.append("decided_by = CAST(:decided_by AS uuid)")
                else:
                    sets.append(f"{attr} = :{attr}")
                params[attr] = val
        if f.approver_role_ids is not None:
            sets.append("approver_role_ids = CAST(:approver_role_ids AS int[])")
            params["approver_role_ids"] = list(f.approver_role_ids)
        where = "WHERE id = :id"
        if token is not None:
            where += " AND lease_token = CAST(:token AS uuid)"
            params["token"] = token
        row = (
            (
                await u.session.execute(
                    text(
                        f"UPDATE {self._t('runs')} SET {', '.join(sets)} {where} RETURNING {_RUN_COLUMNS}"
                    ),
                    params,
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._run(row) if row else None

    # ---- children ----
    async def children(self, u: Any, parent_run_id: int) -> list[RunRow]:
        rows = (
            (
                await u.session.execute(
                    text(
                        f"SELECT {_RUN_COLUMNS} FROM {self._t('runs')} "
                        "WHERE parent_run_id = :p ORDER BY position NULLS LAST, id"
                    ),
                    {"p": parent_run_id},
                )
            )
            .mappings()
            .all()
        )
        return [self._run(r) for r in rows]

    async def open_children_exist(self, u: Any, parent_run_id: int) -> bool:
        found = (
            await u.session.execute(
                text(
                    f"SELECT 1 FROM {self._t('runs')} WHERE parent_run_id = :p "
                    "AND state_type NOT IN ('completed','failed','cancelled') LIMIT 1"
                ),
                {"p": parent_run_id},
            )
        ).first()
        return found is not None

    async def wake_parent(
        self, u: Any, parent_run_id: int, *, state: State, now: datetime
    ) -> RunRow | None:
        row = (
            (
                await u.session.execute(
                    text(
                        f"UPDATE {self._t('runs')} SET state_type = :st, state_name = :sn, "
                        "  scheduled_at = :now, state_at = :now "
                        "WHERE id = :p AND state_type = 'paused' AND NOT EXISTS ("
                        f"  SELECT 1 FROM {self._t('runs')} c WHERE c.parent_run_id = :p "
                        "    AND c.state_type NOT IN ('completed','failed','cancelled')) "
                        f"RETURNING {_RUN_COLUMNS}"
                    ),
                    {"p": parent_run_id, "st": state.type.value, "sn": state.name, "now": now},
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._run(row) if row else None

    async def next_case_no(self, u: Any, tenant_id: int) -> int:
        await u.session.execute(
            text("SELECT pg_advisory_xact_lock(:k, :t)"),
            {"k": self.case_lock_key, "t": tenant_id},
        )
        return int(
            (
                await u.session.execute(
                    text(
                        f"SELECT COALESCE(MAX(case_no), 0) + 1 FROM {self._t('runs')} "
                        "WHERE tenant_id = :t"
                    ),
                    {"t": tenant_id},
                )
            ).scalar_one()
        )

    # ---- subjects / events / attempts ----
    async def insert_subjects(
        self, u: Any, run: RunRow, subjects: Sequence[Any], *, blocking: bool
    ) -> None:
        for s in subjects:
            kind, uid, entity_id = _subject_tuple(s)
            try:
                async with u.session.begin_nested():
                    await u.session.execute(
                        text(
                            f"INSERT INTO {self._t('subjects')} "
                            "(run_id, tenant_id, action_key, kind, uid, entity_id, blocking) "
                            "VALUES (:r, :t, :k, :kind, :uid, :eid, :blocking)"
                        ),
                        {
                            "r": run.id,
                            "t": run.tenant_id,
                            "k": run.action_key,
                            "kind": kind,
                            "uid": uid,
                            "eid": entity_id,
                            "blocking": blocking,
                        },
                    )
            except IntegrityError as exc:
                raise SubjectBusy(f"{run.action_key} is already open for {kind}:{uid}") from exc

    async def release_subjects(self, u: Any, run_id: int) -> None:
        await u.session.execute(
            text(f"UPDATE {self._t('subjects')} SET blocking = false WHERE run_id = :r"),
            {"r": run_id},
        )

    async def subjects_of(self, u: Any, run_id: int) -> list[SubjectRow]:
        rows = (
            (
                await u.session.execute(
                    text(
                        f"SELECT kind, uid, entity_id, blocking FROM {self._t('subjects')} "
                        "WHERE run_id = :r"
                    ),
                    {"r": run_id},
                )
            )
            .mappings()
            .all()
        )
        return [
            SubjectRow(
                kind=r["kind"], uid=r["uid"], entity_id=r["entity_id"], blocking=r["blocking"]
            )
            for r in rows
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
        row = (
            (
                await u.session.execute(
                    text(
                        f"INSERT INTO {self._t('events')} "
                        "(tenant_id, run_id, root_run_id, event_type, actor_id, payload, created_at) "
                        "VALUES (:t, :r, :root, :e, CAST(:actor AS uuid), CAST(:payload AS jsonb), :now) "
                        "RETURNING id, tenant_id, run_id, root_run_id, event_type, actor_id, payload, "
                        "created_at"
                    ),
                    {
                        "t": run.tenant_id,
                        "r": run.id,
                        "root": run.root_run_id or run.id,
                        "e": event_type,
                        "actor": actor_id,
                        "payload": _j(payload or {}),
                        "now": now,
                    },
                )
            )
            .mappings()
            .one()
        )
        return EventRow(
            id=row["id"],
            tenant_id=row["tenant_id"],
            run_id=row["run_id"],
            root_run_id=row["root_run_id"],
            event_type=row["event_type"],
            actor_id=str(row["actor_id"]) if row["actor_id"] else None,
            payload=row["payload"] or {},
            created_at=row["created_at"],
        )

    async def events_of(self, u: Any, root_run_id: int) -> list[EventRow]:
        rows = (
            (
                await u.session.execute(
                    text(
                        "SELECT id, tenant_id, run_id, root_run_id, event_type, actor_id, payload, "
                        f"created_at FROM {self._t('events')} WHERE root_run_id = :r ORDER BY id"
                    ),
                    {"r": root_run_id},
                )
            )
            .mappings()
            .all()
        )
        return [
            EventRow(
                id=r["id"],
                tenant_id=r["tenant_id"],
                run_id=r["run_id"],
                root_run_id=r["root_run_id"],
                event_type=r["event_type"],
                actor_id=str(r["actor_id"]) if r["actor_id"] else None,
                payload=r["payload"] or {},
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def insert_attempt(self, u: Any, new: NewAttempt) -> AttemptRow:
        row = (
            await u.session.execute(
                text(
                    f"INSERT INTO {self._t('attempts')} "
                    "(run_id, root_run_id, tenant_id, attempt_no, worker, started_at, ended_at, "
                    " duration_ms, outcome, error, error_type, method, url, status_code, request, "
                    " response) "
                    "VALUES (:run_id, :root, :t, :no, :worker, :started, :ended, :dur, :outcome, "
                    " :error, :error_type, :method, :url, :status, CAST(:request AS jsonb), "
                    " CAST(:response AS jsonb)) "
                    "RETURNING id"
                ),
                {
                    "run_id": new.run_id,
                    "root": new.root_run_id,
                    "t": new.tenant_id,
                    "no": new.attempt_no,
                    "worker": new.worker,
                    "started": new.started_at,
                    "ended": new.ended_at,
                    "dur": new.duration_ms,
                    "outcome": new.outcome,
                    "error": new.error,
                    "error_type": new.error_type,
                    "method": new.method,
                    "url": new.url,
                    "status": new.status_code,
                    "request": _maybe_j(new.request),
                    "response": _maybe_j(new.response),
                },
            )
        ).scalar_one()
        return AttemptRow(
            id=row,
            run_id=new.run_id,
            root_run_id=new.root_run_id,
            tenant_id=new.tenant_id,
            attempt_no=new.attempt_no,
            worker=new.worker,
            started_at=new.started_at,
            ended_at=new.ended_at,
            duration_ms=new.duration_ms,
            outcome=new.outcome,
            error=new.error,
            error_type=new.error_type,
            method=new.method,
            url=new.url,
            status_code=new.status_code,
            request=new.request,
            response=new.response,
        )

    async def last_attempt(self, u: Any, run_id: int) -> AttemptRow | None:
        row = (
            (
                await u.session.execute(
                    text(
                        "SELECT id, run_id, root_run_id, tenant_id, attempt_no, worker, started_at, "
                        "ended_at, duration_ms, outcome, error, error_type, method, url, status_code, "
                        f"request, response FROM {self._t('attempts')} WHERE run_id = :r "
                        "ORDER BY attempt_no DESC LIMIT 1"
                    ),
                    {"r": run_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._attempt(row) if row else None

    async def attempts_of(self, u: Any, run_ids: Sequence[int]) -> list[AttemptRow]:
        if not run_ids:
            return []
        rows = (
            (
                await u.session.execute(
                    text(
                        "SELECT id, run_id, root_run_id, tenant_id, attempt_no, worker, started_at, "
                        "ended_at, duration_ms, outcome, error, error_type, method, url, status_code, "
                        f"request, response FROM {self._t('attempts')} "
                        "WHERE run_id = ANY(:ids) ORDER BY run_id, attempt_no"
                    ),
                    {"ids": list(run_ids)},
                )
            )
            .mappings()
            .all()
        )
        return [self._attempt(r) for r in rows]

    async def retries_used(self, u: Any, run_id: int) -> int:
        count = (
            await u.session.execute(
                text(
                    f"SELECT count(*) FROM {self._t('attempts')} "
                    "WHERE run_id = :r AND outcome = 'retry'"
                ),
                {"r": run_id},
            )
        ).scalar_one()
        return int(count)

    def _attempt(self, m: Any) -> AttemptRow:
        return AttemptRow(
            id=m["id"],
            run_id=m["run_id"],
            root_run_id=m["root_run_id"],
            tenant_id=m["tenant_id"],
            attempt_no=m["attempt_no"],
            worker=m["worker"],
            started_at=m["started_at"],
            ended_at=m["ended_at"],
            duration_ms=m["duration_ms"],
            outcome=m["outcome"],
            error=m["error"],
            error_type=m["error_type"],
            method=m["method"],
            url=m["url"],
            status_code=m["status_code"],
            request=m["request"],
            response=m["response"],
        )

    # ---- worker ----
    async def revive(self, u: Any, run_id: int, *, now: datetime) -> None:
        reset = (
            "SET state_type = 'scheduled', state_name = 'Resumed', scheduled_at = :now, "
            "state_at = :now, lease_token = NULL, lease_owner = NULL, lease_until = NULL, "
            "ended_at = NULL"
        )
        # the run itself
        await u.session.execute(
            text(f"UPDATE {self._t('runs')} {reset} WHERE id = :id"), {"id": run_id, "now": now}
        )
        # failed/crashed descendants
        await u.session.execute(
            text(
                f"WITH RECURSIVE sub AS ( "
                f"  SELECT id FROM {self._t('runs')} WHERE parent_run_id = :id "
                f"  UNION ALL SELECT r.id FROM {self._t('runs')} r JOIN sub ON r.parent_run_id = sub.id "
                ") "
                f"UPDATE {self._t('runs')} {reset} "
                "WHERE id IN (SELECT id FROM sub) AND state_type IN ('failed','crashed')"
            ),
            {"id": run_id, "now": now},
        )
        # paused/failed ancestors
        await u.session.execute(
            text(
                f"WITH RECURSIVE anc AS ( "
                f"  SELECT parent_run_id AS id FROM {self._t('runs')} WHERE id = :id "
                f"  UNION ALL SELECT r.parent_run_id FROM {self._t('runs')} r JOIN anc ON r.id = anc.id "
                "   WHERE r.parent_run_id IS NOT NULL "
                ") "
                f"UPDATE {self._t('runs')} {reset} "
                "WHERE id IN (SELECT id FROM anc WHERE id IS NOT NULL) "
                "  AND state_type IN ('paused','failed','crashed')"
            ),
            {"id": run_id, "now": now},
        )

    async def sweep(self, u: Any, *, now: datetime, registry_idempotent: Any) -> SweepReport:
        crashed = await u.session.execute(
            text(
                f"UPDATE {self._t('runs')} SET state_type = 'crashed', state_name = 'Crashed', "
                "  state_at = :now, lease_token = NULL, lease_owner = NULL, lease_until = NULL, "
                "  error = 'attempt lease expired' "
                # A known leaf, OR any child: a child's `leaf` is NULL until it first settles, so a
                # non-idempotent leaf (e.g. seccl.payment) that crashes mid-send still has leaf NULL.
                # Treat it as crashed so its parent's replay parks it (needs_attention) rather than
                # the resume clause rescheduling and RE-SENDING it. Only root composites resume.
                "WHERE state_type = 'running' AND lease_until < :now "
                "  AND (leaf IS TRUE OR parent_run_id IS NOT NULL) "
                "RETURNING id, root_run_id, tenant_id, attempt_count, lease_owner"
            ),
            {"now": now},
        )
        crashed_rows = crashed.mappings().all()
        for r in crashed_rows:
            # The crashed attempt is numbered after the LAST ROW IN THE TABLE, not after
            # attempt_count, and attempt_count is moved up to match. The two drift: a crash never
            # bumps attempt_count, so a run that crashed, was revived by retry and crashed again
            # collided with its own first crashed row on UNIQUE(run_id, attempt_no) — and because
            # the sweep is one transaction, that one row aborted every sweep from then on, and
            # nothing crashed or timed out was recovered anywhere. The same drift numbered the
            # revived run's next real attempt 1 again, so it could never settle either.
            no = (
                await u.session.execute(
                    text(
                        f"SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM {self._t('attempts')} "
                        "WHERE run_id = :r"
                    ),
                    {"r": r["id"]},
                )
            ).scalar_one()
            await u.session.execute(
                text(
                    f"INSERT INTO {self._t('attempts')} "
                    "(run_id, root_run_id, tenant_id, attempt_no, worker, started_at, ended_at, "
                    " outcome, error) "
                    "VALUES (:r, :root, :t, :no, :worker, :now, :now, 'crashed', "
                    " 'attempt lease expired')"
                ),
                {
                    "r": r["id"],
                    "root": r["root_run_id"] or r["id"],
                    "t": r["tenant_id"],
                    "no": int(no),
                    "worker": r["lease_owner"],
                    "now": now,
                },
            )
            await u.session.execute(
                text(f"UPDATE {self._t('runs')} SET attempt_count = :no WHERE id = :r"),
                {"r": r["id"], "no": int(no)},
            )
        resumed = await u.session.execute(
            text(
                f"UPDATE {self._t('runs')} SET state_type = 'scheduled', state_name = 'Resumed', "
                "  scheduled_at = :now, state_at = :now, lease_token = NULL, lease_owner = NULL, "
                "  lease_until = NULL "
                # Resume only ROOT composites (safe to replay); crashed children go to the crashed
                # clause above so the parent decides whether a re-run is safe.
                "WHERE state_type = 'running' AND lease_until < :now "
                "  AND leaf IS NOT TRUE AND parent_run_id IS NULL"
            ),
            {"now": now},
        )
        timed = await u.session.execute(
            text(
                f"UPDATE {self._t('runs')} SET state_type = 'failed', state_name = 'TimedOut', "
                "  ended_at = :now, state_at = :now, lease_token = NULL, lease_until = NULL "
                "WHERE state_type IN ('scheduled','running') AND timeout_at < :now "
                "  AND (lease_until IS NULL OR lease_until < :now)"
            ),
            {"now": now},
        )
        return SweepReport(
            crashed_leaves=len(crashed_rows),
            resumed_runs=resumed.rowcount,
            timed_out=timed.rowcount,
        )

    async def notify(self, u: Any, run_id: int) -> None:
        await u.session.execute(
            text("SELECT pg_notify(:c, :p)"),
            {"c": f"{self.schema}_runs", "p": str(run_id)},
        )

    async def due_run_ids(self, u: Any, *, now: datetime, limit: int) -> list[int]:
        rows = (
            (
                await u.session.execute(
                    text(
                        f"SELECT id FROM {self._t('runs')} WHERE state_type = 'scheduled' "
                        "AND scheduled_at <= :now AND (lease_until IS NULL OR lease_until < :now) "
                        "ORDER BY scheduled_at, id LIMIT :n"
                    ),
                    {"now": now, "n": limit},
                )
            )
            .scalars()
            .all()
        )
        return [int(r) for r in rows]


def _subject_tuple(s: Any) -> tuple[str, str, int | None]:
    if isinstance(s, Subject):
        return s.kind, s.uid, s.entity_id
    if isinstance(s, dict):
        return s["kind"], s["uid"], s.get("entity_id")
    return s.kind, s.uid, getattr(s, "entity_id", None)
