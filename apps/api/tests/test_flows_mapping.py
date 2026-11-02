"""Unit tests for the data-gathering write-back mapper (form datapoints → crm.contacts patch)."""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows.mapping import (
    FieldMapping,
    MappingError,
    apply_mapping,
    parse_mapping,
)


def test_transforms_pipeline() -> None:
    m = FieldMapping(source="pc", target="postcode", transforms=("postcode",))
    assert m.run("  ec1a  1bb ") == "EC1A 1BB"
    assert FieldMapping("d", "dob", ("iso_date",)).run("7/3/1990") == "1990-03-07"
    assert FieldMapping("d", "dob", ("iso_date",)).run("1990-03-07") == "1990-03-07"
    assert FieldMapping("n", "national_id", ("digits",)).run("AB 12 34 56 C") == "123456"
    assert FieldMapping("name", "first_name", ("trim", "title")).run("  jo  ") == "Jo"


def test_empty_becomes_none() -> None:
    assert FieldMapping("x", "y", ("trim",)).run("   ") is None
    assert FieldMapping("x", "y").run(None) is None


def test_a_uk_bank_write_drops_a_leftover_iban_and_does_not_store_bacs() -> None:
    from app.flows.step_handlers import _exclusive_bank_rail

    merged = _exclusive_bank_rail(
        {
            "name": "A",
            "sort_code": "12-34-56",
            "account_number": "12345678",
            "method": "bacs",
            "iban": "GB00",
            "bic": "BARCGB22",
        },
        {
            "sort_code": "12-34-56",
            "account_number": "12345678",
            "method": "bacs",
        },
    )
    assert merged == {"name": "A", "sort_code": "12-34-56", "account_number": "12345678"}


def test_faster_payment_is_kept_and_an_international_write_drops_the_uk_rail() -> None:
    from app.flows.step_handlers import _exclusive_bank_rail

    kept = _exclusive_bank_rail(
        {"name": "A", "sort_code": "12-34-56", "method": "faster_payment", "iban": "GB00"},
        {"sort_code": "12-34-56", "method": "faster_payment"},
    )
    assert kept["method"] == "faster_payment"
    assert "iban" not in kept

    switched = _exclusive_bank_rail(
        {
            "name": "A",
            "iban": "GB82WEST12345698765432",
            "bic": "BARCGB22",
            "sort_code": "12-34-56",
            "account_number": "12345678",
            "method": "faster_payment",
        },
        {"iban": "GB82WEST12345698765432", "bic": "BARCGB22"},
    )
    assert switched == {"name": "A", "iban": "GB82WEST12345698765432", "bic": "BARCGB22"}


def test_apply_mapping_builds_patch() -> None:
    mappings = [
        FieldMapping("first", "first_name", ("trim", "title")),
        FieldMapping("pc", "postcode", ("postcode",)),
    ]
    res = apply_mapping(mappings, {"first": "jo", "pc": "ec1a 1bb"})
    assert res.patch == {"first_name": "Jo", "postcode": "EC1A 1BB"}
    assert res.errors == []


def test_required_missing_is_error_and_omitted() -> None:
    res = apply_mapping([FieldMapping("email", "email", required=True)], {"email": "  "})
    assert res.patch == {}
    assert res.errors and "email" in res.errors[0]


def test_parse_mapping_validates() -> None:
    parsed = parse_mapping(
        [{"source": "pc", "target": "postcode", "transforms": ["postcode"], "required": True}]
    )
    assert parsed == [FieldMapping("pc", "postcode", ("postcode",), True)]

    with pytest.raises(MappingError):
        parse_mapping([{"source": "x", "target": "y", "transforms": ["nope"]}])
    with pytest.raises(MappingError):
        parse_mapping([{"target": "y"}])  # missing source
    with pytest.raises(MappingError):
        parse_mapping("not a list")


