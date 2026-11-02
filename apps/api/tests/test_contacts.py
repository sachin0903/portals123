"""Firm self-service contact (client) write path -- app.contacts.

Exercises the functions the platform portal's firm_session drives (create/update/assign/
link), against a real service_role session (mirrors db.py::firm_session). Every function is
tenant-scoped: the key assertions are that a firm can only touch its OWN contacts, and can
only assign/link its OWN people -- a cross-tenant attempt fails closed (LookupError), even
though the session itself is BYPASSRLS.

No truncate (service_session is shared): uses unique uids/emails so it is order-independent.
"""

import pathlib
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import bank_check, contacts, national_insurance
from app.contacts import UpdateContact
from app.flows.form_validation import national_insurance_in_use
from tests.conftest import assert_public_id


async def _mk_tenant(session: AsyncSession) -> int:
    suffix = uuid.uuid4().hex[:10]
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {"uid": f"ten_{suffix}", "alias": f"firm-{suffix}", "name": f"Firm {suffix}"},
            )
        ).scalar_one()
    )


async def _mk_user(
    session: AsyncSession, tenant_id: int, types: str = "{}", *, is_adviser: bool = False
) -> str:
    """`types` is `auth.users.type` (0124) and `is_adviser` its 0125 companion. Both default to
    "no", so a login only advises when a test says so — which is what `assign_adviser` requires,
    and the point of the column."""
    suffix = uuid.uuid4().hex[:10]
    return str(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.users"
                    "  (uid, tenant_id, email, workos_user_id, enabled, type, is_adviser)"
                    " VALUES (:uid, :t, :email, :wos, true, CAST(:ty AS text[]), :adv)"
                    " RETURNING id"
                ),
                {
                    "uid": f"usr_{suffix}",
                    "t": tenant_id,
                    "email": f"{suffix}@firm.test",
                    "wos": f"wos_{suffix}",
                    "ty": types,
                    "adv": is_adviser,
                },
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_create_update_and_relate_contact(service_session: AsyncSession) -> None:
    s = service_session
    tenant = await _mk_tenant(s)
    adviser = await _mk_user(s, tenant, "{staff}", is_adviser=True)
    client_login = await _mk_user(s, tenant, "{client}")

    created = await contacts.create_contact(
        s,
        tenant_id=tenant,
        type="individual",
        service_type="advised",
        surname="Cole",
        dob=date(1980, 2, 3),
        marital_status="married",
        retirement_age=65,
    )
    assert created["type"] == "individual"
    assert_public_id(created["uid"])
    contact_id = int(created["id"])
    stored = (
        (
            await s.execute(
                text("SELECT dob, marital_status, retirement_age FROM crm.contacts WHERE id = :id"),
                {"id": contact_id},
            )
        )
        .mappings()
        .one()
    )
    assert dict(stored) == {
        "dob": date(1980, 2, 3),
        "marital_status": "married",
        "retirement_age": 65,
    }

    updated = await contacts.update_contact(
        s,
        tenant_id=tenant,
        contact_id=contact_id,
        fields={"surname": "Cole-Smith", "mobile": None},
    )
    assert updated["surname"] == "Cole-Smith"

    assigned = await contacts.assign_adviser(
        s, tenant_id=tenant, contact_id=contact_id, adviser_id=adviser
    )
    assert assigned["role"] == "adviser"
    # idempotent re-assign re-roles rather than erroring
    reassigned = await contacts.assign_adviser(
        s, tenant_id=tenant, contact_id=contact_id, adviser_id=adviser, role="paraplanner"
    )
    assert reassigned["role"] == "paraplanner"

    linked = await contacts.link_user(
        s, tenant_id=tenant, contact_id=contact_id, user_id=client_login, capacity="self"
    )
    assert linked["capacity"] == "self"

    assert (
        await contacts.unassign_adviser(
            s, tenant_id=tenant, contact_id=contact_id, adviser_id=adviser
        )
    )["ok"]
    assert (
        await contacts.unlink_user(s, tenant_id=tenant, contact_id=contact_id, user_id=client_login)
    )["ok"]


@pytest.mark.asyncio
async def test_a_new_adviser_moves_the_old_one_to_support_not_off_the_team(
    service_session: AsyncSession,
) -> None:
    """One adviser per client (0245). Naming a new one keeps the old one on the team, as support,
    so nobody loses a client they were working on without the firm saying so."""
    s = service_session
    tenant = await _mk_tenant(s)
    first = await _mk_user(s, tenant, "{staff}", is_adviser=True)
    chosen = await _mk_user(s, tenant, "{staff}", is_adviser=True)
    contact_id = int(
        (await contacts.create_contact(s, tenant_id=tenant, type="individual", surname="Client"))[
            "id"
        ]
    )
    await contacts.assign_adviser(s, tenant_id=tenant, contact_id=contact_id, adviser_id=first)

    await contacts.assign_adviser(s, tenant_id=tenant, contact_id=contact_id, adviser_id=chosen)

    rows = (
        await s.execute(
            text(
                "SELECT adviser_id::text, role FROM crm.client_advisers "
                "WHERE contact_id = :c ORDER BY adviser_id::text"
            ),
            {"c": contact_id},
        )
    ).all()
    assert dict(rows) == {first: "support", chosen: "adviser"}


@pytest.mark.asyncio
async def test_a_team_holds_one_adviser_and_any_number_of_other_staff(
    service_session: AsyncSession,
) -> None:
    s = service_session
    tenant = await _mk_tenant(s)
    adviser = await _mk_user(s, tenant, "{staff}", is_adviser=True)
    paraplanner = await _mk_user(s, tenant, "{staff}")
    administrator = await _mk_user(s, tenant, "{staff}")
    contact_id = int((await contacts.create_contact(s, tenant_id=tenant, type="individual"))["id"])

    await contacts.assign_adviser(s, tenant_id=tenant, contact_id=contact_id, adviser_id=adviser)
    for who, role in ((paraplanner, "paraplanner"), (administrator, "administrator")):
        await contacts.assign_adviser(
            s, tenant_id=tenant, contact_id=contact_id, adviser_id=who, role=role
        )

    roles = dict(
        (
            await s.execute(
                text(
                    "SELECT adviser_id::text, role FROM crm.client_advisers WHERE contact_id = :c"
                ),
                {"c": contact_id},
            )
        ).all()
    )
    assert roles == {adviser: "adviser", paraplanner: "paraplanner", administrator: "administrator"}

    # A word outside the list is the caller's mistake, refused before anything is written.
    with pytest.raises(ValueError, match="unknown team role"):
        await contacts.assign_adviser(
            s, tenant_id=tenant, contact_id=contact_id, adviser_id=paraplanner, role="lead"
        )


@pytest.mark.asyncio
async def test_the_creator_joins_the_team_as_adviser_only_if_they_are_one(
    service_session: AsyncSession,
) -> None:
    """A non-adviser with `contacts:create` still joins the team (or a book-scoped creator could
    not see the client they made), but as support: the adviser is never a login that may not hold
    a book."""
    s = service_session
    tenant = await _mk_tenant(s)
    adviser = await _mk_user(s, tenant, "{staff}", is_adviser=True)
    paraplanner = await _mk_user(s, tenant, "{staff}")

    for who, expected in ((adviser, "adviser"), (paraplanner, "support")):
        workos = (
            await s.execute(
                text("SELECT workos_user_id FROM auth.users WHERE id = CAST(:u AS uuid)"),
                {"u": who},
            )
        ).scalar_one()
        contact_id = int(
            (
                await contacts.create_contact(
                    s, tenant_id=tenant, type="individual", created_by_workos=workos
                )
            )["id"]
        )
        team = (
            await s.execute(
                text(
                    "SELECT adviser_id::text, role FROM crm.client_advisers WHERE contact_id = :c"
                ),
                {"c": contact_id},
            )
        ).all()
        assert [tuple(r) for r in team] == [(who, expected)]


