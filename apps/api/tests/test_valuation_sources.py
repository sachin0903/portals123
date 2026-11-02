"""Cash is summable across source systems. A VALUE is not. (0117)

`platform.valuations` is keyed `(account_id, as_of_date, source_system)`, so from the moment
Morgan Lloyd merges into it one pension account can hold two rows for the same day: the
custodian's, and the scheme administrator's. The two are not the same kind of statement, and the
difference is the whole design:

* **The administrator's fund figure is our own number coming back.** For a SIPP, Morgan Lloyd is
  not the custodian — the assets sit at SEI or Seccl and we PUSH the valuation to ML
  (`cam_createpolicyvaluation`). Its `crystallised + uncrystallised` is the figure we last sent,
  re-derived. Summing it with the custodian's `market_value` double-counts the investments.
* **The bank balance is genuinely new.** The scheme's own account, at the administrator, separate
  from the cash in the custodian's account — and invisible today, because every cash figure in the
  portals is derived from custodian POSITIONS in the lake.

So the rule every reader follows: take a VALUE from one source system, take CASH from all of them.
These tests hold the account-detail read to it against real Postgres, because the trap is a
property of the schema (a unique key that permits two rows) rather than of any one query.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# No `pytestmark`: `asyncio_mode = "auto"` already runs the coroutines, and marking the one SYNC
# test in this file with it is a warning.
_TODAY = dt.date(2026, 9, 3)


async def _fixture(session: AsyncSession) -> dict[str, int]:
    """A firm with one pension account, and nothing valued yet."""
    from tests.conftest import SECCL_PLATFORM_SQL

    suffix = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"val_{suffix}", "a": f"val-{suffix}", "n": f"Valuations {suffix}"},
            )
        ).scalar_one()
    )
    await session.execute(text(SECCL_PLATFORM_SQL))
    contact = int(
        (
            await session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, first_name, surname, "
                    "  email) VALUES (:t, :u, 'individual', 'Iris Bell', 'Iris', 'Bell', :e) "
                    "RETURNING id"
                ),
                {"t": tenant, "u": f"c{suffix}0000"[:12], "e": f"iris.{suffix}@example.com"},
            )
        ).scalar_one()
    )
    account = int(
        (
            await session.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, uid, contact_id, name, status, "
                    "  value) VALUES (:t, :u, :c, 'Bell SIPP', 'active', 100000) RETURNING id"
                ),
                {"t": tenant, "u": f"a{suffix}0000"[:12], "c": contact},
            )
        ).scalar_one()
    )
    return {"tenant": tenant, "contact": contact, "account": account}


async def _value(
    session: AsyncSession,
    ids: dict[str, int],
    *,
    source: str,
    market: float | None,
    cash: float | None = None,
    crystallised: float | None = None,
    uncrystallised: float | None = None,
    as_of: dt.date = _TODAY,
) -> None:
    await session.execute(
        text(
            "INSERT INTO platform.valuations (tenant_id, account_id, as_of_date, source_system, "
            "  market_value, cash_value, crystallised_value, uncrystallised_value) "
            "VALUES (:t, :a, :d, :s, :mv, :cv, :cry, :uncry)"
        ),
        {
            "t": ids["tenant"],
            "a": ids["account"],
            "d": as_of,
            "s": source,
            "mv": market,
            "cv": cash,
            "cry": crystallised,
            "uncry": uncrystallised,
        },
    )


async def _totals(session: AsyncSession, ids: dict[str, int]) -> dict[str, Any]:
    """What a reader following the rule computes: one value, all the cash."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT "
                    "  (SELECT market_value FROM platform.valuations "
                    "    WHERE account_id = :a AND source_system = :custodian "
                    "    ORDER BY as_of_date DESC LIMIT 1) AS value, "
                    "  (SELECT sum(cash_value) FROM platform.valuations v "
                    "     JOIN (SELECT source_system, max(as_of_date) AS as_of_date "
                    "             FROM platform.valuations WHERE account_id = :a "
                    "            GROUP BY source_system) latest "
                    "       ON latest.source_system = v.source_system "
                    "      AND latest.as_of_date = v.as_of_date "
                    "    WHERE v.account_id = :a) AS cash"
                ),
                {"a": ids["account"], "custodian": "seccl"},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