def test_the_seeded_mappings_canonicalise_what_the_forms_display() -> None:
    """The marital and employment fields store the book slug. `snake` is idempotent on that
    slug, and still folds a label an older in-flight fact-find stored (`Single`,
    `Self-Employed`) so a resumed journey does not write the label into the column."""
    from app.flows.onboarding_seed import ADDITIONAL_MAPPING, PENSION_MAPPING

    result = apply_mapping(
        parse_mapping(PENSION_MAPPING),
        {
            "pension.maritalStatus": "single",
            "pension.employmentStatus": "self_employed",
            "pension.retirementAge": 65,
        },
    )
    assert result.errors == []
    assert result.patch == {
        "marital_status": "single",
        "employment_status": "self_employed",
        "retirement_age": "65",
    }
    legacy = apply_mapping(
        parse_mapping(PENSION_MAPPING),
        {
            "pension.maritalStatus": "Single",
            "pension.employmentStatus": "Self-Employed",
            "pension.retirementAge": 65,
        },
    )
    assert legacy.errors == []
    assert legacy.patch["marital_status"] == "single"
    assert legacy.patch["employment_status"] == "self_employed"

    offshore = apply_mapping(
        parse_mapping(ADDITIONAL_MAPPING),
        {
            "source_of_wealth": "  Other  ",
            "source_of_wealth_other": "  Sale of a vintage car  ",
            "pep": False,
        },
    )
    # A real column, trimmed...
    assert offshore.patch["source_of_wealth"] == "Other"
    assert offshore.patch["source_of_wealth_detail"] == "Sale of a vintage car"
    # ...and one with no column, which `_write_contact_patch` folds into crm.contacts.data rather
    # than dropping. An answer on the client beats an answer in a form response.
    assert offshore.patch["pep"] is False


@pytest.mark.asyncio
async def test_us_person_and_the_w9_or_w8_are_separate_answers(
    service_session: AsyncSession,
) -> None:
    """Is a US person? records only Yes or No. The W-9 or W-8 is its own answer, and a pair
    that disagrees is refused with words the adviser can act on."""
    from app.flows.step_handlers import _write_contact_patch

    s = service_session
    tenant = int(
        (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES ('fatca_t', 'fatca-t', 'FATCA') RETURNING id"
                )
            )
        ).scalar_one()
    )
    cid = int(
        (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type) "
                    "VALUES (:t, 'con_fatca', 'individual') RETURNING id"
                ),
                {"t": tenant},
            )
        ).scalar_one()
    )

    async def row() -> dict[str, object]:
        return dict(
            (
                await s.execute(
                    text(
                        "SELECT us_person, fatca_status, fatca_certified_at "
                        "FROM crm.contacts WHERE id = :c"
                    ),
                    {"c": cid},
                )
            )
            .mappings()
            .one()
        )

    # No W-9 or W-8: most clients have no US assets, so the answer stands alone.
    await _write_contact_patch(s, tenant, cid, {"us_person": "No"})
    got = await row()
    assert got["us_person"] is False
    assert got["fatca_status"] is None
    assert got["fatca_certified_at"] is None

    await _write_contact_patch(s, tenant, cid, {"us_person": "No", "fatca_status": "w8"})
    got = await row()
    assert got["fatca_status"] == "w8"
    assert got["fatca_certified_at"] is not None

    # The same W-8 given again keeps the date it was first given.
    await s.execute(
        text("UPDATE crm.contacts SET fatca_certified_at = DATE '2024-01-02' WHERE id = :c"),
        {"c": cid},
    )
    await _write_contact_patch(s, tenant, cid, {"us_person": "No", "fatca_status": "w8"})
    assert str((await row())["fatca_certified_at"]) == "2024-01-02"

    with pytest.raises(ValueError, match="A US person has a W-9, not a W-8."):
        await _write_contact_patch(s, tenant, cid, {"us_person": "Yes", "fatca_status": "w8"})
    # The W-8 is on the record, so the message says so.
    with pytest.raises(ValueError, match="The client record holds a W-8."):
        await _write_contact_patch(s, tenant, cid, {"us_person": "Yes"})
    with pytest.raises(ValueError, match="A W-9 is only for a US person."):
        await _write_contact_patch(s, tenant, cid, {"fatca_status": "w9"})
    with pytest.raises(ValueError, match="Pick W-9 or W-8."):
        await _write_contact_patch(s, tenant, cid, {"fatca_status": "w7"})

    await _write_contact_patch(s, tenant, cid, {"us_person": "Yes", "fatca_status": "W9"})
    got = await row()
    assert got["us_person"] is True
    assert got["fatca_status"] == "w9"


def test_apply_mapping_resolves_nested_address() -> None:
    """Composite address field stores a nested object; mapping sources stay dotted."""
    mappings = [
        FieldMapping("address.line1", "address.line1", ("trim",)),
        FieldMapping("address.postcode", "address.postcode", ("postcode",)),
        FieldMapping("address.city", "address.city", ("trim",)),
    ]
    res = apply_mapping(
        mappings,
        {
            "address": {
                "line1": "  12 Beaufort Gardens ",
                "city": "Bristol",
                "postcode": "bs8 2qt",
            }
        },
    )
    assert res.errors == []
    assert res.patch == {
        "address.line1": "12 Beaufort Gardens",
        "address.city": "Bristol",
        "address.postcode": "BS8 2QT",
    }
    # Exact flat key still wins over nest.
    flat = apply_mapping(
        [FieldMapping("address.line1", "address.line1")],
        {"address.line1": "flat", "address": {"line1": "nested"}},
    )
    assert flat.patch["address.line1"] == "flat"