def _ctx(session: AsyncSession, tenant_id: int, workos: str = "wos_staff") -> object:
    """A FirmContext as ``firm_session`` builds one, so a test can drive the route rather than the
    service function — presence-vs-absence of a field is decided in the router's ``model_dump``."""
    from app.auth import Principal
    from app.db import FirmContext

    return FirmContext(
        session=session,
        tenant_id=tenant_id,
        principal=Principal(user_id=workos, permissions=["contacts:write"]),
    )


@pytest.mark.asyncio
async def test_a_field_sent_as_null_is_cleared_and_an_absent_one_is_left_alone(
    service_session: AsyncSession,
) -> None:
    """Clearing a stale detail used to be a silent no-op that still reported "Client updated": the
    router sent every unset field as ``None`` and the service layer dropped every ``None``. Both
    halves had to move together, so this drives PATCH /firm/contacts/{id} rather than the service
    function -- the guarantee only holds if the router sends ONLY what the caller sent."""
    from app.routers import firm as firm_router

    s = service_session
    tenant = await _mk_tenant(s)
    created = await contacts.create_contact(
        s,
        tenant_id=tenant,
        type="individual",
        surname="Cole",
        title="Dr",
        email="dr@cole.test",
        mobile="07700 900123",
    )
    cid = int(created["id"])
    ctx = _ctx(s, tenant)

    saved = await firm_router.update_contact(
        contact_uid=str(cid),
        contact_id=cid,
        body=firm_router.UpdateContact.model_validate({"address": {"line1": "1 High St"}}),
        ctx=ctx,  # type: ignore[arg-type]
    )
    assert saved["address"] == {"line1": "1 High St"}

    # Clear the mobile and the address; say nothing at all about the other fields.
    saved = await firm_router.update_contact(
        contact_uid=str(cid),
        contact_id=cid,
        body=firm_router.UpdateContact.model_validate({"mobile": None, "address": None}),
        ctx=ctx,  # type: ignore[arg-type]
    )
    assert saved["mobile"] is None
    assert saved["address"] is None
    assert (saved["surname"], saved["title"], saved["email"]) == ("Cole", "Dr", "dr@cole.test")
    # A real SQL NULL, not the jsonb literal `null` (which json.dumps(None) would have stored, and
    # which reads back as None over JSON while failing every `address IS NULL` query).
    assert (
        await s.execute(text("SELECT address IS NULL FROM crm.contacts WHERE id = :c"), {"c": cid})
    ).scalar_one() is True


@pytest.mark.asyncio
async def test_a_not_null_column_is_never_cleared(service_session: AsyncSession) -> None:
    """`enabled` is NOT NULL (0002:125). Now that a present null means "clear it", an explicit
    `{"enabled": null}` would reach the UPDATE and 500 on the constraint; it is dropped instead."""
    from app.routers import firm as firm_router

    s = service_session
    tenant = await _mk_tenant(s)
    made = await contacts.create_contact(s, tenant_id=tenant, type="individual", surname="Vale")
    cid = int(made["id"])
    saved = await firm_router.update_contact(
        contact_uid=str(cid),
        contact_id=cid,
        body=firm_router.UpdateContact.model_validate({"enabled": None, "mobile": None}),
        ctx=_ctx(s, tenant),  # type: ignore[arg-type]
    )
    assert saved["enabled"] is True
    assert saved["mobile"] is None


@pytest.mark.asyncio
async def test_a_w8_sent_as_null_is_cleared_with_its_date(service_session: AsyncSession) -> None:
    """The client record's blank W-9 or W-8 posts a null for the form and its date. Both clear,
    and the US-person answer it did not send stays."""
    from app.routers import firm as firm_router

    s = service_session
    tenant = await _mk_tenant(s)
    made = await contacts.create_contact(s, tenant_id=tenant, type="individual", surname="Vale")
    cid = int(made["id"])
    ctx = _ctx(s, tenant)
    await firm_router.update_contact(
        contact_uid=str(cid),
        contact_id=cid,
        body=firm_router.UpdateContact.model_validate(
            {"us_person": False, "fatca_status": "w8", "fatca_certified_at": "2026-10-01"}
        ),
        ctx=ctx,  # type: ignore[arg-type]
    )
    await firm_router.update_contact(
        contact_uid=str(cid),
        contact_id=cid,
        body=firm_router.UpdateContact.model_validate(
            {"fatca_status": None, "fatca_certified_at": None}
        ),
        ctx=ctx,  # type: ignore[arg-type]
    )
    row = (
        await s.execute(
            text(
                "SELECT us_person, fatca_status, fatca_certified_at FROM crm.contacts WHERE id = :c"
            ),
            {"c": cid},
        )
    ).one()
    assert (row.us_person, row.fatca_status, row.fatca_certified_at) == (False, None, None)


@pytest.mark.asyncio
async def test_writes_are_tenant_scoped(service_session: AsyncSession) -> None:
    s = service_session
    tenant_a = await _mk_tenant(s)
    tenant_b = await _mk_tenant(s)
    adviser_b = await _mk_user(s, tenant_b)

    contact_a = int((await contacts.create_contact(s, tenant_id=tenant_a, type="individual"))["id"])

    # A firm cannot assign another firm's staff to its client...
    with pytest.raises(LookupError):
        await contacts.assign_adviser(
            s, tenant_id=tenant_a, contact_id=contact_a, adviser_id=adviser_b
        )

    # ...nor touch a contact that isn't its own (tenant_b claiming contact_a).
    with pytest.raises(LookupError):
        await contacts.update_contact(
            s, tenant_id=tenant_b, contact_id=contact_a, fields={"surname": "Nope"}
        )
    with pytest.raises(LookupError):
        await contacts.link_user(s, tenant_id=tenant_b, contact_id=contact_a, user_id=adviser_b)


# ---- bank details -----------------------------------------------------------------------------
# `crm.contacts.bank_account` is free-form jsonb, which for BANK data is not good enough: a sort
# code keyed as "12 34 5" used to be stored happily and only fail when someone tried to pay against
# it. These pin the single validated door onto the column.


def test_bank_details_are_canonicalised() -> None:
    """Whatever separators the adviser types, one shape is stored — so the value the account page
    renders and the value a payment would be built from are the same string."""
    for keyed in ("12-34-56", "123456", "12 34 56", "12/34/56"):
        out = contacts.normalise_bank_account(
            {"name": "T Caldwell", "sort_code": keyed, "account_number": "12345678"}
        )
        assert out == {
            "name": "T Caldwell",
            "sort_code": "12-34-56",
            "account_number": "12345678",
        }


def test_a_seven_digit_account_number_is_padded() -> None:
    """The Bacs convention: a bank quoting seven digits means a leading zero. Storing seven would
    make the number fail validation wherever it is next used."""
    out = contacts.normalise_bank_account(
        {"name": "T Caldwell", "sort_code": "123456", "account_number": "1234567"}
    )
    assert out is not None
    assert out["account_number"] == "01234567"


def test_method_defaults_to_bacs_and_is_not_written() -> None:
    """BACS is free and the default — a firm need never state it, so an absent or explicit
    `bacs` method is not stored, the same convention `bank` already follows."""
    for method in (None, "", "bacs", "BACS"):
        out = contacts.normalise_bank_account(
            {
                "name": "T Caldwell",
                "sort_code": "123456",
                "account_number": "12345678",
                "method": method,
            }
        )
        assert out == {
            "name": "T Caldwell",
            "sort_code": "12-34-56",
            "account_number": "12345678",
        }


