"""The second switch and the meter — who may use the AI gates, and what it has cost them.

``app/flows/ai.py`` is the pure/HTTP half; this is the half that needs a database. Split for the
``lookup`` reason and one more: everything here is a CONTROL (an entitlement, a budget, an
append-only record), and controls that live next to the thing they control tend to be edited
together with it. Schema: ``0050_flow_ai``.

**The per-tenant switch.** ``ANTHROPIC_API_KEY`` says the deployment CAN call a model;
``flows.ai_tenants`` says a given firm MAY. Both must be true and both are re-checked at dispatch,
not only at publish — a published flow outlives the decision that allowed it. Absence is the safe
default: no row means not enabled, so a firm becomes able to use this only when an operator writes
one. It is not firm self-service, and that is deliberate: sending a firm's clients' free text to a
third-party model is Graphene's decision as platform provider, the same posture that makes AML
adjudication operations-only (``docs/aml.md``).

**The budget, and why it is a real one.** Per tenant, per calendar month, in tokens. Summed before
every call, recorded after every call. When it is reached the action refuses with a reason naming
the ceiling — the step latches ``failed``, the case derives to ``blocked``, and an operator can see
it, raise the ceiling and re-run. That is the whole point: the programme already carries one honest
unmetered spend (screening, from a tenant flow, bounded only by ``kyc.pick_valid`` reusing a
pass — ``ReviewHandler.preflight`` is the seam), and shipping a second one would turn an
acknowledged gap into a habit.

**Two honest limits on the bound**, because a cap described as airtight when it is not is worse
than one described accurately:

1. *It is checked, not reserved.* Two calls that start before either finishes both see the
   pre-call total, so the overshoot is bounded by (concurrent AI calls) × (per-call tokens) — at
   worst a few thousand tokens past the line, since ``flowConcurrency`` bounds the first term and
   ``MAX_DRAFT_TOKENS`` the second. A reservation would need a lock on the tenant's counter held
   across a 30-second network call, which trades a small overshoot for a real contention problem on
   the dispatch path.
2. *It counts what the provider reported.* A call that fails before a response — a timeout, a
   connection error — burns wall-clock and possibly provider-side tokens that never reach us, and
   is recorded with zero tokens and outcome ``refused``. The row exists (the attempt is visible);
   the tokens are the ones we can actually account for.

**The meter writes on its own connection, and that is not an accident.** ``record`` opens a
``system_transaction`` rather than using the caller's session. Every action runs inside a savepoint
that rolls back on refusal, and a metering row that rolls back with the work is not a meter: the
tokens were spent whether or not the memory write afterwards succeeded. So the spend is committed
independently of the outcome it is recording. The same connection is used for the budget READ,
because ``flows.ai_usage`` has no ``authenticated`` grant at all (0050, the ``case_memory``
posture) and an action node can be dispatched from a firm-session request as easily as from the
worker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import system_transaction
from app.flows.ai import AIRefused

logger = logging.getLogger("flows.ai")

#: The two gates, mirroring ``flows.ai_usage.purpose``'s CHECK.
PURPOSES: frozenset[str] = frozenset({"classify", "draft_message"})

#: ``flows.ai_usage.outcome``'s CHECK. ``unknown`` is not a failure — it is the model answering
#: outside the declared set and the engine substituting the reserved label. Kept separate from
#: ``ok`` because a classify whose unknown rate is climbing is a labelling problem an author can
#: fix, and folding it into success would hide exactly that.
OUTCOMES: frozenset[str] = frozenset({"ok", "unknown", "refused"})


@dataclass(frozen=True)
class TenantAI:
    """A firm's AI entitlement as it stands right now."""

    enabled: bool
    monthly_token_cap: int


