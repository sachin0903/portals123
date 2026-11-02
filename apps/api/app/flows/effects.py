"""The action executor — what an automation actually DOES, and the audit story that makes it safe.

This is the ``"action"`` half of A1's dispatch seam (``app/flows/triggers.py``): the consumer
decides WHETHER an automation fires; this module performs the work. One executor serves both
tiers — the standalone rules of Phase A and the flow action-steps of Phase B — because the
contract's ``actionTypes`` is deliberately one vocabulary, and two implementations of "send the
client an email" is how the two tiers drift into behaving differently.

Three rules govern everything here.

**1. An action calls the service layer a human calls.** ``raise_task`` goes through
``app/tasks/service.py::create_task``; ``assign_adviser`` through ``app/contacts.py``;
``set_case_status`` through ``orchestrator.set_case_status``; ``apply_tag`` through
``app/tags.py``. Not one of them writes its own SQL against those tables. This is not tidiness:
those functions carry the business rules (an assignee must hold an active membership in this
tenant, a case cannot leave a terminal state, an adviser must belong to the firm), and a parallel
path around them is a permission check that exists on the human route and not on the automated
one. Where a rule lived only in a router, the service was extracted so both call it — that is why
``orchestrator.set_case_status`` is new.

**2. An action that could never run is refused at authoring.** ``preflight`` is exported for the
rules API to call before enabling an automation: an action whose declared permission is held by no
role in the tenant is not a runtime error waiting to happen, it is an authoring mistake, and the
person who can fix it is looking at the screen right now. Our established preflight philosophy
(``ReviewHandler.preflight``, the publish validators) applied to the rule tier.

**3. Every action emits its normal action event, with the automation as the actor.** Via
``app/audit_actions.py::record_automation_action``, on this session, inside the
dispatch savepoint — so automated work appears in the audit log a firm can already
browse, attributable, filterable, and rolled back with the work if the work rolls back.
The event carries the chain marker the
consumer's self-trigger guard reads, which is what stops an automation's own output firing it
again.

**Failure semantics.** A savepoint wraps each firing (``automation.consider``), so raising rolls
back this firing and nothing else. This module therefore distinguishes two kinds of "no":

* ``ActionRefused`` — the action cannot be performed as authored (no such template, nobody to
  email, a case that does not exist). Returned as ``DispatchResult(ok=False, detail=…)``, which
  the consumer records as a ``failed`` delivery with the reason on it. The savepoint still rolls
  back, so a partially-applied action leaves nothing behind.
* Anything else raising — a genuine fault. It propagates to the consumer, which dead-letters it
  and reports the error. Deliberately NOT swallowed into a false success: an automation that
  reports "done" having done nothing is the single worst outcome available to this module.

**The registration seam.** Every action in the contract is implemented here now — B3 landed the
document acts and ``webhook_out``, D1 landed ``lookup`` (whose HTTP and mapping halves live in
``app/flows/lookup.py`` and register through this same ``register_action(...)``). The seam stays:
a contract entry declared ahead of its handler carries ``implemented=False`` in ``ACTIONS`` and is
refused by ``preflight`` with "not available yet", rather than being accepted as an automation
that would fail at 3am.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit_actions as actions_mod
from app import contacts as contacts_mod
from app import permissions as perms
from app import tags as tags_mod
from app.comms import ledger
from app.config import settings
from app.email import render as email_render
from app.email import shell as email_shell
from app.email import transport as email_mod
from app.flows import interpolation, memory
from app.flows.triggers import AutomationContext, DispatchResult
from app.render import jinja_engine
from app.tasks import service as tasks_service

logger = logging.getLogger("automations.actions")


class ActionRefused(Exception):
    """The action cannot be performed as authored. Carries a reason fit to show a firm's author.

    Distinct from a fault: this is a considered "no", so the executor turns it into a recorded
    ``failed`` delivery with the reason attached rather than a dead letter with a stack trace.
    """


# ---- The action registry (mirror of flow-contract.json → actionTypes) -------------------------


@dataclass(frozen=True)
class EffectSpec:
    """One action type: what a human would need, and what the trail should say it did.

    ``permission`` mirrors the contract's ``actionTypes.<type>.permission`` verbatim — it is what
    ``preflight`` checks a tenant role holds, and ``tests/test_effects.py`` pins the mirror
    against the JSON so the two cannot drift.

    ``event_type``/``method``/``route`` are the identity of the equivalent HUMAN act, so an
    automated task and a manually raised one land in the audit log as the same kind of thing. Where
    an action has no human route yet (the tag actions), the values are the operation id such a
    route WILL have — see ``_TAG_ROUTE_NOTE``.
    """

    type: str
    permission: str
    event_type: str
    method: str
    route: str
    #: False = authorable in the builder, not runnable here; ``preflight`` refuses it at authoring.
    implemented: bool = True


# THE TAG-ROUTE NOTE: `apply_tag`/`remove_tag` have no human route yet, so their event types are the
# operation ids one WILL generate — name a `tags` router's routes `apply-tag`/`remove-tag`, or
# manual and automated tagging silently become two event types (pinned in test_effects.py).

ACTIONS: dict[str, EffectSpec] = {
    a.type: a
    for a in (
        EffectSpec(
            "apply_tag",
            perms.CONTACTS_WRITE.slug,
            "action.tags-apply-tag",
            "POST",
            "/tags/{target}/{target_id}",
        ),
        EffectSpec(
            "remove_tag",
            perms.CONTACTS_WRITE.slug,
            "action.tags-remove-tag",
            "DELETE",
            "/tags/{target}/{target_id}/{tag}",
        ),
        EffectSpec("raise_task", perms.TASKS_WRITE.slug, "task.created", "POST", "/tasks"),
        EffectSpec(
            "send_email",
            perms.DOCUMENTS_SEND.slug,
            # The client-facing release act — hence documents:send, as for an adviser sending one.
            "document.sent",
            "POST",
            "/documents/send",
        ),
        EffectSpec(
            "assign_adviser",
            perms.CONTACTS_WRITE.slug,
            "client.adviser_assigned",
            "POST",
            # The live route, which is uid-addressed (docs/api.md -> "Addressing"). It matters that
            # this MATCHES: a flow assigning an adviser and a human doing it are the same act, and
            # the row each writes is read by the same consumers. Two shapes for one event is a
            # reader that works until the wrong half of the stream reaches it.
            "/firm/contacts/{contact_uid}/advisers",
        ),
        EffectSpec(
            "set_case_status",
            perms.CASES_WRITE.slug,
            "action.flows-set-case-status",
            "PATCH",
            "/flows/cases/{case_uid}/status",
        ),
        # ---- the document acts + the outbound webhook ------------------------------------------
        EffectSpec(
            "request_document",
            perms.DOCUMENTS_SEND.slug,
            "document_request.created",
            "POST",
            "/documents/requests",
        ),
        EffectSpec(
            "generate_document",
            perms.DOCUMENTS_WRITE.slug,
            "document.generated",
            "POST",
            "/documents/generate",
        ),
        EffectSpec(
            "send_document",
            perms.DOCUMENTS_SEND.slug,
            "document.sent",
            "POST",
            "/documents/{uid}/send",
        ),
        # No human equivalent, so this event type names no future route (unlike the tag actions).
        EffectSpec(
            "webhook_out",
            perms.AUTOMATIONS_WRITE.slug,
            "action.automations-webhook-out",
            "POST",
            "/automations/webhook",
        ),
        # Like `webhook_out`, no human equivalent, so this event type names no future route either.
        EffectSpec(
            "lookup",
            perms.AUTOMATIONS_WRITE.slug,
            "action.automations-lookup",
            "POST",
            "/automations/lookup",
        ),
        # The AI gates: `automations:write`, nothing client-facing — a classify writes to memory.
        EffectSpec(
            "classify",
            perms.AUTOMATIONS_WRITE.slug,
            "action.automations-classify",
            "POST",
            "/automations/classify",
        ),
        # `tasks:write`, not `documents:send`: this raises a task, it releases nothing to a client.
        # Its own event type on purpose — a task a model drafted is not one an adviser wrote.
        EffectSpec(
            "draft_message",
            perms.TASKS_WRITE.slug,
            "action.automations-draft-message",
            "POST",
            "/automations/draft-message",
        ),
    )
}


@dataclass(frozen=True)
class ActionOutcome:
    """What one handler did.

    ``changed`` is the honest "was there an effect" answer, and it drives whether an action event
    is written: re-applying a tag the client already carries is not an action taken, and emitting
    one would both pollute the trail and (because the stream is a trigger source) hand every
    downstream automation a firing that describes nothing. Same philosophy as the request-layer
    floor's "effects, not attempts", one level further in.
    """

    detail: str
    changed: bool = True
    #: Allowlisted id/enum fields naming what was acted on — lands as ``metadata.subject``.
    subject: dict[str, Any] = field(default_factory=dict)
    #: The matched path params of the equivalent human route.
    path_params: dict[str, Any] = field(default_factory=dict)
    #: The case this action acted on, reported back to the consumer's per-case budget.
    case_uid: str | None = None


#: A handler performs ONE action type. Raises ``ActionRefused`` for a considered no.
ActionHandler = Callable[
    [AsyncSession, AutomationContext, Mapping[str, Any]], Awaitable[ActionOutcome]
]

HANDLERS: dict[str, ActionHandler] = {}


def register_action(action_type: str, handler: ActionHandler) -> None:
    """Register a handler for one action type. The Phase B seam: B3's document actions call this
    at import of their own module and need change nothing here. Re-registration overwrites (module
    re-import under test), matching ``triggers.register_target``."""
    if action_type not in ACTIONS:
        raise KeyError(
            f"'{action_type}' is not in the action contract — add it to flow-contract.json, "
            "ACTIONS here, and the TS side together"
        )
    HANDLERS[action_type] = handler


# ---- Config reading: small, total, and forgiving of the two authored shapes -------------------


def action_config(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The authored parameters out of a stored ``flows.automations.action`` value.

    Two shapes are accepted, deliberately: ``{"type": …, "config": {…}}`` (nested — what the rules
    API is expected to store) and ``{"type": …, <key>: <value>, …}`` (flattened, which is how the
    contract lists ``config`` and how the engine's early fixtures write it). Tolerating both costs
    four lines and removes an entire class of "the rule saved fine and never ran" bug from the
    seam between this module and an API being written in parallel. The nested form wins when both
    are present.
    """
    nested = spec.get("config")
    if isinstance(nested, Mapping):
        return {**{k: v for k, v in spec.items() if k not in ("type", "config")}, **nested}
    return {k: v for k, v in spec.items() if k != "type"}


