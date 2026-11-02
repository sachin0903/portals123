"""Unit tests for the three-tier planner — how a tier-scoped definition fans across an application
graph into cases + steps, with the cross-case KYC gate. Pure logic, no DB.
"""

from datetime import date

from app.flows.planning import (
    AccountSpec,
    ApplicationGraph,
    GateContext,
    Participant,
    is_kyc_relevant,
    onboarding_role,
    plan_execution,
)


def _defn() -> list[dict[str, object]]:
    # A realistic tiered onboarding: gather + KYC per person; terms (joint, per-participant) +
    # set up the account; a doc generated once for the application.
    return [
        {
            "id": "details",
            "type": "fill_form",
            "scope": "participant",
            "config": {"form_uid": "f1"},
        },
        {"id": "idv", "type": "verify_identity", "scope": "participant", "config": {}},
        {
            "id": "aml",
            "type": "aml_check",
            "scope": "participant",
            "depends_on": ["idv"],
            "config": {},
        },
        {
            "id": "terms",
            "type": "accept_terms",
            "scope": "account",
            "per_participant": True,
            "config": {"document_uid": "d1"},
        },
        {
            "id": "open",
            "type": "setup_account",
            "scope": "account",
            "depends_on": ["terms"],
            "config": {"provider": "seccl"},
        },
        {"id": "pack", "type": "generate_document", "scope": "application", "config": {}},
    ]


def test_kyc_relevance() -> None:
    assert is_kyc_relevant(Participant(1, "individual", frozenset({"owner"})))
    assert is_kyc_relevant(Participant(2, "individual", frozenset({"trustee"})))
    assert not is_kyc_relevant(Participant(3, "individual", frozenset({"beneficiary"})))
    assert not is_kyc_relevant(Participant(4, "child", frozenset({"owner"})))
    assert not is_kyc_relevant(Participant(5, "organisation", frozenset({"owner"})))


def test_jisa_child_owner_is_not_kyc_relevant() -> None:
    child = Participant(1, "individual", frozenset({"owner"}))
    adult = Participant(2, "individual", frozenset({"registered_contact"}))
    graph = ApplicationGraph(
        participants=(child, adult),
        accounts=(AccountSpec(9, (1, 2), product="JISA"),),
    )
    assert not is_kyc_relevant(child, graph)
    assert is_kyc_relevant(adult, graph)
    cases = plan_execution(_defn(), graph)
    child_profile = next(c for c in cases if c.kind == "profile" and c.subject_id == 1)
    adult_profile = next(c for c in cases if c.kind == "profile" and c.subject_id == 2)
    assert "idv" not in {s.step_key for s in child_profile.steps}
    assert "idv" in {s.step_key for s in adult_profile.steps}
    assert next(c for c in cases if c.kind == "account").gate_on_contacts == (2,)
    assert onboarding_role(child, graph) == "jisa_child"
    assert onboarding_role(adult, graph) == "jisa_registered_contact"


def test_minor_owner_with_registered_contact_is_not_kyc_relevant_by_age() -> None:
    child = Participant(1, "individual", frozenset({"owner"}), date(2016, 4, 12))
    adult = Participant(2, "individual", frozenset({"registered_contact"}), date(1984, 3, 2))
    graph = ApplicationGraph(
        participants=(child, adult),
        accounts=(
            AccountSpec(
                9,
                (1, 2),
                product="GIA",
                registered_contact_ids=(2,),
            ),
        ),
    )
    assert not is_kyc_relevant(child, graph)
    assert is_kyc_relevant(adult, graph)


def test_a_jisa_party_on_an_adult_account_remains_an_applicant() -> None:
    person = Participant(1, "individual", frozenset({"owner", "registered_contact"}))
    graph = ApplicationGraph(
        participants=(person,),
        accounts=(
            AccountSpec(9, (1,), product="JISA"),
            AccountSpec(10, (1,), product="ISA"),
        ),
    )
    assert onboarding_role(person, graph) == "applicant"


