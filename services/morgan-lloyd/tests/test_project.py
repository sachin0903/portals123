"""The interlace project compiles: decorators registered, DAG resolves."""

from __future__ import annotations

from pathlib import Path

from interlace.project import Project

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_MODELS = {
    # whole-book list diffs
    "raw.contacts",
    "raw.transfers",
    "raw.beneficiaries",
    "raw.employer_accounts",
    "raw.providers",
    # Scheme-scoped custom actions (models/raw_scoped.py). They land data now because the scope
    # derives itself from cam_schemerefs; ML_SYNC_SCHEME_REFS is only a narrowing override.
    "raw.adviser_contacts",
    "raw.bank_transactions",
    "raw.scheme_detail",
    "raw.contribution_applications",
    "raw.received_contributions",
    # OData entity sets, read whole (models/raw_odata.py)
    "raw.schemes",
    "raw.scheme_refs",
    "raw.contributions",
    "raw.pension_contributions",
    "raw.benefit_applications",
    "raw.policies",
    "raw.fees",
    "raw.link_policy_contact",
    # platform
    "platform.clients",
    "platform.transfers",
    "platform.beneficiaries",
}


def test_project_compiles_with_expected_models() -> None:
    project = Project.load(ROOT)
    compiled = project.compile()
    assert EXPECTED_MODELS <= set(compiled.models)

    # dependency edges resolved from SQL references
    assert "raw.contacts" in compiled.models["platform.clients"].dependencies
    assert "raw.adviser_contacts" in compiled.models["platform.clients"].dependencies
    assert "raw.transfers" in compiled.models["platform.transfers"].dependencies

    # keyed merges wired for the whole-book diffs
    assert compiled.models["raw.contacts"].strategy == "full_merge"
    assert compiled.models["raw.contacts"].key == ("contactid",)
    assert compiled.models["raw.transfers"].key == ("cam_personalpolicyid",)

    # every scheduled model carries a valid cron (build_triggers would reject junk)
    from interlace.scheduler.engine import build_triggers

    triggers = build_triggers(compiled)
    assert len(triggers) >= 10