async def tenant_state(session: AsyncSession, tenant_id: int) -> TenantAI | None:
    """This firm's row, or ``None`` when it has never been enabled.

    Takes a session rather than opening one: publish preflight calls it on the author's own firm
    session (``flows.ai_tenants`` carries a ``tenant_lineage`` read policy for exactly that), and
    the runtime path calls it on the service_role connection ``_budget_session`` provides.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT enabled, monthly_token_cap FROM flows.ai_tenants WHERE tenant_id = :t"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return TenantAI(enabled=bool(row["enabled"]), monthly_token_cap=int(row["monthly_token_cap"]))


def enablement_issue(state: TenantAI | None) -> str | None:
    """Why this firm may not use an AI action, or ``None``. One wording, three readers (publish
    preflight, the builder's badge via the validate endpoint, and the runtime refusal) — so an
    author is told the same thing wherever they meet it."""
    if state is None:
        return (
            "this firm is not enabled for AI actions. Graphene operations enables a firm for them "
            "(Admin → the firm → AI), because sending client text to a model is a platform "
            "decision rather than a firm setting."
        )
    if not state.enabled:
        return (
            "AI actions are switched off for this firm. Graphene operations can switch them back "
            "on (Admin → the firm → AI)."
        )
    return None


async def period_tokens(session: AsyncSession, tenant_id: int) -> int:
    """Tokens this firm has spent in the current calendar month (UTC).

    UTC rather than the firm's own timezone: the ceiling is a cost control reconciled against a
    provider bill, and the bill's month is not a per-firm question. A firm's automation *schedule*
    honours its timezone (``automation.limits_for``) because that one is about when a client is
    contacted; this one is about money.
    """
    total = (
        await session.execute(
            text(
                "SELECT COALESCE(sum(input_tokens + output_tokens), 0) FROM flows.ai_usage "
                "WHERE tenant_id = :t "
                "  AND occurred_at >= date_trunc('month', (now() AT TIME ZONE 'utc'))"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    return int(total or 0)


async def check_budget(tenant_id: int) -> TenantAI:
    """The full runtime gate: enabled, and inside the month's ceiling. Raises ``AIRefused``.

    Opens its own service_role transaction — see the module header on why the caller's session
    cannot be used for ``flows.ai_usage``.
    """
    async with system_transaction() as session:
        state = await tenant_state(session, tenant_id)
        issue = enablement_issue(state)
        if issue is not None:
            raise AIRefused(issue)
        assert state is not None  # noqa: S101 - narrowed by enablement_issue above
        spent = await period_tokens(session, tenant_id)

    if spent >= state.monthly_token_cap:
        raise AIRefused(
            f"this firm has used its AI allowance for this month "
            f"({spent:,} of {state.monthly_token_cap:,} tokens). The journey is parked and can be "
            "re-run once Graphene operations raises the allowance or the month rolls over."
        )
    return state


async def record(
    *,
    tenant_id: int,
    purpose: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    outcome: str,
    case_id: int | None = None,
    node_id: str | None = None,
    automation_uid: str | None = None,
    memory_key: str | None = None,
) -> None:
    """Append one metering row, on its own connection, whatever the caller's transaction does.

    **The fact, never the content.** No prompt, no completion, no fragment of either — the table
    has no column for one and this signature has no parameter for one (0050's header on why). What
    lands is what an operator needs to answer "what is this costing and is it working".

    Never raises. A meter that could fail a step would be a control that breaks the thing it
    measures; a metering write that fails is logged and the step proceeds. The cost of that is an
    under-count on a database blip, which is the right way round.
    """
    if purpose not in PURPOSES or outcome not in OUTCOMES:  # pragma: no cover - guarded by callers
        logger.warning(
            "refusing to meter an unknown AI purpose/outcome",
            extra={"event": "ai_usage_bad_shape", "purpose": purpose, "outcome": outcome},
        )
        return
    try:
        async with system_transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO flows.ai_usage "
                    "  (tenant_id, purpose, model, input_tokens, output_tokens, latency_ms, "
                    "   outcome, case_id, node_id, automation_uid, memory_key) "
                    "VALUES (:t, :p, :m, :i, :o, :l, :out, :c, :n, :a, :k)"
                ),
                {
                    "t": tenant_id,
                    "p": purpose,
                    "m": model[:200],
                    "i": max(0, int(input_tokens)),
                    "o": max(0, int(output_tokens)),
                    "l": max(0, int(latency_ms)),
                    "out": outcome,
                    "c": case_id,
                    "n": node_id,
                    "a": automation_uid,
                    "k": memory_key,
                },
            )
    except Exception:  # noqa: BLE001 - a meter must never fail the work it is measuring
        logger.exception(
            "could not record AI usage; the call still happened",
            extra={"event": "ai_usage_write_failed", "tenant_id": tenant_id, "purpose": purpose},
        )


# ---------------------------------------------------------------------------------------------
# The operations surface
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Summary:
    """What the admin AI card shows for one firm."""

    enabled: bool
    monthly_token_cap: int
    tokens_this_month: int
    calls_this_month: int
    configured: bool


async def summary(session: AsyncSession, tenant_id: int) -> Summary:
    """One firm's switch, ceiling and month-to-date spend. Reads on the caller's session, which the
    admin route runs as service_role."""
    state = await tenant_state(session, tenant_id)
    row = (
        (
            await session.execute(
                text(
                    "SELECT COALESCE(sum(input_tokens + output_tokens), 0) AS tokens, "
                    "       count(*) AS calls "
                    "FROM flows.ai_usage WHERE tenant_id = :t "
                    "  AND occurred_at >= date_trunc('month', (now() AT TIME ZONE 'utc'))"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    return Summary(
        enabled=bool(state and state.enabled),
        monthly_token_cap=(
            state.monthly_token_cap if state else settings.ai_default_monthly_token_cap
        ),
        tokens_this_month=int(row["tokens"] or 0),
        calls_this_month=int(row["calls"] or 0),
        # Whether the DEPLOYMENT can call a model at all. Surfaced next to the switch because
        # "enabled for this firm" on a deployment with no key is a setting that does nothing, and
        # an operator should be able to see that without reading the deploy config.
        configured=bool(settings.anthropic_api_key.strip()),
    )


async def set_state(
    session: AsyncSession,
    *,
    tenant_id: int,
    enabled: bool,
    monthly_token_cap: int | None,
    notes: str | None,
) -> None:
    """Upsert a firm's entitlement. Service_role only (the admin route's session).

    A ``None`` cap on a NEW row takes the deployment default; on an existing row it leaves the
    ceiling alone, so "switch this firm off" cannot silently reset a ceiling somebody set
    deliberately.
    """
    await session.execute(
        text(
            "INSERT INTO flows.ai_tenants (tenant_id, enabled, monthly_token_cap, notes) "
            "VALUES (:t, :e, COALESCE(:cap, :default_cap), :n) "
            "ON CONFLICT (tenant_id) DO UPDATE SET "
            "  enabled = EXCLUDED.enabled, "
            "  monthly_token_cap = COALESCE(:cap, flows.ai_tenants.monthly_token_cap), "
            "  notes = EXCLUDED.notes"
        ),
        {
            "t": tenant_id,
            "e": enabled,
            "cap": monthly_token_cap,
            "default_cap": settings.ai_default_monthly_token_cap,
            "n": notes,
        },
    )
