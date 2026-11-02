"""A contact's phone survives whatever shape reached the column -- app.contacts.phone_expr.

`crm.contacts.mobile` and `crm.contacts.phone` are text columns written by three providers, and
Seccl publishes its numbers as OBJECTS -- `{"number", "isMobile", "locale"}`. The conformed model
extracts `->> 'number'` now,
but text took the whole object without complaint for as long as it did not, so any book merged
before that fix still holds rows where a number belongs. The failure is SILENT: nothing raises, the
object simply appears on a client's record, in a client-facing email, and in the payload sent to
Morgan Lloyd.

Two properties are pinned here, and the second is the one that makes this worth a test file:

1. an object reaching the column still reads as a number, and
2. a NORMAL number is returned completely untouched.

(2) is the trap. The obvious implementation is `phone::jsonb ->> 'number'`, which throws on every
ordinary value in the column -- so it would trade a cosmetic defect for a 500 on the clients
listing. These cases run the real expression against real Postgres because that is the only way to
tell a working extraction from one that raises: a unit test over a Python string cannot see a cast.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contacts import (
    PHONE_FORMAT_ERROR,
    contact_phone,
    normalise_phone,
    phone_expr,
    validate_contact_phone,
)

#: (what is in the column, what a reader must see). The middle four are the shapes a naive
#: `::jsonb` cast fails on -- a plain number, a blank, an object with no `number`, and text that
#: merely starts like JSON.
CASES: tuple[tuple[str | None, str | None], ...] = (
    ('{"number": "07715215412", "isMobile": true, "locale": "en-GB"}', "07715215412"),
    # THE SHAPE THAT CAUGHT A REAL DIVERGENCE. The object is present and its number is BLANK, which
    # `substring` answers with '' — matched, but empty — where a failed match answers NULL. A bare
    # COALESCE cannot tell those apart, so this used to fall through and return the whole object as
    # the phone, while the Python side collapsed the same value to None. Both say NULL/None now: a
    # blank number is not a number. This case was missing from the suite, which is exactly why the
    # `test_the_python_rule_agrees_with_the_sql` guard below could pass while the two disagreed.
    ('{"number": "", "isMobile": true}', None),
    ('{"number": "", "isMobile": true, "locale": "en-GB"}', None),
    # Key order is the vendor's, not ours, so the pattern must not depend on `number` being first.
    ('{"isMobile": true, "number": "01315550123", "locale": "en-GB"}', "01315550123"),
    ("07715215412", "07715215412"),
    ("+44 7715 215412", "+44 7715 215412"),
    ("", ""),
    ('{"isMobile": true}', '{"isMobile": true}'),
    ("{not json at all", "{not json at all"),
    (None, None),
)


@pytest.mark.parametrize(("stored", "expected"), CASES)
async def test_the_read_expression_yields_a_number_and_never_raises(
    service_session: AsyncSession, stored: str | None, expected: str | None
) -> None:
    s = service_session
    tenant = int(
        (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, 'Phone') RETURNING id"
                ),
                {"uid": f"ten_{uuid.uuid4().hex}", "alias": f"phone-{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()
    )
    contact = int(
        (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (uid, tenant_id, type, mobile) "
                    "VALUES (:uid, :t, 'individual', :phone) RETURNING id"
                ),
                {"uid": uuid.uuid4().hex[:12], "t": tenant, "phone": stored},
            )
        ).scalar_one()
    )

    # The expression exactly as the four readers build it, run by Postgres rather than described.
    read = (
        await s.execute(
            text(
                # noqa on the next line: `phone_expr` returns a literal built in app.contacts
                # from a column name of ours -- no request value reaches it.
                f"SELECT {phone_expr('c.mobile')} AS phone "  # noqa: S608
                "FROM crm.contacts c WHERE c.id = :id"
            ),
            {"id": contact},
        )
    ).scalar_one()
    assert read == expected


@pytest.mark.parametrize(("stored", "expected"), CASES)
def test_the_python_rule_agrees_with_the_sql(stored: str | None, expected: str | None) -> None:
    """`normalise_phone` is for a value already in hand; it must not disagree with the column read.

    Two rules for one question is how half a fix ships: the SQL would clean the listing while a
    caller normalising a provider payload in Python kept writing the object back.
    """
    assert normalise_phone(stored) == expected


def test_the_python_rule_takes_the_object_itself_as_well_as_its_text() -> None:
    # A caller reading a provider payload holds the dict; a caller reading the book holds the text.
    assert normalise_phone({"number": "07715215412", "isMobile": True}) == "07715215412"
    # An object with nothing usable in it is no phone number, not an empty string pretending to be.
    assert normalise_phone({"isMobile": True}) is None
    assert normalise_phone({"number": None}) is None
    assert normalise_phone({"number": ""}) is None


def test_validate_contact_phone_accepts_e164_and_non_uk_national() -> None:
    """UAT-56/69: do not require 11 UK digits. An overseas mobile and E.164 both pass."""
    assert validate_contact_phone("+14155552671") == "+14155552671"
    # Ten digits and a leading 0 is not a UK number: France writes mobiles this way.
    assert validate_contact_phone("0612345678") == "0612345678"
    assert validate_contact_phone("+44 7700 900123") == "+447700900123"
    # A UK trunk prefix is the same number as +44. 078…, 0044… and +440… are one value.
    assert validate_contact_phone("07812 345678") == "+447812345678"
    assert validate_contact_phone("020 7946 0958") == "+442079460958"
    assert validate_contact_phone("00447812345678") == "+447812345678"
    assert validate_contact_phone("+4407812345678") == "+447812345678"
    with pytest.raises(ValueError, match="11 digits"):
        validate_contact_phone("078123456")
    assert validate_contact_phone("") is None
    with pytest.raises(ValueError, match="international format"):
        validate_contact_phone("12345")
    with pytest.raises(ValueError, match="international format"):
        validate_contact_phone("not-a-number")
    assert PHONE_FORMAT_ERROR.startswith("Enter a phone number")


def test_a_bare_number_with_no_plus_and_no_leading_zero_is_refused() -> None:
    """The defect the portal surfaced: `1111111` is not a national number in any plan this book
    uses, and it is not international either — it simply cleared E.164's seven-digit floor and
    stored as a number nobody could ring (the record read it back as "+11 1111 1")."""
    for junk in ("1111111", "7700900123", "123456789"):
        with pytest.raises(ValueError, match="international format"):
            validate_contact_phone(junk)

    # Still accepted, and deliberately: a leading zero is how several numbering plans write a
    # national number, and `+` says which country it belongs to.
    assert validate_contact_phone("0612345678") == "0612345678"
    assert validate_contact_phone("+14155552671") == "+14155552671"


def test_contact_phone_takes_a_string_a_provider_object_or_the_first_of_a_list() -> None:
    """Onboarding still receives a leftover list from a case that answered before the column
    became text. The first usable number is the phone; an empty answer clears it."""
    assert contact_phone("+44 7700 900123") == "+447700900123"
    assert contact_phone({"number": "07715215412", "isMobile": False}) == "+447715215412"
    assert contact_phone([{"label": "home", "number": "0117 900 0000"}]) == "+441179000000"
    assert contact_phone([]) is None
    assert contact_phone(None) is None