def _text(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ActionRefused(f"'{key}' must be text")
    return value.strip() or None


def _required_text(config: Mapping[str, Any], key: str) -> str:
    value = _text(config, key)
    if not value:
        raise ActionRefused(f"the action names no '{key}'")
    return value


def _int(config: Mapping[str, Any], key: str) -> int | None:
    value = config.get(key)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


# ---- The interpolation context ----------------------------------------------------------------


async def build_context(session: AsyncSession, ctx: AutomationContext) -> dict[str, Any]:
    """The mapping an action's copy interpolates against.

    Starts from the SAME facts the entry conditions were evaluated against (``ctx.facts``), so an
    author who filtered on a field can also print it — one vocabulary, one picker, no surprises.
    Then adds the display fields conditions have no use for but copy always wants: the client's
    name and email, and the automation's own name (``{{ automation.name }}`` in a task
    description is how an operator knows what raised the thing in their inbox).

    Kept separate from ``automation.build_facts`` because the two want different things: that one
    is read on EVERY candidate evaluation and is deliberately lean, this one runs once per actual
    dispatch and can afford a join.

    **Case memory is part of the vocabulary, not an extra a caller opts into.** ``remember`` is an
    authored per-key mapping precisely because the values it promotes are meant to be printed into
    an email or a task title (``app/flows/memory.py``'s header, ``send_email``'s contract comment);
    a memory that never reached copy would make that whole safety argument moot. So it is merged
    here, once, for every action — not at one call site that remembered to ask.

    Read at most once per dispatch, and only when there is nothing already carrying it: a flow
    action node hands us ``orchestrator.case_context`` as its facts, which fetched memory in the
    query it was already making, and re-reading it here would double that path's cost to arrive at
    the same mapping. A rule firing with no case simply has no memory scope — the namespace is
    still present and empty, so ``{{ memory.x || "…" }}`` renders its fallback rather than
    behaving differently from an empty case.
    """
    context: dict[str, Any] = dict(ctx.facts)
    context["automation.name"] = ctx.automation_name
    context["automation.uid"] = ctx.automation_uid
    context["trigger.key"] = ctx.trigger_key
    if memory.NAMESPACE not in context:
        context[memory.NAMESPACE] = {}
        if ctx.case_uid:
            context.update(
                await memory.context_for_case_uid(
                    session, tenant_id=ctx.tenant_id, case_uid=ctx.case_uid
                )
            )
    if ctx.case_uid:
        context["case.uid"] = ctx.case_uid
    if ctx.subject_kind == "contact" and ctx.subject_id is not None:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT COALESCE(name, trim(concat_ws(' ', first_name, surname))) AS name, "
                        "       first_name, surname, email "
                        "FROM crm.contacts WHERE id = :c AND tenant_id = :t"
                    ),
                    {"c": ctx.subject_id, "t": ctx.tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            context.update(
                {
                    "contact.name": row["name"],
                    "contact.first_name": row["first_name"],
                    "contact.surname": row["surname"],
                    "contact.email": row["email"],
                    "contact.id": ctx.subject_id,
                }
            )
    return context


def _render(template: str, context: Mapping[str, Any]) -> str:
    """Interpolate plain text (a task title, a subject line). No escaping: the sink is a database
    column read back into a UI that escapes on render, not an HTML document."""
    return interpolation.render(template, context)


# ---- Subject resolution -----------------------------------------------------------------------


def _require_contact(ctx: AutomationContext) -> int:
    if ctx.subject_kind != "contact" or ctx.subject_id is None:
        raise ActionRefused(
            "this trigger did not name a client, so there is nobody to act on — use a trigger "
            "with a client subject"
        )
    return int(ctx.subject_id)


async def _require_case_id(session: AsyncSession, ctx: AutomationContext) -> tuple[int, str]:
    if not ctx.case_uid:
        raise ActionRefused("this firing did not happen inside a journey, so there is no case")
    case_id = (
        await session.execute(
            text("SELECT id FROM flows.cases WHERE uid = :u AND tenant_id = :t"),
            {"u": ctx.case_uid, "t": ctx.tenant_id},
        )
    ).scalar_one_or_none()
    if case_id is None:
        raise ActionRefused(f"case {ctx.case_uid} no longer exists")
    return int(case_id), ctx.case_uid


# ---- The actions ------------------------------------------------------------------------------


async def _resolve_tag_target(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> tuple[tags_mod.Target, int, str | None]:
    """Which contact/case a tag action acts on. Defaults to ``contact`` — the contract lists
    ``target`` as optional config and a client is what the overwhelming majority of triggers
    name."""
    target = (_text(config, "target") or "contact").lower()
    if target not in tags_mod.TARGETS:
        raise ActionRefused(f"'{target}' is not a tag target (contact or case)")
    if target == "case":
        case_id, case_uid = await _require_case_id(session, ctx)
        return "case", case_id, case_uid
    return "contact", _require_contact(ctx), ctx.case_uid


async def apply_tag(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    target, target_id, case_uid = await _resolve_tag_target(session, ctx, config)
    raw = _required_text(config, "tag")
    try:
        # Interpolated ("{{ x }}-client"), then normalised so it shares a typed label's namespace.
        label = tags_mod.normalise(_render(raw, await build_context(session, ctx)))
    except tags_mod.TagError as exc:
        raise ActionRefused(str(exc)) from exc
    changed = await tags_mod.apply(
        session,
        tenant_id=ctx.tenant_id,
        target=target,
        target_id=target_id,
        tag=label,
        source="automation",
    )
    return ActionOutcome(
        detail=(f"tagged '{label}'" if changed else f"already tagged '{label}'"),
        changed=changed,
        subject={"tag": label, "target": target},
        path_params={"target": target, "target_id": target_id},
        case_uid=case_uid,
    )


async def remove_tag(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    target, target_id, case_uid = await _resolve_tag_target(session, ctx, config)
    raw = _required_text(config, "tag")
    try:
        label = tags_mod.normalise(_render(raw, await build_context(session, ctx)))
    except tags_mod.TagError as exc:
        raise ActionRefused(str(exc)) from exc
    changed = await tags_mod.remove(
        session, tenant_id=ctx.tenant_id, target=target, target_id=target_id, tag=label
    )
    return ActionOutcome(
        detail=(f"removed tag '{label}'" if changed else f"tag '{label}' was not applied"),
        changed=changed,
        subject={"tag": label, "target": target},
        path_params={"target": target, "target_id": target_id, "tag": label},
        case_uid=case_uid,
    )


#: Closed on purpose: "the client's adviser" survives staff turnover; a hard-coded person doesn't.
TASK_ASSIGNEES = ("role", "adviser", "client")


async def _task_assignee(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> dict[str, Any]:
    """Resolve ``assign_to`` into ``create_task``'s assignee arguments."""
    assign_to = (_text(config, "assign_to") or "role").lower()
    if assign_to not in TASK_ASSIGNEES:
        raise ActionRefused(
            f"'{assign_to}' is not a task assignee — use one of {', '.join(TASK_ASSIGNEES)}"
        )
    if assign_to == "role":
        slug = _required_text(config, "assignee_role_slug")
        role_id = (
            await session.execute(
                text("SELECT id FROM auth.roles WHERE tenant_id = :t AND slug = :s"),
                {"t": ctx.tenant_id, "s": slug},
            )
        ).scalar_one_or_none()
        if role_id is None:
            raise ActionRefused(f"this firm has no '{slug}' role to assign the task to")
        return {"assignee_kind": "role", "assignee_role_id": int(role_id)}
    if assign_to == "client":
        return {"assignee_kind": "contact", "assignee_contact_id": _require_contact(ctx)}

    contact_id = _require_contact(ctx)
    # `role = 'adviser'` is unique per contact (0245), so no tie-break ordering is needed.
    adviser_id = (
        await session.execute(
            text(
                "SELECT adviser_id FROM crm.client_advisers "
                "WHERE tenant_id = :t AND contact_id = :c AND role = 'adviser'"
            ),
            {"t": ctx.tenant_id, "c": contact_id},
        )
    ).scalar_one_or_none()
    if adviser_id is None:
        raise ActionRefused(
            "this client has no assigned adviser, so there is nobody to give the task to"
        )
    return {"assignee_kind": "user", "assignee_user_id": str(adviser_id)}


async def raise_task(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    context = await build_context(session, ctx)
    title = _render(_required_text(config, "title"), context)
    description = config.get("description")
    body = _render(description, context) if isinstance(description, str) and description else None
    assignee = await _task_assignee(session, ctx, config)

    subject_type: str | None = None
    subject_id: str | None = None
    if ctx.subject_kind == "contact" and ctx.subject_id is not None:
        subject_type, subject_id = "contact", str(ctx.subject_id)

    due_in = _int(config, "due_in_days")
    due_date: str | None = None
    if due_in is not None:
        # Computed in Postgres so the due date shares the clock ``created_at`` defaults to.
        due_date = str(
            (
                await session.execute(
                    text("SELECT (current_date + make_interval(days => :d))::date"), {"d": due_in}
                )
            ).scalar_one()
        )

    try:
        task = await tasks_service.create_task(
            session,
            tenant_id=ctx.tenant_id,
            title=title,
            description=body,
            category=_text(config, "category") or "generic",
            priority=_text(config, "priority") or "normal",
            subject_type=subject_type,
            subject_id=subject_id,
            source_kind="automation",
            # A redelivery must reuse the task; a schedule must raise a fresh one, so gets no key.
            source_ref=(
                f"{ctx.automation_uid}:{ctx.event_id}" if ctx.event_id is not None else None
            ),
            due_date=due_date,
            **assignee,
        )
    except tasks_service.TaskError as exc:
        raise ActionRefused(str(exc)) from exc

    return ActionOutcome(
        detail=f"raised task {task['uid']}",
        subject={
            "assignee_kind": assignee["assignee_kind"],
            "subject_type": subject_type,
            "priority": task["priority"],
        },
        path_params={},
        case_uid=ctx.case_uid,
    )


async def assign_adviser(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    contact_id = _require_contact(ctx)
    adviser_id = _required_text(config, "adviser_id")
    # Unauthored ``role`` means the adviser slot (0245), same as the service's own default —
    # `_text` would otherwise hand the service a literal ``None``, which is not one of its roles.
    role = _text(config, "role") or contacts_mod.ADVISER_ROLE
    try:
        await contacts_mod.assign_adviser(
            session,
            tenant_id=ctx.tenant_id,
            contact_id=contact_id,
            adviser_id=adviser_id,
            role=role,
        )
    except LookupError as exc:
        # The service's own tenant checks — the rules a parallel INSERT would have skipped.
        raise ActionRefused(str(exc)) from exc
    except ValueError as exc:
        # An authored `role` outside the four allowed values — a config mistake, not a fault.
        raise ActionRefused(str(exc)) from exc
    # The uid, because that is what the HTTP route records — one shape for one event, whoever
    # raised it. `_SUBJECTS` reads either, so this is not what keeps the notification working; it
    # is what stops the two paths diverging for the next reader who writes
    # `path_params->>'contact_uid'` and finds it on human-raised rows only.
    contact_uid = (
        await session.execute(
            text("SELECT uid FROM crm.contacts WHERE id = :id AND tenant_id = :t"),
            {"id": contact_id, "t": ctx.tenant_id},
        )
    ).scalar_one_or_none()
    return ActionOutcome(
        detail=f"assigned adviser {adviser_id}",
        subject={"adviser_id": adviser_id},
        # Falls back to the serial if the row somehow cannot be read: an event with the old
        # shape still resolves (both are declared), where an empty param resolves to nothing.
        path_params=(
            {"contact_uid": str(contact_uid)} if contact_uid else {"contact_id": contact_id}
        ),
        case_uid=ctx.case_uid,
    )


async def set_case_status(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    # Imported here: a module-scope import would put this module inside the orchestrator's cycle.
    from app.flows import orchestrator

    _, case_uid = await _require_case_id(session, ctx)
    status = _required_text(config, "status")
    try:
        changed = await orchestrator.set_case_status(
            session,
            tenant_id=ctx.tenant_id,
            case_uid=case_uid,
            status=status,
            reason=f"set by automation “{ctx.automation_name}”",
        )
    except orchestrator.CaseStatusRefused as exc:
        raise ActionRefused(str(exc)) from exc
    return ActionOutcome(
        detail=(
            f"case {case_uid} set to {status}"
            if changed
            else f"case {case_uid} was already finished"
        ),
        changed=changed,
        subject={"status": status},
        path_params={"case_uid": case_uid},
        case_uid=case_uid,
    )


# send_email — THE CONTROL: a rule names a `template_code`; we send its CURRENT PUBLISHED version,
# so copy a client reads went through the firm's own publish gate. Components and linked datasets
# are refused at preflight; `schema.css` is print CSS, so the body wears `app/email/transport.py`'s
# shell.

#: Closed on purpose: an authored address would be a standing exfiltration channel.
EMAIL_RECIPIENTS = ("client", "adviser", "support")


async def _recipient(session: AsyncSession, ctx: AutomationContext, to: str) -> tuple[str, str]:
    """``(address, description)`` for an authored ``to``."""
    if to == "client":
        contact_id = _require_contact(ctx)
        email = (
            await session.execute(
                text("SELECT email FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
                {"c": contact_id, "t": ctx.tenant_id},
            )
        ).scalar_one_or_none()
        if not email:
            raise ActionRefused("this client has no email address on file")
        return str(email), "the client"
    if to == "adviser":
        contact_id = _require_contact(ctx)
        email = (
            await session.execute(
                text(
                    "SELECT u.email FROM crm.client_advisers ca "
                    "JOIN auth.users u ON u.id = ca.adviser_id "
                    # `role = 'adviser'` is unique per contact (0245), so no tie-break ordering
                    # is needed.
                    "WHERE ca.tenant_id = :t AND ca.contact_id = :c AND ca.role = 'adviser'"
                ),
                {"t": ctx.tenant_id, "c": contact_id},
            )
        ).scalar_one_or_none()
        if not email:
            raise ActionRefused("this client has no assigned adviser with an email address")
        return str(email), "the client's adviser"
    # 'support': the firm's own support mailbox — internal notices that need not become tasks.
    brand = await email_mod.brand_for(session, ctx.tenant_id, portal="platform")
    if not brand.support_email:
        raise ActionRefused("this firm has no support email address set in its branding")
    return brand.support_email, "the firm's support address"


@dataclass(frozen=True)
class ResolvedTemplate:
    """A published template version, ready to render as an email body."""

    template_id: int
    version_id: int
    version_number: int
    name: str
    html: str
    subject: str | None
    #: The firm's own editor CSS and its authored preview line. BOTH were dropped on this path while
    #: `render_published_email` honoured them — so one template arrived styled from a notification
    #: rule and unstyled, with no preview line, from a journey.
    css: str
    preheader: str | None


async def resolve_email_template(
    session: AsyncSession, *, tenant_id: int, code: str
) -> ResolvedTemplate:
    """The firm's EMAIL template for ``code``, at its current published version.

    Reads ``email.templates`` — the surface a firm actually authors emails in. It read
    ``documents.templates`` until Aug 2026, which meant a template written in the email editor was
    unreachable from a journey, and the document-shaped refusals that guarded that mistake are gone
    with it.

    Refuses — with a reason an author can act on — an absent template, one with no published
    version, and one with no content. Called by BOTH ``send_email`` at runtime and ``preflight`` at
    authoring, which is what makes "an unpublished template is a preflight failure, not a runtime
    surprise" true rather than aspirational.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT t.id AS template_id, t.name, "
                    "       v.id AS version_id, v.version_number, v.subject, v.preheader, v.schema "
                    "FROM email.templates t "
                    "LEFT JOIN email.template_versions v "
                    "       ON v.id = t.current_published_version_id "
                    # `archived_at IS NULL`, as `render_published_email` already required:
                    # archiving leaves `current_published_version_id` standing, so an
                    # archived template kept sending from a journey after a firm retired it.
                    "WHERE t.tenant_id = :t AND t.code = :c AND t.archived_at IS NULL"
                ),
                {"t": tenant_id, "c": code},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ActionRefused(
            f"this firm has no email template with the code '{code}' — create it in Emails first"
        )
    if row["version_id"] is None:
        raise ActionRefused(
            f"the template '{code}' has no published version — publish it before an automation "
            "can send it"
        )
    schema = row["schema"] if isinstance(row["schema"], dict) else {}
    body = schema.get("html")
    if not isinstance(body, str) or not body.strip():
        raise ActionRefused(f"the published version of '{code}' has no content")
    # The SUBJECT is a column on the version, not a schema key — an email template holds its own
    # subject and preview line where a document template had neither.
    subject = row["subject"]
    return ResolvedTemplate(
        template_id=int(row["template_id"]),
        version_id=int(row["version_id"]),
        version_number=int(row["version_number"]),
        name=str(row["name"]),
        html=body,
        subject=subject.strip() if isinstance(subject, str) and subject.strip() else None,
        css=str(schema.get("css") or ""),
        preheader=(
            str(row["preheader"]).strip()
            if isinstance(row["preheader"], str) and str(row["preheader"]).strip()
            else None
        ),
    )


#: A merge field's name — the engine's own rule, which states why (`jinja_engine.VARIABLE_NAME_RE`).
#: No dots: see ``parse_email_variables``.
_VARIABLE_NAME_RE = jinja_engine.VARIABLE_NAME_RE

#: Merge fields per send — `lookup.MAX_MAPPING_KEYS`'s reasoning, lower: these fill blanks in copy.
MAX_EMAIL_VARIABLES = 20

#: Length before interpolation: fine for a sentence, useless as a way round the publish gate.
MAX_EMAIL_VARIABLE_CHARS = 500


def parse_email_variables(config: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, str]:
    """``{name: interpolated string}`` — the template merge fields the CASE cannot carry.

    WHY THIS EXISTS. A merge field the case facts do not carry renders EMPTY and silently — a
    template saying "Your {{ document_name }} is ready" delivers "Your  is ready" with nothing
    anywhere saying so. These are the values an author supplies for exactly those fields.

    WHAT IT IS NOT. Not a second place to author copy — that is the control this whole section is
    built around. A value interpolates in the AUTHORED grammar (``{{ key || fallback }}``, what
    ``raise_task.title`` uses), so it is composed from facts with a literal fallback and the
    sentence around it is still the firm's published template; `MAX_EMAIL_VARIABLE_CHARS` keeps that
    true rather than a matter of taste. A JSON OBJECT STRING is accepted as well as an object,
    ``lookup._mapping``'s reason: the builder's config fields are textareas.

    THE TWO REFUSALS, and the second is the one that matters:

    * A DOTTED NAME. ``jinja_engine.render`` expands dotted keys into nested dicts, so a variable
      called ``brand.support_email`` is not a variable at all — it is a surgical overwrite of one
      token inside a scope the firm's own branding owns.
    * A NAME THAT SHADOWS A SCOPE. Same mechanism one level up: a variable called ``brand`` replaces
      the whole nested dict, so an author could put any address they liked in a client-facing email
      under the firm's own name. The reserved set is DERIVED from the context about to be rendered
      rather than kept as a literal list — a list goes stale the day a scope is added to
      ``build_context``, and silently, in the direction that matters.
    """
    raw = config.get("variables")
    if raw is None:
        return {}
    if isinstance(raw, str):
        body = raw.strip()
        if not body:
            return {}
        try:
            raw = json.loads(body)
        except ValueError as exc:
            raise ActionRefused(
                "'variables' is not a readable set of fields — write it as "
                '{"document_name": "your statement"}'
            ) from exc
    if not isinstance(raw, Mapping):
        raise ActionRefused("'variables' must be a set of named fields")
    if len(raw) > MAX_EMAIL_VARIABLES:
        raise ActionRefused(f"an email may fill in at most {MAX_EMAIL_VARIABLES} merge fields")

    # Top level only: `contact.email` reserves `contact`; the dotted-name check covers the rest.
    reserved = {str(key).split(".", 1)[0] for key in context}
    out: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not _VARIABLE_NAME_RE.match(name.strip()):
            raise ActionRefused(
                f"'{name}' is not a usable merge field name — letters, numbers and underscores "
                "only, and no dots"
            )
        clean = name.strip()
        if clean in reserved:
            raise ActionRefused(
                f"'{clean}' is a field the case already provides — filling it in here "
                "would replace the real value, so give the merge field its own name"
            )
        if not isinstance(value, str):
            raise ActionRefused(f"the value for '{clean}' must be text")
        if len(value) > MAX_EMAIL_VARIABLE_CHARS:
            raise ActionRefused(
                f"the value for '{clean}' is longer than {MAX_EMAIL_VARIABLE_CHARS} characters — a "
                "merge field fills a blank in approved copy; longer copy belongs in the template"
            )
        out[clean] = value
    return out


async def send_email(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    code = _required_text(config, "template_code")
    to = (_text(config, "to") or "client").lower()
    if to not in EMAIL_RECIPIENTS:
        raise ActionRefused(
            f"'{to}' is not an email recipient — use one of {', '.join(EMAIL_RECIPIENTS)}"
        )
    template = await resolve_email_template(session, tenant_id=ctx.tenant_id, code=code)
    address, who = await _recipient(session, ctx, to)
    context = await build_context(session, ctx)

    # THE FIRM'S BRAND GOES IN THE MERGE CONTEXT, and it was the one send path that left it out.
    # This renders a FIRM-AUTHORED template — the same document the editor writes and every other
    # send path merges — whose masthead is `{% if brand.logo_url %}<img …>{% else %}<div>{{
    # brand.name }}</div>{% endif %}`. With no `brand` in scope, Jinja resolves both arms to
    # Undefined: the condition is false, so the image never renders, and the else arm renders an
    # EMPTY div. So a journey email arrived with no logo AND no name — not a degraded masthead but
    # an absent one, which reads as "branding is broken" rather than as a missing value.
    #
    # `brand_for` was already called below for `email_shell.shell`, so the outer chrome was on
    # brand while the body it wrapped was not: the closer a firm looked, the more it looked like
    # the editor was at fault. It is resolved HERE now, once, and both uses read the same object —
    # `brand_tokens` is the one shape every template's `{{ brand.* }}` is written against
    # (`email/transport.py`), so this path cannot offer a differently-shaped brand either.
    #
    # It also makes the shadow guard below real rather than vacuous: `reserved` is derived from the
    # context, so `brand` becomes a name an author's own variable cannot take.
    brand = await email_mod.brand_for(session, ctx.tenant_id, portal="client")
    context["brand"] = email_mod.brand_tokens(brand)

    # `_render`, not Jinja: a value is DATA to the template and cannot smuggle in a `{% … %}`.
    variables = parse_email_variables(config, context)
    context.update({name: _render(value, context) for name, value in variables.items()})

    # Rendered by the TEMPLATE ENGINE, not `interpolation.py`: the email editor writes Jinja and
    # interpolation passes a non-match through UNCHANGED, so raw Jinja would reach the inbox.
    # `to_jinja` first — `{{ key || text }}` is a Jinja syntax error. Autoescape escapes merged
    # VALUES, not the firm's own markup; the SUBJECT renders with it off, since `&` must stay `&`.
    try:
        # `render_with_diagnostics` also names the tokens that came out Undefined — silent blanks.
        body, missing = jinja_engine.render_with_diagnostics(
            interpolation.to_jinja(template.html), context
        )
        subject_line, subject_missing = jinja_engine.render_with_diagnostics(
            interpolation.to_jinja(template.subject or template.name), context, autoescape=False
        )
        # The preheader renders through the SAME engine as the body, and autoescaped: it is a third
        # authored field, so a Format or an If-empty on it is Jinja like everywhere else, and
        # `interpolation` would pass such a token through unchanged into the one line an inbox shows
        # beside the subject. `sendable` requires its caller to have escaped this, which autoescape
        # is how every other send path satisfies — `preview.py` and `render.py` both already
        # rendered it this way, so a journey shipped braces for a preheader the author had just
        # watched preview correctly.
        preheader_line, preheader_missing = jinja_engine.render_with_diagnostics(
            interpolation.to_jinja(template.preheader or ""), context
        )
        unsourced = sorted(set(missing) | set(subject_missing) | set(preheader_missing))
    except Exception as exc:  # noqa: BLE001 — a filter's own error is not a TemplateError
        # A template the engine cannot render is a refusal naming the code, not a traceback. The
        # catch is BARE because Jinja does not wrap what a filter throws: `{{ x | round(2) }}` on a
        # string is a plain `TypeError`, and only the parse errors are `TemplateError`s. `getattr`
        # for the same reason — a `TypeError` has no `.message`, and reading it would raise from
        # inside the handler.
        raise ActionRefused(
            f"the '{code}' email template could not be rendered "
            f"({getattr(exc, 'message', None) or exc}) — open it in the email editor and check "
            "its merge fields and conditions"
        ) from exc

    sender = await email_mod.resolve_sender(session, ctx.tenant_id, portal="client")
    # `shell` escapes the heading and passes `body` through: the Jinja pass already escaped it.
    shell = email_shell.shell(
        subject_line,
        body,
        "Open your portal" if settings.client_portal_base_url else None,
        settings.client_portal_base_url or None,
        brand,
    )
    # `shell` returns a FRAGMENT: without this there is no doctype, charset or Outlook reset. The
    # firm's own CSS and its authored preview line ride along, as they do on every other send path —
    # dropping them made one template arrive styled from a rule and unstyled from a journey.
    # The author's own images made reachable: this path renders a firm's arbitrary template, which
    # is the one most likely to carry a picture the author dropped in themselves.
    document = email_render.sendable(
        shell,
        css=template.css,
        preheader=preheader_line,
        asset_origin=await email_mod.asset_origin(session, ctx.tenant_id),
    )
    sent = await email_mod.send_email(
        to=address,
        subject=subject_line,
        html=document,
        sender=sender.from_addr,
        reply_to=sender.reply_to,
        ctx=ledger.SendContext(tenant_id=ctx.tenant_id, template_code=code),
    )
    if not sent:
        # `send_email`'s False is two things: SUPPRESSION is a deployment state, so the step
        # completes with `changed=False` (out of the trigger stream); a rejection stays a refusal.
        # The send refreshed the saved allowlist, so this reads the same decision.
        suppressed = email_mod.suppression_reason(address) or email_mod.sending_disabled()
        if suppressed is not None:
            return ActionOutcome(
                detail=f"the email to {who} was not sent",
                changed=False,
                subject={"template_code": code, "to": to},
                path_params={},
                case_uid=ctx.case_uid,
            )
        raise ActionRefused(f"the email to {who} could not be sent")
    detail = f"emailed {who} using '{code}' v{template.version_number}"
    if unsourced:
        # Named, not refused: the email went, but a silent blank is how "Your  is ready" ships.
        detail += ". No value for " + ", ".join(unsourced)
    return ActionOutcome(
        detail=detail,
        subject={"template_code": code, "to": to},
        path_params={},
        case_uid=ctx.case_uid,
    )


# The document actions: `app/documents/service.py` exists for these three, so the human and
# automated paths are ONE function. `preflight` asks the permission the service does not carry.


def _document_service() -> Any:
    """The documents service, imported lazily.

    It pulls in the render/template lint and the email shell, and this module is imported by the
    step-handler registry — a module-scope import would put the whole documents stack inside the
    engine's import graph for the benefit of three handlers. Same reasoning as ``set_case_status``.
    """
    from app.documents import service as docs_service

    return docs_service


async def _target_contact(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> int:
    """Which client a document act is FOR.

    ``target_contact`` defaults to the firing's own subject (the overwhelming majority: "when a
    client does X, ask them for Y"). An explicit contact id is allowed — a corporate client's
    administrator, a trustee — but it must belong to THIS firm, checked here rather than trusted
    from the authored config: the id travels in a rule that may have been copied between firms.
    """
    raw = config.get("target_contact")
    if raw is None or (isinstance(raw, str) and raw.strip().lower() in ("", "client", "subject")):
        return _require_contact(ctx)
    contact_id = _int({"c": raw}, "c")
    if contact_id is None:
        raise ActionRefused(
            f"'{raw}' is not a client to send this to — use 'client' for the client this "
            "automation is about, or a client's id"
        )
    known = (
        await session.execute(
            text("SELECT 1 FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
            {"c": contact_id, "t": ctx.tenant_id},
        )
    ).scalar_one_or_none()
    if known is None:
        raise ActionRefused(f"client {contact_id} is not a client of this firm")
    return contact_id


async def request_document(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    """Ask a client to upload a document — the client-facing request, portal item and email."""
    docs = _document_service()
    contact_id = await _target_contact(session, ctx, config)
    context = await build_context(session, ctx)
    title = _render(_text(config, "title") or "Upload a document", context)
    category = _text(config, "category")
    try:
        req_uid = await docs.create_request(
            session,
            tenant_id=ctx.tenant_id,
            contact_id=contact_id,
            items=[docs.RequestItemSpec(action="upload", title=title, category=category)],
            # No authored message: free text from a rule must not reach a client.
            message=None,
        )
    except docs.DocumentServiceError as exc:
        raise ActionRefused(str(exc)) from exc
    return ActionOutcome(
        detail=f"asked the client for '{title}' ({req_uid})",
        subject={"contact_id": contact_id, "action": "upload", "category": category},
        path_params={},
        case_uid=ctx.case_uid,
    )


async def generate_document(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    """Render a template to a document, unattended.

    The action completes when the render is ENQUEUED, not when the PDF exists: the worker renders
    out of band, and an action is by definition something the engine does and moves on from. A
    render that then fails marks the DOCUMENT failed (visible in the documents surface, retryable
    there) — if a journey must not proceed until the document exists, the author wants the
    ``generate_document`` STEP, which waits for the worker's callback. That difference is the whole
    reason the contract lets both exist under one name.
    """
    docs = _document_service()
    template_uid = _required_text(config, "template_uid")
    contact_id: int | None = None
    if ctx.subject_kind == "contact" and ctx.subject_id is not None:
        contact_id = int(ctx.subject_id)
    try:
        doc_uid = await docs.generate(
            session,
            tenant_id=ctx.tenant_id,
            template_uid=template_uid,
            client_contact_id=contact_id,
            # The same marker the flow step writes, so automated documents have one provenance.
            metadata={
                "source": "automation",
                "automation_uid": ctx.automation_uid,
                "dataset": config.get("dataset"),
            },
            # Collapses a redelivery onto one document; a schedule gets none.
            idempotency_key=(
                f"{ctx.automation_uid}:{ctx.event_id}" if ctx.event_id is not None else None
            ),
        )
    except docs.DocumentServiceError as exc:
        raise ActionRefused(str(exc)) from exc
    return ActionOutcome(
        detail=f"queued document {doc_uid} from '{template_uid}'",
        subject={"template_uid": template_uid, "document_uid": doc_uid},
        path_params={},
        case_uid=ctx.case_uid,
    )


async def send_document(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    """Release an existing document to the client: portal visibility + the notification email."""
    docs = _document_service()
    document_uid = _required_text(config, "document_uid")
    contact_id = await _target_contact(session, ctx, config)
    try:
        outcome = await docs.send(
            session,
            tenant_id=ctx.tenant_id,
            document_uid=document_uid,
            contact_ids=[contact_id],
            # No covering note and no named sender: an automation is not a person.
            message=None,
            sent_by_name=None,
        )
    except docs.DocumentServiceError as exc:
        raise ActionRefused(str(exc)) from exc
    told = "and emailed them" if outcome.notified else "but could not email them"
    return ActionOutcome(
        detail=f"released '{outcome.document_name or document_uid}' to the client {told}",
        subject={"document_uid": document_uid, "contact_id": contact_id},
        path_params={"uid": document_uid},
        case_uid=ctx.case_uid,
    )


# webhook_out: the endpoint is NAMED, never typed (`AUTOMATION_WEBHOOK_ENDPOINTS` is an operator's
# setting), and it is enqueued in the same transaction, so the outbox owns delivery and retries.

#: The outbox ``event_type`` the dispatcher routes to the webhook sender.
WEBHOOK_EVENT_TYPE = "automation.webhook"

#: How many context keys one webhook may carry — a bound, so a rule cannot ship the whole context.
MAX_PAYLOAD_KEYS = 30


def webhook_endpoint(key: str) -> str | None:
    """The configured URL for an endpoint key, or None when this deployment has no such endpoint."""
    return settings.automation_webhook_endpoints.get(key)


def _payload_keys(config: Mapping[str, Any]) -> list[str]:
    raw = config.get("payload_keys")
    if raw is None:
        return []
    if isinstance(raw, str):
        # A single key, or the comma-separated spelling a text field produces.
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, list) or not all(isinstance(k, str) for k in raw):
        raise ActionRefused("'payload_keys' must be a list of fields to send")
    keys = [k.strip() for k in raw if k.strip()]
    if len(keys) > MAX_PAYLOAD_KEYS:
        raise ActionRefused(f"a webhook may carry at most {MAX_PAYLOAD_KEYS} fields")
    return keys


async def webhook_out(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    from app.flows import notifications

    key = _required_text(config, "endpoint_key")
    url = webhook_endpoint(key)
    if url is None:
        raise ActionRefused(
            f"'{key}' is not a webhook endpoint this deployment knows — an operator configures "
            "endpoints in AUTOMATION_WEBHOOK_ENDPOINTS, so a flow never names a raw URL"
        )
    keys = _payload_keys(config)
    context = await build_context(session, ctx)
    # Absent keys ride as null: a receiver parsing a fixed shape should see "no value", not a gap.
    body = {k: context.get(k) for k in keys}
    await notifications.enqueue(
        session,
        tenant_id=ctx.tenant_id,
        event_type=WEBHOOK_EVENT_TYPE,
        payload={
            "endpoint_key": key,
            "url": url,
            "body": body,
            "automation_uid": ctx.automation_uid,
            "automation_name": ctx.automation_name,
            "trigger_key": ctx.trigger_key,
            "case_uid": ctx.case_uid,
        },
    )
    return ActionOutcome(
        detail=f"queued a webhook to '{key}'",
        subject={"endpoint_key": key, "fields": len(keys)},
        path_params={},
        case_uid=ctx.case_uid,
    )


# lookup: a failure is an `ActionRefused` → a `failed` step and a `blocked`, retryable case, never
# the latched terminal `failed`. The event names the endpoint key and the memory keys, not a value.


async def lookup(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    from app.flows import conditions
    from app.flows import lookup as lookup_mod

    key = _required_text(config, "endpoint_key")
    url = lookup_mod.endpoint(key)
    if url is None:
        # Re-resolved live: a published flow outlives the deploy that allowlisted its endpoint.
        raise ActionRefused(
            f"'{key}' is not a lookup endpoint this deployment knows (configured: "
            f"{lookup_mod.known_keys()}) — an operator sets them in AUTOMATION_LOOKUP_ENDPOINTS, "
            "so a flow never names a URL"
        )
    # A lookup's whole output is case memory, so a firing with no case has nowhere to put it.
    case_id, case_uid = await _require_case_id(session, ctx)

    try:
        request_mapping = lookup_mod.parse_request_mapping(config)
        response_mapping = lookup_mod.parse_response_mapping(config)
    except lookup_mod.LookupRefused as exc:
        raise ActionRefused(str(exc)) from exc

    # The entry conditions' own facts plus this case's memory, which `build_context` merges.
    context: dict[str, Any] = await build_context(session, ctx)
    body = {
        field: conditions.resolve_field(source, context)
        for field, source in request_mapping.items()
    }

    try:
        payload = await lookup_mod.fetch(key, url, body)
        values = lookup_mod.select(payload, response_mapping)
        if not values:
            raise lookup_mod.LookupRefused(
                f"the '{key}' endpoint answered, but its answer carried none of the "
                f"{len(response_mapping)} field(s) this lookup maps"
            )
        # strict=True: these values ARE the output and a branch downstream is about to test them.
        written = await memory.write_many(
            session,
            tenant_id=ctx.tenant_id,
            case_id=case_id,
            values=values,
            source="action",
            node_id=_node_id(ctx),
            strict=True,
        )
    except (lookup_mod.LookupRefused, memory.MemoryRefused) as exc:
        raise ActionRefused(str(exc)) from exc

    return ActionOutcome(
        detail=f"looked up '{key}' and remembered {', '.join(written)}",
        subject={"endpoint_key": key, "memory_keys": written},
        path_params={},
        case_uid=case_uid,
    )


# The AI gates — AI ROUTES AND DRAFTS: `classify` writes a label a firm-authored BRANCH decides
# on and `draft_message` raises a TASK for a person. Neither may grow a send —
# `tests/test_flows_ai.py` AST-scans this module. The event records the call, never the content.


def _ai_input(config: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, str]:
    """The authored slice, resolved against the fact context.

    The context is built the same way every other action's copy is (``build_context``), so an author
    who can filter on a field can also feed it to a model — one vocabulary, one picker. What differs
    is that only the NAMED fields travel: there is no path from here that hands ``ai.py`` the rest.
    """
    from app.flows import ai as ai_mod

    return ai_mod.collect(ai_mod.parse_input_fields(config), context)


async def classify(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    """Read the authored slice, write ONE label from the author's closed set to case memory."""
    from app.flows import ai as ai_mod
    from app.flows import ai_usage

    # Like `lookup`: the whole output is case memory, so a firing with no case has nowhere to go.
    case_id, case_uid = await _require_case_id(session, ctx)
    node_id = _node_id(ctx)

    try:
        question = ai_mod.parse_prompt(config, "question", "what to decide about the material")
        labels = ai_mod.parse_labels(config)
        remember_as = ai_mod.parse_remember_as(config)
        # Both switches re-read live: a firm switched off or an allowance spent stops the call now.
        if not ai_mod.available():
            raise ai_mod.AIRefused("AI actions are unavailable. Contact support.")
        await ai_usage.check_budget(ctx.tenant_id)
        fields = _ai_input(config, await build_context(session, ctx))
    except ai_mod.AIRefused as exc:
        raise ActionRefused(str(exc)) from exc

    try:
        result = await ai_mod.classify(question=question, labels=labels, fields=fields)
    except ai_mod.AIRefused as exc:
        # Metered although it produced nothing: a parked journey's call belongs with the others.
        await ai_usage.record(
            tenant_id=ctx.tenant_id,
            purpose="classify",
            model=ai_mod.model_name(),
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            outcome="refused",
            case_id=case_id,
            node_id=node_id,
            automation_uid=ctx.automation_uid,
        )
        raise ActionRefused(str(exc)) from exc

    # Metered before the memory write and on its own connection: the tokens were spent regardless.
    await ai_usage.record(
        tenant_id=ctx.tenant_id,
        purpose="classify",
        model=result.call.model,
        input_tokens=result.call.input_tokens,
        output_tokens=result.call.output_tokens,
        latency_ms=result.call.latency_ms,
        outcome="ok" if result.in_set else "unknown",
        case_id=case_id,
        node_id=node_id,
        automation_uid=ctx.automation_uid,
        memory_key=remember_as,
    )

    try:
        # strict=True, as for `lookup`: this value IS the output and a branch will test it.
        await memory.write_many(
            session,
            tenant_id=ctx.tenant_id,
            case_id=case_id,
            values={remember_as: result.label},
            source="action",
            node_id=node_id,
            strict=True,
        )
    except memory.MemoryRefused as exc:
        raise ActionRefused(str(exc)) from exc

    return ActionOutcome(
        detail=(
            f"classified as '{result.label}' into {remember_as}"
            if result.in_set
            else f"could not classify this, so {remember_as} is '{result.label}'"
        ),
        subject={
            "memory_key": remember_as,
            "label": result.label,
            "in_set": result.in_set,
            "labels": len(labels),
            "model": result.call.model,
        },
        path_params={},
        case_uid=case_uid,
    )


#: How long a raised draft's title may be before it stops being a title.
MAX_DRAFT_TITLE = 120

#: The notice a drafted message wears — the reviewer must know nobody has checked it (pinned).
DRAFT_NOTICE = (
    "Drafted automatically and NOT reviewed. Check every fact against the client's record, "
    "edit as needed, and send it yourself — nothing has been sent."
)


async def _draft_assignee(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> dict[str, Any]:
    """Who reviews the draft.

    A ROLE, or the client's own adviser — and deliberately NOT the closed set ``raise_task`` uses,
    which includes ``client``. Assigning a client the review of unreviewed, model-written copy
    addressed to them is the exact failure this action exists to prevent, so the option does not
    exist here rather than being documented as a bad idea.
    """
    slug = _text(config, "assignee_role_slug")
    if slug:
        role_id = (
            await session.execute(
                text("SELECT id FROM auth.roles WHERE tenant_id = :t AND slug = :s"),
                {"t": ctx.tenant_id, "s": slug},
            )
        ).scalar_one_or_none()
        if role_id is None:
            raise ActionRefused(f"this firm has no '{slug}' role to give the draft to")
        return {"assignee_kind": "role", "assignee_role_id": int(role_id)}

    contact_id = _require_contact(ctx)
    # `role = 'adviser'` is unique per contact (0245), so no tie-break ordering is needed.
    adviser_id = (
        await session.execute(
            text(
                "SELECT adviser_id FROM crm.client_advisers "
                "WHERE tenant_id = :t AND contact_id = :c AND role = 'adviser'"
            ),
            {"t": ctx.tenant_id, "c": contact_id},
        )
    ).scalar_one_or_none()
    if adviser_id is None:
        raise ActionRefused(
            "this client has no assigned adviser and the action names no role, so there is nobody "
            "to review the draft"
        )
    return {"assignee_kind": "user", "assignee_user_id": str(adviser_id)}


async def draft_message(
    session: AsyncSession, ctx: AutomationContext, config: Mapping[str, Any]
) -> ActionOutcome:
    """Draft client-facing copy and raise it as a task for a human. Sends nothing."""
    from app.flows import ai as ai_mod
    from app.flows import ai_usage

    node_id = _node_id(ctx)
    case_id: int | None = None
    if ctx.case_uid:
        case_id = (
            await session.execute(
                text("SELECT id FROM flows.cases WHERE uid = :u AND tenant_id = :t"),
                {"u": ctx.case_uid, "t": ctx.tenant_id},
            )
        ).scalar_one_or_none()

    try:
        brief = ai_mod.parse_prompt(config, "brief", "what the draft should say")
        if not ai_mod.available():
            raise ai_mod.AIRefused("AI actions are unavailable. Contact support.")
        await ai_usage.check_budget(ctx.tenant_id)
        context = await build_context(session, ctx)
        fields = _ai_input(config, context)
    except ai_mod.AIRefused as exc:
        raise ActionRefused(str(exc)) from exc

    # Resolved BEFORE the call: a draft nobody can be given is a token spend for nothing.
    assignee = await _draft_assignee(session, ctx, config)

    try:
        written = await ai_mod.draft(brief=brief, fields=fields)
    except ai_mod.AIRefused as exc:
        await ai_usage.record(
            tenant_id=ctx.tenant_id,
            purpose="draft_message",
            model=ai_mod.model_name(),
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            outcome="refused",
            case_id=case_id,
            node_id=node_id,
            automation_uid=ctx.automation_uid,
        )
        raise ActionRefused(str(exc)) from exc

    await ai_usage.record(
        tenant_id=ctx.tenant_id,
        purpose="draft_message",
        model=written.model,
        input_tokens=written.input_tokens,
        output_tokens=written.output_tokens,
        latency_ms=written.latency_ms,
        outcome="ok",
        case_id=case_id,
        node_id=node_id,
        automation_uid=ctx.automation_uid,
    )

    title = _render(_text(config, "title") or "Review a drafted message", context)[:MAX_DRAFT_TITLE]
    subject_type: str | None = None
    subject_id: str | None = None
    if ctx.subject_kind == "contact" and ctx.subject_id is not None:
        subject_type, subject_id = "contact", str(ctx.subject_id)

    due_in = _int(config, "due_in_days")
    due_date: str | None = None
    if due_in is not None:
        due_date = str(
            (
                await session.execute(
                    text("SELECT (current_date + make_interval(days => :d))::date"), {"d": due_in}
                )
            ).scalar_one()
        )

    try:
        task = await tasks_service.create_task(
            session,
            tenant_id=ctx.tenant_id,
            title=title,
            # In the task BODY, behind the notice: a documents row would be releasable.
            description=f"{DRAFT_NOTICE}\n\n{written.text}",
            # Fixed, not authored: a review task is a review task.
            category="generic",
            priority="normal",
            subject_type=subject_type,
            subject_id=subject_id,
            source_kind="automation",
            source_ref=(
                f"{ctx.automation_uid}:{ctx.event_id}" if ctx.event_id is not None else None
            ),
            due_date=due_date,
            **assignee,
        )
    except tasks_service.TaskError as exc:
        raise ActionRefused(str(exc)) from exc

    return ActionOutcome(
        detail=f"drafted a message for review as task {task['uid']}",
        subject={
            "assignee_kind": assignee["assignee_kind"],
            "subject_type": subject_type,
            "model": written.model,
            "characters": len(written.text),
        },
        path_params={},
        case_uid=ctx.case_uid,
    )


def _node_id(ctx: AutomationContext) -> str | None:
    """The graph node a memory value was written by, for the case-memory row.

    An action node's firing carries it inside ``automation_uid`` (``flow:{code}:{node}``, built by
    ``step_handlers._action_context``) because ``AutomationContext`` is the RULE tier's shape and
    has no node of its own. Read back rather than threaded through: adding a field to that context
    for one action would put a flow-only concept in the vocabulary every target sees.
    """
    if ctx.automation_uid.startswith("flow:"):
        return ctx.automation_uid.rsplit(":", 1)[-1] or None
    return None


for _type, _handler in (
    ("apply_tag", apply_tag),
    ("remove_tag", remove_tag),
    ("raise_task", raise_task),
    ("send_email", send_email),
    ("assign_adviser", assign_adviser),
    ("set_case_status", set_case_status),
    ("request_document", request_document),
    ("generate_document", generate_document),
    ("send_document", send_document),
    ("webhook_out", webhook_out),
    ("lookup", lookup),
    ("classify", classify),
    ("draft_message", draft_message),
):
    register_action(_type, _handler)


# ---- Publish preflight ------------------------------------------------------------------------


async def preflight(
    session: AsyncSession, *, tenant_id: int, action: Mapping[str, Any]
) -> str | None:
    """Why this action could never run in this firm, or ``None`` if it can.

    Called by the rules API before an automation is ENABLED (A4). The philosophy is the house one:
    a check that can only be made at authoring time should be made at authoring time, because the
    person who can fix it is present, whereas at 3am inside a client's journey nobody is.

    Four refusals, in the order an author would hit them:

    1. an action type outside the contract, or one whose handler this deployment has not
       registered (the Phase B actions);
    2. an action whose declared permission is held by NO role in the tenant — nobody in the firm
       could perform this act by hand, so the automation is asserting an authority the firm has
       not granted anyone;
    3. per-action static checks that need no runtime facts (a ``send_email`` naming a template
       that is absent or unpublished, a ``set_case_status`` naming a working state);
    4. malformed required config.

    NOT checked here, deliberately: anything that depends on the firing (does this client have an
    email address, is there an adviser assigned). Those are runtime truths that change hourly, and
    refusing to save a rule because a client happens to lack an adviser today would be wrong.
    """
    action_type = action.get("type")
    if not isinstance(action_type, str) or action_type not in ACTIONS:
        return f"'{action_type}' is not an action type"
    spec = ACTIONS[action_type]
    if action_type not in HANDLERS or not spec.implemented:
        return f"the '{action_type}' action is not available yet"

    if not await permission_held_by_any_role(
        session, tenant_id=tenant_id, permission=spec.permission
    ):
        return (
            f"no role in this firm holds '{spec.permission}', which is what a person needs to "
            f"{action_type.replace('_', ' ')} — grant it to a role first, or this automation "
            "would be doing something nobody here is allowed to do"
        )

    config = action_config(action)
    try:
        if action_type == "send_email":
            code = _required_text(config, "template_code")
            to = (_text(config, "to") or "client").lower()
            if to not in EMAIL_RECIPIENTS:
                return (
                    f"'{to}' is not an email recipient — use one of {', '.join(EMAIL_RECIPIENTS)}"
                )
            await resolve_email_template(session, tenant_id=tenant_id, code=code)
        elif action_type == "set_case_status":
            from app.flows.states import TERMINAL, State

            status = _required_text(config, "status")
            try:
                state = State(status)
            except ValueError:
                return f"'{status}' is not a case status"
            if state not in TERMINAL:
                return (
                    f"'{status}' is a working state the engine re-derives from the case's steps, "
                    "so setting it would not survive the next event — only "
                    f"{', '.join(sorted(s.value for s in TERMINAL))} can be set directly"
                )
        elif action_type in ("apply_tag", "remove_tag"):
            tag_target = (_text(config, "target") or "contact").lower()
            if tag_target not in tags_mod.TARGETS:
                return f"'{tag_target}' is not a tag target (contact or case)"
            raw = _required_text(config, "tag")
            # A fully interpolated tag cannot be checked here; a blank or over-long literal can.
            if not interpolation.keys(raw):
                tags_mod.normalise(raw)
        elif action_type == "raise_task":
            _required_text(config, "title")
            assign_to = (_text(config, "assign_to") or "role").lower()
            if assign_to not in TASK_ASSIGNEES:
                return (
                    f"'{assign_to}' is not a task assignee — use one of {', '.join(TASK_ASSIGNEES)}"
                )
            if assign_to == "role":
                slug = _required_text(config, "assignee_role_slug")
                exists = (
                    await session.execute(
                        text("SELECT 1 FROM auth.roles WHERE tenant_id = :t AND slug = :s"),
                        {"t": tenant_id, "s": slug},
                    )
                ).scalar_one_or_none()
                if exists is None:
                    return f"this firm has no '{slug}' role to assign the task to"
            category = _text(config, "category") or "generic"
            if category not in tasks_service.CATEGORIES:
                return f"'{category}' is not a task category"
            priority = _text(config, "priority") or "normal"
            if priority not in tasks_service.PRIORITIES:
                return f"'{priority}' is not a task priority"
        elif action_type == "assign_adviser":
            _required_text(config, "adviser_id")
            role = _text(config, "role")
            if role is not None and role not in contacts_mod.CLIENT_TEAM_ROLES:
                return (
                    f"'{role}' is not a client team role — use one of "
                    f"{', '.join(contacts_mod.CLIENT_TEAM_ROLES)}"
                )
        elif action_type == "request_document":
            _required_text(config, "title")
            _static_target_contact(config)
        elif action_type == "generate_document":
            uid = _required_text(config, "template_uid")
            published = (
                await session.execute(
                    text(
                        "SELECT 1 FROM documents.templates t "
                        "WHERE t.uid = :u AND t.tenant_id = :t AND t.archived_at IS NULL "
                        "  AND t.current_published_version_id IS NOT NULL"
                    ),
                    {"u": uid, "t": tenant_id},
                )
            ).first()
            if published is None:
                return (
                    f"the template '{uid}' is missing, archived, or has no published version — "
                    "publish it before an automation can render it"
                )
        elif action_type == "send_document":
            doc = _required_text(config, "document_uid")
            _static_target_contact(config)
            found = (
                await session.execute(
                    text("SELECT 1 FROM documents.documents WHERE uid = :u AND tenant_id = :t"),
                    {"u": doc, "t": tenant_id},
                )
            ).first()
            if found is None:
                return f"this firm has no document '{doc}' to release"
        elif action_type == "webhook_out":
            key = _required_text(config, "endpoint_key")
            if webhook_endpoint(key) is None:
                known = ", ".join(sorted(settings.automation_webhook_endpoints)) or "none"
                return (
                    f"'{key}' is not a webhook endpoint this deployment knows (configured: "
                    f"{known}) — endpoints are set by an operator, so a flow never names a URL"
                )
            _payload_keys(config)
        elif action_type == "lookup":
            # The grammar too: a mapping naming no memory key learns nothing, silently, at runtime.
            from app.flows import lookup as lookup_mod

            return lookup_mod.preflight_issue(config)
        elif action_type in ("classify", "draft_message"):
            # Both switches at publish: `ai.preflight_issue` is the static half, the entitlement
            # needs the author's own firm session; branch coverage is `graph._classify_issues`.
            from app.flows import ai as ai_mod
            from app.flows import ai_usage

            if (issue := ai_mod.preflight_issue(action_type, config)) is not None:
                return issue
            entitlement = await ai_usage.tenant_state(session, tenant_id)
            if (issue := ai_usage.enablement_issue(entitlement)) is not None:
                return issue
    except (ActionRefused, tags_mod.TagError) as exc:
        return str(exc)
    return None


def _static_target_contact(config: Mapping[str, Any]) -> None:
    """The authoring half of ``_target_contact``: the SHAPE is checkable now, the id's existence is
    a runtime fact (a client can be archived long after a rule is armed), so only the shape is."""
    raw = config.get("target_contact")
    if raw is None or (isinstance(raw, str) and raw.strip().lower() in ("", "client", "subject")):
        return
    if _int({"c": raw}, "c") is None:
        raise ActionRefused(
            f"'{raw}' is not a client to send this to — use 'client' for the client this "
            "automation is about, or a client's id"
        )


async def permission_held_by_any_role(
    session: AsyncSession, *, tenant_id: int, permission: str
) -> bool:
    """Does ANY role in this tenant hold this permission (write implying read)?

    "Any role", not "the author's roles": the automation is not run by its author, so the question
    is whether the firm has granted this authority to anyone at all. A firm where nobody may send
    documents has decided something, and an automation that emails clients would be routing around
    that decision.
    """
    slugs = {permission}
    if permission.endswith(":read"):
        slugs.add(permission[: -len(":read")] + ":write")
    slugs.add(perms.SYSTEM.slug)
    found = (
        await session.execute(
            text(
                "SELECT 1 FROM auth.role_permissions rp "
                "JOIN auth.roles r ON r.id = rp.role_id "
                "JOIN auth.permissions p ON p.id = rp.permission_id "
                "WHERE r.tenant_id = :t AND p.slug = ANY(:slugs) LIMIT 1"
            ),
            {"t": tenant_id, "slugs": sorted(slugs)},
        )
    ).scalar_one_or_none()
    return found is not None


# ---- Execution + the dispatch target ----------------------------------------------------------


async def execute(
    session: AsyncSession,
    ctx: AutomationContext,
    spec: Mapping[str, Any],
    *,
    source: str = "rule",
) -> DispatchResult:
    """Run ONE authored action and record that it happened. The whole of both tiers' execution.

    ``source`` is the tier, and it rides on the audit event's automation marker: ``"rule"`` for a
    standalone automation, ``"flow"`` for an action node inside a journey. It is a parameter rather
    than something inferred here because the flow tier calls this directly (from
    ``step_handlers.ActionStepHandler``) with no trigger key to infer from — and an action that
    lied about which tier ran it would send an operator reading the trail to the wrong screen.

    The caller owns the savepoint. ``automation.consider`` wraps the rule tier's call; the step
    handler wraps the flow tier's. Both need one for the same reason: a refused or faulting action
    must leave nothing behind, and neither may take its caller's other work down with it.
    """
    action_type = spec.get("type")
    if not isinstance(action_type, str) or action_type not in ACTIONS:
        return DispatchResult(ok=False, detail=f"'{action_type}' is not an action type")
    action = ACTIONS[action_type]
    handler = HANDLERS.get(action_type)
    if handler is None or not action.implemented:
        return DispatchResult(ok=False, detail=f"the '{action_type}' action is not available yet")

    try:
        outcome = await handler(session, ctx, action_config(spec))
    except ActionRefused as exc:
        # A considered no: the consumer records it as a `failed` delivery carrying this reason.
        return DispatchResult(ok=False, detail=str(exc))

    if outcome.changed:
        await actions_mod.record_automation_action(
            session,
            event_type=action.event_type,
            method=action.method,
            route=action.route,
            tenant_id=ctx.tenant_id,
            automation_uid=ctx.automation_uid,
            automation_name=ctx.automation_name,
            # Depth is READ, not re-counted: the consumer adds one when it reads the marker.
            chain_depth=ctx.chain_depth,
            source=source,
            path_params=outcome.path_params,
            subject=outcome.subject,
        )
    else:
        logger.info(
            "automation action was a no-op",
            extra={
                "event": "automation_action_noop",
                "automation_uid": ctx.automation_uid,
                "tenant_id": ctx.tenant_id,
                "action_type": action_type,
            },
        )
    return DispatchResult(ok=True, detail=outcome.detail, case_uid=outcome.case_uid)


class ActionTarget:
    """The ``"action"`` dispatch kind: the rule tier's adapter onto ``execute``."""

    async def run(
        self, session: AsyncSession, ctx: AutomationContext, spec: Mapping[str, Any]
    ) -> DispatchResult:
        # A rule on a case trigger IS flow-sourced work — the marker names the firing's origin.
        source = "flow" if ctx.trigger_key.startswith("case.") else "rule"
        return await execute(session, ctx, spec, source=source)


def _register() -> None:
    """Register with A1's dispatch seam at import.

    Imported inside the function for the same import-cycle reason as ``set_case_status``: this
    module already imports ``AutomationContext``/``DispatchResult`` from ``triggers`` at the top,
    which is fine, but the registration itself is a side effect and belongs somewhere a reader
    can see it happen.
    """
    from app.flows import triggers

    triggers.register_target("action", ActionTarget())


_register()
