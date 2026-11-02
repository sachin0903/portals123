"""The firm-policy façade over baton: the doors call these, not the engine directly.

``start`` applies the firm's policy (is the action on, may this caller run it, must someone
approve it, is a reason required) and creates the run in the caller's transaction, so it commits
with the booked row. ``advance_inline`` is then run after the commit for a short budget — fast
provider calls return their status, anything longer continues on the worker. ``decide`` / ``retry``
/ ``resume`` / ``cancel`` act on an existing run.

Policy still lives in ``actions.policies`` and is read through the same helpers the old engine
used (app.actions.runs), so the firm's existing settings carry straight over.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from baton import (
    AdvanceResult,
    Approval,
    ApprovalError,
    NotFound,
    StartResult,
    Subject,
    SubjectBusy,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.baton_engine import get_engine
from app.actions.runs import ActionError, _approval, _invoke_allowed, _policy


@contextmanager
def _as_action_error() -> Iterator[None]:
    """Translate baton's API errors into the ``ActionError`` the doors already catch, so the
    endpoints handle a baton run and a legacy run the same way."""
    try:
        yield
    except ApprovalError as exc:
        raise ActionError(str(exc), status=exc.status) from exc
    except SubjectBusy as exc:
        raise ActionError(str(exc), status=409) from exc
    except NotFound as exc:
        raise ActionError("That run was not found.", status=404) from exc


REASON_REQUIRED = "Explain why this change is needed — a second person will review it."


def _subject(s: Any) -> Subject:
    if isinstance(s, Subject):
        return s
    if isinstance(s, dict):
        kind = s.get("kind") or s.get("entity_type")
        uid = s.get("uid") or s.get("entity_uid")
        return Subject(kind=str(kind), uid=str(uid), entity_id=s.get("entity_id"))
    raise TypeError(f"cannot read a subject from {s!r}")


async def start(
    session: AsyncSession,
    *,
    action_key: str,
    tenant_id: int,
    params: dict[str, Any],
    idempotency_key: str,
    requested_by: str | None = None,
    role_ids: Sequence[int] | None = None,
    subjects: Sequence[Any] = (),
    contact_id: int | None = None,
    justification: str | None = None,
    meta: dict[str, Any] | None = None,
    approved: bool = False,
) -> StartResult:
    """Start a run under the firm's policy. ``approved`` says a second person has ALREADY agreed
    to this act — the record save it follows was held and approved — so the approval gate is not
    raised a second time; the enabled switch, the invoke roles and a manual halt still apply."""
    engine = get_engine()
    defn = engine.registry.get(action_key)
    if defn is None:
        raise ActionError("That action is not in the catalogue.", status=404)
    policy = await _policy(session, tenant_id=tenant_id, action_key=action_key, provider=None)
    if policy is not None and not policy["enabled"]:
        raise ActionError("This action is switched off.", status=409)
    # A manual action has no provider API yet: it halts like an approval hold, and accepting it
    # completes the run by hand — the engine settles it at gate-release (engine.decide reads
    # meta.manual) and never runs the body. It rides the same gate, so approval and manual share
    # the switch below.
    manual = bool(policy and policy.get("manual"))
    # role_ids None means this is not a fresh firm request (a system transaction, or a release of
    # a held booking); the door that opened it already decided the caller may send.
    if (
        role_ids is not None
        and policy is not None
        and not await _invoke_allowed(
            session, tenant_id=tenant_id, policy_id=int(policy["id"]), role_ids=list(role_ids)
        )
    ):
        raise ActionError("You are not allowed to run this action.", status=403)
    approval_on, approval_roles = await _approval(
        session, tenant_id=tenant_id, action_key=action_key
    )
    if approved:
        approval_on = False
    if (
        (approval_on or manual)
        and policy is not None
        and policy["require_justification"]
        and not (justification or "").strip()
    ):
        raise ActionError(REASON_REQUIRED, status=422)
    approval = Approval(roles=approval_roles) if (approval_on or manual) else None
    # Translate baton's API errors (SubjectBusy, ApprovalError, NotFound) into the ActionError the
    # doors catch, so a one-open-per-subject clash is a clean 409, not an unhandled 500. The other
    # entry points (decide/retry/…) already do this; start did not.
    with _as_action_error():
        return await engine.start(
            engine.store.bind(session),
            defn,
            tenant_id=tenant_id,
            params=params,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            contact_id=contact_id,
            subjects=[_subject(s) for s in subjects],
            approval=approval,
            justification=justification,
            meta={**(meta or {}), "manual": True} if manual else (meta or {}),
        )


async def advance_inline(run_uid: str, *, budget_seconds: float = 5.0) -> AdvanceResult:
    """Drive the run after its creating transaction has committed. Call this outside that
    transaction; it opens its own."""
    return await get_engine().advance(run_uid, budget_seconds=budget_seconds)


async def owns_run(session: AsyncSession, *, tenant_id: int, run_uid: str) -> bool:
    """Whether this uid names a baton run for this firm. The decide/retry endpoints route on it:
    a baton run goes through the engine, a legacy ``actions.runs`` row through the old path."""
    engine = get_engine()
    run = await engine.store.run_by_uid(engine.store.bind(session), run_uid, tenant_id=tenant_id)
    return run is not None


REJECT_REASON_REQUIRED = "Say why you are rejecting this."


async def decide(
    session: AsyncSession,
    *,
    run_uid: str,
    decision: str,
    actor_id: str | None,
    role_ids: Sequence[int],
    note: str | None = None,
) -> Any:
    """Approve or reject a held run. A rejection needs a reason, as a book review's does: the
    person who raised it reads the note, and "rejected" alone leaves them to guess. The engine
    takes a bare reject; this façade is where the firm's rule lives."""
    if decision == "reject" and not (note or "").strip():
        raise ActionError(REJECT_REASON_REQUIRED, status=422)
    engine = get_engine()
    with _as_action_error():
        return await engine.decide(
            engine.store.bind(session),
            run_uid,
            decision=decision,
            actor_id=actor_id,
            role_ids=list(role_ids),
            note=note,
        )