class TestTheSchemaPermitsTwoRowsForOneDay:
    async def test_a_custodian_and_an_administrator_row_coexist(
        self, service_session: AsyncSession
    ) -> None:
        """The premise everything else here rests on. `(account_id, as_of_date, source_system)`
        is unique, so `source_system` is what keeps the two apart — and it is stamped by the
        sink's projection (`'{provider}'`), never by a provider's own model."""
        ids = await _fixture(service_session)
        await _value(service_session, ids, source="seccl", market=100000, cash=2500)
        await _value(
            service_session,
            ids,
            source="morgan-lloyd",
            market=100000,
            cash=1500.25,
            crystallised=12000,
            uncrystallised=88000,
        )
        count = (
            await service_session.execute(
                text("SELECT count(*) FROM platform.valuations WHERE account_id = :a"),
                {"a": ids["account"]},
            )
        ).scalar_one()
        assert count == 2

    async def test_the_same_source_twice_on_one_day_is_refused(
        self, service_session: AsyncSession
    ) -> None:
        """Which is what makes the merge idempotent — a re-run updates rather than appending."""
        ids = await _fixture(service_session)
        await _value(service_session, ids, source="morgan-lloyd", market=100000)
        with pytest.raises(Exception, match="valuations_unique|duplicate key"):
            await _value(service_session, ids, source="morgan-lloyd", market=999)


class TestTheRule:
    async def test_cash_sums_across_sources_and_the_value_does_not(
        self, service_session: AsyncSession
    ) -> None:
        """The client's actual position: £100,000 invested (once, not twice) and £4,000.25 of
        cash in two places. Summing the values would report £200,000 of investments that do not
        exist."""
        ids = await _fixture(service_session)
        await _value(service_session, ids, source="seccl", market=100000, cash=2500)
        await _value(service_session, ids, source="morgan-lloyd", market=100000, cash=1500.25)
        totals = await _totals(service_session, ids)

        assert float(totals["value"]) == pytest.approx(100000)
        assert float(totals["cash"]) == pytest.approx(4000.25)

    async def test_a_negative_administrator_balance_reduces_the_total(
        self, service_session: AsyncSession
    ) -> None:
        """`cam_getscheme` says the bank balance can be negative, and an overdrawn scheme is
        something an adviser has to see rather than something to clamp at zero."""
        ids = await _fixture(service_session)
        await _value(service_session, ids, source="seccl", market=100000, cash=2500)
        await _value(service_session, ids, source="morgan-lloyd", market=100000, cash=-250)
        totals = await _totals(service_session, ids)
        assert float(totals["cash"]) == pytest.approx(2250)

    async def test_only_the_latest_day_per_source_counts(
        self, service_session: AsyncSession
    ) -> None:
        """The table is a SERIES — `cam_getscheme` returns the current position only, so each run
        appends a row. Summing the whole history would multiply the cash by the number of runs."""
        ids = await _fixture(service_session)
        for day, cash in ((dt.date(2026, 9, 1), 900), (dt.date(2026, 9, 2), 1500.25)):
            await _value(
                service_session, ids, source="morgan-lloyd", market=100000, cash=cash, as_of=day
            )
        await _value(service_session, ids, source="seccl", market=100000, cash=2500)
        totals = await _totals(service_session, ids)
        assert float(totals["cash"]) == pytest.approx(4000.25)

    async def test_a_source_with_no_cash_contributes_nothing_rather_than_a_zero(
        self, service_session: AsyncSession
    ) -> None:
        ids = await _fixture(service_session)
        await _value(service_session, ids, source="seccl", market=100000, cash=2500)
        await _value(service_session, ids, source="morgan-lloyd", market=100000, cash=None)
        totals = await _totals(service_session, ids)
        assert float(totals["cash"]) == pytest.approx(2500)