def test_faster_payment_is_stored_when_chosen() -> None:
    out = contacts.normalise_bank_account(
        {
            "name": "T Caldwell",
            "sort_code": "123456",
            "account_number": "12345678",
            "method": "faster_payment",
        }
    )
    assert out is not None
    assert out["method"] == "faster_payment"


def test_normalise_bank_account_itself_does_not_gate_on_method() -> None:
    """The strict bacs-or-faster-payment check is `set_bank_account`'s, not this shared core's:
    the account-level door (`bank_accounts.normalise_entry`) calls this function for the core
    fields alone and carries a WIDER method vocabulary of its own (CHAPS, SWIFT, Direct Debit…)
    through the same dict, validating it itself afterwards — this must not raise on that."""
    out = contacts.normalise_bank_account(
        {
            "name": "T Caldwell",
            "sort_code": "123456",
            "account_number": "12345678",
            "method": "cheque",
        }
    )
    assert out == {
        "name": "T Caldwell",
        "sort_code": "12-34-56",
        "account_number": "12345678",
    }


@pytest.mark.asyncio
async def test_set_bank_account_refuses_a_method_the_client_door_does_not_offer(
    service_session: AsyncSession,
) -> None:
    """A person's own bank details offer only BACS or Faster Payment — the wider vocabulary
    (CHAPS, SWIFT, Direct Debit…) belongs to the account-level door, not this one."""
    s = service_session
    tenant = await _mk_tenant(s)
    cid = int((await contacts.create_contact(s, tenant_id=tenant, type="individual"))["id"])
    with pytest.raises(ValueError, match="Payment method"):
        await contacts.set_bank_account(
            s,
            tenant_id=tenant,
            contact_id=cid,
            bank_account={
                "name": "T Caldwell",
                "sort_code": "123456",
                "account_number": "12345678",
                "method": "cheque",
            },
        )


def test_international_rows_carry_no_method_even_if_one_is_posted() -> None:
    """An IBAN row always reaches SEI over SWIFT — there is nothing to choose, so a stray
    `method` on an international payload is simply not carried through."""
    out = contacts.normalise_bank_account(
        {
            "name": "T Caldwell",
            "iban": "GB82 WEST 1234 5698 7654 32",
            "bic": "BARCGB22",
            "method": "faster_payment",
        }
    )
    assert out == {"name": "T Caldwell", "iban": "GB82WEST12345698765432", "bic": "BARCGB22"}


def test_a_blank_form_clears_rather_than_storing_an_empty_object() -> None:
    assert contacts.normalise_bank_account(None) is None
    assert contacts.normalise_bank_account({}) is None
    assert (
        contacts.normalise_bank_account(
            {"name": "  ", "bank": "", "sort_code": None, "account_number": ""}
        )
        is None
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"name": "A B", "sort_code": "12345", "account_number": "12345678"}, "sort code"),
        ({"name": "A B", "sort_code": "1234567", "account_number": "12345678"}, "sort code"),
        ({"name": "A B", "sort_code": "12-34-5X", "account_number": "12345678"}, "sort code"),
        ({"name": "A B", "sort_code": "123456", "account_number": "123456"}, "account number"),
        ({"name": "A B", "sort_code": "123456", "account_number": "1234567890"}, "account number"),
        ({"name": "", "sort_code": "123456", "account_number": "12345678"}, "account holder"),
        ({"name": "A B123", "sort_code": "123456", "account_number": "12345678"}, "letters"),
        ({"name": "A B!", "sort_code": "123456", "account_number": "12345678"}, "letters"),
    ],
)
def test_a_malformed_shape_is_refused_and_the_message_names_the_field(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message) as exc:
        contacts.normalise_bank_account(payload)
    # Never echo bank data back: the message names the field, not what was keyed.
    for digits in ("12345", "1234567890", "12-34-5X"):
        assert digits not in str(exc.value)


@pytest.mark.parametrize("name", ["O'Brien", "Anne-Marie Smith", "François Müller"])
def test_an_accented_hyphenated_or_apostrophised_name_is_accepted(name: str) -> None:
    """The rule is "no digits or symbols", not "ASCII letters only" — a real name legitimately
    carries an apostrophe, a hyphen or an accent."""
    out = contacts.normalise_bank_account(
        {"name": name, "sort_code": "123456", "account_number": "12345678"}
    )
    assert out is not None
    assert out["name"] == name


class TestUpdateContactNameValidation:
    """`UpdateContact.first_name`/`.surname` reject the same characters `normalise_bank_account`
    does — one rule, shared, so a contact's own name and their bank account holder's name can't
    disagree about what counts as a name."""

    def test_digits_or_symbols_are_refused(self) -> None:
        for bad in ("Andrew123", "Andrew!", "3M"):
            with pytest.raises(ValueError, match="letters"):
                UpdateContact(first_name=bad)
            with pytest.raises(ValueError, match="letters"):
                UpdateContact(surname=bad)

    def test_a_real_name_is_accepted(self) -> None:
        for good in ("O'Brien", "Anne-Marie", "François"):
            assert UpdateContact(first_name=good).first_name == good
            assert UpdateContact(surname=good).surname == good

    def test_absent_and_blank_are_left_alone(self) -> None:
        """`exclude_unset` is what makes an absent field mean "untouched" — the validator must
        not turn that into a refusal, and a blank string (clearing the field) is not a name to
        validate at all."""
        assert UpdateContact().first_name is None
        assert UpdateContact(first_name=None).first_name is None
        assert UpdateContact(first_name="").first_name == ""


# Wikipedia's GB example — compact, spaced, and mixed-case all store the same string.
_IBAN = "GB82WEST12345698765432"


def test_an_international_account_is_stored_compact() -> None:
    out = contacts.normalise_bank_account(
        {"name": "T Caldwell", "iban": "GB82 WEST 1234 5698 7654 32", "bic": "barcgb22"}
    )
    assert out == {"name": "T Caldwell", "iban": _IBAN, "bic": "BARCGB22"}


def test_scheme_iban_names_iban_not_the_sort_code() -> None:
    """The form posts the rail it is showing. Without that, a switch to International with IBAN
    still blank would complain about a sort code the adviser is not looking at."""
    with pytest.raises(ValueError, match="IBAN") as exc:
        contacts.normalise_bank_account({"scheme": "iban", "name": "T Caldwell", "bic": "BARCGB22"})
    assert "sort code" not in str(exc.value).lower()


def test_a_checksum_invalid_iban_is_refused_without_echoing_it() -> None:
    invalid_iban = "GB00WEST12345698765432"
    with pytest.raises(ValueError, match="IBAN") as exc:
        contacts.normalise_bank_account(
            {"name": "T Caldwell", "iban": invalid_iban, "bic": "BARCGB22"}
        )
    assert invalid_iban not in str(exc.value)


@pytest.mark.asyncio
async def test_set_bank_account_round_trips_and_is_tenant_scoped(
    service_session: AsyncSession,
) -> None:
    s = service_session
    tenant = await _mk_tenant(s)
    other = await _mk_tenant(s)
    cid = int((await contacts.create_contact(s, tenant_id=tenant, type="individual"))["id"])

    saved = await contacts.set_bank_account(
        s,
        tenant_id=tenant,
        contact_id=cid,
        bank_account={
            "name": "T Caldwell",
            "bank": "Barclays",
            "sort_code": "20 00 00",
            "account_number": "87654321",
        },
    )
    assert saved["bank_account"] == {
        "name": "T Caldwell",
        "bank": "Barclays",
        "sort_code": "20-00-00",
        "account_number": "87654321",
    }

    # Blanking every field clears the column rather than storing {}.
    cleared = await contacts.set_bank_account(
        s, tenant_id=tenant, contact_id=cid, bank_account=None
    )
    assert cleared["bank_account"] is None

    with pytest.raises(LookupError):
        await contacts.set_bank_account(
            s,
            tenant_id=other,
            contact_id=cid,
            bank_account={"name": "X", "sort_code": "123456", "account_number": "12345678"},
        )