def test_apply_mapping_passes_nationality_list_through() -> None:
    res = apply_mapping(
        [FieldMapping("nationality", "nationality")],
        {"nationality": [" GB ", "", "IE", None]},
    )
    assert res.errors == []
    assert res.patch == {"nationality": ["GB", "IE"]}
    # trim-like transforms still keep a list.
    trimmed = apply_mapping(
        [FieldMapping("nationality", "nationality", ("trim",))],
        {"nationality": ["  FR  "]},
    )
    assert trimmed.patch["nationality"] == ["FR"]


def test_every_seeded_mapping_parses() -> None:
    """A malformed mapping raises only when a journey RUNS the step, which is far too late."""
    from app.flows.onboarding_seed import (
        ADDITIONAL_MAPPING,
        CORE_MAPPING,
        EMPLOYER_MAPPING,
        ORG_MAPPING,
        PENSION_MAPPING,
    )

    for mapping in (
        CORE_MAPPING,
        ORG_MAPPING,
        PENSION_MAPPING,
        ADDITIONAL_MAPPING,
        EMPLOYER_MAPPING,
    ):
        assert parse_mapping(mapping)
    core = parse_mapping(CORE_MAPPING)
    # The fact-find's one number is the PRIMARY MOBILE. The source key stays `phone` on purpose:
    # `documents.form_responses` is keyed by field key, so renaming it would strand the answer of
    # every in-flight case.
    assert any(m.source == "phone" and m.target == "mobile" for m in core)
    assert any(m.source == "other_numbers" and m.target == "phone" for m in core)


def test_a_structured_answer_passes_through_whole() -> None:
    """A list of OBJECTS is not a list of strings, and must not be `str()`-ed element-wise.

    The `phoneNumbers` editor answers `[{"label","number"}]`. The old list branch stringified each
    item, which produced `"{'label': 'home', 'number': '07700900123'}"` — a value that stores
    without complaint, renders as itself on the record and is readable by nothing downstream.
    """
    rows = [{"label": "home", "number": "01179000000"}]
    out = apply_mapping(
        parse_mapping([{"source": "other_numbers", "target": "phone"}]),
        {"other_numbers": rows},
    )
    # The column is text. A leftover list answer is flattened to its first number by the
    # contact normaliser, which the onboarding step runs before the write.
    assert out.patch["phone"] == rows

    # A transform pipeline is `str -> str` and has nothing to say about an object, so asking for
    # one is an authoring mistake — raised here rather than applied to the repr of a dict.
    with pytest.raises(MappingError):
        apply_mapping(
            parse_mapping([{"source": "other_numbers", "target": "phone", "transforms": ["trim"]}]),
            {"other_numbers": rows},
        )


@pytest.mark.asyncio
async def test_write_contact_patch_jsonb_columns_and_nationality(
    service_session: AsyncSession,
) -> None:
    """address / bank_account land on their columns; nationality is text[]."""
    from app.flows.step_handlers import _write_contact_patch

    s = service_session
    tenant = int(
        (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES ('jb_t', 'jb-t', 'Jsonb') RETURNING id"
                )
            )
        ).scalar_one()
    )
    cid = int(
        (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type) "
                    "VALUES (:t, 'con_jb', 'individual') RETURNING id"
                ),
                {"t": tenant},
            )
        ).scalar_one()
    )

    await _write_contact_patch(
        s,
        tenant,
        cid,
        {
            "address.line1": "12 Beaufort Gardens",
            "address.city": "Bristol",
            "address.postcode": "BS8 2QT",
            "bank_account.account_name": "Miss H Vale",
            "bank_account.account_number": "12345678",
            "bank_account.sort_code": "123456",
            "nationality": ["GB", "IE"],
            "tax_residency": "GB",
        },
    )
    row = (
        (
            await s.execute(
                text(
                    "SELECT address, bank_account, nationality, tax_residency, data "
                    "FROM crm.contacts WHERE id = :c"
                ),
                {"c": cid},
            )
        )
        .mappings()
        .one()
    )
    assert row["address"] == {
        "line1": "12 Beaufort Gardens",
        "city": "Bristol",
        "postcode": "BS8 2QT",
    }
    assert row["bank_account"] == {
        "name": "Miss H Vale",
        "account_number": "12345678",
        "sort_code": "12-34-56",
    }
    assert list(row["nationality"]) == ["GB", "IE"]
    assert list(row["tax_residency"]) == ["GB"]
    assert row["data"] in (None, {})
    assert "address" not in (row["data"] or {})
    assert "bank_account" not in (row["data"] or {})


