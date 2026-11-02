"""The seeded advice journey (`flows/advice_seed.py`): the shape it ships, and that it seeds once.

The runtime it runs on is covered by `test_advice_journey_runtime.py`. These tests pin the
CONFIGURATION: a person-subject intake with no account, the step graph and who each step is for,
gather mode on the two client forms, the staff-only adviser assessment, the remembered risk band,
the scored questionnaire, and that re-running the seed republishes nothing and duplicates nothing.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows import advice_seed, onboarding_seed
from app.flows.definitions import publish_issues, validate_definition
from app.flows.document_context import storage_keys
from app.flows.form_validation import missing_required, repeating_columns
from app.flows.memory import promotions
from app.flows.scoring import apply_scoring, parse_scoring
from tests.test_applications_api import _scalar

FORM_UIDS = {code: f"frm_{code}" for code in advice_seed.FORMS}


def _fact_find() -> dict[str, Any]:
    return validate_definition(advice_seed._build_fact_find_definition(FORM_UIDS))


def _steps(definition: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["id"]: step for step in definition["steps"]}


def _fields(code: str) -> dict[str, tuple[Any, ...]]:
    return {row[0]: row for row in advice_seed.FORMS[code][2]}


def test_a_new_tenant_does_not_get_the_fact_find() -> None:
    """Written for one firm and kept here as the reference for it. Provisioning applies onboarding
    and nothing else; `scripts/seed_advice_journey.py` is what writes this, for the tenant named."""
    source = Path(__file__).parents[1].joinpath("app/provisioning.py").read_text()
    assert "await seed_advice_journey" not in source
    assert "from app.flows.advice_seed import" not in source


# --- the definition --------------------------------------------------------------------------


def test_the_journey_starts_from_a_person_and_opens_no_account() -> None:
    definition = _fact_find()
    intake = definition["intake"]
    assert intake["subject"] == "person"
    assert intake["collect_platform"] is False
    assert intake["collect_custody"] is False
    assert intake["collect_product"] is False
    assert intake["allow_new"] is False
    assert intake["review"] is False
    assert not any(step["type"] == "setup_account" for step in _steps(definition).values())


def test_review_defaults_off_for_a_person_and_on_when_the_journey_opens_accounts() -> None:
    person = advice_seed._build_fact_find_definition(FORM_UIDS)
    del person["intake"]["review"]
    assert validate_definition(person)["intake"]["review"] is False

    accounts = advice_seed._build_fact_find_definition(FORM_UIDS)
    accounts["intake"] = {"subject": "accounts"}
    assert validate_definition(accounts)["intake"]["review"] is True

    asked = advice_seed._build_fact_find_definition(FORM_UIDS)
    asked["intake"]["review"] = True
    assert validate_definition(asked)["intake"]["review"] is True


def test_the_journey_is_the_fact_find_and_not_a_letter() -> None:
    steps = _steps(_fact_find())
    assert list(steps) == ["fact_find", "risk", "adviser", "acknowledge"]
    assert steps["fact_find"]["depends_on"] == []
    assert steps["risk"]["depends_on"] == []
    assert steps["adviser"]["depends_on"] == ["fact_find", "risk"]
    assert steps["acknowledge"]["depends_on"] == ["fact_find", "risk"]
    assert "letter" not in steps
    assert not any(step["type"] == "generate_document" for step in steps.values())
    assert steps["acknowledge"]["type"] == "accept_terms"
    assert steps["acknowledge"]["assignee"] == "client"
    assert steps["acknowledge"]["config"]["content"].strip()
    assert publish_issues(_fact_find()) == []


def test_the_client_forms_are_gather_steps_and_the_assessment_is_the_advisers() -> None:
    steps = _steps(_fact_find())
    for key in ("fact_find", "risk"):
        assert steps[key]["type"] == "fill_form"
        assert steps[key]["scope"] == "participant"
        assert steps[key]["assignee"] == "client"
        assert steps[key]["config"]["mode"] == "gather"
    assert steps["adviser"]["type"] == "fill_form"
    assert steps["adviser"]["scope"] == "participant"
    assert steps["adviser"]["assignee"] == "staff"


def test_the_step_keys_are_the_ones_a_letter_context_reads() -> None:
    """A suitability letter a firm authors later reads `fact_find` and `risk` from this journey
    (`document_context.letter_inputs`). Those step ids have to be the names it files them under."""
    for key in ("fact_find", "risk"):
        assert key in storage_keys(key)


def test_the_risk_step_remembers_the_computed_band_and_nothing_else() -> None:
    config = _steps(_fact_find())["risk"]["config"]
    assert config["remember"] == {"risk_band": "band"}
    assert config["form_uid"] == FORM_UIDS["advice_risk_profile"]
    remembered = promotions(
        config["remember"],
        {"risk_explore": "agree", "band": "posted"},
        {"band": "moderate", "score": 36},
    )
    assert remembered == {"risk_band": "moderate"}


def test_the_milestones_are_not_the_onboarding_stages() -> None:
    fact_find = _fact_find()["milestones"]
    assert [m["status"] for m in fact_find] == ["fact_find", "recorded", "declared"]
    assert [m["label"] for m in fact_find] == [
        "Your details",
        "With your adviser",
        "Your declaration",
    ]
    assert fact_find[0]["reached_when"] == ["fact_find", "risk"]
    assert fact_find[-1]["reached_when"] == ["acknowledge"]
    names = {m["status"] for m in fact_find}
    assert not {"details", "sign", "verification", "setup"} & names


# --- the forms ---------------------------------------------------------------------------------


def test_the_fact_find_carries_the_schedules_as_repeating_groups() -> None:
    fields = _fields("advice_fact_find")
    groups = {
        "dependants",
        "mortgages",
        "bank_accounts",
        "investments",
        "liabilities",
        "protection_policies",
        "pensions",
    }
    for key in groups:
        assert fields[key][1] == "repeating", key
        columns = repeating_columns(fields[key][7])
        assert columns, key
        assert all(col["type"] != "repeating" for col in columns)
    # The plans table a suitability letter would repeat: the columns its rows are read by.
    pension_columns = {col["key"]: col for col in repeating_columns(fields["pensions"][7])}
    assert {"provider", "scheme_type", "policy_number", "fund_value", "transfer_value"} <= set(
        pension_columns
    )
    assert pension_columns["fund_value"]["type"] == "currency"
    assert pension_columns["fund_value"]["required"] is True
    assert pension_columns["transfer_value"]["required"] is False
    # Optional schedules: a client with no mortgage has nothing to add.
    assert fields["mortgages"][4] is False
    # A started row still needs its required cells (`form_validation.repeating_gap`).
    seeded = [
        {
            "field_key": key,
            "field_type": row[1],
            "label": row[2],
            "required": row[4],
            "conditions": row[5],
            "validation": row[6],
            "options": row[7],
        }
        for key, row in fields.items()
    ]
    gaps = missing_required(
        seeded,
        {
            "pensions": [{"provider": "Nest", "scheme_type": "Money purchase"}],
        },
    )
    assert "Pensions (Current fund value)" in gaps


def test_the_fact_find_maps_identity_to_the_client_record_and_nothing_from_the_schedules() -> None:
    mapping = {row["source"]: row["target"] for row in advice_seed.FACT_FIND_MAPPING}
    assert mapping["first_name"] == "first_name"
    assert mapping["surname"] == "surname"
    assert mapping["dob"] == "dob"
    assert mapping["national_identifier"] == "national_identifier"
    assert mapping["marital_status"] == "marital_status"
    assert mapping["address.postcode"] == "address.postcode"
    assert mapping["intended_retirement_age"] == "retirement_age"
    fields = _fields("advice_fact_find")
    for source in mapping:
        assert source.split(".")[0] in fields, source
    schedules = {"dependants", "mortgages", "bank_accounts", "investments", "pensions"}
    assert not {source.split(".")[0] for source in mapping} & schedules
    # The same targets the onboarding fact-find already writes, so one record, one spelling.
    onboarding_targets = {
        row["target"] for row in (*onboarding_seed.CORE_MAPPING, *onboarding_seed.PENSION_MAPPING)
    }
    assert set(mapping.values()) <= onboarding_targets


def test_the_questionnaire_scores_twelve_likert_items_into_five_bands() -> None:
    scoring = parse_scoring(advice_seed.RISK_SCORING)
    assert scoring is not None
    assert len(scoring["fields"]) == 12
    assert [band["label"] for band in scoring["bands"]] == [
        "Cautious",
        "Cautious to Moderate",
        "Moderate",
        "Moderate to Adventurous",
        "Adventurous",
    ]
    for band in scoring["bands"]:
        assert band["definition"].startswith("You are prepared to take")
    # Even ordinal weights: every scored item offers exactly the points 1–5.
    for field in scoring["fields"]:
        assert sorted(field["weights"].values()) == [1, 2, 3, 4, 5], field["field_key"]
    # Contiguous bands over the whole 12–60 range.
    edges = [(band["min"], band["max"]) for band in scoring["bands"]]
    assert edges[0][0] == 12 and edges[-1][1] == 60
    for (_lo, hi), (lo, _hi) in zip(edges, edges[1:], strict=False):
        assert lo == hi + 1


def test_a_risk_averse_statement_is_scored_in_reverse() -> None:
    weights = {f["field_key"]: f["weights"] for f in advice_seed.RISK_SCORING["fields"]}
    assert weights["risk_explore"]["strongly_agree"] == 5
    assert weights["risk_safe_money"]["strongly_agree"] == 1
    assert weights["risk_safe_money"]["strongly_disagree"] == 5
    assert weights["risk_feel"]["excited"] == 5
    assert weights["risk_feel"]["panicked"] == 1


def test_every_scored_item_is_a_question_on_the_form_with_those_options() -> None:
    fields = _fields("advice_risk_profile")
    for field in advice_seed.RISK_SCORING["fields"]:
        row = fields[field["field_key"]]
        assert row[1] == "select"
        offered = {option["value"] for option in row[7]["options"]}
        assert offered == set(field["weights"]), field["field_key"]
    # The three-way product choice is asked and not scored.
    assert "risk_product" in fields
    assert "risk_product" not in {f["field_key"] for f in advice_seed.RISK_SCORING["fields"]}


def test_capacity_for_loss_is_asked_and_not_scored() -> None:
    fields = _fields("advice_risk_profile")
    scored = {f["field_key"] for f in advice_seed.RISK_SCORING["fields"]}
    capacity = [key for key, row in fields.items() if row[3] == "Capacity for loss"]
    assert "emergency_cover" in capacity
    assert "emergency_cover_notes" in capacity
    assert not set(capacity) & scored
    notes = fields["emergency_cover_notes"]
    assert notes[5] == {"field": "emergency_cover", "operator": "equals", "value": "Yes"}
    # Answering every item at the most cautious end lands in Cautious; the capacity answers add
    # nothing to the total whatever they say.
    cautious = {
        f["field_key"]: min(f["weights"], key=f["weights"].get)
        for f in advice_seed.RISK_SCORING["fields"]
    }
    _answers, outputs = apply_scoring(
        {**cautious, "emergency_cover": "Yes", "emergency_cover_notes": "Savings"},
        advice_seed.RISK_SCORING,
    )
    assert outputs == {
        "band": "cautious",
        "score": 12,
        "label": "Cautious",
        "definition": advice_seed.RISK_BANDS[0]["definition"],
    }
    adventurous = {
        f["field_key"]: max(f["weights"], key=f["weights"].get)
        for f in advice_seed.RISK_SCORING["fields"]
    }
    assert apply_scoring(adventurous, advice_seed.RISK_SCORING)[1]["band"] == "adventurous"


def test_the_adviser_assessment_holds_vulnerability_documents_and_the_meeting() -> None:
    fields = _fields("advice_adviser_assessment")
    assert fields["vulnerable"][7] == advice_seed._opts(["Yes", "No", "Potential"])
    assert fields["vulnerability_categories"][7]["options"] == [
        {"value": v, "label": v} for v in ("Health", "Life event", "Resilience", "Capability")
    ]
    assert fields["documents_sighted"][1] == "multiSelect"
    assert fields["meeting_notes"][1] == "textarea"
    assert fields["meeting_notes"][4] is True
    # The recommendation is not part of the fact find; a letter journey asks it itself.
    assert "cost_comparison" not in fields
    assert "recommendation" not in fields


def test_seeded_field_keys_are_unique_per_form() -> None:
    for code, (_name, _entity, fields, _scoring) in advice_seed.FORMS.items():
        keys = [row[0] for row in fields]
        assert len(keys) == len(set(keys)), code


# --- seeding a tenant --------------------------------------------------------------------------


async def _tenant(sess: AsyncSession, *, with_user: bool) -> int:
    suffix = uuid.uuid4().hex[:8]
    tenant = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"as_{suffix}",
            a=f"as-{suffix}",
            n=f"Advice seed {suffix}",
        )
    )
    if with_user:
        await _scalar(
            sess,
            "INSERT INTO auth.users (uid, tenant_id, email) VALUES (:u, :t, :e) RETURNING id",
            u=f"usr_{suffix}",
            t=tenant,
            e=f"adviser_{suffix}@t.test",
        )
    return tenant


async def _counts(sess: AsyncSession, tenant: int) -> dict[str, int]:
    return {
        "forms": int(
            await _scalar(
                sess,
                "SELECT count(*) FROM documents.forms WHERE tenant_id = :t AND code = ANY(:c)",
                t=tenant,
                c=list(advice_seed.FORMS),
            )
        ),
        "fields": int(
            await _scalar(
                sess,
                "SELECT count(*) FROM documents.form_fields ff "
                "JOIN documents.forms f ON f.id = ff.form_id "
                "WHERE f.tenant_id = :t AND f.code = ANY(:c)",
                t=tenant,
                c=list(advice_seed.FORMS),
            )
        ),
        "flows": int(
            await _scalar(
                sess,
                "SELECT count(*) FROM flows.flows WHERE tenant_id = :t AND code = :c",
                t=tenant,
                c=advice_seed.FACT_FIND_FLOW,
            )
        ),
        "flow_versions": int(
            await _scalar(
                sess,
                "SELECT count(*) FROM flows.flow_versions v JOIN flows.flows f ON f.id = v.flow_id "
                "WHERE f.tenant_id = :t AND f.code = :c",
                t=tenant,
                c=advice_seed.FACT_FIND_FLOW,
            )
        ),
        "templates": int(
            await _scalar(
                sess,
                "SELECT count(*) FROM documents.templates WHERE tenant_id = :t",
                t=tenant,
            )
        ),
    }


@pytest.mark.asyncio
async def test_the_seed_publishes_the_forms_and_the_flow_and_reruns_without_duplicating(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    tenant = await _tenant(sess, with_user=True)
    first = await advice_seed.seed_advice_journey(sess, tenant_id=tenant)
    assert first["created"] is True
    assert first["updated"] is False
    assert first["retired"] is False
    assert set(first["forms"]) == set(advice_seed.FORMS)  # type: ignore[arg-type]
    assert first["flow_uid"] == first["fact_find_uid"]

    counts = await _counts(sess, tenant)
    assert counts["forms"] == 3
    assert counts["fields"] == sum(len(spec[2]) for spec in advice_seed.FORMS.values())
    assert counts["flows"] == 1
    assert counts["flow_versions"] == 1
    # No template and no letter journey: the recommendation is the firm's to author.
    assert counts["templates"] == 0
    assert (
        await _scalar(
            sess,
            "SELECT count(*) FROM flows.flows WHERE tenant_id = :t AND code = :c",
            t=tenant,
            c=advice_seed.RETIRED_PENSION_FLOW,
        )
        == 0
    )

    # Every form is published; the questionnaire is scored and the others are not.
    rows = (
        (
            await sess.execute(
                text(
                    "SELECT code, status, scoring FROM documents.forms "
                    "WHERE tenant_id = :t AND code = ANY(:c) ORDER BY code"
                ),
                {"t": tenant, "c": list(advice_seed.FORMS)},
            )
        )
        .mappings()
        .all()
    )
    assert all(row["status"] == "published" for row in rows)
    scoring = {row["code"]: row["scoring"] for row in rows}
    assert scoring["advice_fact_find"] is None
    assert scoring["advice_adviser_assessment"] is None
    assert scoring["advice_risk_profile"] == advice_seed.RISK_SCORING

    fact_find = await _scalar(
        sess,
        "SELECT v.definition FROM flows.flows f "
        "JOIN flows.flow_versions v ON v.id = f.current_published_version_id "
        "WHERE f.tenant_id = :t AND f.code = :c",
        t=tenant,
        c=advice_seed.FACT_FIND_FLOW,
    )
    if isinstance(fact_find, str):
        fact_find = json.loads(fact_find)
    assert fact_find["intake"]["subject"] == "person"
    fact_steps = {step["id"]: step for step in fact_find["steps"]}
    assert fact_steps["fact_find"]["config"]["form_uid"] == first["forms"]["advice_fact_find"]  # type: ignore[index]
    assert fact_steps["risk"]["config"]["remember"] == {"risk_band": "band"}
    assert (
        await _scalar(
            sess,
            "SELECT status FROM flows.flows WHERE tenant_id = :t AND code = :c",
            t=tenant,
            c=advice_seed.FACT_FIND_FLOW,
        )
        == "published"
    )

    # Re-running changes nothing: same uids, no new versions, the fields rewritten in place.
    again = await advice_seed.seed_advice_journey(sess, tenant_id=tenant)
    assert again["created"] is False
    assert again["updated"] is False
    assert again["fact_find_uid"] == first["fact_find_uid"]
    assert again["forms"] == first["forms"]
    assert await _counts(sess, tenant) == counts


@pytest.mark.asyncio
async def test_the_seeded_flow_plans_a_two_person_journey(service_session: AsyncSession) -> None:
    """The seed is a definition the engine can start: a two-person journey asks each person the
    two client forms, and holds the adviser's step and the declaration until they are in."""
    from app.db import FirmContext
    from app.permissions import CASES_WRITE
    from app.routers import applications as api
    from tests.test_applications_api import _principal

    sess = service_session
    tenant = await _tenant(sess, with_user=True)
    await advice_seed.seed_advice_journey(sess, tenant_id=tenant)
    suffix = uuid.uuid4().hex[:8]
    people = [
        int(
            await _scalar(
                sess,
                "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, email) "
                "VALUES (:t, :u, 'individual', :f, 'Seed', :e) RETURNING id",
                t=tenant,
                u=f"ct_{name}_{suffix}",
                f=name,
                e=f"{name}.{suffix}@t.test",
            )
        )
        for name in ("Ada", "Grace")
    ]
    ctx = FirmContext(session=sess, tenant_id=tenant, principal=_principal(CASES_WRITE))
    created = await api.create_application(
        api.NewApplication(flow_code=advice_seed.FACT_FIND_FLOW, accounts=[], clients=people),
        principal=_principal(CASES_WRITE),
        ctx=ctx,
    )
    rows = (
        (
            await sess.execute(
                text(
                    "SELECT c.kind, c.subject_id, cs.step_key, cs.status "
                    "FROM flows.case_steps cs JOIN flows.cases c ON c.id = cs.case_id "
                    "JOIN flows.executions e ON e.id = c.execution_id "
                    "WHERE e.uid = :u ORDER BY c.kind, c.subject_id, cs.id"
                ),
                {"u": created["execution_uid"]},
            )
        )
        .mappings()
        .all()
    )
    by_case: dict[tuple[str, Any], dict[str, str]] = {}
    for row in rows:
        by_case.setdefault((row["kind"], row["subject_id"]), {})[row["step_key"]] = row["status"]
    assert sorted(kind for kind, _ in by_case) == ["profile", "profile"]
    for person in people:
        steps = by_case[("profile", person)]
        assert set(steps) == {"fact_find", "risk", "adviser", "acknowledge"}
        assert steps["fact_find"] == "pending"
        assert steps["risk"] == "pending"
    # The two client forms are released at kickoff; the adviser's and the acknowledgement wait.
    released = {
        row["step_key"]
        for row in (
            await sess.execute(
                text(
                    "SELECT cs.step_key FROM flows.case_steps cs "
                    "JOIN flows.cases c ON c.id = cs.case_id "
                    "JOIN flows.executions e ON e.id = c.execution_id "
                    "WHERE e.uid = :u AND cs.request_item_id IS NOT NULL"
                ),
                {"u": created["execution_uid"]},
            )
        ).mappings()
    }
    assert released == {"fact_find", "risk"}


