"""0210 moves the fact-find's employer answers out of `crm.contacts.data` and into the columns the
Employer card reads. Run here as SQL against a real Postgres, because the jsonb surgery — copy a
nested leaf into a column, then remove exactly that leaf and nothing beside it — is the part a
description cannot check, and the migration cannot be re-run on the database it has already fixed.
"""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_BACKFILL_SQL = (
    Path(__file__).resolve().parent.parent / "migrations/sql/0210_employer_facts_backfill.sql"
).read_text(encoding="utf-8")


async def _contact(sess: AsyncSession, tenant: int, uid: str, data: str, **columns: object) -> int:
    cols = ", ".join(["tenant_id", "uid", "type", "data", *columns])
    vals = ", ".join([":t", ":u", "'individual'", "CAST(:d AS jsonb)", *[f":{c}" for c in columns]])
    insert = f"INSERT INTO crm.contacts ({cols}) VALUES ({vals}) RETURNING id"  # noqa: S608
    return int(
        (
            await sess.execute(text(insert), {"t": tenant, "u": uid, "d": data, **columns})
        ).scalar_one()
    )


async def _row(sess: AsyncSession, cid: int) -> dict[str, object]:
    return dict(
        (
            await sess.execute(
                text(
                    "SELECT employer_contributions, employer_name, employer_registration_number, "
                    "       source_of_wealth_detail, data "
                    "FROM crm.contacts WHERE id = :c"
                ),
                {"c": cid},
            )
        )
        .mappings()
        .one()
    )


@pytest.mark.asyncio
async def test_the_backfill_moves_the_legacy_shape_into_the_columns_and_keeps_the_rest(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    tenant = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES ('bf210_t', 'bf210-t', 'Backfill') RETURNING id"
                )
            )
        ).scalar_one()
    )

    # The shape every onboarding before the fix wrote: the tick and the company under
    # `data.pension`, the "Other" words as a top-level key, and the scheme's bank details beside
    # the company — those have no column and must survive.
    legacy = await _contact(
        sess,
        tenant,
        "bf210_legacy",
        '{"created_via": "onboarding", "source_of_wealth_detail": "Sold her house", '
        '"pension": {"hasEmployerContributions": true, "retirementAgeNote": "checked", '
        '"employerContributions": {"name": "Acme Ltd", "registrationNumber": "SC123456", '
        '"accountNumber": "12345678", "sortCode": "112233"}}}',
    )
    # An adviser who has since typed on the record: the column wins, the stale `data` copy goes.
    typed = await _contact(
        sess,
        tenant,
        "bf210_typed",
        '{"pension": {"hasEmployerContributions": false, '
        '"employerContributions": {"name": "Old Name Ltd"}}}',
        employer_contributions=True,
        employer_name="Acme Holdings Ltd",
    )
    # A "No" with nothing else under `pension`: the object is emptied and removed, not left as
    # `"pension": {}` for the overflow to walk.
    declined = await _contact(
        sess, tenant, "bf210_declined", '{"pension": {"hasEmployerContributions": false}}'
    )
    # Nothing to do with employers. Not touched, byte for byte.
    other = await _contact(
        sess, tenant, "bf210_other", '{"created_via": "onboarding", "offshore": {"pep": false}}'
    )

    connection = await sess.connection()
    await connection.exec_driver_sql(_BACKFILL_SQL)
    # The migration's own `RESET ROLE` hands the session back to the request role.
    await sess.execute(text("SET LOCAL ROLE service_role"))

    got = await _row(sess, legacy)
    assert got["employer_contributions"] is True
    assert got["employer_name"] == "Acme Ltd"
    assert got["employer_registration_number"] == "SC123456"
    assert got["source_of_wealth_detail"] == "Sold her house"
    assert got["data"] == {
        "created_via": "onboarding",
        "pension": {
            "retirementAgeNote": "checked",
            "employerContributions": {"accountNumber": "12345678", "sortCode": "112233"},
        },
    }

    got = await _row(sess, typed)
    assert got["employer_contributions"] is True
    assert got["employer_name"] == "Acme Holdings Ltd"
    assert got["data"] == {}

    got = await _row(sess, declined)
    assert got["employer_contributions"] is False
    assert got["employer_name"] is None
    assert got["data"] == {}

    got = await _row(sess, other)
    assert got["employer_contributions"] is None
    assert got["data"] == {"created_via": "onboarding", "offshore": {"pep": False}}


@pytest.mark.asyncio
async def test_the_backfill_is_safe_to_run_twice(service_session: AsyncSession) -> None:
    """A deploy that retries must not turn a column value back into nothing or invent one."""
    sess = service_session
    tenant = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES ('bf210_r', 'bf210-r', 'Rerun') RETURNING id"
                )
            )
        ).scalar_one()
    )
    cid = await _contact(
        sess,
        tenant,
        "bf210_rerun",
        '{"pension": {"hasEmployerContributions": true, '
        '"employerContributions": {"name": "Acme Ltd", "sortCode": "112233"}}}',
    )
    connection = await sess.connection()
    await connection.exec_driver_sql(_BACKFILL_SQL)
    await sess.execute(text("SET LOCAL ROLE service_role"))
    first = await _row(sess, cid)
    connection = await sess.connection()
    await connection.exec_driver_sql(_BACKFILL_SQL)
    await sess.execute(text("SET LOCAL ROLE service_role"))
    assert await _row(sess, cid) == first
    assert first["employer_name"] == "Acme Ltd"
    assert first["data"] == {"pension": {"employerContributions": {"sortCode": "112233"}}}
