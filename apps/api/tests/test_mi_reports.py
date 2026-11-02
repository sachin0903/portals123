"""Shipped management reports: the query, the switch, and the cap.

The SQL is ours, so a bind that does not match the statement is a bug in this file's catalogue
rather than a firm's. The value and date grains are proved against Postgres because a SUM that
includes a closed account, or a movement dated only by ``posted_at``, is invisible in a unit of
the string.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.lake.holdings import _HOLDINGS_SUMMARY_SQL, CASH_NAME_NEEDLE, is_cash_position
from app.reports.cash import assemble
from app.reports.catalogue import CATALOGUE, get
from app.reports.runner import (
    ReportNotFound,
    ReportTooLarge,
    ReportUnavailable,
    enabled_codes,
    execute,
    run,
)
from tests.conftest import principal_conn


def test_every_sql_report_binds_only_its_parameters() -> None:
    for report in CATALOGUE:
        if report.sql is None:
            assert report.reads_lake
            continue
        named = set(text(report.sql)._bindparams)
        assert named == {param.key for param in report.params}, report.code


def test_a_money_market_fund_is_cash_on_the_holdings_rule() -> None:
    assert is_cash_position("Cash", None)
    assert is_cash_position("MRCXXXXXXX", "GBP")
    assert is_cash_position("Stock", "Royal London Money Market")
    assert is_cash_position(None, "Liquidity fund")
    assert not is_cash_position("Stock", "Global Equity")
    # Word boundaries: a substring ``cash`` once paid a withdrawal out of an equity fund.
    assert not is_cash_position("Stock", "Cashel Capital Global Equity Fund")
    assert not is_cash_position("Stock", "Encashment Growth Trust")
    assert CASH_NAME_NEEDLE in _HOLDINGS_SUMMARY_SQL
    assert "mrcxxxxxxx" in _HOLDINGS_SUMMARY_SQL


def test_cash_is_the_cash_class_and_an_unseen_account_is_omitted() -> None:
    rows = assemble(
        [
            {
                "client_name": "Ada Shaw",
                "client_id": "c-ada",
                "account_id": "a-ada",
                "wrapper": "GIA",
                "model": "Balanced",
                "currency": "GBP",
                "market_value": Decimal("10000"),
                "external_id": "seen",
                "provider": "seccl",
            },
            {
                "client_name": "Bea Shaw",
                "wrapper": "ISA",
                "model": None,
                "currency": "GBP",
                "market_value": Decimal("500"),
                "external_id": "missing",
                "provider": "seccl",
            },
        ],
        [
            {"account_external_id": "seen", "klass": "Cash", "value": 1000},
            {"account_external_id": "seen", "klass": "Equities", "value": 9000},
        ],
    )
    assert len(rows) == 1
    assert rows[0]["cash"] == Decimal("1000")
    assert rows[0]["cash_share"] == Decimal("10")
    assert rows[0]["client_id"] == "c-ada"
    assert rows[0]["account_id"] == "a-ada"


async def _book(session: AsyncSession) -> dict[str, Any]:
    """One firm: a client with an adviser and a support role on their team (0245: one adviser per
    client), an open and a closed account, and an empty client."""
    suffix = uuid.uuid4().hex[:8]
    tenant = int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"mi{suffix}"[:12], "a": f"mi-{suffix}", "n": f"MI {suffix}"},
            )
        ).scalar_one()
    )

    async def user(first: str) -> str:
        return str(
            (
                await session.execute(
                    text(
                        "INSERT INTO auth.users "
                        "  (uid, tenant_id, email, workos_user_id, first_name, last_name, enabled) "
                        "VALUES (:u, :t, :e, :w, :f, 'Adviser', true) RETURNING id"
                    ),
                    {
                        "u": f"u{first[:1]}{suffix}"[:12],
                        "t": tenant,
                        "e": f"{first.lower()}.{suffix}@mi.test",
                        "w": f"wos_{first}_{suffix}",
                        "f": first,
                    },
                )
            ).scalar_one()
        )

    ada_adviser, bea_adviser = await user("Ada"), await user("Bea")
    # A staff row is visible to a firm session only through a membership (auth.users RLS).
    for adviser_id in (ada_adviser, bea_adviser):
        await session.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": adviser_id, "t": tenant},
        )
    contact = int(
        (
            await session.execute(
                text(
                    "INSERT INTO crm.contacts "
                    "  (tenant_id, uid, type, first_name, surname, email, service_type) "
                    "VALUES (:t, :u, 'individual', 'Ada', 'Shaw', :e, 'advised') RETURNING id"
                ),
                {"t": tenant, "u": f"c{suffix}"[:12], "e": f"ada.{suffix}@mi.test"},
            )
        ).scalar_one()
    )
    await session.execute(
        text(
            "INSERT INTO crm.contacts (tenant_id, uid, type, name, service_type) "
            "VALUES (:t, :u, 'organisation', 'Empty Trust', 'advised')"
        ),
        {"t": tenant, "u": f"e{suffix}"[:12]},
    )
    for adviser, role in ((ada_adviser, "adviser"), (bea_adviser, "support")):
        await session.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :c, :a, :r)"
            ),
            {"t": tenant, "c": contact, "a": adviser, "r": role},
        )
    product = int(
        (
            await session.execute(
                text(
                    "INSERT INTO platform.products "
                    "  (tenant_id, product_type, name, code, enabled) "
                    "VALUES (:t, 'GIA', 'GIA', :code, true) RETURNING id"
                ),
                {"t": tenant, "code": f"gia-{suffix}"},
            )
        ).scalar_one()
    )
    model = int(
        (
            await session.execute(
                text(
                    "INSERT INTO platform.models "
                    "  (tenant_id, name, target_return, model_charge) "
                    "VALUES (:t, 'Balanced', 6, 0.25) RETURNING id"
                ),
                {"t": tenant},
            )
        ).scalar_one()
    )
    account = int(
        (
            await session.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "  (tenant_id, contact_id, uid, name, status, value, currency, "
                    "   product_id, model_id) "
                    "VALUES (:t, :c, :u, 'Ada GIA', 'active', 1000, 'GBP', :p, :m) RETURNING id"
                ),
                {
                    "t": tenant,
                    "c": contact,
                    "u": f"a{suffix}"[:12],
                    "p": product,
                    "m": model,
                },
            )
        ).scalar_one()
    )
    await session.execute(
        text(
            "INSERT INTO platform.accounts "
            "  (tenant_id, contact_id, uid, name, status, value, currency, product_id) "
            "VALUES (:t, :c, :u, 'Ada closed', 'closed', 500, 'GBP', :p)"
        ),
        {"t": tenant, "c": contact, "u": f"z{suffix}"[:12], "p": product},
    )
    await session.execute(
        text(
            "INSERT INTO platform.links "
            "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
            "VALUES (:t, 'account', :a, 'seccl', 'primary', :x)"
        ),
        {"t": tenant, "a": account, "x": f"ext-{suffix}"},
    )
    for source, as_of, market in (
        ("seccl", date(2026, 1, 31), 400),
        ("seccl", date(2026, 6, 30), 1000),
        ("morgan-lloyd", date(2026, 6, 30), 800),
    ):
        await session.execute(
            text(
                "INSERT INTO platform.valuations "
                "  (tenant_id, account_id, as_of_date, source_system, market_value) "
                "VALUES (:t, :a, :d, :s, :v)"
            ),
            {"t": tenant, "a": account, "d": as_of, "s": source, "v": market},
        )
    await session.execute(
        text(
            "INSERT INTO platform.transactions "
            "  (tenant_id, account_id, contact_id, source_system, source_transaction_id, "
            "   type, amount, transaction_date) "
            "VALUES (:t, :a, :c, 'seccl', :sid, 'deposit', 10, :d)"
        ),
        {"t": tenant, "a": account, "c": contact, "sid": f"dep-{suffix}", "d": date(2026, 3, 10)},
    )
    # No contact_id, as a synced movement arrives: the custodian names the account only.
    await session.execute(
        text(
            "INSERT INTO platform.transactions "
            "  (tenant_id, account_id, source_system, source_transaction_id, "
            "   type, amount, posted_at) "
            "VALUES (:t, :a, 'seccl', :sid, 'fee', 2, :d)"
        ),
        {
            "t": tenant,
            "a": account,
            "sid": f"fee-{suffix}",
            "d": datetime(2026, 3, 15, 12, tzinfo=UTC),
        },
    )
    await session.execute(
        text(
            "INSERT INTO platform.transactions "
            "  (tenant_id, account_id, contact_id, source_system, source_transaction_id, "
            "   type, amount, transaction_date) "
            "VALUES (:t, :a, :c, 'seccl', :sid, 'deposit', 99, :d)"
        ),
        {"t": tenant, "a": account, "c": contact, "sid": f"old-{suffix}", "d": date(2026, 1, 2)},
    )
    return {
        "tenant": tenant,
        "account": account,
        "external_id": f"ext-{suffix}",
        "client_id": f"c{suffix}"[:12],
        "account_id": f"a{suffix}"[:12],
        "transaction_id": f"dep-{suffix}",
    }


async def test_client_value_excludes_a_closed_account_and_the_adviser_column_names_the_adviser(
    service_session: AsyncSession,
) -> None:
    book = await _book(service_session)
    report = get("clients_market_value")
    assert report is not None
    result = await execute(service_session, report, {}, preview=True, tenant_id=book["tenant"])
    ada = next(row for row in result.rows if row["client_name"] == "Ada Shaw")
    empty = next(row for row in result.rows if row["client_name"] == "Empty Trust")
    assert ada["client_id"] == book["client_id"]
    assert ada["portfolio_value"] == 1000
    assert ada["open_accounts"] == 1
    # Ada Adviser holds `role = 'adviser'` (0245: unique per contact); Bea Adviser is a support
    # role on the same team, so the column names the adviser only.
    assert "Ada Adviser" in str(ada["advisers"])
    assert "Bea Adviser" not in str(ada["advisers"])
    assert empty["portfolio_value"] is None
    assert empty["status"] == "lead"

    accounts = get("accounts")
    assert accounts is not None
    account_rows = await execute(
        service_session, accounts, {}, preview=True, tenant_id=book["tenant"]
    )
    open_account = next(row for row in account_rows.rows if row["account_id"] == book["account_id"])
    assert open_account["client_id"] == book["client_id"]

    # `adviser_book` counts only `role = 'adviser'` rows: Bea's support role gives her no book at
    # all, not an empty one -- an "adviser's book" report must count only clients where they are
    # the adviser.
    advisers = get("adviser_book")
    assert advisers is not None
    book_rows = await execute(service_session, advisers, {}, preview=True, tenant_id=book["tenant"])
    by_name = {row["adviser"]: row for row in book_rows.rows}
    assert by_name["Ada Adviser"]["market_value"] == 1000
    assert by_name["Ada Adviser"]["clients"] == 1
    assert "Bea Adviser" not in by_name


async def test_a_movement_dated_only_by_posted_at_falls_in_the_range(
    service_session: AsyncSession,
) -> None:
    book = await _book(service_session)
    report = get("transactions")
    assert report is not None
    result = await execute(
        service_session,
        report,
        {"from_date": "2026-03-01", "to_date": "2026-03-31"},
        preview=True,
        tenant_id=book["tenant"],
    )
    amounts = sorted(row["amount"] for row in result.rows)
    assert amounts == [2, 10]
    assert any(row["transaction_date"] == "2026-03-15" for row in result.rows)
    deposit = next(row for row in result.rows if row["amount"] == 10)
    assert deposit["transaction_id"] == book["transaction_id"]
    assert deposit["client_id"] == book["client_id"]
    assert deposit["account_id"] == book["account_id"]
    # The fee carries no contact_id. The client is read off the account, so the row is kept
    # and named.
    fee = next(row for row in result.rows if row["amount"] == 2)
    assert fee["client_id"] == book["client_id"]
    assert fee["client_name"] == "Ada Shaw"


async def test_latest_valuations_keep_each_source_and_drop_the_older_date(
    service_session: AsyncSession,
) -> None:
    book = await _book(service_session)
    report = get("latest_valuations")
    assert report is not None
    result = await execute(service_session, report, {}, preview=True, tenant_id=book["tenant"])
    values = sorted(row["market_value"] for row in result.rows)
    assert values == [800, 1000]
    assert {row["source"] for row in result.rows} == {"seccl", "morgan-lloyd"}
    assert all(row["as_of_date"] == "2026-06-30" for row in result.rows)
    assert all(row["client_id"] == book["client_id"] for row in result.rows)
    assert all(row["account_id"] == book["account_id"] for row in result.rows)


async def test_a_disabled_report_is_absent_and_an_enabled_one_runs(
    service_session: AsyncSession,
) -> None:
    book = await _book(service_session)
    code = "clients_market_value"
    await service_session.execute(
        text("DELETE FROM platform.mi_reports WHERE code = :c"), {"c": code}
    )
    assert code not in await enabled_codes(service_session)
    with pytest.raises(ReportNotFound):
        await run(service_session, code, {}, preview=True, tenant_id=book["tenant"])

    await service_session.execute(
        text("INSERT INTO platform.mi_reports (code, enabled) VALUES (:c, true)"),
        {"c": code},
    )
    result = await run(service_session, code, {}, preview=True, tenant_id=book["tenant"])
    assert any(row["client_name"] == "Ada Shaw" for row in result.rows)

    await service_session.execute(
        text("DELETE FROM platform.mi_reports WHERE code = :c"), {"c": code}
    )


async def test_over_the_cap_refuses_the_file(service_session: AsyncSession) -> None:
    book = await _book(service_session)
    report = get("clients_market_value")
    assert report is not None
    with pytest.raises(ReportTooLarge) as caught:
        await execute(
            service_session,
            report,
            {},
            preview=False,
            tenant_id=book["tenant"],
            row_limit=1,
        )
    assert "no file was written" in caught.value.detail
    preview = await execute(
        service_session, report, {}, preview=True, tenant_id=book["tenant"], row_limit=1
    )
    assert preview.truncated
    assert len(preview.rows) == 1


async def test_a_missing_holdings_slice_writes_nothing(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = await _book(service_session)
    from app.lake.holdings import HoldingsUnavailable

    async def _down(*_args: object, **_kwargs: object) -> list[dict[str, Any]]:
        raise HoldingsUnavailable("slice missing")

    monkeypatch.setattr("app.reports.cash.firm_holdings_summary", _down)
    report = get("uninvested_cash")
    assert report is not None

    import json

    claims = {
        "tenant_id": book["tenant"],
        "tenant_ids": [book["tenant"]],
        "portal": "platform",
        "permissions": ["contacts:read_all"],
        "contact_ids": [],
        "account_ids": [],
    }
    async with service_session.begin_nested():
        await service_session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"),
            {"c": json.dumps(claims)},
        )
        await service_session.execute(text("SET LOCAL ROLE authenticated"))
        with pytest.raises(ReportUnavailable) as caught:
            await execute(service_session, report, {}, preview=True)
    assert "nothing was written" in caught.value.detail


async def test_the_preset_roles_can_preview_and_export(service_session: AsyncSession) -> None:
    rows = (
        await service_session.execute(
            text(
                "SELECT r.slug, p.slug "
                "FROM auth.tenants t "
                "JOIN auth.roles r ON r.tenant_id = t.id "
                "JOIN auth.role_permissions rp ON rp.role_id = r.id "
                "JOIN auth.permissions p ON p.id = rp.permission_id "
                "WHERE t.is_model AND p.slug IN ('reports:read', 'reports:export')"
            )
        )
    ).all()
    held = {(role, perm) for role, perm in rows}
    for role in ("owner", "adviser", "compliance"):
        assert (role, "reports:read") in held
        assert (role, "reports:export") in held
    assert not any(role == "client" for role, _perm in held)


def test_a_firm_session_cannot_change_the_switch(super_engine: Any, app_engine: Any) -> None:
    code = "accounts"
    with super_engine.connect() as conn:
        conn.execute(text("DELETE FROM platform.mi_reports WHERE code = :c"), {"c": code})
        conn.execute(
            text("INSERT INTO platform.mi_reports (code, enabled) VALUES (:c, false)"),
            {"c": code},
        )
    try:
        with principal_conn(
            app_engine,
            {
                "portal": "platform",
                "permissions": ["reports:export"],
                "tenant_id": 1,
                "tenant_ids": [1],
            },
        ) as conn:
            # No UPDATE grant: a firm session is refused, not filtered down to zero rows.
            with pytest.raises(ProgrammingError):
                conn.execute(
                    text("UPDATE platform.mi_reports SET enabled = true WHERE code = :c"),
                    {"c": code},
                )
            conn.rollback()
        with super_engine.connect() as conn:
            enabled = conn.execute(
                text("SELECT enabled FROM platform.mi_reports WHERE code = :c"),
                {"c": code},
            ).scalar()
            assert enabled is False
    finally:
        with super_engine.connect() as conn:
            conn.execute(text("DELETE FROM platform.mi_reports WHERE code = :c"), {"c": code})