@pytest.mark.asyncio
async def test_set_bank_account_round_trips_a_faster_payment_choice(
    service_session: AsyncSession,
) -> None:
    s = service_session
    tenant = await _mk_tenant(s)
    cid = int((await contacts.create_contact(s, tenant_id=tenant, type="individual"))["id"])

    saved = await contacts.set_bank_account(
        s,
        tenant_id=tenant,
        contact_id=cid,
        bank_account={
            "name": "T Caldwell",
            "sort_code": "20 00 00",
            "account_number": "87654321",
            "method": "faster_payment",
        },
    )
    assert saved["bank_account"] == {
        "name": "T Caldwell",
        "sort_code": "20-00-00",
        "account_number": "87654321",
        "method": "faster_payment",
    }

    # Reverting to BACS (the default) CLEARS the stored choice rather than leaving it stranded —
    # a whole-object replace, so an absent `method` on the next save means BACS.
    reverted = await contacts.set_bank_account(
        s,
        tenant_id=tenant,
        contact_id=cid,
        bank_account={
            "name": "T Caldwell",
            "sort_code": "20 00 00",
            "account_number": "87654321",
        },
    )
    assert reverted["bank_account"] == {
        "name": "T Caldwell",
        "sort_code": "20-00-00",
        "account_number": "87654321",
    }


@pytest.mark.asyncio
async def test_the_general_patch_validates_bank_details_too(
    service_session: AsyncSession,
) -> None:
    """There must be exactly one door onto the column: whichever field list a caller sends, the
    same normalisation runs."""
    s = service_session
    tenant = await _mk_tenant(s)
    cid = int((await contacts.create_contact(s, tenant_id=tenant, type="individual"))["id"])
    with pytest.raises(ValueError, match="sort code"):
        await contacts.update_contact(
            s,
            tenant_id=tenant,
            contact_id=cid,
            fields={"bank_account": {"name": "A B", "sort_code": "1", "account_number": "1"}},
        )
    saved = await contacts.update_contact(
        s,
        tenant_id=tenant,
        contact_id=cid,
        fields={
            "bank_account": {
                "name": "A B",
                "sort_code": "112233",
                "account_number": "44556677",
            }
        },
    )
    assert saved["bank_account"]["sort_code"] == "11-22-33"  # type: ignore[index]


# ── Vulnerability (FCA FG21/1) ────────────────────────────────────────────────────────────────


class TestNormaliseVulnerability:
    """The validator behind PUT /crm/contacts/{id}/vulnerability. It gates a banner on the
    client's profile, so a half-recorded state must be impossible rather than merely unlikely."""

    def test_the_four_drivers_are_the_fcas(self) -> None:
        assert contacts.VULNERABILITY_REASONS == (
            "health",
            "life_event",
            "resilience",
            "capability",
        )

    def test_records_multiple_drivers(self) -> None:
        """They compound — a bereavement that leaves someone unable to absorb a loss is a life
        event AND a resilience driver, and forcing a single 'main' one loses that."""
        got = contacts.normalise_vulnerability(
            {"is_vulnerable": True, "reasons": ["resilience", "life_event"], "is_temporary": True}
        )
        assert got["reasons"] == ["life_event", "resilience"]
        assert got["is_temporary"] is True

    def test_reasons_come_back_in_the_fcas_order_not_the_callers(self) -> None:
        """Stable output: two records with the same drivers compare equal, and a badge row does
        not reshuffle between saves."""
        a = contacts.normalise_vulnerability(
            {"is_vulnerable": True, "reasons": ["capability", "health"]}
        )
        b = contacts.normalise_vulnerability(
            {"is_vulnerable": True, "reasons": ["health", "capability"]}
        )
        assert a["reasons"] == b["reasons"] == ["health", "capability"]

    def test_duplicates_collapse(self) -> None:
        got = contacts.normalise_vulnerability(
            {"is_vulnerable": True, "reasons": ["health", "health"]}
        )
        assert got["reasons"] == ["health"]

    def test_a_single_reason_may_arrive_as_a_bare_string(self) -> None:
        """A one-option form control posts a string, not a list."""
        got = contacts.normalise_vulnerability({"is_vulnerable": True, "reasons": "health"})
        assert got["reasons"] == ["health"]

    def test_vulnerable_with_no_driver_is_refused(self) -> None:
        """The flag alone is what 0053 had, and it does not support the judgement the policy
        asks for: 'vulnerable' with no reason tells an adviser nothing about what to change."""
        with pytest.raises(ValueError, match="at least one reason"):
            contacts.normalise_vulnerability({"is_vulnerable": True, "reasons": []})

    def test_an_unknown_driver_is_named_in_the_error(self) -> None:
        with pytest.raises(ValueError, match="vibes"):
            contacts.normalise_vulnerability({"is_vulnerable": True, "reasons": ["vibes"]})

    def test_clearing_drops_the_drivers_with_it(self) -> None:
        """Matches the 0054 CHECK: a reason without the flag would read as 'not vulnerable' to
        anything testing the boolean, so clearing has to clear all of it."""
        got = contacts.normalise_vulnerability(
            {
                "is_vulnerable": False,
                "reasons": ["health"],
                # 0116's characteristics clear with the rest — its own CHECK enforces that at
                # the database, and this is where the intention is made coherent first.
                "characteristics": ["bereavement"],
                "notes": "stale",
            }
        )
        assert got == {
            "is_vulnerable": False,
            "reasons": None,
            "characteristics": None,
            "is_temporary": None,
            "notes": None,
            "recorded_at": None,
        }

    def test_not_established_is_a_third_state(self) -> None:
        """None is not False: 'we have not worked out whether this passes' is a real answer and
        is not the same as 'permanent'."""
        got = contacts.normalise_vulnerability({"is_vulnerable": True, "reasons": ["health"]})
        assert got["is_temporary"] is None

    def test_blank_notes_normalise_to_none(self) -> None:
        got = contacts.normalise_vulnerability(
            {"is_vulnerable": True, "reasons": ["health"], "notes": "   "}
        )
        assert got["notes"] is None

    def test_labels_cover_every_driver(self) -> None:
        """The portal renders these words; an unlabelled driver would show as a raw enum."""
        for reason in contacts.VULNERABILITY_REASONS:
            assert contacts.reason_label(reason)[0].isupper()
        assert contacts.reason_label("life_event") == "Life event"


