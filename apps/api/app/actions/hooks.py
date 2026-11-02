"""The app's seam into baton: database role, firm policy on a child, and the journey callback.

baton calls these inside its own transactions. ``begin_session`` receives the raw session; the
other three receive a store unit whose ``.session`` is the live session, so a small accessor
copes with both. See docs/baton.md.
"""

from __future__ import annotations

import logging
from typing import Any

from baton import Approval, Skip
from sqlalchemy import text

logger = logging.getLogger(__name__)

#: A book review whose approval also means a custodian push: once the worker has written the
#: record, start the provider action that sends it — the same run the door starts on the
#: immediate path. The second person has agreed, so the push is started ``approved`` and is not
#: gated again.
_CUSTODIAN_PUSH: dict[str, str] = {
    "account.model_changed": "model.attach",
    "account.fee_packages_changed": "fee.attach",
}


def _pushes(run: Any) -> bool:
    """Only a SET pushes: clearing a model or every fee package is a local removal."""
    params = run.params or {}
    raw = params.get("payload")
    payload: dict[str, Any] = raw if isinstance(raw, dict) else {}
    if run.action_key == "account.model_changed":
        return payload.get("model_id") is not None
    return bool(payload.get("fee_package_ids"))


def _session(arg: Any) -> Any:
    """baton hands begin_session the session and the others a unit; normalise to a session."""
    return getattr(arg, "session", arg)