@pytest.mark.asyncio
async def test_a_client_submission_fills_a_blank_and_leaves_a_held_fact(
    service_session: AsyncSession,
) -> None:
    """The fact find a client completes must not rewrite the identity the firm already holds.

    A blank (middle name, the second address line, marital status) is still filled, because the
    record did not have one. The same patch from staff replaces, which is the adviser editing
    the record.
    """
    from app.flows.step_handlers import _write_contact_patch

    s = service_session
    suffix = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:u, :a, 'Held') RETURNING id"
                ),
                {"u": f"hd_{suffix}", "a": f"hd-{suffix}"},
            )
        ).scalar_one()
    )
    cid = int(
        (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, dob, "
                    "  address) "
                    "VALUES (:t, :u, 'individual', 'Mac', 'Gaunt', '1993-10-08', "
                    "  CAST(:addr AS jsonb)) RETURNING id"
                ),
                {
                    "t": tenant,
                    "u": f"hd_{suffix}",
                    "addr": '{"line1": "1 Cushat Gardens", "city": "Dalkeith"}',
                },
            )
        ).scalar_one()
    )

    await _write_contact_patch(
        s,
        tenant,
        cid,
        {
            "first_name": "Mack",
            "surname": "Gaunt",
            "middle_name": "James",
            "dob": "1990-01-01",
            "marital_status": "married",
            "address.line1": "99 Other Road",
            "address.line2": "Flat 2",
        },
        fill_blanks_only=True,
    )
    row = (
        (
            await s.execute(
                text(
                    "SELECT first_name, middle_name, surname, dob::text AS dob, marital_status, "
                    "       address FROM crm.contacts WHERE id = :c"
                ),
                {"c": cid},
            )
        )
        .mappings()
        .one()
    )
    assert row["first_name"] == "Mac"
    assert row["surname"] == "Gaunt"
    assert row["dob"] == "1993-10-08"
    assert row["address"]["line1"] == "1 Cushat Gardens"
    assert row["address"]["city"] == "Dalkeith"
    assert row["middle_name"] == "James"
    assert row["marital_status"] == "married"
    assert row["address"]["line2"] == "Flat 2"

    await _write_contact_patch(s, tenant, cid, {"first_name": "Corrected"})
    replaced = (
        await s.execute(text("SELECT first_name FROM crm.contacts WHERE id = :c"), {"c": cid})
    ).scalar_one()
    assert replaced == "Corrected"