class TestDeclaredVocabularies:
    """The accepted values live in three places — the pydantic model, `contacts.py` and a CHECK
    constraint in the migration. They must be one list, or a value the API accepts becomes a 500
    at the database (or worse, a value the DB accepts is refused by the API for no visible
    reason). The migration SQL is the source of truth; this reads it."""

    @staticmethod
    def _check_values(sql: str, constraint: str) -> set[str]:
        """The quoted literals inside one named CHECK constraint."""
        import re

        block = sql.split(constraint, 1)[1]
        # Up to the end of that constraint's parenthesised expression.
        block = block.split("),\n", 1)[0]
        return set(re.findall(r"'([a-z_0-9]+)'", block))

    def _sql(self, name: str) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "migrations" / "sql"
        return (root / name).read_text(encoding="utf-8")

    def test_seeded_form_rewrite_runs_as_service_role(self) -> None:
        """form_fields is FORCE RLS. An UPDATE as migrator reports success and changes nothing,
        which is how 0239 left every seeded journey on the old title list."""
        original = self._sql("0239_contact_closed_lists.sql")
        assert original.index("SET LOCAL ROLE service_role") < original.index(
            "documents.form_fields"
        )
        follow = self._sql("0241_seeded_form_lists.sql")
        assert follow.index("SET LOCAL ROLE service_role") < follow.index(
            "UPDATE documents.form_fields"
        )
        assert "RESET ROLE" in follow

    def test_genders_match_the_database(self) -> None:
        sql = self._sql("0239_contact_closed_lists.sql")
        assert self._closed_check(sql, "contacts_gender_check") == set(contacts.GENDERS)
        sink = self._sink_sql()
        assert self._closed_check(sink, "CASE WHEN s.gender") == set(contacts.GENDERS)

    def test_marital_statuses_match_the_database(self) -> None:
        sql = self._sql("0239_contact_closed_lists.sql")
        assert self._closed_check(sql, "contacts_marital_status_check") == set(
            contacts.MARITAL_STATUSES
        )
        sink = self._sink_sql()
        assert self._closed_check(sink, "CASE WHEN s.marital_status") == set(
            contacts.MARITAL_STATUSES
        )

    @staticmethod
    def _closed_check(sql: str, marker: str) -> set[str]:
        """The quoted literals of the first ``IN (...)`` after ``marker``."""
        import re

        block = sql.split(marker, 1)[1]
        match = re.search(r"IN\s*\(([^)]*)\)", block)
        assert match is not None
        return set(re.findall(r"'([a-z_0-9]+)'", match.group(1)))

    @staticmethod
    def _sink_sql() -> str:
        from pathlib import Path

        return (
            Path(__file__).resolve().parents[3] / "pkgs/book-sink/src/book_sink/entities.py"
        ).read_text(encoding="utf-8")

    def test_employment_statuses_match_the_database(self) -> None:
        sql = self._sql("0053_contact_pension_facts.sql")
        assert self._check_values(sql, "contacts_employment_status_check") == set(
            contacts.EMPLOYMENT_STATUSES
        )

    def test_vulnerability_reasons_match_the_database(self) -> None:
        sql = self._sql("0054_contact_vulnerability.sql")
        assert self._check_values(sql, "contacts_vulnerability_reasons_check") == set(
            contacts.VULNERABILITY_REASONS
        )

    @pytest.mark.pkgs_ui_pin
    def test_vulnerability_reasons_match_the_shared_ui(self) -> None:
        """Three copies of the FCA's four drivers have to agree: the DB CHECK, this module, and
        `pkgs/ui` book/clients, which renders the badge an adviser reads. The API container ships
        without pkgs/ui, so nothing but a test can hold the last pair together -- the same reason
        the block-contract is pinned from both languages. A driver spelled differently in the
        component would render as the raw slug (`life_event`) beside the correctly-spelled ones."""
        import re
        from pathlib import Path

        types_ts = (
            Path(__file__).resolve().parents[3] / "pkgs/ui/src/lib/components/book/clients/types.ts"
        ).read_text(encoding="utf-8")
        union = types_ts.split("export type VulnerabilityReason =", 1)[1].split(";", 1)[0]
        labels = types_ts.split("VULNERABILITY_LABELS: Record<VulnerabilityReason, string> = {", 1)[
            1
        ].split("};", 1)[0]
        expected = set(contacts.VULNERABILITY_REASONS)
        assert set(re.findall(r"'([a-z_]+)'", union)) == expected
        assert set(re.findall(r"^\s*([a-z_]+):", labels, re.M)) == expected

    def test_tax_reliefs_match_the_database(self) -> None:
        sql = self._sql("0129_contact_kyc_facts.sql")
        assert self._check_values(sql, "contacts_tax_relief_check") == set(contacts.TAX_RELIEFS)

    def test_vulnerability_characteristics_match_the_database(self) -> None:
        """All 24, and the CHECK is an `<@` containment rather than an `IN`, so the helper's
        literal scan is the right reader for it."""
        sql = self._sql("0129_contact_kyc_facts.sql")
        found = self._check_values(sql, "contacts_vulnerability_characteristics_check")
        assert found == set(contacts.VULNERABILITY_CHARACTERISTICS)
        assert len(found) == 24

    def test_every_characteristic_sits_under_exactly_one_driver(self) -> None:
        """The grouping is presentational, but it still has to PARTITION: a characteristic under
        two drivers would render twice, and one under none would be storable and unreachable."""
        grouped = [
            slug
            for driver in contacts.VULNERABILITY_REASONS
            for slug in contacts.CHARACTERISTICS_BY_DRIVER[driver]
        ]
        assert sorted(grouped) == sorted(contacts.VULNERABILITY_CHARACTERISTICS)
        assert len(grouped) == len(set(grouped)), "a characteristic appears under two drivers"
        assert set(contacts.CHARACTERISTICS_BY_DRIVER) == set(contacts.VULNERABILITY_REASONS)

    def test_every_characteristic_has_a_label(self) -> None:
        for slug in contacts.VULNERABILITY_CHARACTERISTICS:
            assert contacts.characteristic_label(slug) != slug, f"{slug} has no label"

    @pytest.mark.pkgs_ui_pin
    def test_the_new_vocabularies_match_the_shared_ui(self) -> None:
        """Same rule as the drivers: the API container ships without pkgs/ui, so only a test holds
        the two together, and a mis-spelled slug renders as the raw value beside its correctly
        labelled neighbours. Four copies of the 24 characteristics would be unmanageable, which is
        why the platform app RE-EXPORTS the union from pkgs/ui rather than declaring a fourth."""
        import re
        from pathlib import Path

        types_ts = (
            Path(__file__).resolve().parents[3] / "pkgs/ui/src/lib/components/book/clients/types.ts"
        ).read_text(encoding="utf-8")

        chars = types_ts.split("export type VulnerabilityCharacteristic =", 1)[1].split(";", 1)[0]
        assert set(re.findall(r"'([a-z_]+)'", chars)) == set(contacts.VULNERABILITY_CHARACTERISTICS)

        char_labels = types_ts.split(
            "VULNERABILITY_CHARACTERISTIC_LABELS: Record<VulnerabilityCharacteristic, string> = {",
            1,
        )[1].split("};", 1)[0]
        assert set(re.findall(r"^\s*([a-z_]+):", char_labels, re.M)) == set(
            contacts.VULNERABILITY_CHARACTERISTICS
        )

        reliefs = types_ts.split("export type TaxRelief =", 1)[1].split(";", 1)[0]
        assert set(re.findall(r"'([a-z_]+)'", reliefs)) == set(contacts.TAX_RELIEFS)

        grouping = types_ts.split("CHARACTERISTICS_BY_DRIVER: Record<", 1)[1].split("};", 1)[0]
        for driver, slugs in contacts.CHARACTERISTICS_BY_DRIVER.items():
            block = grouping.split(f"{driver}: [", 1)[1].split("]", 1)[0]
            assert set(re.findall(r"'([a-z_]+)'", block)) == set(slugs), (
                f"the {driver} group differs between Python and pkgs/ui"
            )

    def test_retirement_age_bounds_match_the_database(self) -> None:
        sql = self._sql("0053_contact_pension_facts.sql")
        assert "BETWEEN 50 AND 75" in sql
        assert contacts.RETIREMENT_AGE_RANGE == (50, 75)

    def test_the_patch_model_accepts_exactly_those_values(self) -> None:
        """The pydantic Literals are written out (they cannot reference a runtime tuple), so this
        is what stops them drifting from `contacts.py`."""
        import typing

        def literals(field: str) -> set[str]:
            annotation = UpdateContact.model_fields[field].annotation
            return {
                arg
                for union_member in typing.get_args(annotation)
                for arg in typing.get_args(union_member)
                if isinstance(arg, str)
            }

        assert literals("gender") == set(contacts.GENDERS)
        assert literals("marital_status") == set(contacts.MARITAL_STATUSES)
        assert literals("employment_status") == set(contacts.EMPLOYMENT_STATUSES)
        assert literals("tax_relief") == set(contacts.TAX_RELIEFS)

    @pytest.mark.pkgs_ui_pin
    def test_closed_lists_match_the_form_vocabulary(self) -> None:
        """The field types `gender`, `marital` and `employment` offer these values and no others.

        The API container does not import pkgs/ui, so this reads the TypeScript. Title is
        free text on the column and is pinned from the onboarding seed instead.
        """
        import re
        from pathlib import Path

        ts = (
            Path(__file__).resolve().parents[3] / "pkgs/ui/src/lib/forms/vocabularies.ts"
        ).read_text(encoding="utf-8")

        def values(const: str) -> list[str]:
            block = ts.split(f"export const {const} = [", 1)[1].split("] as const", 1)[0]
            return re.findall(r"value: '([^']*)'", block)

        assert values("GENDER_OPTIONS") == list(contacts.GENDERS)
        assert values("MARITAL_OPTIONS") == list(contacts.MARITAL_STATUSES)
        assert values("EMPLOYMENT_OPTIONS") == list(contacts.EMPLOYMENT_STATUSES)

    def test_the_free_text_kyc_fields_are_patchable(self) -> None:
        """0116's columns are only reachable if the model accepts them AND `_UPDATABLE` allows
        them — two lists, and a field in one but not the other saves nothing while answering 200.
        `occupation` is the one that matters most: it is the field the whole employment-status
        distinction exists for."""
        for field in (
            "occupation",
            "occupation_detail",
            "country_of_birth",
            "town_of_birth",
            "source_of_funds",
            "source_of_funds_detail",
            "tax_relief",
        ):
            assert field in UpdateContact.model_fields, f"{field} is not on the patch model"
            assert field in contacts._UPDATABLE, f"{field} is not in _UPDATABLE"

    def test_vulnerability_stays_off_the_general_patch(self) -> None:
        """It has its own validated door (0054's posture for special-category-adjacent data), so
        the characteristics must not become patchable as a side effect of 0116."""
        for field in (
            "is_vulnerable",
            "vulnerability_reasons",
            "vulnerability_characteristics",
        ):
            assert field not in UpdateContact.model_fields
            assert field not in contacts._UPDATABLE


