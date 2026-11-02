"""The employer's bank account (0271): the fact-find rows that used to sit in ``data`` move onto
the column, and the record's PATCH writes it through the same normaliser as the client's own."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import contacts as contacts_mod

pytestmark = pytest.mark.asyncio

_SQL = (
    Path(__file__).resolve().parent.parent / "migrations" / "sql" / "0271_employer_bank_account.sql"
)


async def _seed(url: str, data: dict) -> tuple[int, int]:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    n = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = (
            await s.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES "
                    "('ten_' || :n, 'eb-' || :n, 'Employer Bank Firm') RETURNING id"
                ),
                {"n": n},
            )
        ).scalar_one()
        cid = (
            await s.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, data) "
                    "VALUES (:t, :u, 'individual', 'Mark', 'Black', CAST(:d AS jsonb)) "
                    "RETURNING id"
                ),
                {"t": tid, "u": f"con_{n}", "d": json.dumps(data)},
            )
        ).scalar_one()
    await eng.dispose()
    return int(tid), int(cid)


async def _row(url: str, cid: int) -> dict:
    eng = create_async_engine(url)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            (
                await s.execute(
                    text("SELECT employer_bank_account, data FROM crm.contacts WHERE id = :c"),
                    {"c": cid},
                )
            )
            .mappings()
            .one()
        )
        out = dict(row)
    await eng.dispose()
    return out


async def test_the_backfill_moves_the_fact_find_pair_onto_the_column(db):
    """The rows written before the mapping targeted the column: the digits become the canonical
    `12-34-56` / 8-digit pair, and the two keys leave `data` — along with the object they
    emptied — so the record stops listing them as "2 more from the fact-find"."""
    data = {
        "pension": {"employerContributions": {"sortCode": "040040", "accountNumber": "1234567"}},
        "offshore": {"pep": False},
    }
    tid, cid = await _seed(db["app"], data)
    # Re-run the migration's own file the way the deploy does — as the migrator, which may
    # ALTER the table — against a row written the old way after the column already exists.
    eng = create_async_engine(db["migrator"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text(_SQL.read_text(encoding="utf-8")))
    await eng.dispose()
    row = await _row(db["app"], cid)
    assert row["employer_bank_account"] == {"sort_code": "04-00-40", "account_number": "01234567"}
    assert row["data"] == {"offshore": {"pep": False}}
    del tid


async def test_the_record_writes_the_employer_account_through_the_bank_normaliser(db):
    tid, cid = await _seed(db["app"], {})
    eng = create_async_engine(db["app"])
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        saved = await contacts_mod.update_contact(
            s,
            tenant_id=tid,
            contact_id=cid,
            fields={
                "employer_bank_account": {
                    "name": "Halloway Dental Ltd",
                    "sort_code": "04 00 40",
                    "account_number": "1234567",
                }
            },
        )
        assert saved["employer_bank_account"] == {
            "name": "Halloway Dental Ltd",
            "sort_code": "04-00-40",
            "account_number": "01234567",
        }
        # a keying slip is refused, naming the field and never a digit
        with pytest.raises(ValueError) as refused:
            await contacts_mod.update_contact(
                s,
                tenant_id=tid,
                contact_id=cid,
                fields={"employer_bank_account": {"sort_code": "12", "account_number": "1"}},
            )
        assert "sort code" in str(refused.value).lower()
        # every field blank clears it
        cleared = await contacts_mod.update_contact(
            s,
            tenant_id=tid,
            contact_id=cid,
            fields={"employer_bank_account": {"name": "", "sort_code": "", "account_number": ""}},
        )
        assert cleared["employer_bank_account"] is None
    await eng.dispose()