@pytest.mark.asyncio
async def test_account_preferences_write_after_kickoff_and_before_terms(
    service_session: AsyncSession,
) -> None:
    """The moved choices update the account; transfer becomes visible to the terms condition."""
    from app.flows.step_handlers import _write_account_preferences

    s = service_session
    suffix = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:u, :a, 'Account choices') RETURNING id"
                ),
                {"u": f"ac_{suffix}", "a": f"ac-{suffix}"},
            )
        ).scalar_one()
    )
    contact = int(
        (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type) "
                    "VALUES (:t, :u, 'individual') RETURNING id"
                ),
                {"t": tenant, "u": f"co_{suffix}"},
            )
        ).scalar_one()
    )
    account = int(
        (
            await s.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, uid, contact_id, status, custodian) "
                    "VALUES (:t, :u, :c, 'draft', 'sei') RETURNING id"
                ),
                {"t": tenant, "u": f"aa_{suffix}", "c": contact},
            )
        ).scalar_one()
    )

    await _write_account_preferences(
        s,
        tenant,
        account,
        {
            "account_name": "Harriet's SIPP",
            "currency": "GBP",
            "investment_capacity": "advisory",
            "has_initial_cash": True,
            "initial_cash_amount": 10_000,
            "dividend_treatment": "reinvest",
            "dividend_settlement_currency": "account_base",
            "has_transfer": True,
            "transfer_type": "cash",
            "transfer_extent": "partial",
            "transfer_amount": 25_000,
            "has_contribution": True,
            "contribution_amount": 500,
            "contribution_frequency": "monthly",
            "contribution_expected_by": "2026-10-01",
            "has_regular_withdrawal": True,
            "withdrawal_amount": 250,
            "withdrawal_frequency": "monthly",
            "withdrawal_start_date": "2027-01-15",
            "has_third_party_provider": True,
            "third_party_provider": "Legacy Custodian",
            "third_party_account_name": "Harriet Vale",
            "third_party_account_number": "12345678",
            "third_party_sort_code": "112233",
            "beneficiaries": [
                {"first_name": "Alex", "surname": "Vale", "portion": 60},
                {"first_name": "Sam", "surname": "Vale", "portion": 40},
            ],
            "account_activity_nature": "Long-term investment",
            "account_activity_standing_orders": "None",
            "account_activity_funds_in": 50_000,
            "account_activity_funds_out": 5_000,
            "account_activity_max_turnover": 20,
            "account_activity_transaction_scale": 10_000,
        },
    )

    row = (
        (
            await s.execute(
                text(
                    "SELECT name, currency, investment_capacity, dividend_treatment, transfer, "
                    "       payments, withdrawals, data FROM platform.accounts WHERE id = :a"
                ),
                {"a": account},
            )
        )
        .mappings()
        .one()
    )
    assert row["name"] == "Harriet's SIPP"
    assert row["currency"] == "GBP"
    assert row["investment_capacity"] == "advisory"
    assert row["dividend_treatment"] == "reinvest"
    assert row["data"]["dividend_settlement_currency"] == "account_base"
    assert row["transfer"]["intent"] is True
    assert row["transfer"]["transfer_type"] == "cash"
    assert row["transfer"]["extent"] == "partial"
    assert row["data"]["regular_contribution"] == {
        "intent": True,
        "amount": 500,
        "frequency": "monthly",
        "expected_by": "2026-10-01",
    }
    # Lists, like every other writer of these columns. The opening cash is a one-off, on `data`.
    assert row["payments"] == [
        {
            "amount": 500.0,
            "frequency": "monthly",
            "next_date": "2026-10-01",
            "method": None,
            "reference": None,
        }
    ]
    assert row["data"]["initial_cash_deposit"] == {"investment_amount": 10_000}
    assert row["withdrawals"] == [
        {
            "amount": 250.0,
            "frequency": "monthly",
            "next_date": "2027-01-15",
            "method": None,
            "reference": None,
        }
    ]
    assert len(row["data"]["beneficiaries"]) == 2
    assert row["data"]["third_party_provider_bank_details"]["account_number"] == "12345678"
    assert row["data"]["account_activity"]["funds_in"] == 50_000

    # Re-opening Account details reads the same answers back.
    from app.auth import Principal
    from app.db import FirmContext
    from app.routers.applications import _account_form_prefill

    ctx = FirmContext(
        session=s,
        tenant_id=tenant,
        principal=Principal(
            user_id="wos_staff", organisation_id="org_1", role="adviser", permissions=[]
        ),
    )
    asked = {
        "initial_cash_amount",
        "contribution_amount",
        "contribution_expected_by",
        "withdrawal_amount",
        "withdrawal_start_date",
    }
    values = await _account_form_prefill(ctx, account, asked)
    assert values == {
        "initial_cash_amount": 10_000,
        "contribution_amount": 500,
        "contribution_expected_by": "2026-10-01",
        "withdrawal_amount": 250,
        "withdrawal_start_date": "2027-01-15",
    }


@pytest.mark.asyncio
async def test_account_preferences_keep_the_settlement_currency_on_sei_only(
    service_session: AsyncSession,
) -> None:
    """The settlement currency is an SEI choice: kept on SEI, dropped on any other custodian.

    SEI is read the way the form context reads it — the account's custodian, else the
    application's provider — so an account whose own custodian is not yet stamped still keeps it.
    """
    from app.flows.step_handlers import _write_account_preferences

    s = service_session
    suffix = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:u, :a, 'Settlement choices') RETURNING id"
                ),
                {"u": f"st_{suffix}", "a": f"st-{suffix}"},
            )
        ).scalar_one()
    )
    contact = int(
        (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type) "
                    "VALUES (:t, :u, 'individual') RETURNING id"
                ),
                {"t": tenant, "u": f"co_{suffix}"},
            )
        ).scalar_one()
    )
    sei_application = int(
        (
            await s.execute(
                text(
                    "INSERT INTO crm.applications (tenant_id, provider, uid, status) "
                    "VALUES (:t, 'sei', :u, 'draft') RETURNING id"
                ),
                {"t": tenant, "u": f"app_{suffix}"},
            )
        ).scalar_one()
    )

    async def account(uid: str, custodian: str | None, application: int | None) -> int:
        return int(
            (
                await s.execute(
                    text(
                        "INSERT INTO platform.accounts "
                        "  (tenant_id, uid, contact_id, status, custodian, application_id) "
                        "VALUES (:t, :u, :c, 'draft', "
                        "        CAST(:p AS platform.provider_code), :a) RETURNING id"
                    ),
                    {
                        "t": tenant,
                        "u": f"{uid}_{suffix}",
                        "c": contact,
                        "p": custodian,
                        "a": application,
                    },
                )
            ).scalar_one()
        )

    async def settlement_after_save(account_id: int) -> object:
        await _write_account_preferences(
            s,
            tenant,
            account_id,
            {
                "account_name": "Harriet's GIA",
                "currency": "GBP",
                "investment_capacity": "advisory",
                "dividend_settlement_currency": "account_base",
            },
        )
        data = (
            await s.execute(
                text("SELECT data FROM platform.accounts WHERE id = :a"), {"a": account_id}
            )
        ).scalar_one()
        return (data or {}).get("dividend_settlement_currency")

    assert await settlement_after_save(await account("sei", "sei", None)) == "account_base"
    assert await settlement_after_save(await account("app", None, sei_application)) == (
        "account_base"
    )
    assert await settlement_after_save(await account("seccl", "seccl", None)) is None
    assert await settlement_after_save(await account("none", None, None)) is None