def test_pension_forms_belong_only_to_the_applicant_who_owns_the_pension() -> None:
    """One SIPP in a family journey is not three people's pension fact-find."""
    david = Participant(1, "individual", frozenset({"owner"}), date(2010, 2, 18))
    john = Participant(2, "individual", frozenset({"owner"}), date(1979, 10, 22))
    lisa = Participant(
        3,
        "individual",
        frozenset({"owner", "registered_contact"}),
        date(1981, 8, 2),
    )
    graph = ApplicationGraph(
        participants=(david, john, lisa),
        accounts=(
            AccountSpec(11, (1, 3), product="JISA", registered_contact_ids=(3,)),
            AccountSpec(12, (2, 3), product="GIA"),
            AccountSpec(13, (2,), product="SIPP"),
            AccountSpec(14, (2,), product="ISA"),
            AccountSpec(15, (3,), product="ISA"),
        ),
    )
    definition = [
        {
            "id": "core",
            "type": "fill_form",
            "scope": "participant",
            "visibility": {
                "field": "applicant.onboarding_role",
                "operator": "equals",
                "value": "applicant",
            },
            "config": {},
        },
        {
            "id": "pension",
            "type": "fill_form",
            "scope": "participant",
            "visibility": {
                "field": "applicant.has_pension_product",
                "operator": "equals",
                "value": True,
            },
            "config": {},
        },
        {
            "id": "employer",
            "type": "fill_form",
            "scope": "participant",
            "visibility": {
                "field": "applicant.has_sipp_product",
                "operator": "equals",
                "value": True,
            },
            "config": {},
        },
    ]

    profiles = {
        case.subject_id: {step.step_key for step in case.steps}
        for case in plan_execution(definition, graph)
        if case.kind == "profile"
    }
    assert profiles == {
        2: {"core", "pension", "employer"},
        3: {"core"},
    }
    # This small definition has no JISA-child step, so no empty profile case is invented for David.
    assert 1 not in profiles


def test_joint_account_two_owners() -> None:
    graph = ApplicationGraph(
        participants=(
            Participant(101, "individual", frozenset({"owner"})),
            Participant(102, "individual", frozenset({"owner"})),
        ),
        accounts=(AccountSpec(900, (101, 102)),),
    )
    cases = plan_execution(_defn(), graph)
    profiles = [c for c in cases if c.kind == "profile"]
    accounts = [c for c in cases if c.kind == "account"]
    apps = [c for c in cases if c.kind == "application"]

    # Two profile cases (one per owner), each with details + idv + aml fanned to that person.
    assert {c.subject_id for c in profiles} == {101, 102}
    for p in profiles:
        keys = sorted(s.step_key for s in p.steps)
        assert keys == ["aml", "details", "idv"]
        assert all(s.participant_subject_id == p.subject_id for s in p.steps)
        # depends_on carried through by key (version-stable), not instance id.
        assert next(s for s in p.steps if s.step_key == "aml").depends_on == ("idv",)

    # One account case; terms fans to BOTH owners (per_participant), setup_account is single.
    assert len(accounts) == 1
    acc = accounts[0]
    terms = [s for s in acc.steps if s.step_key == "terms"]
    assert sorted(s.participant_subject_id for s in terms) == [101, 102]
    opens = [s for s in acc.steps if s.step_key == "open"]
    assert len(opens) == 1 and opens[0].participant_subject_id is None

    # Cross-case gate: the account waits on BOTH owners' profile (KYC) cases.
    assert sorted(acc.gate_on_contacts) == [101, 102]

    # One application-level case for the generate_document step.
    assert len(apps) == 1 and [s.step_key for s in apps[0].steps] == ["pack"]


