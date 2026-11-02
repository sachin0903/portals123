"""Projection: generic engine state → the legacy onboarding label on ``crm.applications``.

The engine's own state is domain-neutral (``app/flows/states.py``). Onboarding's familiar statuses
(``with_client``/``identity_under_review``/…) are **not** stored on the engine — they are *derived*
here for the UI and written to ``crm.applications.status`` as a projection (docs/flows.md §3). Swap
this function for a different config and the same engine drives a different workflow's labels.

This is a faithful re-expression of the legacy ``computeDerivedApplicationStatus``
(platform-main ``pkgs/db/src/application-state.ts``), but reading the engine's generic state + the
gate context rather than mutating a status column directly. Pure + unit-tested.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.flows.states import State

# The legacy onboarding vocabulary (platform-main APPLICATION_STATUSES). Kept here in the projection
# layer ONLY — never in the engine. crm.applications.status is free-form text; its generated
# `completed` column keys on the literal 'completed', which this mapping preserves.
DRAFT = "draft"
#: Legacy: the ONE label the old app used for every client-facing phase. Superseded by the two
#: phase labels below and no longer projected, but still read by the UI — rows written before this
#: split keep it, and the surfaces map it onto ``FACT_FIND`` (the phase it named at that position).
WITH_CLIENT = "with_client"
#: The two phases ``with_client`` used to conflate. The split is not cosmetic: the label named an
#: ACTOR ("with client") while sitting at a position in a sequence of PHASES, and the seeded
#: onboarding assigns every fact-find to STAFF — so the very first thing an adviser had to do was
#: reported to them as being with the client, and the board filed it that way. A phase says what
#: work is outstanding; who owes it is a separate question the UI answers from the step assignees
#: (``open_client_steps``), so neither has to lie to express the other.
FACT_FIND = "fact_find"
TERMS = "terms"
IDENTITY_UNDER_REVIEW = "identity_under_review"
AML_UNDER_REVIEW = "aml_under_review"
OFFSHORE_UNDER_REVIEW = "offshore_under_review"
PROCESSING = "processing"
COMPLETED = "completed"
CANCELLED = "cancelled"
SYNC_FAILED = "sync_failed"


@dataclass(frozen=True)
class OnboardingGates:
    """The gate facts the onboarding label derivation reads (aggregated over KYC-relevant
    participants). Mirrors the legacy fast-path booleans + declaration, plus ``forms_ok`` (the
    data-gathering fact-find) which the tiered model runs before terms."""

    #: The adviser has attested to their own work and handed the application over — in the legacy
    #: app a boolean column (`applications.adviser_declaration`) and the SOLE trigger for
    #: draft → with_client; here the `declaration` step, an application-scoped `task`.
    #: The adviser has attested to their own work and handed over. In the legacy app a column
    #: (``applications.adviser_declaration``) and the sole trigger for draft → with_client; here
    #: the ``declaration`` step, an application-scoped ``task``.
    adviser_declared: bool = False
    #: Whether anything has been planned at all — "not started" vs "started, not yet declared".
    started: bool = False
    forms_ok: bool = True  # all data-gathering fact-find forms submitted (vacuous ⇒ True)
    terms_ok: bool = False
    identity_ok: bool = False
    aml_ok: bool = False
    has_offshore: bool = False
    offshore_approved: bool = False
    synced: bool | None = None  # None = not attempted; True/False once provider write-back runs


def project_onboarding_status(state: State, gates: OnboardingGates) -> str:
    """Map (engine state + gates) → the legacy ``crm.applications.status`` label.

    Terminal engine states map straight across (``completed``/``cancelled``; ``failed`` →
    ``sync_failed``, the legacy failure sink). Otherwise the label follows the furthest gate the
    application has NOT yet cleared.

    The gate order is ``declaration → fact_find → terms → identity → aml → offshore → processing``
    — the legacy order (``docs/onboarding-legacy.md`` §3), restored once ordering learned to cross
    tiers. It could not be expressed before: identity and AML are PARTICIPANT-scoped and terms are
    ACCOUNT-scoped, and a prerequisite in another case counted as satisfied, so wiring the spine
    this way compiled to no prerequisite at all and released identity at kickoff. ``dependencies``
    now resolves across the execution's cases, so the seed wires the client's terms after the
    adviser's declaration and the checks after those.

    That sequence is now strictly forward. ``with_client`` used to stand at BOTH the fact-find and
    the terms position, which no linear stepper can draw honestly — reaching terms redrew the two
    review stages it had already passed as still to come. ``FACT_FIND``/``TERMS`` give each phase
    its own place, so a stage stepper is simply an index into this list.
    """
    if state == State.COMPLETED:
        return COMPLETED
    if state == State.CANCELLED:
        return CANCELLED
    if state == State.FAILED:
        return SYNC_FAILED

    # Pre-start: nothing has been planned yet. `adviser_declared` cannot answer this on its own —
    # it is false both before a run begins and while the adviser is still working.
    if not gates.started:
        return DRAFT
    # The adviser's own preparation: the data-gathering fact-find AND the declaration that hands
    # the application over. Deliberately NOT "with client" — who fills a form in is the flow
    # author's choice (the seed assigns all five to staff) and the phase is the same either way.
    if not gates.forms_ok or not gates.adviser_declared:
        return FACT_FIND
    # Terms — the client's own act, and in the legacy app the FIRST thing they did.
    if not gates.terms_ok:
        return TERMS
    # The compliance checks then run on what the client has just given us.
    if not gates.identity_ok:
        return IDENTITY_UNDER_REVIEW
    if not gates.aml_ok:
        return AML_UNDER_REVIEW
    if gates.has_offshore and not gates.offshore_approved:
        return OFFSHORE_UNDER_REVIEW
    # All gates cleared → ready to open accounts / awaiting provider write-back.
    return PROCESSING


#: The adviser's own to-dos ON THE APPLICATION — in the seeded flow, exactly the `declaration`.
#: Matched on SHAPE (an application-scoped `task`) rather than on the seed's step id: the projection
#: must not know one flow's vocabulary, and any firm authoring an application-level adviser task is
#: making the same statement — "my part is done".
_DECLARATION_TYPES = frozenset({"task"})

# KYC/terms/fact-find step types the onboarding gates aggregate over.
_IDENTITY_TYPES = frozenset({"verify_identity"})
_AML_TYPES = frozenset({"aml_check"})
_TERMS_TYPES = frozenset({"accept_terms"})
_FORM_TYPES = frozenset({"fill_form"})


def gates_from_steps(
    steps: Iterable[tuple[str, str, str]],
    *,
    declared: bool,
    has_offshore: bool,
    offshore_approved: bool,
    outstanding_empty: bool = True,
) -> OnboardingGates:
    """Aggregate the onboarding gate facts from the execution's fanned steps.

    ``steps`` is ``(step_type, status, scope)`` across every case. A gate is satisfied when *every*
    step of its type is ``fulfilled`` (vacuously true when a flow has no steps of that type — a flow
    with no identity step doesn't gate on identity). ``FAILED`` never satisfies a gate.

    ``forms_ok`` counts only the **participant-scope** fact-find (profile-tier data gather) — not
    account-tier forms like account-activity, which belong to the same adviser phase but are keyed
    per account.

    ``declared`` is ANDed with the application's own adviser tasks. The caller passes the legacy
    execution-data flag, which the engine sets at start and is therefore true for the whole run;
    on its own it can never say "the adviser has not handed this over yet". The `declaration` step
    is what actually carries that now, so a flow that has one is not declared until it is done.

    ``outstanding_empty`` is the catalogue check: every terms-visible participant has accepted the
    current published version of every onboarding term that applies. In-flight journeys that still
    pin only ``content`` / ``document_uid`` keep the step-fulfilment half; a later Graphene or firm
    publish still holds the gate until those versions are accepted.
    """
    rows = list(steps)

    def _all_fulfilled(types: frozenset[str], *, scope: str | None = None) -> bool:
        # A SKIPPED step is one a condition ruled out — the organisation form on an individual,
        # the pension and employer forms on a GIA. It is not outstanding work, so it cannot hold a
        # gate: counting it as unfulfilled pinned every individual GIA journey at `fact_find` for
        # its whole life, and the stage stepper never ticked a stage the work had long passed.
        relevant = [
            status
            for (stype, status, sc) in rows
            if stype in types and (scope is None or sc == scope) and status != "skipped"
        ]
        return all(s == "fulfilled" for s in relevant)

    return OnboardingGates(
        adviser_declared=declared and _all_fulfilled(_DECLARATION_TYPES, scope="application"),
        started=bool(rows),
        forms_ok=_all_fulfilled(_FORM_TYPES, scope="participant"),
        terms_ok=_all_fulfilled(_TERMS_TYPES) and outstanding_empty,
        identity_ok=_all_fulfilled(_IDENTITY_TYPES),
        aml_ok=_all_fulfilled(_AML_TYPES),
        has_offshore=has_offshore,
        offshore_approved=offshore_approved,
    )
