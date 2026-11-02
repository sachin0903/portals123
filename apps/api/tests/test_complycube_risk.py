"""ComplyCube's risk profile per client, against a real Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.complycube import risk
from tests.complycube_support import (
    Bed,
    FakeProvider,
    Maker,
    fetch,
    insert_check,
    seed_bed,
)

PROFILE = {
    "overall": "high",
    "countryRisk": {"risk": "low"},
    "politicalExposureRisk": {"risk": "high"},
    "occupationRisk": {"risk": "not_set"},
    "watchlistRisk": {"risk": "medium"},
    "updatedAt": "2026-10-02T09:00:00.000Z",
}


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def bed(maker: Maker) -> AsyncIterator[Bed]:
    seeded = await seed_bed(maker)
    yield seeded
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("DELETE FROM compliance.client_risk WHERE client_id = :c"), {"c": seeded.client_pk}
        )
    await seeded.cleanup()


async def _refresh(bed: Bed, provider: FakeProvider, check_pk: int) -> bool:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return await risk.refresh_for_check(s, provider.client(), check_pk)


async def _stored(bed: Bed) -> list[dict[str, Any]]:
    return await fetch(
        bed.maker,
        "SELECT overall, profile, provider_updated_at, tenant_id FROM compliance.client_risk "
        "WHERE client_id = :c",
        {"c": bed.client_pk},
    )


async def _audit_rows(bed: Bed) -> int:
    rows = await fetch(
        bed.maker,
        "SELECT count(*) AS n FROM audit.log WHERE entity_type = 'compliance.client_risk' "
        "AND tenant_id = :t",
        {"t": bed.tenant_id},
    )
    return int(rows[0]["n"])


async def test_a_change_stores_the_clients_profile(bed: Bed) -> None:
    check_pk = await insert_check(bed, bed.ref("chk"), status="clear")
    provider = FakeProvider(bed)
    provider.risk[bed.provider_client_id] = PROFILE

    assert await _refresh(bed, provider, check_pk) is True

    [row] = await _stored(bed)
    assert row["overall"] == "high"
    assert row["tenant_id"] == bed.tenant_id
    assert row["provider_updated_at"] is not None


async def test_an_unchanged_profile_writes_nothing(bed: Bed) -> None:
    check_pk = await insert_check(bed, bed.ref("same"), status="clear")
    provider = FakeProvider(bed)
    provider.risk[bed.provider_client_id] = PROFILE
    await _refresh(bed, provider, check_pk)
    audited = await _audit_rows(bed)

    assert await _refresh(bed, provider, check_pk) is False
    assert await _audit_rows(bed) == audited

    provider.risk[bed.provider_client_id] = {
        **PROFILE,
        "overall": "medium",
        "updatedAt": "2026-10-03T09:00:00.000Z",
    }
    assert await _refresh(bed, provider, check_pk) is True
    assert (await _stored(bed))[0]["overall"] == "medium"


async def test_a_client_complycube_has_no_profile_for_is_kept_as_not_set(bed: Bed) -> None:
    check_pk = await insert_check(bed, bed.ref("none"), status="clear")
    provider = FakeProvider(bed)  # no profile: ComplyCube answers 404

    await _refresh(bed, provider, check_pk)

    assert (await _stored(bed))[0]["overall"] == "not_set"


async def test_a_failing_fetch_does_not_undo_the_callers_work(bed: Bed) -> None:
    check_pk = await insert_check(bed, bed.ref("busy"), status="clear")
    provider = FakeProvider(bed)
    provider.status_override = 503

    assert await _refresh(bed, provider, check_pk) is False
    assert await _stored(bed) == []


async def test_backfill_fills_a_client_with_a_finished_check(bed: Bed) -> None:
    await insert_check(bed, bed.ref("done"), status="clear")
    provider = FakeProvider(bed)
    provider.risk[bed.provider_client_id] = PROFILE

    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        stored = await risk.backfill(s, provider.client(), limit=50)

    assert stored >= 1
    assert (await _stored(bed))[0]["overall"] == "high"


def test_a_firm_session_cannot_read_risk(super_engine: Engine) -> None:
    """A rating can name a PEP: the request role has no privilege on the table at all."""
    with super_engine.connect() as conn:
        granted = conn.execute(
            text("SELECT has_table_privilege('authenticated', 'compliance.client_risk', 'SELECT')")
        ).scalar_one()
    assert granted is False


def test_overall_reads_as_given_or_not_set() -> None:
    assert risk.overall_of({"overall": "high"}) == "high"
    assert risk.overall_of({}) == "not_set"
    assert risk.overall_of({"overall": "  "}) == "not_set"
    assert risk.overall_of({"overallRisk": "very_high"}) == "very_high"