def test_catalogue_terms_are_planned_once_per_client_across_accounts() -> None:
    """Acceptance is contact + term version, even when several accounts make it applicable."""
    definition = [
        {
            "id": "general",
            "type": "accept_terms",
            "scope": "account",
            "per_participant": True,
            "config": {"term_uid": "term_general"},
        },
        {
            "id": "account_copy",
            "type": "accept_terms",
            "scope": "account",
            "per_participant": True,
            "config": {"document_uid": "account_specific_pdf"},
        },
    ]
    graph = ApplicationGraph(
        participants=(
            Participant(101, "individual", frozenset({"owner"})),
            Participant(102, "individual", frozenset({"owner"})),
        ),
        accounts=(
            AccountSpec(900, (101, 102), product="GIA"),
            AccountSpec(901, (101,), product="ISA"),
        ),
    )

    accounts = [case for case in plan_execution(definition, graph) if case.kind == "account"]
    general = [
        (case.subject_id, step.participant_subject_id)
        for case in accounts
        for step in case.steps
        if step.step_key == "general"
    ]
    account_copy = [
        (case.subject_id, step.participant_subject_id)
        for case in accounts
        for step in case.steps
        if step.step_key == "account_copy"
    ]

    assert general == [(900, 101), (900, 102)]
    assert account_copy == [(900, 101), (900, 102), (901, 101)]


def test_child_participant_gathered_but_not_kyc() -> None:
    # A child owner (e.g. a JISA): gathered (details) but no identity/AML, and NOT in the gate.
    graph = ApplicationGraph(
        participants=(
            Participant(201, "individual", frozenset({"owner"})),
            Participant(202, "child", frozenset({"owner"})),
        ),
        accounts=(AccountSpec(950, (201, 202)),),
    )
    cases = plan_execution(_defn(), graph)
    child_profile = next(c for c in cases if c.kind == "profile" and c.subject_id == 202)
    assert sorted(s.step_key for s in child_profile.steps) == ["details"]  # no idv/aml
    acc = next(c for c in cases if c.kind == "account")
    assert acc.gate_on_contacts == (201,)  # only the adult is a KYC gate


def test_gate_context_release() -> None:
    graph = ApplicationGraph(
        participants=(
            Participant(1, "individual", frozenset({"owner"})),
            Participant(2, "individual", frozenset({"owner"})),
        ),
        accounts=(AccountSpec(9, (1, 2)),),
    )
    acc = next(c for c in plan_execution(_defn(), graph) if c.kind == "account")
    assert not GateContext(frozenset({1})).gate_open(acc)  # only one owner done
    assert GateContext(frozenset({1, 2})).gate_open(acc)  # both done -> released


def test_visibility_offshore_branch() -> None:
    # The offshore data-gather + activity + EDD review only plan for an offshore application.
    defn = [
        {"id": "core", "type": "fill_form", "scope": "participant", "config": {"form_uid": "f"}},
        {
            "id": "offshore_kyc",
            "type": "fill_form",
            "scope": "participant",
            "visibility": {
                "field": "application.custody",
                "operator": "equals",
                "value": "offshore",
            },
            "config": {"form_uid": "fo"},
        },
        {
            "id": "activity",
            "type": "fill_form",
            "scope": "account",
            "visibility": {"field": "account.custody", "operator": "equals", "value": "offshore"},
            "config": {"form_uid": "fa"},
        },
        {
            "id": "edd",
            "type": "review",
            "scope": "account",
            "visibility": {"field": "account.custody", "operator": "equals", "value": "offshore"},
            "config": {"gate": "offshore_approved"},
        },
    ]
    onshore = ApplicationGraph(
        participants=(Participant(1, "individual", frozenset({"owner"})),),
        accounts=(AccountSpec(9, (1,), product="GIA", custody="onshore"),),
        custody="onshore",
    )
    prof = next(c for c in plan_execution(defn, onshore) if c.kind == "profile")
    assert sorted(s.step_key for s in prof.steps) == ["core"]  # no offshore step
    acc = next(c for c in plan_execution(defn, onshore) if c.kind == "account")
    assert [s.step_key for s in acc.steps] == []  # no activity / edd onshore

    offshore = ApplicationGraph(
        participants=(Participant(1, "individual", frozenset({"owner"})),),
        accounts=(AccountSpec(9, (1,), product="GIA", custody="offshore"),),
        custody="offshore",
    )
    prof = next(c for c in plan_execution(defn, offshore) if c.kind == "profile")
    assert sorted(s.step_key for s in prof.steps) == ["core", "offshore_kyc"]
    acc = next(c for c in plan_execution(defn, offshore) if c.kind == "account")
    assert sorted(s.step_key for s in acc.steps) == ["activity", "edd"]


