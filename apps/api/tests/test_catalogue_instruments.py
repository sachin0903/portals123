"""The instruments table reads the whole universe, not the picker's first 500.

THE DEFECT. `catalogue.instruments()` is capped at 500 by name, because it feeds a PICKER. The
admin Instruments TABLE read that same list and searched it in the browser. `platform.instruments`
is global and un-scoped (0041: an ISIN is not tenant data), so it holds every tenant's securities —
SEI's first sync alone landed 6,170. A security outside those first 500 could not be found at all,
and an empty table under a search box reads as "we do not hold that security". Silent truncation is
worse than a slow page: there is no symptom to notice.

A DB test rather than a unit one, because the failure is entirely in what SQL returns. Every type
in play says `list[dict]` either way — only executing the query over more rows than the cap can
tell the two apart.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import catalogue


async def _seed(session: AsyncSession, count: int, marker: str) -> None:
    """`count` instruments named so their alphabetical order is known and reversible."""
    await session.execute(
        text(
            "INSERT INTO platform.instruments (isin, sedol, name, asset_type, currency) "
            "SELECT :m || '-ISIN-' || lpad(g::text, 5, '0'), "
            "       :m || lpad(g::text, 4, '0'), "
            "       :m || ' Fund ' || lpad(g::text, 5, '0'), "
            "       'FUND', 'GBP' "
            "FROM generate_series(1, :n) g"
        ),
        {"m": marker, "n": count},
    )


@pytest.mark.asyncio
class TestInstrumentsPage:
    async def test_it_finds_a_security_past_the_pickers_cap(
        self, service_session: AsyncSession
    ) -> None:
        # The bug in one assertion: 600 rows, and the one we look for sorts at position 600 — well
        # outside `instruments()`'s 500. Searching the picker's slice would find nothing and the
        # page would say the firm does not hold it.
        marker = f"ZZ{uuid.uuid4().hex[:6].upper()}"
        await _seed(service_session, 600, marker)
        needle = f"{marker} Fund 00600"

        picker = await catalogue.instruments(service_session)
        assert not any(row["name"] == needle for row in picker), (
            "the picker is meant to be capped — if this fails the premise has changed"
        )

        page = await catalogue.instruments_page(service_session, q=needle)
        assert page["total"] == 1
        assert [row["name"] for row in page["items"]] == [needle]

    async def test_the_total_counts_the_universe_not_the_window(
        self, service_session: AsyncSession
    ) -> None:
        # `total` is what lets the page say "100 of 6,170". Counting the window would make every
        # listing exactly one window long, which reads as "that is all there is".
        marker = f"ZY{uuid.uuid4().hex[:6].upper()}"
        await _seed(service_session, 250, marker)
        page = await catalogue.instruments_page(service_session, q=marker, limit=100)
        assert len(page["items"]) == 100
        assert page["total"] == 250

    async def test_searching_by_isin_and_sedol(self, service_session: AsyncSession) -> None:
        # Identifiers are stored upper-case. A lowercased needle matching nothing is the same
        # silent-empty failure in miniature, so both are compared case-insensitively.
        marker = f"ZX{uuid.uuid4().hex[:6].upper()}"
        await _seed(service_session, 3, marker)
        by_isin = await catalogue.instruments_page(service_session, q=f"{marker}-isin-00002")
        assert by_isin["total"] == 1
        by_sedol = await catalogue.instruments_page(service_session, q=f"{marker.lower()}0003")
        assert by_sedol["total"] == 1

    async def test_paging_is_stable_across_windows(self, service_session: AsyncSession) -> None:
        # Two rows sharing a sort value can swap between windows without the primary-key tie-break,
        # and one of them is then never seen at all — a row that exists and cannot be reached.
        marker = f"ZW{uuid.uuid4().hex[:6].upper()}"
        await service_session.execute(
            text(
                "INSERT INTO platform.instruments (isin, name, asset_type, currency) "
                "SELECT :m || '-' || g, :m || ' Same Name', 'FUND', 'GBP' "
                "FROM generate_series(1, 10) g"
            ),
            {"m": marker},
        )
        seen: list[int] = []
        for offset in (0, 5):
            page = await catalogue.instruments_page(
                service_session, q=marker, limit=5, offset=offset
            )
            seen.extend(int(row["id"]) for row in page["items"])
        assert len(seen) == 10
        assert len(set(seen)) == 10, "a row appeared twice, or one was skipped, across windows"

    async def test_an_unknown_sort_key_falls_back_rather_than_reaching_sql(
        self, service_session: AsyncSession
    ) -> None:
        # `sort` lands in an identifier position, so it is resolved through an allowlist. Anything
        # unknown must order by name rather than interpolate — and must not raise, or a stale
        # bookmark takes the page down.
        marker = f"ZV{uuid.uuid4().hex[:6].upper()}"
        await _seed(service_session, 3, marker)
        page = await catalogue.instruments_page(
            service_session, q=marker, sort="name; DROP TABLE platform.instruments"
        )
        assert page["total"] == 3
        assert [r["name"] for r in page["items"]] == sorted(r["name"] for r in page["items"])

    async def test_no_query_returns_the_whole_universe_windowed(
        self, service_session: AsyncSession
    ) -> None:
        marker = f"ZU{uuid.uuid4().hex[:6].upper()}"
        await _seed(service_session, 20, marker)
        page = await catalogue.instruments_page(service_session, limit=5)
        assert len(page["items"]) == 5
        assert page["total"] >= 20