class TestTheAllowlist:
    """`_UPDATABLE` is DERIVED from `UpdateContact` now, so the two lists cannot drift.

    They were one contract in two files, kept in step by set equality in both directions — a test
    that detected the drift rather than preventing it, and whose two exception sets both held the
    same single name. Only one half ever failed loudly: Pydantic's default is `extra='ignore'`, so
    a column in `_UPDATABLE` and not on the model is dropped on the way IN, never reaches the
    allowlist to be refused, and the PATCH answers 200 with the record unchanged. That is what
    happened to 0126's `employer_name` and `relevant_uk_earnings`.

    What is left to assert is what the derivation cannot answer: that every name on the model is a
    real COLUMN. A typo there now enters `_UPDATABLE` unchallenged and lands as a SQL error at
    request time rather than as a silent 200 — better, and still not something to find in the wild.
    """

    def test_it_is_the_model_plus_the_one_deliberate_extra(self) -> None:
        # `bank_account` is the deliberate column with no model field (its own door).
        # `justification` is the reverse: a model field that is transport only, never a column.
        assert contacts._UPDATABLE == (
            frozenset(UpdateContact.model_fields) - {"justification"}
        ) | {"bank_account"}

    def test_bank_account_is_patchable_but_not_offered_on_the_model(self) -> None:
        """Its own validated door (`PUT …/bank-account`), through this same SET builder — so it is
        in the allowlist and deliberately absent from the details form's model."""
        assert "bank_account" in contacts._UPDATABLE
        assert "bank_account" not in UpdateContact.model_fields

    async def test_every_updatable_name_is_a_real_column(
        self, service_session: AsyncSession
    ) -> None:
        """Against the real catalogue, because this is the failure the derivation introduced: a
        field name that is not a column used to be caught by set equality against a hand-written
        list of columns, and is now only caught here."""
        rows = await service_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = 'crm' AND table_name = 'contacts'"
            )
        )
        columns = {row[0] for row in rows}
        assert columns, "precondition: the contacts table should have columns"
        assert contacts._UPDATABLE <= columns, sorted(contacts._UPDATABLE - columns)

    def test_the_sections_the_client_record_edits_are_all_reachable(self) -> None:
        """The five sections' fields, named. A derivation catches a DRIFT; this catches a field
        that was never wired at all, which is the failure the restructure actually shipped."""
        for field in (
            # Contact
            "title",
            "first_name",
            "surname",
            "email",
            "mobile",
            "phone",
            "address",
            # Personal
            "dob",
            "gender",
            "nationality",
            "tax_residency",
            "national_identifier",
            "national_identifier_type",
            "us_person",
            # Pension
            "marital_status",
            "employment_status",
            "retirement_age",
            "relevant_uk_earnings",
            "employer_contributions",
            "tax_relief",
            "flexi_access_taken",
            "flexi_access_date",
            # Employer
            "employer_name",
            "employer_registration_number",
            "employer_address",
            # Compliance
            "source_of_wealth",
            "source_of_wealth_detail",
            "occupation",
            "occupation_detail",
            "country_of_birth",
            "town_of_birth",
        ):
            assert field in UpdateContact.model_fields, f"{field} is not on UpdateContact"
            assert field in contacts._UPDATABLE, f"{field} is not in _UPDATABLE"


class TestTheArrayColumns:
    """`nationality` and `tax_residency` are `text[]`, and a list has to survive the SET builder.

    Worth a real-Postgres test rather than a claim: the builder binds every column the same way
    (`col = :col`), and psycopg adapting a Python list to an array is the only reason that works
    for these two. A string would be adapted as a scalar and fail the column type. Both controls
    submit repeated values via `getAll`, so the SET builder always sees a list.
    """

    async def _client(self, session: AsyncSession) -> tuple[int, int]:
        tenant_id = await _mk_tenant(session)
        created = await contacts.create_contact(
            session, tenant_id=tenant_id, type="individual", surname="Halloway"
        )
        return tenant_id, int(created["id"])

    async def test_a_list_of_codes_round_trips(self, service_session: AsyncSession) -> None:
        tenant_id, contact_id = await self._client(service_session)
        await contacts.update_contact(
            service_session,
            tenant_id=tenant_id,
            contact_id=contact_id,
            fields={"nationality": ["GB", "IE"], "tax_residency": ["GB"]},
        )
        row = (
            await service_session.execute(
                text("SELECT nationality, tax_residency FROM crm.contacts WHERE id = :c"),
                {"c": contact_id},
            )
        ).one()
        assert list(row[0]) == ["GB", "IE"]
        assert list(row[1]) == ["GB"]

    async def test_an_empty_list_clears_it(self, service_session: AsyncSession) -> None:
        # Distinct from absent: a client who has renounced a nationality has an EMPTY set, and the
        # form has to be able to say so.
        tenant_id, contact_id = await self._client(service_session)
        await contacts.update_contact(
            service_session,
            tenant_id=tenant_id,
            contact_id=contact_id,
            fields={"nationality": ["GB"]},
        )
        await contacts.update_contact(
            service_session, tenant_id=tenant_id, contact_id=contact_id, fields={"nationality": []}
        )
        stored = (
            await service_session.execute(
                text("SELECT nationality FROM crm.contacts WHERE id = :c"), {"c": contact_id}
            )
        ).scalar_one()
        assert list(stored) == []