async def retry(session: AsyncSession, *, run_uid: str) -> Any:
    engine = get_engine()
    with _as_action_error():
        return await engine.retry(engine.store.bind(session), run_uid)


async def resume(session: AsyncSession, *, run_uid: str) -> Any:
    engine = get_engine()
    with _as_action_error():
        return await engine.resume(engine.store.bind(session), run_uid)


async def cancel(
    session: AsyncSession, *, run_uid: str, actor_id: str | None = None, note: str | None = None
) -> Any:
    engine = get_engine()
    with _as_action_error():
        return await engine.cancel(
            engine.store.bind(session), run_uid, actor_id=actor_id, note=note
        )


async def detail(session: AsyncSession, *, run_uid: str, tenant_id: int) -> Any:
    engine = get_engine()
    with _as_action_error():
        return await engine.get(engine.store.bind(session), run_uid, tenant_id=tenant_id)


async def start_sync(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    account_id: int | None = None,
    requested_by: str | None = None,
) -> StartResult:
    """The Morgan Lloyd *Sync* button on baton. Each press is a fresh run (the button re-runs the
    idempotent sync, which re-reads its recorded ids and skips what is already there), and
    ``client.sync`` is ``one_open_per_subject`` so a concurrent press for the same client is
    refused — ``SubjectBusy`` becomes a 409. The run is created here and advanced by the worker.

    ``role_ids`` is not passed: the door gates with its own ``contacts:sync-morgan-lloyd``
    permission, as the legacy path did, so the actions-policy invoke-role list does not also apply;
    an approval policy on ``client.sync`` still holds the run."""
    from sqlalchemy import text

    from app.ids import new_id

    uid = (
        await session.execute(
            text("SELECT uid FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
            {"c": contact_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if uid is None:
        raise ActionError("Client not found", status=404)
    params: dict[str, Any] = {"contact_id": contact_id}
    tail = ""
    if account_id is not None:
        params["account_id"] = account_id
        tail = f":account:{account_id}"
    try:
        return await start(
            session,
            action_key="client.sync",
            tenant_id=tenant_id,
            params=params,
            idempotency_key=f"client.sync:{contact_id}{tail}:{new_id()}",
            requested_by=requested_by,
            role_ids=None,
            subjects=[Subject("contact", str(uid), contact_id)],
            contact_id=contact_id,
        )
    except SubjectBusy as exc:
        raise ActionError("A sync for this client is already in progress.", status=409) from exc


async def lodge_contribution(
    session: AsyncSession,
    *,
    tenant_id: int,
    uid: str,
    requested_by: str | None = None,
) -> Any:
    """The contribution lodge/retry door on baton. A contribution is money in, so it runs through
    the one money-in action, ``transaction.payment``, which dispatches to the internal lodge leaf
    for a contribution source. Keyed on the contribution, so a repeat press is the same run;
    ``start`` never resumes, so a run that parked on an earlier attempt is resumed here (the lodge
    leaf is idempotent — it re-reads the expectation status). Returns the run (a ``RunView``); the
    door turns it into a 202 with the run uid."""
    from app.actions.runs import _load_account, account_subjects

    # Name what a reviewer needs when the firm holds payments: the account and client the
    # expectation is for, and the figure, as the run's input — a bare `{contribution_uid}` row
    # in the Reviews queue is not decidable.
    expectation = (
        (
            await session.execute(
                text(
                    "SELECT account_id, contact_id, amount::float8 AS amount, due_date, currency "
                    "FROM platform.transaction_expectations WHERE uid = :u AND tenant_id = :t"
                ),
                {"u": uid, "t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    subjects: list[Any] = [Subject("contribution", uid, None)]
    contact_id: int | None = None
    snapshot: dict[str, Any] = {"kind": "contribution"}
    if expectation is not None:
        account = await _load_account(
            session, tenant_id=tenant_id, account_id=int(expectation["account_id"])
        )
        contact_id = (
            int(expectation["contact_id"])
            if expectation["contact_id"] is not None
            else (int(account["contact_id"]) if account and account.get("contact_id") else None)
        )
        if account is not None:
            subjects.extend(account_subjects(account, contact_id))
            if account.get("name"):
                snapshot["account_name"] = account["name"]
        snapshot["amount"] = float(expectation["amount"])
        if expectation["due_date"]:
            snapshot["due_date"] = str(expectation["due_date"])
        if expectation["currency"]:
            snapshot["currency"] = expectation["currency"]
    result = await start(
        session,
        action_key="transaction.payment",
        tenant_id=tenant_id,
        params={"contribution_uid": uid},
        idempotency_key=f"transaction.payment:contribution:{uid}",
        requested_by=requested_by,
        role_ids=None,
        subjects=subjects,
        contact_id=contact_id,
        meta={"input": snapshot},
    )
    run = result.run
    if not result.created and run.state.type.value in {"failed", "crashed"}:
        with _as_action_error():
            run = await resume(session, run_uid=run.uid)
    return run


async def record_at_morgan_lloyd(
    session: AsyncSession,
    *,
    tenant_id: int,
    action_key: str,
    account_id: int,
    uid: str,
    requested_by: str | None = None,
) -> Any:
    """The record-to-Morgan-Lloyd doors (``contribution.record`` / ``transfer.record``) on baton.
    Keyed on the booking uid; the worker records it. Returns the run (a ``RunView``)."""
    from app.actions.runs import _load_account, account_subjects

    account = await _load_account(session, tenant_id=tenant_id, account_id=account_id)
    contact_id = account.get("contact_id") if account else None
    result = await start(
        session,
        action_key=action_key,
        tenant_id=tenant_id,
        params={"account_id": account_id, "uid": uid},
        idempotency_key=f"{action_key}:{uid}",
        requested_by=requested_by,
        role_ids=None,
        subjects=account_subjects(account, contact_id) if account else [],
        contact_id=contact_id,
    )
    return result.run


async def start_from_entity(
    session: AsyncSession,
    *,
    action_key: str,
    tenant_id: int,
    entity_type: str,
    entity_uid: str,
    requested_by: str | None,
    role_ids: Sequence[int] | None,
    approved: bool = False,
) -> StartResult:
    """The generic 'Run this action' door on baton: resolve the record, then start the run. The
    composite resolves the provider itself, so the door only needs the entity and its id.
    ``approved`` is the custodian push that follows an approved review (see ``start``)."""
    from app.actions.runs import ActionError, _account_id_by_uid, _contact_by_uid
    from app.ids import new_id

    subjects: list[Any]
    contact: int | None
    if entity_type == "contact":
        contact_id = await _contact_by_uid(session, tenant_id=tenant_id, uid=entity_uid)
        params: dict[str, Any] = {"contact_id": contact_id}
        subjects = [Subject("contact", entity_uid, contact_id)]
        contact = contact_id
    elif entity_type == "account":
        from app.actions.runs import _load_account, account_subjects

        account_id = await _account_id_by_uid(session, tenant_id=tenant_id, uid=entity_uid)
        params = {"account_id": account_id}
        # The account AND its client, the way the money doors attach them, so the run names
        # the client in the listing and answers the `?contact=` filter.
        account = await _load_account(session, tenant_id=tenant_id, account_id=account_id)
        has_contact = account is not None and account.get("contact_id") is not None
        contact = int(account["contact_id"]) if has_contact and account else None
        subjects = (
            account_subjects(account, contact)
            if account is not None
            else [Subject("account", entity_uid, account_id)]
        )
    else:
        raise ActionError("Run this from the record it belongs to.")
    return await start(
        session,
        action_key=action_key,
        tenant_id=tenant_id,
        params=params,
        idempotency_key=f"manual:{action_key}:{entity_uid}:{new_id()}",
        requested_by=requested_by,
        role_ids=role_ids,
        subjects=subjects,
        contact_id=contact,
        approved=approved,
    )