@pytest.mark.asyncio
async def test_pension_transfer_records_safeguarded_rights_and_the_fca_reference(
    service_session: AsyncSession,
) -> None:
    """A pension transfer keeps the checkbox and the FCA reference. A GIA does not ask them."""
    from app.flows.step_handlers import _write_account_preferences

    s = service_session
    suffix = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:u, :a, 'Safeguarded') RETURNING id"
                ),
                {"u": f"sg_{suffix}", "a": f"sg-{suffix}"},
            )
        ).scalar_one()
    )
    contact = int(
        (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type) "
                    "VALUES (:t, :u, 'individual') RETURNING id"
                ),
                {"t": tenant, "u": f"co_{suffix}"},
            )
        ).scalar_one()
    )

    async def account_for(product_type: str) -> int:
        product = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO platform.products "
                        "  (tenant_id, product_type, name, code) "
                        "VALUES (:t, CAST(:p AS platform.product_type), :n, :c) RETURNING id"
                    ),
                    {
                        "t": tenant,
                        "p": product_type,
                        "n": product_type,
                        "c": f"{product_type}-{suffix}",
                    },
                )
            ).scalar_one()
        )
        return int(
            (
                await s.execute(
                    text(
                        "INSERT INTO platform.accounts "
                        "  (tenant_id, uid, contact_id, status, product_id) "
                        "VALUES (:t, :u, :c, 'draft', :p) RETURNING id"
                    ),
                    {
                        "t": tenant,
                        "u": f"aa_{product_type}_{suffix}",
                        "c": contact,
                        "p": product,
                    },
                )
            ).scalar_one()
        )

    def answers(**extra: object) -> dict[str, object]:
        return {
            "account_name": "Pension transfer",
            "currency": "GBP",
            "investment_capacity": "advisory",
            "has_transfer": True,
            "transfer_type": "cash",
            "transfer_extent": "full",
            **extra,
        }

    pension = await account_for("SIPP")
    with pytest.raises(ValueError, match="FCA reference"):
        await _write_account_preferences(
            s, tenant, pension, answers(safeguarded_benefits=True, fca_reference="  ")
        )
    await _write_account_preferences(
        s,
        tenant,
        pension,
        answers(safeguarded_benefits=True, fca_reference=" 123456 "),
    )
    stored = (
        await s.execute(
            text("SELECT transfer FROM platform.accounts WHERE id = :a"),
            {"a": pension},
        )
    ).scalar_one()
    assert stored["safeguarded_benefits"] is True
    assert stored["fca_reference"] == "123456"

    await _write_account_preferences(
        s,
        tenant,
        pension,
        answers(safeguarded_benefits=False, fca_reference="123456"),
    )
    cleared = (
        await s.execute(
            text("SELECT transfer FROM platform.accounts WHERE id = :a"),
            {"a": pension},
        )
    ).scalar_one()
    assert cleared["safeguarded_benefits"] is False
    assert "fca_reference" not in cleared

    gia = await account_for("GIA")
    await _write_account_preferences(
        s,
        tenant,
        gia,
        answers(account_name="GIA transfer", safeguarded_benefits=True, fca_reference="123456"),
    )
    gia_transfer = (
        await s.execute(
            text("SELECT transfer FROM platform.accounts WHERE id = :a"),
            {"a": gia},
        )
    ).scalar_one()
    assert "safeguarded_benefits" not in gia_transfer
    assert "fca_reference" not in gia_transfer


