"""Unit tests for ``depends_on`` enforcement — the step-ordering rules (pure, no DB).

Before this, ``depends_on`` was validated, planned and persisted and then read by nothing: every
step instantiated at once, so the seeded flow's ``review → idv → aml`` chain handed the client an
identity step (and opened a pending ``crm.kyc_checks`` row) before the fact-find was touched. These
lock the rules the orchestrator now enforces.
"""

import pytest

from app.flows import dependencies
from app.flows.definitions import DefinitionError, validate_definition
from app.flows.domain_events import StepStatus


def _fact(
    key: str,
    status: str = StepStatus.PENDING.value,
    participant: int | None = None,
    deps: tuple[str, ...] = (),
) -> dependencies.StepFact:
    return (key, participant, status, deps)


# ---- unmet() ----
def test_pending_prerequisite_holds_the_step() -> None:
    facts = [_fact("core"), _fact("review", deps=("core",))]
    assert dependencies.unmet("review", None, ("core",), facts) == ("core",)


def test_fulfilled_prerequisite_releases_the_step() -> None:
    facts = [_fact("core", StepStatus.FULFILLED.value), _fact("review", deps=("core",))]
    assert dependencies.unmet("review", None, ("core",), facts) == ()


@pytest.mark.parametrize("status", ["skipped", "cancelled"])
def test_skipped_or_cancelled_prerequisite_never_deadlocks(status: str) -> None:
    # A step conditioned out of the plan (or withdrawn on cancel) must not wedge everything behind
    # it — otherwise an offshore-only prerequisite would freeze every onshore journey.
    facts = [_fact("core", status), _fact("review", deps=("core",))]
    assert dependencies.unmet("review", None, ("core",), facts) == ()


def test_unplanned_prerequisite_is_satisfied() -> None:
    # 'core' was never planned for this case (a visibility condition excluded it): the engine must
    # not hold 'review' on a step that will never exist.
    facts = [_fact("review", deps=("core",))]
    assert dependencies.unmet("review", None, ("core",), facts) == ()


def test_outstanding_keys_keep_authored_order() -> None:
    facts = [_fact("a"), _fact("b"), _fact("c", StepStatus.FULFILLED.value), _fact("z")]
    assert dependencies.unmet("z", None, ("c", "b", "a"), facts) == ("b", "a")


def test_dependency_resolves_per_participant_not_per_key() -> None:
    # Ann's fact-find is done, Bob's is not: Ann's identity step releases, Bob's stays held. Keying
    # on the step KEY alone would release both the moment either owner finished.
    facts = [
        _fact("core", StepStatus.FULFILLED.value, participant=1),
        _fact("core", StepStatus.PENDING.value, participant=2),
        _fact("idv", participant=1, deps=("core",)),
        _fact("idv", participant=2, deps=("core",)),
    ]
    assert dependencies.unmet("idv", 1, ("core",), facts) == ()
    assert dependencies.unmet("idv", 2, ("core",), facts) == ("core",)


def test_shared_step_can_gate_a_fanned_step() -> None:
    # A non-fanned prerequisite in the same case gates every participant's instance.
    facts = [
        _fact("activity", StepStatus.PENDING.value, participant=None),
        _fact("terms", participant=7, deps=("activity",)),
    ]
    assert dependencies.unmet("terms", 7, ("activity",), facts) == ("activity",)


def test_a_shared_step_waits_for_every_participant_instance_of_its_prerequisite() -> None:
    # 'set up the account once EVERY owner has accepted the terms'. Only looking at shared facts
    # skipped every fanned sibling, so the key never entered the map and the step released
    # immediately — the authored rule was accepted by the editor and ignored at runtime.
    facts = [
        _fact("terms", StepStatus.PENDING.value, participant=1),
        _fact("terms", StepStatus.PENDING.value, participant=2),
        _fact("setup", participant=None, deps=("terms",)),
    ]
    assert dependencies.unmet("setup", None, ("terms",), facts) == ("terms",)


def test_a_shared_step_still_waits_when_only_one_participant_has_settled() -> None:
    facts = [
        _fact("terms", StepStatus.FULFILLED.value, participant=1),
        _fact("terms", StepStatus.PENDING.value, participant=2),
        _fact("setup", participant=None, deps=("terms",)),
    ]
    assert dependencies.unmet("setup", None, ("terms",), facts) == ("terms",)


def test_a_shared_step_releases_once_every_participant_instance_has_settled() -> None:
    facts = [
        _fact("terms", StepStatus.FULFILLED.value, participant=1),
        _fact("terms", StepStatus.SKIPPED.value, participant=2),
        _fact("setup", participant=None, deps=("terms",)),
    ]
    assert dependencies.unmet("setup", None, ("terms",), facts) == ()