def test_visibility_organisation_vs_individual_forms() -> None:
    # Individuals get the person fact-find; organisations the org fact-find; KYC individuals only.
    defn = [
        {
            "id": "person",
            "type": "fill_form",
            "scope": "participant",
            "visibility": {
                "field": "applicant.type",
                "operator": "equals",
                "value": "individual",
            },
            "config": {"form_uid": "fp"},
        },
        {
            "id": "org",
            "type": "fill_form",
            "scope": "participant",
            "visibility": {
                "field": "applicant.type",
                "operator": "equals",
                "value": "organisation",
            },
            "config": {"form_uid": "fo"},
        },
        {"id": "idv", "type": "verify_identity", "scope": "participant", "config": {}},
    ]
    graph = ApplicationGraph(
        participants=(
            Participant(1, "individual", frozenset({"owner"})),
            Participant(2, "organisation", frozenset({"owner"})),
        ),
        accounts=(AccountSpec(9, (1, 2)),),
    )
    cases = {c.subject_id: c for c in plan_execution(defn, graph) if c.kind == "profile"}
    assert sorted(s.step_key for s in cases[1].steps) == [
        "idv",
        "person",
    ]  # individual: KYC + person
    assert sorted(s.step_key for s in cases[2].steps) == ["org"]  # org: org form only, no KYC


def test_has_pension_and_transfer_flags() -> None:
    g = ApplicationGraph(
        participants=(),
        accounts=(
            AccountSpec(1, (), product="SIPP"),
            AccountSpec(2, (), product="ISA", has_transfer=True),
        ),
        custody="onshore",
    )
    assert g.has_pension_product and g.has_sipp_product and g.has_transfer
    g2 = ApplicationGraph(participants=(), accounts=(AccountSpec(1, (), product="GIA"),))
    assert not g2.has_pension_product and not g2.has_sipp_product and not g2.has_transfer


def test_pension_data_gather_only_for_pension_products() -> None:
    defn = [
        {
            "id": "pension",
            "type": "fill_form",
            "scope": "participant",
            "visibility": {
                "field": "application.has_pension_product",
                "operator": "equals",
                "value": True,
            },
            "config": {"form_uid": "fp"},
        },
    ]
    gia = ApplicationGraph(
        participants=(Participant(1, "individual", frozenset({"owner"})),),
        accounts=(AccountSpec(9, (1,), product="GIA"),),
    )
    assert all(not c.steps for c in plan_execution(defn, gia) if c.kind == "profile")
    sipp = ApplicationGraph(
        participants=(Participant(1, "individual", frozenset({"owner"})),),
        accounts=(AccountSpec(9, (1,), product="SIPP"),),
    )
    prof = next(c for c in plan_execution(defn, sipp) if c.kind == "profile")
    assert [s.step_key for s in prof.steps] == ["pension"]


def test_single_subject_no_account() -> None:
    # A "compliance review" shape: one individual, no accounts — proves the engine isn't
    # onboarding-shaped. Only a profile case, no account/gate.
    graph = ApplicationGraph(
        participants=(Participant(1, "individual", frozenset({"owner"})),), accounts=()
    )
    cases = plan_execution(
        [{"id": "idv", "type": "verify_identity", "scope": "participant", "config": {}}], graph
    )
    assert len(cases) == 1 and cases[0].kind == "profile"