@pytest.mark.asyncio
async def test_account_preferences_always_write_display_fee_packages(
    service_session: AsyncSession,
) -> None:
    """A package with ``access = display`` applies even when the form names none."""
    from app.flows.step_handlers import _write_account_preferences

    s = service_session
    suffix = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:u, :a, 'Display fees') RETURNING id"
                ),
                {"u": f"df_{suffix}", "a": f"df-{suffix}"},
            )
        ).scalar_one()
    )
    contact = int(
        (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type) "
                    "VALUES (:t, :u, 'individual') RETURNING id"
                ),
                {"t": tenant, "u": f"co_{suffix}"},
            )
        ).scalar_one()
    )
    account = int(
        (
            await s.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, uid, contact_id, status) "
                    "VALUES (:t, :u, :c, 'draft') RETURNING id"
                ),
                {"t": tenant, "u": f"aa_{suffix}", "c": contact},
            )
        ).scalar_one()
    )
    display = int(
        (
            await s.execute(
                text(
                    "INSERT INTO platform.fee_packages "
                    "  (tenant_id, name, category, frequency, fee_type, rule, access) "
                    "VALUES (:t, 'Platform ongoing', 'platform', 'ongoing', 'percentage', "
                    "        CAST(:rule AS jsonb), 'display') RETURNING id"
                ),
                {"t": tenant, "rule": json.dumps({"rate": 0.35})},
            )
        ).scalar_one()
    )
    selectable = int(
        (
            await s.execute(
                text(
                    "INSERT INTO platform.fee_packages "
                    "  (tenant_id, name, category, frequency, fee_type, rule, access) "
                    "VALUES (:t, 'Adviser ongoing', 'adviser', 'ongoing', 'percentage', "
                    "        CAST(:rule AS jsonb), 'select') RETURNING id"
                ),
                {"t": tenant, "rule": json.dumps({"rate": 0.5})},
            )
        ).scalar_one()
    )

    await _write_account_preferences(
        s,
        tenant,
        account,
        {
            "account_name": "Display ISA",
            "currency": "GBP",
            "investment_capacity": "advisory",
            "fee_package_ids": [],
        },
    )
    assigned = [
        int(value)
        for value in (
            await s.execute(
                text(
                    "SELECT fee_package_id FROM platform.account_fee_packages "
                    "WHERE account_id = :a ORDER BY sort_order"
                ),
                {"a": account},
            )
        ).scalars()
    ]
    assert assigned == [display]

    await _write_account_preferences(
        s,
        tenant,
        account,
        {
            "account_name": "Display ISA",
            "currency": "GBP",
            "investment_capacity": "advisory",
            "fee_package_ids": [selectable],
        },
    )
    assigned = [
        int(value)
        for value in (
            await s.execute(
                text(
                    "SELECT fee_package_id FROM platform.account_fee_packages "
                    "WHERE account_id = :a ORDER BY sort_order"
                ),
                {"a": account},
            )
        ).scalars()
    ]
    assert assigned == [display, selectable]


@pytest.mark.asyncio
async def test_a_dotted_target_nests_and_keeps_its_siblings(service_session: AsyncSession) -> None:
    """`data` paths, against a real Postgres — the half a pure test cannot check.

    Two things could go wrong and both are silent. A shallow `||` merge would create a top-level key
    literally named "pension.employerContributions.name", readable by nothing; and merging a nested
    object would REPLACE any existing `pension` wholesale, dropping what an earlier step in the same
    journey wrote.
    """
    from app.flows.step_handlers import _write_contact_patch

    s = service_session
    tenant = int(
        (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES ('nest_t', 'nest-t', 'Nest') RETURNING id"
                )
            )
        ).scalar_one()
    )
    cid = int(
        (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type) "
                    "VALUES (:t, 'con_nest', 'individual') RETURNING id"
                ),
                {"t": tenant},
            )
        ).scalar_one()
    )

    # An earlier step's answer, at the same top-level key the employer step will write under.
    await _write_contact_patch(s, tenant, cid, {"pension.retirementAgeNote": "checked"})
    await _write_contact_patch(
        s,
        tenant,
        cid,
        {
            "pension.employerContributions.name": "Acme Ltd",
            "pension.employerContributions.sortCode": "112233",
            "source_of_wealth": "Salary",
        },
    )
    row = (
        (
            await s.execute(
                text("SELECT data, source_of_wealth FROM crm.contacts WHERE id = :c"), {"c": cid}
            )
        )
        .mappings()
        .one()
    )
    assert row["data"]["pension"]["employerContributions"] == {
        "name": "Acme Ltd",
        "sortCode": "112233",
    }
    # The sibling written by the earlier call survived the nested write.
    assert row["data"]["pension"]["retirementAgeNote"] == "checked"
    # A whitelisted target is still a real column, not a `data` key.
    assert row["source_of_wealth"] == "Salary"
    assert "source_of_wealth" not in row["data"]


