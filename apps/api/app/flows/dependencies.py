"""Step ordering — the pure half of ``depends_on`` enforcement.

An authored step may declare ``depends_on: [step_id, …]``. Until every named prerequisite has
*settled* (fulfilled, or skipped/cancelled so it can never settle), the dependent step is **not
instantiated**: no request item is created, no ``crm.kyc_checks`` row is opened, and neither the
client nor staff is handed the step. It sits ``pending`` with the outstanding keys recorded on
``flows.case_steps.data.awaiting`` so the surfaces can say *why* it isn't actionable yet.

The rule resolves per **instance**, not per key: on a per-participant fan-out, a step's
prerequisite is that person's own instance of the dependency, never someone else's. A prerequisite
that was never planned anywhere (conditioned out) counts as satisfied — the engine must never wedge
a journey on a step it did not create.

**Ordering crosses tiers.** A step's prerequisite may live in another case of the same execution: a
participant-tier identity check can wait on the account-tier terms that person has to accept, which
is the order the legacy app ran in (`docs/onboarding-legacy.md` §3). Prerequisites resolve in the
dependent's own case FIRST and fall back to the rest of the execution, so nothing about
same-case ordering changes; what changes is that a prerequisite planned in a *different* case is
now waited for instead of silently counting as satisfied — which released it at kickoff.

Kept pure (no DB) so the ordering rules are exhaustively unit-testable; the orchestrator supplies
the facts and performs the release.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from app.flows.domain_events import StepStatus

#: Statuses that mean "this prerequisite will never be outstanding again" — the dependent step is
#: released. ``skipped``/``cancelled`` count as settled so a withdrawn or conditioned-out step can
#: never deadlock everything behind it.
SETTLED: frozenset[StepStatus] = frozenset(
    {StepStatus.FULFILLED, StepStatus.SKIPPED, StepStatus.CANCELLED}
)

#: Marker written onto ``case_steps.data`` once a step's artifact exists, so a release pass never
#: instantiates the same instance twice.
INSTANTIATED_KEY = "instantiated"

#: Where the outstanding prerequisite keys are recorded, for the UI's "waiting on …" line.
AWAITING_KEY = "awaiting"


def is_settled(status: StepStatus | str) -> bool:
    return (status if isinstance(status, StepStatus) else StepStatus(status)) in SETTLED


#: One step instance as the dependency resolver sees it.
StepFact = tuple[str, int | None, str, Sequence[str]]  # (key, participant, status, depends_on)


def unmet(
    step_key: str,
    participant: int | None,
    depends_on: Iterable[str],
    facts: Iterable[StepFact],
    foreign: Iterable[StepFact] = (),
) -> tuple[str, ...]:
    """The prerequisites of one instance that are still outstanding, in authored order.

    ``facts`` are every step instance in the same case (the dependency scope). Which sibling
    instances count depends on which side is fanned:

    * a **fanned dependent** (``participant`` set) resolves against its OWN instance of the
      prerequisite, else a shared (non-fanned) instance — never someone else's;
    * a **shared dependent** (``participant`` None) resolves against EVERY instance of the
      prerequisite, so "set up the account once both owners have accepted the terms" holds until the
      last owner has. Only considering shared facts released it immediately — the authored rule was
      accepted by the editor and then ignored at runtime.

    ``foreign`` is every instance in the execution's OTHER cases. It is consulted only for a key
    the dependent's own case does not plan, so same-case ordering is decided exactly as before and
    a cross-tier prerequisite is waited for rather than skipped. The per-instance rules above then
    apply unchanged — a fanned dependent still resolves against its own participant's instances,
    which across tiers means "this person's terms, on every account they hold".

    Keys with no instance ANYWHERE are satisfied: the engine must never wedge a journey on a step it
    did not create.
    """

    def by_key(rows: Iterable[StepFact]) -> dict[str, list[tuple[int | None, str]]]:
        out: dict[str, list[tuple[int | None, str]]] = {}
        for key, part, status, _deps in rows:
            if key == step_key:
                # An instance is never its own prerequisite (a self-edge is a rejected cycle).
                continue
            out.setdefault(key, []).append((part, status))
        return out

    instances = by_key(facts)
    elsewhere = by_key(foreign)

    outstanding: list[str] = []
    for dep in depends_on:
        siblings = instances.get(dep) or elsewhere.get(dep)
        if not siblings:
            continue
        if participant is not None:
            mine = [s for part, s in siblings if part == participant]
            relevant = mine or [s for part, s in siblings if part is None]
        else:
            relevant = [s for _part, s in siblings]
        if relevant and not all(is_settled(s) for s in relevant):
            outstanding.append(dep)
    return tuple(outstanding)


def find_cycles(steps: Iterable[Mapping[str, object]]) -> list[str]:
    """Step ids that participate in (or sit behind) a ``depends_on`` cycle — empty ⇒ a DAG.

    Iterative Kahn peel: whatever cannot be topologically removed is cyclic. Used by definition
    validation so a cyclic flow is rejected at save, not discovered at runtime.
    """
    deps: dict[str, set[str]] = {}
    for s in steps:
        sid = s.get("id")
        if not isinstance(sid, str):
            continue
        raw = s.get("depends_on")
        listed = raw if isinstance(raw, list) else []
        deps[sid] = {d for d in listed if isinstance(d, str)}
    # Only intra-graph edges matter (unknown ids are rejected separately by the validator).
    remaining = {sid: {d for d in ds if d in deps} for sid, ds in deps.items()}
    changed = True
    while changed:
        changed = False
        for sid, ds in list(remaining.items()):
            if not (ds & set(remaining)):
                del remaining[sid]
                changed = True
    return sorted(remaining)