class TestDeletingANote:
    """A note is the author's own remark, so its author may withdraw it and nobody else may.

    The authorship test is in the WHERE clause rather than a read-then-check, which matters because
    this runs as `service_role` — RLS is not going to catch a mistake. Somebody else's note is a
    404, not a 403: whether it exists is not something a caller who may not touch it should learn.
    """

    async def _staff(self, session: AsyncSession, tenant_id: int, workos: str) -> None:
        """A login with a KNOWN WorkOS id — `_mk_user` mints its own, and the delete rule keys on
        exactly this value."""
        suffix = uuid.uuid4().hex[:10]
        await session.execute(
            text(
                "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                "VALUES (:uid, :t, :email, :wos, true)"
            ),
            {
                "uid": f"usr_{suffix}",
                "t": tenant_id,
                "email": f"{suffix}@firm.test",
                "wos": workos,
            },
        )

    async def _noted(self, session: AsyncSession) -> tuple[int, int, str, str]:
        """`workos_user_id` is unique across the whole table and `service_session` is SHARED, so a
        fixed id collides the second time this runs (order-independence is this module's own rule —
        see its header)."""
        workos = f"wos_author_{uuid.uuid4().hex[:8]}"
        tenant_id = await _mk_tenant(session)
        await self._staff(session, tenant_id, workos)
        created = await contacts.create_contact(
            session, tenant_id=tenant_id, type="individual", surname="Halloway"
        )
        contact_id = int(created["id"])
        note = await contacts.add_note(
            session,
            tenant_id=tenant_id,
            contact_id=contact_id,
            author_workos_id=workos,
            body="Reviewed at the annual meeting.",
        )
        # The insert returns the same author the contact-detail join would, so a portal can
        # prepend the note without waiting for that load. Staff in this fixture have no name,
        # so the fallback is their email.
        assert note["author"] and "@firm.test" in str(note["author"])
        assert note["body"] == "Reviewed at the annual meeting."
        return tenant_id, contact_id, str(note["uid"]), workos

    async def test_the_author_may_delete_their_own(self, service_session: AsyncSession) -> None:
        tenant_id, contact_id, note_uid, workos = await self._noted(service_session)
        await contacts.delete_note(
            service_session,
            tenant_id=tenant_id,
            contact_id=contact_id,
            note_uid=note_uid,
            author_workos_id=workos,
        )
        remaining = (
            await service_session.execute(
                text("SELECT count(*) FROM crm.contact_notes WHERE uid = :n"), {"n": note_uid}
            )
        ).scalar_one()
        assert remaining == 0

    async def test_a_colleagues_note_is_not_found(self, service_session: AsyncSession) -> None:
        tenant_id, contact_id, note_uid, _ = await self._noted(service_session)
        colleague = f"wos_colleague_{uuid.uuid4().hex[:8]}"
        await self._staff(service_session, tenant_id, colleague)
        with pytest.raises(LookupError):
            await contacts.delete_note(
                service_session,
                tenant_id=tenant_id,
                contact_id=contact_id,
                note_uid=note_uid,
                author_workos_id=colleague,
            )
        # And it is still there.
        assert (
            await service_session.execute(
                text("SELECT count(*) FROM crm.contact_notes WHERE uid = :n"), {"n": note_uid}
            )
        ).scalar_one() == 1

    async def test_another_firms_note_is_not_found(self, service_session: AsyncSession) -> None:
        _, contact_id, note_uid, workos = await self._noted(service_session)
        other = await _mk_tenant(service_session)
        with pytest.raises(LookupError):
            await contacts.delete_note(
                service_session,
                tenant_id=other,
                contact_id=contact_id,
                note_uid=note_uid,
                author_workos_id=workos,
            )


class TestTheNoteUidBackfill:
    """0127's `uid` back-fill has to work on a database that already HAS notes.

    The suite builds each database from scratch, where `crm.contact_notes` is empty and `SET NOT
    NULL` is satisfied trivially — so the obvious back-fill shape (0111's: add the column, `UPDATE
    ... WHERE uid IS NULL`, then SET NOT NULL) passed every test and failed on the first database
    with a note in it.

    The cause is `FORCE ROW LEVEL SECURITY`: the policies bind the table's OWNER too, so the
    migrator's UPDATE is filtered to the rows its (absent) claims can see and reports `UPDATE 0`,
    while SET NOT NULL validates every row regardless. This asserts the property that fix relies
    on — a note written through the service path comes out with a uid of its own.
    """

    async def test_a_note_written_after_the_migration_has_its_own_uid(
        self, service_session: AsyncSession
    ) -> None:
        tenant_id = await _mk_tenant(service_session)
        created = await contacts.create_contact(
            service_session, tenant_id=tenant_id, type="individual", surname="Halloway"
        )
        contact_id = int(created["id"])
        uids = set()
        for body in ("First note.", "Second note."):
            note = await contacts.add_note(
                service_session,
                tenant_id=tenant_id,
                contact_id=contact_id,
                author_workos_id=f"wos_{uuid.uuid4().hex[:8]}",
                body=body,
            )
            assert note["uid"], "a note with no uid has no address"
            uids.add(note["uid"])
        # DISTINCT, which is what a VOLATILE default buys: a non-volatile one would stamp every
        # existing row with the SAME value and the unique index would refuse the migration.
        assert len(uids) == 2

    def test_the_backfill_does_not_use_a_policy_filtered_update(self) -> None:
        """A source scan, because the failure mode is invisible to an empty-database test."""
        sql = (
            pathlib.Path(__file__).resolve().parents[1] / "migrations/sql/0139_client_sections.sql"
        ).read_text(encoding="utf-8")
        notes = sql[sql.index("crm.contact_notes gains a uid") :]
        assert "ADD COLUMN IF NOT EXISTS uid text NOT NULL DEFAULT" in notes
        assert "UPDATE crm.contact_notes SET uid" not in notes


class TestThePassportPairIsReachable:
    """0129 added `passport_number`/`passport_country` and wired neither end.

    Present only in that migration, its down-revision and a COMMENT — not on the model, not in the
    allowlist, not in the record's projection, and no SEI path writing them. SEI enrolment asks for
    the passport and publishes it straight back as `COUNTRY_OF_CITIZENSHIP_FIRST_PASSPORT`, so the
    round trip exists; what did not was any way to record it. This is 0127's own finding a second
    time — a field nobody can see is a field nobody fills in, the way `relevant_uk_earnings` went
    five migrations with no editor while the relievable-limit check ran on the £3,600 floor.
    """

    def test_both_columns_are_patchable(self) -> None:
        for field in ("passport_number", "passport_country"):
            assert field in UpdateContact.model_fields
            assert field in contacts._UPDATABLE

    async def test_a_patch_actually_stores_them(self, service_session: AsyncSession) -> None:
        """Against real Postgres, because the failure this pair had is a PATCH that answers 200 and
        saves nothing — which is what happens when only one of the two allowlists carries a name.
        """
        tenant_id = await _mk_tenant(service_session)
        created = await contacts.create_contact(
            service_session, tenant_id=tenant_id, type="individual", name="Ada Client"
        )
        await contacts.update_contact(
            service_session,
            tenant_id=tenant_id,
            contact_id=int(created["id"]),
            fields={"passport_number": "5386201931", "passport_country": "GB"},
        )
        row = (
            await service_session.execute(
                text(
                    "SELECT passport_number, passport_country FROM crm.contacts"
                    " WHERE id = :id AND tenant_id = :t"
                ),
                {"id": int(created["id"]), "t": tenant_id},
            )
        ).one()
        assert row == ("5386201931", "GB")


