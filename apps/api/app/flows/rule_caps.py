"""The rule-tier caps that need a query to check — server-side, where enforcement lives.

``triggers.CAPS`` is the contract's structural-limit block. Most of it is checked at publish
(``graph.validate_definition``: nodes, branches, conditions, wait days) and ``maxRulesPerTenant``
is checked where automations are created. ``maxTriggersPerFlow`` was checked in **the builder
only** — ``TriggerPanel`` counts the rules pointing at the flow on screen and stops offering a
fifth. That is a courtesy, not enforcement: the API is the boundary, and a cap a client enforces
is a cap that is not enforced.

**Why the cap exists**, so nobody raises it to make a refusal go away: every automation pointing
at one flow is another way that flow can be started for the same client, evaluated in priority
order on every matching event. Four is already more entry points than an author can hold in their
head when asking "why did this journey start?", and the answer to that question is the thing the
rule tier's precedence design exists to keep answerable.

Deliberately shaped as an issue-list contributor rather than a raiser: the automations router
reports EVERY problem with a submitted rule at once (its ``_validate`` collects them so the
condition builder can render them all), and a helper that raised would make this one jump the
queue and hide the rest.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows import triggers

MAX_TRIGGERS_PER_FLOW = int(triggers.CAPS["maxTriggersPerFlow"])


async def flow_trigger_cap_issue(
    session: AsyncSession,
    tenant_id: int,
    *,
    flow_uid: str | None,
    exclude_uid: str | None = None,
) -> str | None:
    """``None`` if this rule may target ``flow_uid``, else the refusal to show the author.

    ``exclude_uid`` is the automation being EDITED. Without it, saving a rule that already targets
    the flow would count itself and refuse at the cap — an author would be unable to rename the
    fourth rule pointing at a journey, which is the classic way an edit path inherits a create
    path's counting bug.

    Counts every stored rule, enabled or not: a disabled automation is a rule the author can turn
    back on with one click, so letting five exist as long as one is off would make the cap depend
    on a toggle rather than on the definition.
    """
    if not flow_uid:
        return None
    count = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM flows.automations a "
                    "JOIN flows.flows f ON f.id = a.flow_id "
                    "WHERE a.tenant_id = :t AND f.uid = :f "
                    "  AND (CAST(:x AS text) IS NULL OR a.uid <> :x)"
                ),
                {"t": tenant_id, "f": flow_uid, "x": exclude_uid},
            )
        ).scalar_one()
    )
    if count < MAX_TRIGGERS_PER_FLOW:
        return None
    return (
        f"This journey already has {count} automations that can start it — the most allowed is "
        f"{MAX_TRIGGERS_PER_FLOW}. Point this rule at a different journey, or retire one of the "
        "existing ones."
    )