def test_a_fanned_step_ignores_another_participants_prerequisite() -> None:
    # The per-instance rule must survive the shared-dependent fix: Ann's step releases on Ann's
    # fact-find even though Bob's is still outstanding.
    facts = [
        _fact("core", StepStatus.FULFILLED.value, participant=1),
        _fact("core", StepStatus.PENDING.value, participant=2),
        _fact("idv", participant=1, deps=("core",)),
    ]
    assert dependencies.unmet("idv", 1, ("core",), facts) == ()


def test_a_fanned_instance_prefers_its_own_prerequisite_over_a_shared_one() -> None:
    facts = [
        _fact("core", StepStatus.PENDING.value, participant=7),
        _fact("core", StepStatus.FULFILLED.value, participant=None),
        _fact("idv", participant=7, deps=("core",)),
    ]
    assert dependencies.unmet("idv", 7, ("core",), facts) == ("core",)


def test_a_step_never_depends_on_itself_instance() -> None:
    # The instance under test is excluded from its own facts, so its own pending status can't be
    # mistaken for an unmet prerequisite of the same key.
    facts = [_fact("solo", deps=("solo",))]
    assert dependencies.unmet("solo", None, (), facts) == ()


# ---- find_cycles() / validation ----
def test_acyclic_graph_has_no_cycles() -> None:
    steps = [
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["a", "b"]},
    ]
    assert dependencies.find_cycles(steps) == []


def test_two_step_cycle_is_reported() -> None:
    steps = [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}]
    assert dependencies.find_cycles(steps) == ["a", "b"]


def test_self_dependency_is_a_cycle() -> None:
    assert dependencies.find_cycles([{"id": "a", "depends_on": ["a"]}]) == ["a"]


def test_unknown_dependency_is_not_a_cycle() -> None:
    # Unknown ids are a separate (already existing) validation error, not a loop.
    assert dependencies.find_cycles([{"id": "a", "depends_on": ["nope"]}]) == []


def _step(sid: str, deps: list[str]) -> dict[str, object]:
    return {
        "id": sid,
        "type": "task",
        "title": sid.title(),
        "assignee": "staff",
        "scope": "application",
        "depends_on": deps,
        "config": {},
    }


def test_validate_definition_rejects_a_dependency_cycle() -> None:
    with pytest.raises(DefinitionError, match="loop"):
        validate_definition({"steps": [_step("a", ["b"]), _step("b", ["a"])]})


def test_validate_definition_accepts_a_chain() -> None:
    out = validate_definition({"steps": [_step("a", []), _step("b", ["a"])]})
    assert [s["depends_on"] for s in out["steps"]] == [[], ["a"]]


class TestCrossTierOrdering:
    """Ordering across cases — what unblocks the legacy terms-before-checks order.

    A prerequisite in another case used to count as satisfied ("or at another tier"), so wiring a
    participant-tier step after an account-tier one compiled to no prerequisite at all and released
    it at kickoff. `foreign` is consulted only for keys the dependent's own case does not plan.
    """

    def test_a_prerequisite_in_another_case_is_waited_for(self) -> None:
        foreign = [("terms", 7, "pending", ())]
        assert dependencies.unmet("idv", 7, ["terms"], [], foreign) == ("terms",)

    def test_it_resolves_to_the_dependents_OWN_participant_across_tiers(self) -> None:
        # Two owners each hold their own terms; A's identity check waits on A's, not on B's.
        foreign = [("terms", 1, "fulfilled", ()), ("terms", 2, "pending", ())]
        assert dependencies.unmet("idv", 1, ["terms"], [], foreign) == ()
        assert dependencies.unmet("idv", 2, ["terms"], [], foreign) == ("terms",)

    def test_one_participant_waits_on_their_terms_across_every_account(self) -> None:
        # The same person on two accounts: settled on one, outstanding on the other ⇒ still held.
        foreign = [("terms", 5, "fulfilled", ()), ("terms", 5, "pending", ())]
        assert dependencies.unmet("idv", 5, ["terms"], [], foreign) == ("terms",)

    def test_the_local_case_still_decides_when_it_plans_the_key(self) -> None:
        # Same-case ordering is untouched: a local instance wins over anything foreign.
        local = [("terms", 3, "fulfilled", ())]
        foreign = [("terms", 3, "pending", ())]
        assert dependencies.unmet("idv", 3, ["terms"], local, foreign) == ()

    def test_a_key_planned_nowhere_is_still_satisfied(self) -> None:
        # The rule that stops a conditioned-out step wedging everything behind it.
        assert (
            dependencies.unmet("idv", 1, ["never_planned"], [], [("terms", 1, "pending", ())]) == ()
        )

    def test_omitting_foreign_behaves_exactly_as_before(self) -> None:
        assert dependencies.unmet("idv", 1, ["terms"], []) == ()