class TestSourceOfWealthIsStored:
    """The Compliance card's picker. Allowlisted since 0139, and still the failure mode that
    card can hit: PATCH answers 200 (the field is on both allowlists) while the column is
    unchanged if the SET never ran — which is what the portal reads as "Save did nothing".
    """

    async def test_a_patch_actually_stores_it(self, service_session: AsyncSession) -> None:
        tenant_id = await _mk_tenant(service_session)
        created = await contacts.create_contact(
            service_session, tenant_id=tenant_id, type="individual", surname="Halloway"
        )
        contact_id = int(created["id"])
        await contacts.update_contact(
            service_session,
            tenant_id=tenant_id,
            contact_id=contact_id,
            fields={
                "source_of_wealth": "Other",
                "source_of_wealth_detail": "Sale of a vintage car",
            },
        )
        row = (
            await service_session.execute(
                text(
                    "SELECT source_of_wealth, source_of_wealth_detail FROM crm.contacts"
                    " WHERE id = :id AND tenant_id = :t"
                ),
                {"id": contact_id, "t": tenant_id},
            )
        ).one()
        assert row == ("Other", "Sale of a vintage car")


def _directory_hit() -> bank_check.BankCheck:
    parsed = bank_check.parse_response(
        {
            "valid": True,
            "stateid": 0,
            "sortcode": "100000",
            "accountnumber": "03151060",
            "bankname": "BANK OF ENGLAND",
            "bacs": True,
            "fasterpayments": False,
        },
        sort_code="100000",
        account_number="31510604",
    )
    assert parsed is not None
    return parsed


@pytest.mark.asyncio
async def test_a_confirmed_bank_account_stores_the_directory_name(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _ok(_sort: str, _number: str, **_extra: object) -> bank_check.BankCheck:
        return _directory_hit()

    monkeypatch.setattr(bank_check, "check_uk_account", _ok)
    s = service_session
    tenant = await _mk_tenant(s)
    created = await contacts.create_contact(s, tenant_id=tenant, type="individual", surname="Cole")
    saved = await contacts.set_bank_account(
        s,
        tenant_id=tenant,
        contact_id=int(created["id"]),
        bank_account={
            "name": "Jo Cole",
            "sort_code": "10-00-00",
            "account_number": "31510604",
        },
    )
    assert saved["bank_account"]["bank"] == "BANK OF ENGLAND"
    assert saved["bank_account"]["account_number"] == "03151060"
    assert saved["bank_account"]["name"] == "Jo Cole"


@pytest.mark.asyncio
async def test_an_unchanged_pair_is_not_asked_again_and_keeps_the_bank_name(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def _ok(_sort: str, _number: str, **_extra: object) -> bank_check.BankCheck:
        nonlocal calls
        calls += 1
        return _directory_hit()

    monkeypatch.setattr(bank_check, "check_uk_account", _ok)
    s = service_session
    tenant = await _mk_tenant(s)
    created = await contacts.create_contact(s, tenant_id=tenant, type="individual", surname="Cole")
    contact_id = int(created["id"])
    saved = await contacts.set_bank_account(
        s,
        tenant_id=tenant,
        contact_id=contact_id,
        bank_account={
            "name": "Jo Cole",
            "sort_code": "10-00-00",
            "account_number": "31510604",
        },
    )
    again = await contacts.set_bank_account(
        s,
        tenant_id=tenant,
        contact_id=contact_id,
        bank_account={
            "name": "Jo A Cole",
            "sort_code": saved["bank_account"]["sort_code"],
            "account_number": saved["bank_account"]["account_number"],
        },
    )
    assert calls == 1
    assert again["bank_account"]["name"] == "Jo A Cole"
    assert again["bank_account"]["bank"] == "BANK OF ENGLAND"


@pytest.mark.asyncio
async def test_a_rejected_bank_account_is_not_stored(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _no(_sort: str, _number: str, **_extra: object) -> bank_check.BankCheck:
        parsed = bank_check.parse_response(
            {"valid": False, "stateid": -1}, sort_code="000000", account_number="12345678"
        )
        assert parsed is not None
        return parsed

    monkeypatch.setattr(bank_check, "check_uk_account", _no)
    s = service_session
    tenant = await _mk_tenant(s)
    created = await contacts.create_contact(s, tenant_id=tenant, type="individual", surname="Cole")
    contact_id = int(created["id"])
    bank = {"name": "Jo Cole", "sort_code": "00-00-00", "account_number": "12345678"}
    with pytest.raises(bank_check.BankDetailsRejected, match="isn't recognised"):
        await contacts.set_bank_account(
            s, tenant_id=tenant, contact_id=contact_id, bank_account=bank
        )
    stored = (
        await s.execute(
            text("SELECT bank_account FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
            {"c": contact_id, "t": tenant},
        )
    ).scalar_one()
    assert stored is None


@pytest.mark.asyncio
async def test_an_unreachable_directory_still_saves(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _down(_sort: str, _number: str, **_extra: object) -> None:
        return None

    monkeypatch.setattr(bank_check, "check_uk_account", _down)
    s = service_session
    tenant = await _mk_tenant(s)
    created = await contacts.create_contact(s, tenant_id=tenant, type="individual", surname="Cole")
    saved = await contacts.set_bank_account(
        s,
        tenant_id=tenant,
        contact_id=int(created["id"]),
        bank_account={
            "name": "Jo Cole",
            "sort_code": "00-00-00",
            "account_number": "12345678",
        },
    )
    assert saved["bank_account"]["account_number"] == "12345678"
    assert "bank" not in saved["bank_account"]


@pytest.mark.asyncio
async def test_a_national_insurance_number_is_unique_within_the_firm(
    service_session: AsyncSession,
) -> None:
    s = service_session
    tenant = await _mk_tenant(s)
    other = await _mk_tenant(s)
    first = await contacts.create_contact(s, tenant_id=tenant, type="individual", surname="Cole")
    second = await contacts.create_contact(s, tenant_id=tenant, type="individual", surname="Ada")
    elsewhere = await contacts.create_contact(
        s, tenant_id=other, type="individual", surname="Elsewhere"
    )
    await contacts.update_contact(
        s,
        tenant_id=tenant,
        contact_id=int(first["id"]),
        fields={
            "national_identifier": "qq 12 34 56 c",
            "national_identifier_type": "National Insurance Number",
        },
    )
    stored_ni = (
        await s.execute(
            text("SELECT national_identifier FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
            {"c": int(first["id"]), "t": tenant},
        )
    ).scalar_one()
    assert stored_ni == "QQ123456C"
    with pytest.raises(ValueError, match=national_insurance.IN_USE):
        await contacts.update_contact(
            s,
            tenant_id=tenant,
            contact_id=int(second["id"]),
            fields={
                "national_identifier": "QQ123456C",
                "national_identifier_type": "nino",
            },
        )
    # Another firm may hold the same number. The check is the firm's book, not the platform.
    await contacts.update_contact(
        s,
        tenant_id=other,
        contact_id=int(elsewhere["id"]),
        fields={"national_identifier": "QQ123456C", "national_identifier_type": "nino"},
    )
    other_ni = (
        await s.execute(
            text("SELECT national_identifier FROM crm.contacts WHERE id = :c AND tenant_id = :t"),
            {"c": int(elsewhere["id"]), "t": other},
        )
    ).scalar_one()
    assert other_ni == "QQ123456C"
    assert (
        await national_insurance_in_use(
            s,
            tenant_id=tenant,
            answers={
                "national_identifier": "QQ 12 34 56 C",
                "national_identifier_type": "nino",
            },
            contact_id=int(first["id"]),
        )
        is None
    )
    assert (
        await national_insurance_in_use(
            s,
            tenant_id=tenant,
            answers={
                "national_identifier": "QQ123456C",
                "national_identifier_type": "nino",
            },
            contact_id=int(second["id"]),
        )
        == national_insurance.IN_USE
    )
