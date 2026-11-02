"""Publish-time structural checks — the reasons a definition is refused before it can strand a
client at runtime (pure, no DB; the DB-backed reference checks live in the router).

The gap these close: a request-backed step at a scope with no person to fulfil it planned happily,
created no artifact, and sat ``pending`` forever with no actor — the account-scope ``accept_terms``
and application-scope ``fill_form`` dead ends.
"""

import pytest

from app.flows.definitions import publish_issues


def _step(sid: str, stype: str, **kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": sid,
        "type": stype,
        "title": sid.title(),
        "assignee": "client",
        "config": {},
    }
    base.update(kw)
    return base


def test_empty_definition_needs_a_step() -> None:
    assert publish_issues({"steps": []}) == ["Add at least one step before publishing."]


def test_missing_required_config_is_reported() -> None:
    issues = publish_issues({"steps": [_step("pack", "generate_document", scope="application")]})
    assert any("template uid" in i for i in issues)


def test_setup_account_needs_no_config() -> None:
    """The platform/product live on the application the adviser assembled; the step's config is only
    an override. Requiring platform_id made the SEEDED flow (whose setup config is `{}`)
    unpublishable, so the editor could never re-publish it."""
    assert publish_issues({"steps": [_step("open", "setup_account", scope="account")]}) == []


def test_account_scope_terms_without_per_owner_is_refused() -> None:
    # The exact seeded-flow dead end: the case subject is an account, so no contact is resolved, no
    # request item is created and the step can never complete.
    issues = publish_issues(
        {
            "steps": [
                _step(
                    "terms",
                    "accept_terms",
                    scope="account",
                    per_participant=False,
                    config={"document_uid": "d1"},
                )
            ]
        }
    )
    assert any("One per owner" in i for i in issues)


def test_account_scope_staff_form_is_fine() -> None:
    # A STAFF-assigned account-level form (the seeded "Account activity") is answerable: the engine
    # binds its request to the account's primary owner and staff complete it.
    issues = publish_issues(
        {
            "steps": [
                _step(
                    "activity",
                    "fill_form",
                    scope="account",
                    assignee="staff",
                    config={"form_uid": "f1"},
                )
            ]
        }
    )
    assert issues == []


def test_account_scope_terms_with_per_owner_is_fine() -> None:
    issues = publish_issues(
        {
            "steps": [
                _step(
                    "terms",
                    "accept_terms",
                    scope="account",
                    per_participant=True,
                    config={"document_uid": "d1"},
                )
            ]
        }
    )
    assert issues == []


def test_written_declaration_is_enough_to_publish_terms() -> None:
    issues = publish_issues(
        {
            "steps": [
                _step(
                    "terms",
                    "accept_terms",
                    scope="account",
                    per_participant=True,
                    config={"content": "I agree to the terms."},
                )
            ]
        }
    )
    assert issues == []


def test_terms_without_copy_or_pdf_is_refused() -> None:
    issues = publish_issues(
        {
            "steps": [
                _step("terms", "accept_terms", scope="account", per_participant=True, config={})
            ]
        }
    )
    assert any("needs a library term, a written declaration, or a PDF" in i for i in issues)


def test_terms_pinned_to_a_library_term_is_fine() -> None:
    # The catalogue is the third and preferred door: a step that names a published term carries no
    # copy of its own, because the version the client accepts is pinned from `terms.terms`.
    issues = publish_issues(
        {
            "steps": [
                _step(
                    "terms",
                    "accept_terms",
                    scope="account",
                    per_participant=True,
                    config={"term_uid": "trm123456789"},
                )
            ]
        }
    )
    assert issues == []


def test_application_scope_form_is_refused() -> None:
    issues = publish_issues(
        {"steps": [_step("facts", "fill_form", scope="application", config={"form_uid": "f1"})]}
    )
    assert any("nobody to complete it" in i for i in issues)


def test_participant_scope_form_is_fine() -> None:
    issues = publish_issues(
        {"steps": [_step("facts", "fill_form", scope="participant", config={"form_uid": "f1"})]}
    )
    assert issues == []


def test_upload_at_application_scope_is_refused() -> None:
    issues = publish_issues({"steps": [_step("proof", "upload_document", scope="application")]})
    assert any("nobody to complete it" in i for i in issues)


def test_staff_only_step_types_are_unaffected_by_the_actor_rule() -> None:
    # A review/task/generate_document needs no per-person artifact — application scope is fine.
    issues = publish_issues(
        {
            "steps": [
                _step("t", "task", scope="application", assignee="staff"),
                _step("r", "review", scope="account", assignee="staff"),
            ]
        }
    )
    assert issues == []


# ---- the flagship: the SEEDED onboarding flow must be publishable -------------------------------
def test_seeded_onboarding_definition_validates_and_is_publishable() -> None:
    """The seed writes its definition straight into a `published` version without validating it. If
    that definition can't pass validation + publish_issues, the one flow every firm starts from can
    never be re-published from the editor — it becomes read-only in practice."""
    from app.flows import onboarding_seed
    from app.flows.definitions import validate_definition

    forms = {code: f"frm_{code}" for code in onboarding_seed.FORMS}
    from app.flows import onboarding_terms

    terms = {code: f"doc_{code}" for code in onboarding_terms.TERMS}
    definition = onboarding_seed._build_definition(forms, terms)
    normalised = validate_definition(definition)  # rejects unknown keys, cycles, bad references
    assert publish_issues(normalised) == []


def test_a_renamed_config_key_is_accepted_and_ignored() -> None:
    """`setup_account`'s allowlist swapped `platform_id` for `provider` when the registry serial
    became the provider code (0138), and nothing rewrote `flows.flow_versions.definition` — so a
    firm that had authored a custodian override could no longer validate, save or republish its
    own flow, with no route back but SQL on a jsonb column.

    A TYPO IS STILL REFUSED. The distinction is what must survive: an unknown key catches a
    mistake that would otherwise be a silent no-op; a key this estate itself renamed is not one.
    """
    from app.flows import definitions

    legacy = {
        "steps": [
            {
                "id": "acct",
                "type": "setup_account",
                "title": "Open the account",
                "config": {"platform_id": 3, "product_id": 1},
            }
        ]
    }
    definitions.validate_definition(legacy)

    typo = {
        "steps": [
            {
                "id": "acct",
                "type": "setup_account",
                "title": "Open the account",
                "config": {"platfrom_id": 3},
            }
        ]
    }
    with pytest.raises(definitions.DefinitionError, match="unknown config keys"):
        definitions.validate_definition(typo)


def test_an_aml_step_provider_is_accepted_and_ignored() -> None:
    """An AML step always screens on ComplyCube, so its ``provider`` setting is gone. A flow saved
    with one (``manual`` was the builder's default) must still validate, save and publish."""
    from app.flows import definitions

    for provider in ("manual", "complycube"):
        legacy = {
            "steps": [
                {
                    "id": "aml",
                    "type": "aml_check",
                    "title": "AML screening",
                    "config": {"provider": provider, "validity_days": 365},
                }
            ]
        }
        normalised = definitions.validate_definition(legacy)
        assert publish_issues(normalised) == []
