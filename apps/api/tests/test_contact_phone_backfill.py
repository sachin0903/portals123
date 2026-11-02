"""0165's backfill decides where every existing client's number lands, and it cannot be re-run.

Every non-empty legacy `crm.contacts.phone` value is classified exactly once — promoted to
`mobile_phone`, or filed into `phone_numbers` as `landline` or `other` — and there is no second pass
to correct a bad call. A number filed under the wrong LABEL is one click for an adviser; a mobile
that never became the primary mobile is a client the firm stops being able to ring, and nothing on
any screen says why.

So the three expressions that make that decision are pinned here, run by POSTGRES rather than
described — the same posture `test_contact_phone.py` takes for `phone_expr`, and for the same
reason: these are POSIX regexes inside a SQL string, and a Python `re` unit test would not be
running the thing that ships. Each pattern is read OUT of the migration file, so editing the
migration without editing these cases fails rather than silently passing against a stale copy.
"""

from pathlib import Path

import pytest
from sqlalchemy import Engine, text

_MIGRATION = (
    Path(__file__).parents[1] / "migrations/sql/0164_contact_phone_numbers.sql"
).read_text(encoding="utf-8")

#: Lifted verbatim from the migration. `in _MIGRATION` is the whole point of the assertion below:
#: it is what ties these cases to the SQL that actually runs.
UK_MOBILE = r"^(\+?44|0)7\d{9}$"
UK_LANDLINE = r"^(\+?44|0)[123]\d{8,9}$"


def test_the_patterns_here_are_the_ones_the_migration_uses() -> None:
    """Drift guard. Without it the cases below keep passing against a pattern nobody ships."""
    assert UK_MOBILE in _MIGRATION
    assert UK_LANDLINE in _MIGRATION
    assert r"^\+?44[1-9]\d{8,9}$" in _MIGRATION, "the uk_canonical guard changed"


@pytest.mark.parametrize(
    ("number", "mobile", "landline"),
    [
        # A UK mobile in all three spellings the book actually holds.
        ("07700900123", True, False),
        ("+447700900123", True, False),
        ("447700900123", True, False),
        # The 01 / 02 / 03 landline ranges, national and international.
        ("01179000000", False, True),
        ("+441179000000", False, True),
        ("02079460000", False, True),
        ("03001234567", False, True),
        # NEITHER, and this is the case the labelling turns on: an overseas number is filed as
        # `other` rather than guessed at. Calling a US mobile a "landline" would be a statement
        # about the line that nothing in the data supports.
        ("+14155552671", False, False),
        ("0612345678", False, False),
        # A trunk/international prefix is mandatory. Ten digits beginning with a plausible area
        # digit are incomplete, not a UK number to promote or label.
        ("7700900123", False, False),
        ("1179000000", False, False),
        # A mobile must never also read as a landline, or the CASE that labels the entry would
        # depend on which branch was written first.
        ("07000000000", True, False),
    ],
)
def test_a_number_is_classified_as_exactly_what_it_is(
    super_engine: Engine, number: str, mobile: bool, landline: bool
) -> None:
    with super_engine.connect() as connection:
        got = connection.execute(
            text("SELECT :n ~ :m AS is_mobile, :n ~ :l AS is_landline"),
            {"n": number, "m": UK_MOBILE, "l": UK_LANDLINE},
        ).one()
    assert (got.is_mobile, got.is_landline) == (mobile, landline)
    # The two are mutually exclusive by construction; if that ever stops holding, the entry label
    # becomes a coin toss rather than a reading of the number.
    assert not (got.is_mobile and got.is_landline)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        # The whole reason the duplicate check needs a canonical form: one column written by a
        # vendor in E.164, the other typed nationally by an adviser, same line.
        ("+447700900123", "07700900123"),
        ("447700900123", "07700900123"),
        ("07700900123", "07700900123"),
        ("+441179000000", "01179000000"),
        # NOT rewritten. A national number that merely begins "44" is a different number from
        # `0` + the rest, and mangling it here would make two distinct numbers compare equal --
        # which in the backfill means silently dropping one of them.
        ("441234567", "441234567"),
        # Nothing to do with the UK.
        ("+14155552671", "+14155552671"),
        ("", ""),
    ],
)
def test_uk_canonical_only_rewrites_a_full_international_uk_number(
    super_engine: Engine, stored: str, expected: str
) -> None:
    # The function itself is `pg_temp` and dropped at the end of the migration, so its BODY is
    # re-run here rather than called. Same expression, stated once in the migration and once here,
    # tied together by the drift guard above.
    expression = (
        "SELECT CASE WHEN :n ~ '^\\+?44[1-9]\\d{8,9}$' "
        "THEN '0' || regexp_replace(:n, '^\\+?44', '') ELSE :n END"
    )
    with super_engine.connect() as connection:
        assert connection.execute(text(expression), {"n": stored}).scalar_one() == expected


def test_the_backfill_never_discards_a_value() -> None:
    """Every non-empty `phone` reaches one of the two destinations -- read off the SQL itself.

    The classification CASE has exactly two arms: promote, or append. There is no `ELSE NULL` and
    no arm that leaves a non-empty value nowhere, which is the property that makes the dropped
    column safe to drop. Stated as a test because the arm that would lose data is the easy one to
    add later while "tidying" the CASE.
    """
    backfill = _MIGRATION[_MIGRATION.index("WITH unwrapped AS (") :]
    # The only rows excluded from the UPDATE are the empty ones and the exact duplicates.
    assert "WHERE compact IS NOT NULL" in backfill
    assert "IS DISTINCT FROM pg_temp.uk_canonical(" in backfill
    # And the append arm is unconditional -- anything not promoted is filed, never dropped.
    assert "COALESCE(c.phone_numbers, '[]'::jsonb) || jsonb_build_array(" in backfill
    assert "ELSE NULL" not in backfill
