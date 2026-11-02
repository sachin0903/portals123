"""A person's ``crm.contacts.name`` is their first, middle and surname, whoever writes the row
(``0246_contact_name_from_parts``), against a real Postgres.

Each write path used to spell it its own way, and a flow's form write-back never touched it. The
rule now lives in a BEFORE trigger, so these tests write the row directly, as every path does in
the end.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.complycube_support import Bed, Maker, as_json, fetch, insert_check, seed_bed


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def bed(maker: Maker) -> AsyncIterator[Bed]:
    """A firm with one individual, named ``Ada Lovelace`` and holding no name parts yet."""
    seeded = await seed_bed(maker)
    yield seeded
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("DELETE FROM flows.outbox WHERE tenant_id = :t"), {"t": seeded.tenant_id}
        )
    await seeded.cleanup()


async def _write(bed: Bed, set_sql: str, params: dict[str, Any] | None = None) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(f"UPDATE crm.contacts SET {set_sql} WHERE id = :c"),  # noqa: S608 - test-only literal
            {**(params or {}), "c": bed.contact_id},
        )


async def _name(bed: Bed, contact_id: int | None = None) -> str | None:
    rows = await fetch(
        bed.maker,
        "SELECT name FROM crm.contacts WHERE id = :c",
        {"c": contact_id or bed.contact_id},
    )
    return rows[0]["name"]


async def _insert(bed: Bed, uid: str, columns: dict[str, Any]) -> int:
    names = ", ".join(columns)
    values = ", ".join(f":{key}" for key in columns)
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return int(
            (
                await s.execute(
                    text(
                        f"INSERT INTO crm.contacts (tenant_id, uid, {names}) "  # noqa: S608 - test-only literal
                        f"VALUES (:t, :u, {values}) RETURNING id"
                    ),
                    {"t": bed.tenant_id, "u": uid, **columns},
                )
            ).scalar_one()
        )


async def test_a_new_person_is_named_from_first_middle_and_surname(bed: Bed) -> None:
    """Whatever spelling the writer sent: kickoff, the partner API and imports all land the same."""
    contact = await _insert(
        bed,
        f"nm1{bed.nonce}",
        {
            "type": "individual",
            "name": "LOVELACE, A",
            "first_name": " Ada ",
            "middle_name": "Augusta",
            "surname": "King",
        },
    )
    assert await _name(bed, contact) == "Ada Augusta King"


async def test_a_changed_part_renames_the_person(bed: Bed) -> None:
    await _write(bed, "first_name = 'Ada', surname = 'Lovelace'")
    assert await _name(bed) == "Ada Lovelace"
    await _write(bed, "middle_name = 'Augusta'")
    assert await _name(bed) == "Ada Augusta Lovelace"
    await _write(bed, "surname = 'King', middle_name = NULL")
    assert await _name(bed) == "Ada King"


async def test_a_person_without_both_parts_keeps_the_name_they_have(bed: Bed) -> None:
    """A lone first name must not replace a fuller name."""
    await _write(bed, "first_name = 'Ada'")
    assert await _name(bed) == "Ada Lovelace"
    await _write(bed, "name = 'Ada Byron'")
    assert await _name(bed) == "Ada Byron"


async def test_a_new_name_for_a_person_with_both_parts_is_the_parts(bed: Bed) -> None:
    await _write(bed, "first_name = 'Ada', surname = 'Lovelace'")
    await _write(bed, "name = 'Countess Lovelace'")
    assert await _name(bed) == "Ada Lovelace"


async def test_an_untouched_name_is_not_rewritten(bed: Bed, super_engine: Engine) -> None:
    """No backfill: a name spelt before 0246 stays until its parts or the name itself change.
    Re-sending the same parts, or the same name, is not a change."""
    with super_engine.connect() as conn:
        conn.execute(text("SET session_replication_role = replica"))
        try:
            conn.execute(
                text(
                    "UPDATE crm.contacts SET first_name = 'Ada', middle_name = 'Augusta', "
                    "  surname = 'Lovelace', name = 'Ada Lovelace' WHERE id = :c"
                ),
                {"c": bed.contact_id},
            )
            conn.commit()
        finally:
            conn.execute(text("SET session_replication_role = origin"))
    await _write(bed, "first_name = 'Ada', surname = 'Lovelace', name = 'Ada Lovelace'")
    await _write(bed, "email = 'ada@example.test'")
    assert await _name(bed) == "Ada Lovelace"
    await _write(bed, "middle_name = 'Augusta'")
    assert await _name(bed) == "Ada Lovelace"
    await _write(bed, "middle_name = 'A.'")
    assert await _name(bed) == "Ada A. Lovelace"


async def test_an_organisation_keeps_its_own_name(bed: Bed) -> None:
    contact = await _insert(
        bed,
        f"nm2{bed.nonce}",
        {"type": "organisation", "name": "Caldwell Holdings Ltd"},
    )
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("UPDATE crm.contacts SET name = 'Caldwell Group plc' WHERE id = :c"),
            {"c": contact},
        )
    assert await _name(bed, contact) == "Caldwell Group plc"


async def _outbox(bed: Bed) -> list[dict[str, Any]]:
    rows = await fetch(
        bed.maker,
        "SELECT event_type, payload FROM flows.outbox WHERE tenant_id = :t ORDER BY id",
        {"t": bed.tenant_id},
    )
    return [{**row, "payload": as_json(row["payload"])} for row in rows]


async def test_a_middle_name_alone_does_not_ask_for_a_new_screen(bed: Bed) -> None:
    """ComplyCube is never sent a middle name, so the new name it makes is not a change 0223
    counts; a surname change still asks for a new screen."""
    await _write(bed, "first_name = 'Ada', surname = 'Lovelace'")
    await insert_check(bed, bed.ref("clear"), origin="portal", status="clear")

    await _write(bed, "middle_name = 'Augusta'")
    assert await _name(bed) == "Ada Augusta Lovelace"
    assert await _outbox(bed) == []

    await _write(bed, "surname = 'King'")
    assert await _name(bed) == "Ada Augusta King"
    assert [row["event_type"] for row in await _outbox(bed)] == ["aml.rescreen.requested"]


async def test_a_fact_find_that_adds_a_middle_name_does_not_ask_for_a_new_screen(bed: Bed) -> None:
    """A flow's write-back sends every mapped part in one SET, so first_name is always named. The
    name changes, but for a person with both parts ComplyCube reads the parts, not the name."""
    await _write(bed, "first_name = 'Ada', surname = 'Lovelace'")
    await insert_check(bed, bed.ref("clearff"), origin="portal", status="clear")

    await _write(
        bed,
        "first_name = 'Ada', middle_name = 'Augusta', surname = 'Lovelace', "
        "nationality = ARRAY['GB']",
    )

    assert await _name(bed) == "Ada Augusta Lovelace"
    assert await _outbox(bed) == []


async def test_a_cleared_part_leaves_the_name(bed: Bed) -> None:
    """A name that came from the parts follows them, so a surname the firm removed is not kept in
    the name, where ComplyCube would read it for a person without both parts."""
    await _write(bed, "first_name = 'Ada', middle_name = 'Augusta', surname = 'Lovelace'")
    await _write(bed, "surname = NULL")
    assert await _name(bed) == "Ada Augusta"
    await _write(bed, "middle_name = NULL")
    assert await _name(bed) == "Ada"
    # Both gone: nothing to build a name from, so the last one stays.
    await _write(bed, "first_name = NULL")
    assert await _name(bed) == "Ada"


async def _kyc(bed: Bed, uid: str, kind: str) -> int:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return int(
            (
                await s.execute(
                    text(
                        "INSERT INTO crm.kyc_checks "
                        "  (uid, tenant_id, contact_id, kind, status, provider) "
                        "VALUES (:u, :t, :c, :k, 'passed', 'manual') RETURNING id"
                    ),
                    {"u": uid, "t": bed.tenant_id, "c": bed.contact_id, "k": kind},
                )
            ).scalar_one()
        )


async def test_a_detail_change_expires_every_passed_aml_check(bed: Bed) -> None:
    """``pick_valid`` reuses any passed AML row. Expiring only the row the latest portal check
    points at left an older pass standing, and the next journey completed ``already_verified``
    on the previous person's details. An identity pass is not an AML screen and stays."""
    await _write(bed, "first_name = 'Ada', surname = 'Lovelace'")
    older = await _kyc(bed, bed.ref("kycold"), "aml")
    latest = await _kyc(bed, bed.ref("kycnew"), "aml")
    identity = await _kyc(bed, bed.ref("kycid"), "identity")
    await insert_check(bed, bed.ref("clear"), origin="portal", status="clear", kyc_check_id=latest)

    await _write(bed, "surname = 'King'")

    rows = await fetch(
        bed.maker,
        "SELECT id, status FROM crm.kyc_checks WHERE contact_id = :c",
        {"c": bed.contact_id},
    )
    by_id = {int(row["id"]): row["status"] for row in rows}
    assert by_id[older] == "expired"
    assert by_id[latest] == "expired"
    assert by_id[identity] == "passed"