@pytest.mark.asyncio
async def test_a_tenant_with_no_login_gets_the_whole_seed(service_session: AsyncSession) -> None:
    """Nothing in the seed needs a user any more (the suitability template did). A firm that has
    not yet had a login provisioned still gets the forms and the published journey."""
    sess = service_session
    tenant = await _tenant(sess, with_user=False)
    out = await advice_seed.seed_advice_journey(sess, tenant_id=tenant)
    assert out["flow_uid"]
    assert out["created"] is True
    counts = await _counts(sess, tenant)
    assert counts["forms"] == 3
    assert counts["flows"] == 1


@pytest.mark.asyncio
async def test_the_seed_archives_a_pension_transfer_flow_an_earlier_seed_published(
    service_session: AsyncSession,
) -> None:
    """A tenant seeded before the pension-transfer journey was dropped still has it published.
    The seed archives it (so it leaves the "New journey" picker) and does not delete it: a journey
    running on it keeps its pinned version, and the firm can restore it from the Flows page."""
    sess = service_session
    tenant = await _tenant(sess, with_user=True)
    flow_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flows (tenant_id, uid, code, name, status, entity) "
            "VALUES (:t, :u, :c, 'Pension transfer', 'published', 'advice') RETURNING id",
            t=tenant,
            u=f"flw_{uuid.uuid4().hex[:8]}",
            c=advice_seed.RETIRED_PENSION_FLOW,
        )
    )
    first = await advice_seed.seed_advice_journey(sess, tenant_id=tenant)
    assert first["retired"] is True
    assert (
        await _scalar(sess, "SELECT status FROM flows.flows WHERE id = :f", f=flow_id) == "archived"
    )
    # Idempotent: the second run finds nothing live to retire, and the archived row is kept.
    again = await advice_seed.seed_advice_journey(sess, tenant_id=tenant)
    assert again["retired"] is False
    assert await _scalar(sess, "SELECT count(*) FROM flows.flows WHERE id = :f", f=flow_id) == 1