class GrapheneHooks:
    async def begin_session(self, session: Any, run: Any) -> None:
        # Every engine-owned transaction runs as service_role, as the firm doors already do.
        await _session(session).execute(text("SET LOCAL ROLE service_role"))

    async def before_child(self, unit: Any, parent: Any, child: Any, params: Any) -> Any:
        """The firm's policy on a child about to be created: switched off → complete it Skipped;
        approver roles set → gate it; otherwise run it."""
        from app.actions import runs as legacy
        from app.actions.catalogue import BY_KEY

        session = _session(unit)
        policy = await legacy._policy(
            session, tenant_id=parent.tenant_id, action_key=child.key, provider=None
        )
        if policy is not None and not policy["enabled"]:
            return Skip("This action is switched off.")
        # Approvers on a record-holding row ("Change model", "Change fee packages", the bank
        # rows) hold the RECORD SAVE at its door. As a child of a composite — onboarding's
        # `client.setup` attaching the application's model — the push follows a change a person
        # already captured, and a gate here would park every new account behind a case number
        # the Reviews queue (roots only) never lists.
        spec = BY_KEY.get(child.key)
        if spec is not None and spec.holds_record:
            return None
        on, roles = await legacy._approval(
            session, tenant_id=parent.tenant_id, action_key=child.key
        )
        if on:
            return Approval(roles=roles)
        return None

    async def on_event(self, unit: Any, event: Any, run: Any) -> None:
        """The audit.actions bridge for a PROVIDER action's approval gate. A held book review
        records its own ``review.*`` lifecycle in the reviews fold; a provider action held at its
        gate (a payment an approver role holds, a gated child of a composite) recorded nothing, so
        its approvers were never told it was waiting and its requester never heard the decision.
        The engine's ``run.held`` / ``run.approved`` / ``run.rejected`` are mirrored to the same
        ``review.requested`` / ``review.approved`` / ``review.rejected`` events, in this same
        transaction, so one notification rule covers every kind of hold."""
        if run.meta.get("review"):
            return None
        from app.actions import approvals

        session = _session(unit)
        if event.event_type == "run.held":
            await approvals.notify_held(session, run)
        elif event.event_type in ("run.approved", "run.rejected"):
            await approvals.notify_decided(session, run, event.event_type, actor_id=event.actor_id)
        return None

    async def on_run_terminal(self, unit: Any, run: Any, error: str | None) -> None:
        # Release a journey step that was waiting on this run, the same callback the old engine
        # fired. A no-op until a journey records a baton run uid.
        from app.flows import orchestrator

        session = _session(unit)
        if run.meta.get("review"):
            await self._settle_review(session, run, error)
        elif run.state.type.value == "cancelled":
            # A rejected/cancelled money run leaves a booked, still-dispatchable instruction behind;
            # mark it rejected so a later dispatch cannot send it and the pending row is cancelled.
            await self._settle_rejected_money(session, run)
        hook = getattr(orchestrator, "on_action_run_settled", None)
        if hook is not None:
            await hook(session, run.uid, error=error)

    async def _settle_rejected_money(self, session: Any, run: Any) -> None:
        from app import outbound
        from app.instructions import cancel_instruction

        params = run.params or {}
        instruction_uid = params.get("instruction_uid")
        if instruction_uid:
            await outbound.set_review_status(
                session,
                tenant_id=run.tenant_id,
                origin="instruction",
                origin_uid=str(instruction_uid),
                review="rejected",
            )
            # set_review_status only annotates the transaction rows; the booked instruction stays
            # `pending` and would resurface as an unsent payment (and block the next). Cancel it so
            # the UNSENT_* queries skip it — cancel_instruction refuses once a custodian accepted.
            await cancel_instruction(session, tenant_id=run.tenant_id, uid=str(instruction_uid))
        elif run.action_key == "transaction.transfer" and params.get("uid"):
            await outbound.set_review_status(
                session,
                tenant_id=run.tenant_id,
                origin="transfer",
                origin_uid=str(params["uid"]),
                review="rejected",
            )

    async def _push_after_review(self, session: Any, run: Any, proposer: str | None) -> None:
        """The record is written; now send it to the custodian as its own tracked run.

        An account with no custodian on the book has nowhere to send it: no run is started
        (the money doors make the same check), rather than a Failed `fee.attach` per save. A
        refusal to START the push — the firm switched the action off — must not undo the save
        a second person just approved: the save stands, the refusal is logged, and the push can
        be started from the account. Neither attach action is one-open-per-subject, so a second
        push for the same account queues behind the first rather than being refused."""
        follow = _CUSTODIAN_PUSH.get(str(run.action_key))
        if follow is None or not run.subject_uid or not _pushes(run):
            return
        from app.actions import runs as action_runs
        from app.actions import service as action_service

        account_id = await action_runs._account_id_by_uid(
            session, tenant_id=run.tenant_id, uid=str(run.subject_uid)
        )
        account = await action_runs._load_account(
            session, tenant_id=run.tenant_id, account_id=account_id
        )
        custodian = str((account or {}).get("custodian") or "")
        if account is None or custodian not in action_runs._custodian_codes():
            logger.info(
                "review %s applied; no custodian on account %s, so no %s push",
                run.uid,
                run.subject_uid,
                follow,
            )
            return
        try:
            await action_service.start_from_entity(
                session,
                action_key=follow,
                tenant_id=run.tenant_id,
                entity_type="account",
                entity_uid=str(run.subject_uid),
                requested_by=proposer,
                role_ids=None,
                approved=True,
            )
        except action_runs.ActionError as exc:
            logger.warning(
                "review %s applied but could not start %s for %s: %s",
                run.uid,
                follow,
                run.subject_uid,
                exc,
            )

    async def _settle_review(self, session: Any, run: Any, error: str | None) -> None:
        """A held review's apply has settled: on success write the happened-event the automations
        tail (attributed to the proposer, after the change landed); on an apply FAILURE mark it
        conflicted. ``review.requested`` / ``review.approved`` are already written by the hold /
        decide path, and ``review.rejected`` / ``review.cancelled`` by the reject / cancel path —
        on_run_terminal also fires for those, so only a genuine apply failure is recorded here."""
        from app.reviews.events import record_applied_action, record_review_action

        proposer = str(run.requested_by) if run.requested_by else None
        if error is None:
            if proposer is not None:
                await record_applied_action(
                    session,
                    event_type=str(run.meta.get("event_type") or run.action_key),
                    tenant_id=run.tenant_id,
                    actor_id=proposer,
                    actor_sub=f"user:{proposer}",
                    review_uid=run.uid,
                    subject_type=str(run.subject_kind),
                    subject_id=str(run.subject_uid),
                )
            await self._push_after_review(session, run, proposer)
            return
        # A reject/cancel reaches here as a cancelled run; its event is already recorded. Only a
        # failed apply (a stale-record conflict or a refused business rule) is a conflict.
        if run.state.type.value != "failed":
            return
        await record_review_action(
            session,
            event_type="review.conflicted",
            tenant_id=run.tenant_id,
            actor_id=None,
            actor_sub="system:baton",
            subject={
                "action_key": run.action_key,
                "review_uid": run.uid,
                "subject_type": run.subject_kind,
                "subject_id": run.subject_uid,
                "proposed_by": proposer,
            },
        )