class TestTheCrystallisationSplit:
    async def test_only_the_administrator_states_it(self, service_session: AsyncSession) -> None:
        """No custodian in the estate models crystallisation, so a NULL there is the honest value
        — 0 would claim the fund is wholly uncrystallised, which is a statement about someone's
        pension rather than a missing figure."""
        ids = await _fixture(service_session)
        await _value(service_session, ids, source="seccl", market=100000, cash=2500)
        await _value(
            service_session,
            ids,
            source="morgan-lloyd",
            market=100000,
            crystallised=12000,
            uncrystallised=88000,
        )
        rows = (
            (
                await service_session.execute(
                    text(
                        "SELECT source_system, crystallised_value FROM platform.valuations "
                        "WHERE account_id = :a ORDER BY source_system"
                    ),
                    {"a": ids["account"]},
                )
            )
            .mappings()
            .all()
        )
        by_source = {r["source_system"]: r["crystallised_value"] for r in rows}
        assert by_source["seccl"] is None
        assert float(by_source["morgan-lloyd"]) == pytest.approx(12000)

    async def test_the_split_adds_up_to_the_administrators_fund_total(
        self, service_session: AsyncSession
    ) -> None:
        """Which is the identity the conformed model publishes, and the reason the bank balance
        is NOT folded into `market_value`: if it were, the split would no longer reconcile and
        the cash would stop being separately summable."""
        ids = await _fixture(service_session)
        await _value(
            service_session,
            ids,
            source="morgan-lloyd",
            market=100000.50,
            cash=1500.25,
            crystallised=12000,
            uncrystallised=88000.50,
        )
        row = (
            (
                await service_session.execute(
                    text(
                        "SELECT market_value, cash_value, crystallised_value, "
                        "       uncrystallised_value FROM platform.valuations "
                        "WHERE account_id = :a AND source_system = 'morgan-lloyd'"
                    ),
                    {"a": ids["account"]},
                )
            )
            .mappings()
            .one()
        )
        assert float(row["market_value"]) == pytest.approx(
            float(row["crystallised_value"]) + float(row["uncrystallised_value"])
        )
        # And the cash is genuinely outside it.
        assert float(row["cash_value"]) == pytest.approx(1500.25)


class TestTheAccountDetailReadsIt:
    # SYNC, unlike everything else here: it reads a file rather than the database, and an async
    # function doing blocking pathlib I/O is what ASYNC240 exists to catch.
    def test_it_reads_the_administrator_cash_and_one_crystallisation_split(self) -> None:
        """Cash sums across custodian and scheme. The split does not.

        A custodian row in this block carries ``NULL::numeric`` cash, because the lake already
        supplies that balance. Crystallisation is the administrator's when that source has
        stated one, and the custodian's otherwise — never both, because they describe the same
        fund. ``market_value`` stays unread: the administrator's figure is the valuation we
        pushed it.
        """
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent / "app" / "routers" / "crm.py").read_text(
            encoding="utf-8"
        )
        block = source.split("AS provider_positions")[0][-5200:]
        assert "source_system = ANY(:administrators)" in block
        assert "source_system = ANY(:custodians)" in block
        assert "NULL::numeric" in block
        assert "NOT EXISTS" in block
        assert ":pensions" in block
        assert "p.is_pension" in block

    def test_the_administrator_list_is_derived_from_the_provider_role(self) -> None:
        """Not hardcoded to `morgan_lloyd`: a second scheme administrator would otherwise be
        silently excluded from every cash breakdown, which reads as "that firm has no cash
        there". `PROVIDER_META['role']` already draws the line."""
        from app import providers

        assert providers.administrators() == ("morgan-lloyd",)
        custodians = {
            name
            for name, entry in providers.PROVIDER_META.items()
            if entry.get("role") == "custodian"
        }
        assert custodians == {"seccl", "sei"}
        assert not custodians & set(providers.administrators())

    def test_the_projection_reads_cash_and_the_split_but_never_the_value(self) -> None:
        """The account detail's `provider_positions` block deliberately does not select
        `market_value`: a reader handed the administrator's figure beside the custodian's would
        have no way to know the two overlap. Asserted on the SQL because the alternative is
        trusting a comment."""
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent / "app" / "routers" / "crm.py").read_text(
            encoding="utf-8"
        )
        block = source.split("AS provider_positions")[0][-5200:]
        assert "'cash', v.cash_value" in block
        assert "'crystallised', v.crystallised_value" in block
        assert "v.uncrystallised_value IS NOT NULL" in block
        assert "v.market_value" not in block, (
            "the administrator's fund figure is the valuation WE pushed it — exposing it beside "
            "the custodian's invites a double count (0117)"
        )
