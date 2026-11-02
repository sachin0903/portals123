"""The reject ledger has a reader, which is what makes it worth writing.

`platform.merge_rejects` exists because a firm with no rows and a firm whose rows were ALL refused
look identical from every surface — the reasons are the only thing that tells the two apart
(docs/provider-pipeline.md §5). That argument only holds if somebody can see them, and until this
endpoint nothing read the table at all.

Pooled by reason on purpose: 117 transactions refused for one unmapped type is ONE thing for an
operator to act on. The raw rows stay reachable because chasing a specific refusal back to source
needs the provider's own key — which is all the ledger stores of it, deliberately: a rejected
transaction is still client data and this is not a quarantine.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import platforms

PROVIDER = "seccl"


async def _firm(session: AsyncSession, name: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name)"
                    " VALUES (encode(gen_random_bytes(8), 'hex'),"
                    "         'rej-' || encode(gen_random_bytes(4), 'hex'), :n) RETURNING id"
                ),
                {"n": name},
            )
        ).scalar_one()
    )


async def _reject(
    session: AsyncSession, tenant_id: int, entity: str, reason: str, key: str
) -> None:
    await session.execute(
        text(
            "INSERT INTO platform.merge_rejects"
            " (tenant_id, provider, entity, reason, row_key, occurred_at)"
            " VALUES (:t, :p, :e, :r, :k, timestamptz '2026-10-04 22:54:49+00')"
        ),
        {"t": tenant_id, "p": PROVIDER, "e": entity, "r": reason, "k": key},
    )


async def _ran(
    session: AsyncSession,
    tenant_id: int,
    entity: str,
    rejected: int,
    *,
    at: str = "2026-10-04 22:54:54+00",
) -> None:
    """The import those refusals belong to. The reader shows the newest one."""
    await session.execute(
        text(
            "INSERT INTO platform.merge_runs"
            " (run_id, tenant_id, provider, entity, rejected, run_at)"
            " VALUES (gen_random_uuid(), :t, :p, :e, :n, CAST(:at AS timestamptz))"
        ),
        {"t": tenant_id, "p": PROVIDER, "e": entity, "n": rejected, "at": at},
    )


@pytest.mark.asyncio
async def test_one_reason_is_one_line_however_many_rows_it_refused(
    service_session: AsyncSession,
) -> None:
    """The shape the dev book actually produces: 113 refusals of one type and 4 of another. An
    operator needs two lines and two counts, not 117 rows."""
    firm = await _firm(service_session, "Pooling Firm")
    for index in range(3):
        await _reject(
            service_session,
            firm,
            "transactions",
            "unmapped type: Expectation/FAD Gross Withdrawal",
            f"txn_{index}",
        )
    await _reject(
        service_session, firm, "transactions", "unmapped type: Expectation/UFPLS", "txn_9"
    )
    await _ran(service_session, firm, "transactions", 4)

    result = await platforms.merge_rejects(service_session, PROVIDER, tenant_id=firm)

    assert result["rejected"] == 4
    reasons = {row["reason"]: row for row in result["reasons"]}
    assert reasons["unmapped type: Expectation/FAD Gross Withdrawal"]["rejected"] == 3
    assert reasons["unmapped type: Expectation/UFPLS"]["rejected"] == 1
    # Ordered by how much they refused, so the biggest thing to fix is the first thing read.
    assert result["reasons"][0]["reason"].endswith("FAD Gross Withdrawal")
    assert result["reasons"][0]["entity"] == "transactions"


@pytest.mark.asyncio
async def test_the_raw_rows_carry_the_provider_key_and_nothing_of_the_row(
    service_session: AsyncSession,
) -> None:
    """`row_key` is what makes a refusal chaseable back to source. It is also ALL the ledger keeps
    of the row, which is why this is not a quarantine."""
    firm = await _firm(service_session, "Chasing Firm")
    await _reject(service_session, firm, "valuations", "unresolved account_id: GHOST", "GHOST")
    await _ran(service_session, firm, "valuations", 1)

    result = await platforms.merge_rejects(service_session, PROVIDER, tenant_id=firm)
    row = result["recent"][0]
    assert row["row_key"] == "GHOST"
    assert row["entity"] == "valuations"
    assert row["tenant_name"] == "Chasing Firm"
    assert "payload" not in row and "data" not in row


@pytest.mark.asyncio
async def test_a_firms_rejects_are_its_own(service_session: AsyncSession) -> None:
    """The question is asked per firm from the tenant page, and one firm's refusals must not be
    another's evidence."""
    ours = await _firm(service_session, "Ours")
    theirs = await _firm(service_session, "Theirs")
    await _reject(service_session, ours, "accounts", "unmodelled wrapper: SSAS", "SSAS")
    await _reject(service_session, theirs, "accounts", "unmodelled wrapper: OEIC", "OEIC")
    await _ran(service_session, ours, "accounts", 1)
    await _ran(service_session, theirs, "accounts", 1)

    mine = await platforms.merge_rejects(service_session, PROVIDER, tenant_id=ours)
    assert [row["reason"] for row in mine["reasons"]] == ["unmodelled wrapper: SSAS"]

    # Un-narrowed, an operator sees the provider's whole picture — with the firm counted, so a
    # reason that hits every firm reads differently from one that hits a single firm hard.
    everyone = await platforms.merge_rejects(service_session, PROVIDER)
    reasons = {row["reason"]: row for row in everyone["reasons"]}
    assert {"unmodelled wrapper: SSAS", "unmodelled wrapper: OEIC"} <= set(reasons)
    assert reasons["unmodelled wrapper: SSAS"]["tenants"] == 1


@pytest.mark.asyncio
async def test_a_provider_that_refused_nothing_says_so(service_session: AsyncSession) -> None:
    """An empty answer has to be distinguishable from a broken one: zero, with no reasons, rather
    than an absent key the caller has to interpret."""
    firm = await _firm(service_session, "Clean Firm")
    result = await platforms.merge_rejects(service_session, PROVIDER, tenant_id=firm)
    assert result == {"reasons": [], "recent": [], "rejected": 0}


@pytest.mark.asyncio
async def test_the_total_is_the_sum_of_what_is_shown(service_session: AsyncSession) -> None:
    """Not a second count(*): a page showing "2 reasons" beside a total that disagreed with them
    would send somebody looking for the difference."""
    firm = await _firm(service_session, "Summing Firm")
    await _reject(service_session, firm, "nodes", "a", "1")
    await _reject(service_session, firm, "nodes", "a", "2")
    await _reject(service_session, firm, "nodes", "b", "3")
    await _ran(service_session, firm, "nodes", 3)

    result: dict[str, Any] = await platforms.merge_rejects(
        service_session, PROVIDER, tenant_id=firm
    )
    assert result["rejected"] == sum(int(row["rejected"]) for row in result["reasons"]) == 3


@pytest.mark.asyncio
async def test_a_later_import_that_wrote_the_rows_stops_listing_them(
    service_session: AsyncSession,
) -> None:
    """Select's Cash Transfers were refused once and written on the next import. The table keeps
    the first statement; the page is about the latest one."""
    firm = await _firm(service_session, "Select")
    for index in range(15):
        await _reject(
            service_session, firm, "transactions", "unmapped type: Cash Transfer/?", f"txn_{index}"
        )
    await _ran(service_session, firm, "transactions", 15)
    await _ran(service_session, firm, "transactions", 0, at="2026-10-04 23:58:52+00")

    result = await platforms.merge_rejects(service_session, PROVIDER, tenant_id=firm)
    assert result == {"reasons": [], "recent": [], "rejected": 0}
