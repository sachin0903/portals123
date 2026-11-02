"""Unit tests for the onboarding status projection (generic engine state → legacy label).

Locks the §3 hybrid-state decision: the engine stays generic, onboarding's legacy vocabulary is
derived here. Faithful to platform-main computeDerivedApplicationStatus.
"""

from app.flows.projection import (
    AML_UNDER_REVIEW,
    CANCELLED,
    COMPLETED,
    DRAFT,
    FACT_FIND,
    IDENTITY_UNDER_REVIEW,
    OFFSHORE_UNDER_REVIEW,
    PROCESSING,
    SYNC_FAILED,
    TERMS,
    OnboardingGates,
    gates_from_steps,
    project_onboarding_status,
)
from app.flows.states import State


def test_terminal_states_map_across() -> None:
    g = OnboardingGates()
    assert project_onboarding_status(State.COMPLETED, g) == COMPLETED
    assert project_onboarding_status(State.CANCELLED, g) == CANCELLED
    assert project_onboarding_status(State.FAILED, g) == SYNC_FAILED


def test_pre_declaration_is_draft() -> None:
    assert project_onboarding_status(State.DRAFT, OnboardingGates(adviser_declared=False)) == DRAFT


def test_declared_but_factfind_outstanding_is_the_fact_find_phase() -> None:
    """NOT `with_client`, which is what this returned and why the board lied.

    The label named an ACTOR while standing at a position in a sequence of PHASES, and the seeded
    onboarding assigns every one of its five fact-find forms to STAFF — so a journey whose only
    outstanding work was the adviser's own was filed on the board under "With client" while its
    card read "Blocked — needs you". Who owes the form is answered from the step assignees; the
    phase is the same whoever fills it in.
    """
    g = OnboardingGates(adviser_declared=True, started=True, forms_ok=False, terms_ok=False)
    assert project_onboarding_status(State.ACTIVE, g) == FACT_FIND


def test_factfind_ok_identity_outstanding_is_identity_review() -> None:
    # Terms first (the legacy order): the checks run on what the client has just given us.
    g = OnboardingGates(
        adviser_declared=True, started=True, forms_ok=True, terms_ok=True, identity_ok=False
    )
    assert project_onboarding_status(State.ACTIVE, g) == IDENTITY_UNDER_REVIEW


def test_factfind_identity_ok_aml_outstanding_is_aml_review() -> None:
    g = OnboardingGates(
        adviser_declared=True,
        started=True,
        forms_ok=True,
        terms_ok=True,
        identity_ok=True,
        aml_ok=False,
    )
    assert project_onboarding_status(State.ACTIVE, g) == AML_UNDER_REVIEW


def test_kyc_ok_terms_outstanding_is_the_terms_phase() -> None:
    """Its own label, not a second sitting of `with_client`.

    Sharing one label with the fact-find gave the stepper two positions for one value, so reaching
    terms redrew Identity and AML — both passed by then — as still to come.
    """
    g = OnboardingGates(
        adviser_declared=True,
        started=True,
        forms_ok=True,
        identity_ok=True,
        aml_ok=True,
        terms_ok=False,
    )
    assert project_onboarding_status(State.ACTIVE, g) == TERMS


def test_the_phase_sequence_is_strictly_forward_and_has_no_repeats() -> None:
    """Every phase appears exactly once across the gate ladder — the property a linear stepper
    needs, and the one `with_client` broke by standing in two places."""
    ladder = [
        OnboardingGates(adviser_declared=False),
        OnboardingGates(adviser_declared=True, started=True, forms_ok=False),
        OnboardingGates(adviser_declared=True, started=True, forms_ok=True, terms_ok=False),
        OnboardingGates(
            adviser_declared=True, started=True, forms_ok=True, terms_ok=True, identity_ok=False
        ),
        OnboardingGates(
            adviser_declared=True,
            started=True,
            forms_ok=True,
            terms_ok=True,
            identity_ok=True,
            aml_ok=False,
        ),
        OnboardingGates(
            adviser_declared=True,
            started=True,
            forms_ok=True,
            identity_ok=True,
            aml_ok=True,
            terms_ok=True,
            has_offshore=True,
            offshore_approved=False,
        ),
        OnboardingGates(
            adviser_declared=True,
            started=True,
            forms_ok=True,
            identity_ok=True,
            aml_ok=True,
            terms_ok=True,
        ),
    ]
    labels = [project_onboarding_status(State.ACTIVE, g) for g in ladder]
    assert labels == [
        DRAFT,
        FACT_FIND,
        TERMS,
        IDENTITY_UNDER_REVIEW,
        AML_UNDER_REVIEW,
        OFFSHORE_UNDER_REVIEW,
        PROCESSING,
    ]
    assert len(set(labels)) == len(labels)


def test_offshore_pending_is_offshore_review() -> None:
    g = OnboardingGates(
        adviser_declared=True,
        started=True,
        forms_ok=True,
        terms_ok=True,
        identity_ok=True,
        aml_ok=True,
        has_offshore=True,
        offshore_approved=False,
    )
    assert project_onboarding_status(State.ACTIVE, g) == OFFSHORE_UNDER_REVIEW