def test_the_employer_step_resolves_a_flat_address_key_into_column_leaves() -> None:
    """The seeded employer mapping, run against answers shaped the way the form stores them.

    The form's keys are flat dotted strings (`pension.employerContributions.name`) except the
    address, which a composite field stores as `{"employer_address": {"line1": …}}`. The mapper
    must find both — the dotted key by exact match, the address leaf by nesting — and hand
    `_write_contact_patch` the column names the Employer card reads.
    """
    from app.flows.onboarding_seed import EMPLOYER_MAPPING

    res = apply_mapping(
        parse_mapping(EMPLOYER_MAPPING),
        {
            "pension.hasEmployerContributions": True,
            "pension.employerContributions.name": " Acme Ltd ",
            "pension.employerContributions.registrationNumber": "sc123456",
            "employer_address": {
                "line1": "1 High Street ",
                "line2": "",
                "city": "Edinburgh",
                "postcode": "eh1  1aa",
                "country": "GB",
            },
            "pension.employerContributions.sortCode": "11-22-33",
        },
    )
    assert res.errors == []
    assert res.patch == {
        "employer_contributions": True,
        "employer_name": "Acme Ltd",
        "employer_registration_number": "SC123456",
        "employer_address.line1": "1 High Street",
        "employer_address.city": "Edinburgh",
        "employer_address.postcode": "EH1 1AA",
        "employer_address.country": "GB",
        # The scheme's bank details have a column of their own (0271): the leaf is the jsonb
        # path `_write_contact_patch` merges, not a `data` key the record would list as overflow.
        "employer_bank_account.sort_code": "112233",
    }


@pytest.mark.asyncio
async def test_employer_targets_land_in_the_employer_columns(
    service_session: AsyncSession,
) -> None:
    """The Employer card's columns, not `data` — against a real Postgres, because the boolean
    cast and the jsonb merge are the two halves a pure test cannot see.

    Before this, the employer step wrote every answer under `data.pension.employerContributions`
    and `source_of_wealth_detail` as a top-level `data` key: the card read its columns, found
    nothing, and the answers surfaced only as "more from the fact-find".
    """
    from app.flows.step_handlers import _write_contact_patch

    s = service_session
    tenant = int(
        (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES ('empl_t', 'empl-t', 'Employer') RETURNING id"
                )
            )
        ).scalar_one()
    )
    cid = int(
        (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type) "
                    "VALUES (:t, 'con_empl', 'individual') RETURNING id"
                ),
                {"t": tenant},
            )
        ).scalar_one()
    )
    await _write_contact_patch(
        s,
        tenant,
        cid,
        {
            "employer_contributions": True,
            "employer_name": "Acme Ltd",
            "employer_registration_number": "SC123456",
            "employer_address.line1": "1 High Street",
            "employer_address.postcode": "EH1 1AA",
            "source_of_wealth_detail": "Sold her house",
            "employer_bank_account.sort_code": "112233",
        },
    )
    # A second write merges a leaf into the same jsonb column rather than replacing it.
    await _write_contact_patch(s, tenant, cid, {"employer_address.city": "Edinburgh"})
    row = (
        (
            await s.execute(
                text(
                    "SELECT employer_contributions, employer_name, employer_registration_number, "
                    "       employer_address, employer_bank_account, source_of_wealth_detail, data "
                    "FROM crm.contacts WHERE id = :c"
                ),
                {"c": cid},
            )
        )
        .mappings()
        .one()
    )
    assert row["employer_contributions"] is True
    assert row["employer_name"] == "Acme Ltd"
    assert row["employer_registration_number"] == "SC123456"
    assert row["employer_address"] == {
        "line1": "1 High Street",
        "postcode": "EH1 1AA",
        "city": "Edinburgh",
    }
    assert row["source_of_wealth_detail"] == "Sold her house"
    # The scheme's bank details merge into their own jsonb column (0271), and nothing is left
    # for the record's gathered overflow.
    assert row["employer_bank_account"] == {"sort_code": "112233"}
    assert row["data"] in (None, {})

    # The gate is a fact in both directions: a "No" is stored as false, not dropped.
    await _write_contact_patch(s, tenant, cid, {"employer_contributions": False})
    gate = (
        await s.execute(
            text("SELECT employer_contributions FROM crm.contacts WHERE id = :c"), {"c": cid}
        )
    ).scalar_one()
    assert gate is False