def test_all_gates_cleared_is_processing() -> None:
    g = OnboardingGates(
        adviser_declared=True,
        started=True,
        forms_ok=True,
        terms_ok=True,
        identity_ok=True,
        aml_ok=True,
    )
    assert project_onboarding_status(State.ACTIVE, g) == PROCESSING
    # Onshore with offshore approved also reaches processing.
    g2 = OnboardingGates(
        adviser_declared=True,
        started=True,
        forms_ok=True,
        terms_ok=True,
        identity_ok=True,
        aml_ok=True,
        has_offshore=True,
        offshore_approved=True,
    )
    assert project_onboarding_status(State.ACTIVE, g2) == PROCESSING


def test_gates_from_steps_aggregate_across_participants() -> None:
    # Two people: one's identity done, the other's not → identity gate NOT satisfied.
    steps = [
        ("fill_form", "fulfilled", "participant"),
        ("verify_identity", "fulfilled", "participant"),
        ("verify_identity", "pending", "participant"),
        ("aml_check", "fulfilled", "participant"),
        ("accept_terms", "fulfilled", "account"),
    ]
    g = gates_from_steps(steps, declared=True, has_offshore=False, offshore_approved=False)
    assert g.forms_ok and g.terms_ok and g.aml_ok
    assert not g.identity_ok  # one person's IDV still pending
    assert project_onboarding_status(State.ACTIVE, g) == IDENTITY_UNDER_REVIEW


def test_account_tier_form_does_not_gate_factfind() -> None:
    # A pending account-tier form (account-activity) must NOT hold the label at with_client while
    # the profile-tier fact-find is done and KYC is the real blocker.
    steps = [
        ("fill_form", "fulfilled", "participant"),  # the fact-find is done
        ("fill_form", "pending", "account"),  # account-activity still pending (runs after KYC)
        ("verify_identity", "pending", "participant"),
    ]
    g = gates_from_steps(steps, declared=True, has_offshore=False, offshore_approved=False)
    assert g.forms_ok  # participant fact-find done; the account form doesn't count
    assert project_onboarding_status(State.ACTIVE, g) == IDENTITY_UNDER_REVIEW


def test_gates_vacuously_true_when_no_such_step() -> None:
    # A flow with no AML step doesn't gate on AML.
    g = gates_from_steps(
        [("verify_identity", "fulfilled", "participant"), ("accept_terms", "fulfilled", "account")],
        declared=True,
        has_offshore=False,
        offshore_approved=False,
    )
    assert g.aml_ok and g.identity_ok and g.terms_ok and g.forms_ok
    assert project_onboarding_status(State.ACTIVE, g) == PROCESSING


def test_a_skipped_step_does_not_hold_a_gate() -> None:
    """The seeded onboarding skips the organisation, pension and employer forms on an individual's
    GIA. Those rows stay `skipped` for the journey's whole life, and reading them as unfulfilled
    pinned the projection at `fact_find` however far the work went — the stage stepper on the
    journey page never ticked a stage the client and adviser had both finished."""
    g = gates_from_steps(
        [
            ("fill_form", "fulfilled", "participant"),
            ("fill_form", "skipped", "participant"),
            ("fill_form", "skipped", "participant"),
            ("task", "fulfilled", "application"),
            ("accept_terms", "fulfilled", "account"),
            ("accept_terms", "skipped", "account"),
            ("verify_identity", "pending", "participant"),
        ],
        declared=True,
        has_offshore=False,
        offshore_approved=False,
    )
    assert g.forms_ok and g.adviser_declared and g.terms_ok
    assert project_onboarding_status(State.ACTIVE, g) == IDENTITY_UNDER_REVIEW
    # A gate whose only steps were skipped is vacuous, the same as a flow that never had them.
    only_skipped = gates_from_steps(
        [("aml_check", "skipped", "participant")],
        declared=True,
        has_offshore=False,
        offshore_approved=False,
    )
    assert only_skipped.aml_ok


def test_failed_step_does_not_satisfy_gate() -> None:
    g = gates_from_steps(
        [("aml_check", "failed", "participant")],
        declared=True,
        has_offshore=False,
        offshore_approved=False,
    )
    assert not g.aml_ok


def test_the_declaration_holds_the_application_in_the_advisers_phase() -> None:
    """The handoff the engine had no equivalent of. Every fact-find form is in, but the adviser
    has not yet attested and sent it — so this is still theirs, not "with the client"."""
    held = gates_from_steps(
        [
            ("fill_form", "fulfilled", "participant"),
            ("task", "pending", "application"),
            ("accept_terms", "pending", "account"),
        ],
        declared=True,
        has_offshore=False,
        offshore_approved=False,
    )
    assert held.adviser_declared is False
    assert project_onboarding_status(State.ACTIVE, held) == FACT_FIND

    sent = gates_from_steps(
        [
            ("fill_form", "fulfilled", "participant"),
            ("task", "fulfilled", "application"),
            ("accept_terms", "pending", "account"),
        ],
        declared=True,
        has_offshore=False,
        offshore_approved=False,
    )
    assert sent.adviser_declared is True
    assert project_onboarding_status(State.ACTIVE, sent) == TERMS


def test_a_flow_with_no_declaration_step_is_declared_vacuously() -> None:
    """A firm whose flow has no adviser task is not held for ever on a step it never authored."""
    g = gates_from_steps(
        [("fill_form", "fulfilled", "participant")],
        declared=True,
        has_offshore=False,
        offshore_approved=False,
    )
    assert g.adviser_declared is True


def test_nothing_planned_is_draft_however_the_flag_reads() -> None:
    g = gates_from_steps([], declared=True, has_offshore=False, offshore_approved=False)
    assert g.started is False
    assert project_onboarding_status(State.ACTIVE, g) == DRAFT
