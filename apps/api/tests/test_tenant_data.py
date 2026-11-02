from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app import demo_seed, storage, tenant_data


async def _tenant(session: AsyncSession, *, enabled: bool = False) -> tuple[int, str, str]:
    suffix = uuid.uuid4().hex[:8]
    uid = f"ten_{suffix}"
    alias = f"reset-{suffix}"
    tenant_id = (
        await session.execute(
            text(
                "INSERT INTO auth.tenants (uid,alias,name,enabled) "
                "VALUES (:uid,:alias,'Reset test',:enabled) RETURNING id"
            ),
            {"uid": uid, "alias": alias, "enabled": enabled},
        )
    ).scalar_one()
    return int(tenant_id), uid, alias


@pytest.mark.asyncio
async def test_reset_manifest_classifies_every_tenant_table(
    service_session: AsyncSession,
) -> None:
    await tenant_data.assert_manifest_complete(service_session)


@pytest.mark.asyncio
async def test_a_custodians_landing_tables_are_staging_and_a_reset_drops_them(
    service_session: AsyncSession, super_engine: Engine
) -> None:
    """Per-provider landing tables carry tenant_id and are not in the manifest.

    They are created by the pipeline after the migrations, so the manifest cannot name them.
    A reset still has to drop this firm's, and leave another firm's alone.
    """
    tenant_id, uid, _alias = await _tenant(service_session)
    other = f"ten_{uuid.uuid4().hex[:8]}"
    own = f"{uid}__clients"
    kept = f"{other}__clients"
    with super_engine.connect() as conn:
        conn.execute(text("GRANT book_writer TO app"))
        conn.execute(text("SET ROLE book_writer"))
        conn.execute(text(f'CREATE TABLE inbound_seccl."{own}" (tenant_id int)'))  # noqa: S608
        conn.execute(text(f'CREATE TABLE inbound_sei."{kept}" (tenant_id int)'))  # noqa: S608
        conn.execute(text("RESET ROLE"))
    try:
        await tenant_data.assert_manifest_complete(service_session)
        counts = await tenant_data.clear_database(service_session, tenant_id, uid)
        assert counts["inbound.tables"] == 1
        # Same transaction as the drop. Another connection would still see the table, and
        # dropping it there waits on this transaction's lock.
        own_left = (
            await service_session.execute(
                text("SELECT to_regclass(:n)"), {"n": f'inbound_seccl."{own}"'}
            )
        ).scalar()
        kept_left = (
            await service_session.execute(
                text("SELECT to_regclass(:n)"), {"n": f'inbound_sei."{kept}"'}
            )
        ).scalar()
        assert own_left is None
        assert kept_left is not None
    finally:
        await service_session.execute(text("SET LOCAL ROLE book_writer"))
        await service_session.execute(text(f'DROP TABLE IF EXISTS inbound_sei."{kept}"'))  # noqa: S608
        await service_session.execute(text(f'DROP TABLE IF EXISTS inbound_seccl."{own}"'))  # noqa: S608
        await service_session.execute(text("SET LOCAL ROLE service_role"))
        with super_engine.connect() as conn:
            conn.execute(text("REVOKE book_writer FROM app"))


@pytest.mark.asyncio
async def test_database_reset_clears_domain_and_preserves_configuration(
    service_session: AsyncSession,
) -> None:
    tenant_id, uid, _alias = await _tenant(service_session)
    product_id = (
        await service_session.execute(
            text(
                "INSERT INTO platform.products (tenant_id,product_type,name,code,enabled) "
                "VALUES (:t,'GIA','Test GIA','GIA',true) RETURNING id"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    contact_id = (
        await service_session.execute(
            text(
                "INSERT INTO crm.contacts (tenant_id,type,name,data) "
                "VALUES (:t,'individual','Delete me','{}') RETURNING id"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    await service_session.execute(
        text(
            "INSERT INTO platform.accounts (tenant_id,contact_id,product_id,status,name) "
            "VALUES (:t,:c,:p,'active','Delete me')"
        ),
        {"t": tenant_id, "c": contact_id, "p": product_id},
    )

    counts = await tenant_data.clear_database(service_session, tenant_id, uid)

    assert counts["crm.contacts"] == 1
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM crm.contacts WHERE tenant_id=:t"), {"t": tenant_id}
        )
    ).scalar_one() == 0
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM platform.products WHERE tenant_id=:t"), {"t": tenant_id}
        )
    ).scalar_one() == 1
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM auth.tenants WHERE id=:t"), {"t": tenant_id}
        )
    ).scalar_one() == 1


@pytest.mark.asyncio
async def test_a_client_login_named_by_its_own_records_is_still_removed(
    service_session: AsyncSession,
) -> None:
    """`documents.audit_log.actor_id` is ON DELETE NO ACTION, so the login goes last."""
    tenant_id, uid, _alias = await _tenant(service_session)
    user_id = (
        await service_session.execute(
            text(
                "INSERT INTO auth.users (tenant_id,email,first_name,last_name,enabled,type) "
                "VALUES (:t,:email,'Demo','Client',true,ARRAY['client']::text[]) RETURNING id"
            ),
            {"t": tenant_id, "email": f"client-{uid}@example.test"},
        )
    ).scalar_one()
    await service_session.execute(
        text(
            "INSERT INTO documents.audit_log (tenant_id,actor_id,action) "
            "VALUES (:t,:u,'document.viewed')"
        ),
        {"t": tenant_id, "u": user_id},
    )

    counts = await tenant_data.clear_database(service_session, tenant_id, uid)

    assert counts["documents.audit_log"] == 1
    assert counts["auth.client_users"] == 1
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM auth.users WHERE id=:u"), {"u": user_id}
        )
    ).scalar_one() == 0


@pytest.mark.asyncio
async def test_a_login_a_preserved_row_still_names_is_kept_not_a_failure(
    service_session: AsyncSession,
) -> None:
    tenant_id, uid, _alias = await _tenant(service_session)
    kept, removed = [
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.users (tenant_id,email,first_name,last_name,enabled,type) "
                    "VALUES (:t,:email,'Demo','Client',true,ARRAY['client']::text[]) RETURNING id"
                ),
                {"t": tenant_id, "email": f"{name}-{uid}@example.test"},
            )
        ).scalar_one()
        for name in ("author", "client")
    ]
    await service_session.execute(
        text(
            "INSERT INTO documents.templates "
            "  (uid,tenant_id,code,name,document_type,created_by) "
            "VALUES (:uid,:t,'letter','Letter','letter',:u)"
        ),
        {"uid": f"tpl{uid[-9:]}", "t": tenant_id, "u": kept},
    )

    counts = await tenant_data.clear_database(service_session, tenant_id, uid)

    assert counts["auth.client_users"] == 1
    assert set(
        (
            await service_session.execute(
                text("SELECT id FROM auth.users WHERE tenant_id=:t"), {"t": tenant_id}
            )
        ).scalars()
    ) == {kept}
    assert removed


@pytest.mark.asyncio
async def test_enqueue_requires_suspension_and_pauses_without_losing_config(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, _uid, alias = await _tenant(service_session, enabled=True)
    await service_session.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id,provider,enabled,config) "
            "VALUES (:t,'seccl',true,'{\"node_ids\":[\"demo\"]}')"
        ),
        {"t": tenant_id},
    )
    monkeypatch.setattr(tenant_data.settings, "app_stage", "production")
    with pytest.raises(ValueError, match="production"):
        await tenant_data.enqueue(
            service_session,
            tenant_id=tenant_id,
            kind="reset",
            confirm_alias=alias,
            requested_by=None,
        )
    monkeypatch.setattr(tenant_data.settings, "app_stage", "development")
    with pytest.raises(ValueError, match="suspend"):
        await tenant_data.enqueue(
            service_session,
            tenant_id=tenant_id,
            kind="reset",
            confirm_alias=alias,
            requested_by=None,
        )
    await service_session.execute(
        text("UPDATE auth.tenants SET enabled=false WHERE id=:t"), {"t": tenant_id}
    )
    with pytest.raises(ValueError, match="confirmation"):
        await tenant_data.enqueue(
            service_session,
            tenant_id=tenant_id,
            kind="reset",
            confirm_alias="wrong",
            requested_by=None,
        )
    await tenant_data.enqueue(
        service_session,
        tenant_id=tenant_id,
        kind="reset",
        confirm_alias=alias,
        requested_by=None,
    )
    row = (
        (
            await service_session.execute(
                text(
                    "SELECT enabled,config,refresh_paused_at IS NOT NULL AS paused "
                    "FROM platform.tenant_platforms WHERE tenant_id=:t"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one()
    )
    assert row["enabled"] is True
    assert row["config"]["node_ids"] == ["demo"]
    assert row["paused"] is True
    with pytest.raises(ValueError, match="running"):
        await tenant_data.resume_refreshes(service_session, tenant_id)
    await service_session.execute(
        text(
            "UPDATE platform.tenant_data_jobs SET status='completed',completed_at=now() "
            "WHERE tenant_id=:t"
        ),
        {"t": tenant_id},
    )
    assert await tenant_data.resume_refreshes(service_session, tenant_id) == 1


def test_demo_predicates_skip_empty_id_lists() -> None:
    columns = {"tenant_id", "contact_id", "account_id", "data"}
    assert tenant_data._demo_predicates(
        "platform.instructions",
        columns,
        contacts=[],
        accounts=[],
        apps=[],
        executions=[],
        cases=[],
    ) == ["COALESCE(data->>'seeded_demo','') = 'true'"]
    assert "contact_id = ANY(:contacts)" in tenant_data._demo_predicates(
        "platform.instructions",
        columns,
        contacts=[1],
        accounts=[],
        apps=[],
        executions=[],
        cases=[],
    )


@pytest.mark.asyncio
async def test_seed_book_enqueues_while_the_firm_is_active(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, _uid, alias = await _tenant(service_session, enabled=True)
    await service_session.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id,provider,enabled,config) "
            "VALUES (:t,'seccl',true,'{\"node_ids\":[\"demo\"]}')"
        ),
        {"t": tenant_id},
    )
    monkeypatch.setattr(tenant_data.settings, "app_stage", "development")
    job = await tenant_data.enqueue(
        service_session,
        tenant_id=tenant_id,
        kind="seed_book",
        confirm_alias=alias,
        requested_by=None,
    )
    assert job["kind"] == "seed_book"
    paused = (
        await service_session.execute(
            text("SELECT refresh_pause_reason FROM platform.tenant_platforms WHERE tenant_id=:t"),
            {"t": tenant_id},
        )
    ).scalar_one()
    assert paused == "demo book seed"


@pytest.mark.asyncio
async def test_clear_demo_book_keeps_real_clients(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, uid, _alias = await _tenant(service_session)
    await service_session.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id,provider,enabled,config) "
            "VALUES (:t,'seccl',true,'{}')"
        ),
        {"t": tenant_id},
    )
    await service_session.execute(
        text(
            "INSERT INTO platform.products (tenant_id,product_type,name,code,enabled) "
            "VALUES (:t,'GIA','Demo GIA','GIA',true),"
            "       (:t,'ISA','Demo ISA','ISA',true),"
            "       (:t,'SIPP','Demo SIPP','SIPP',true),"
            "       (:t,'TPOFFBOND','Demo Offshore Bond','TPOFFBOND',false)"
        ),
        {"t": tenant_id},
    )
    await service_session.execute(
        text(
            "INSERT INTO auth.users "
            " (tenant_id,email,first_name,last_name,enabled,type,is_adviser) "
            "VALUES (:t,:email,'Demo','Adviser',true,ARRAY['staff']::text[],true)"
        ),
        {"t": tenant_id, "email": f"adviser-{uid}@example.test"},
    )
    kept_id = (
        await service_session.execute(
            text(
                "INSERT INTO crm.contacts (tenant_id,type,name,email,data) "
                "VALUES (:t,'individual','Keep Me','keep@example.test','{}') RETURNING id"
            ),
            {"t": tenant_id},
        )
    ).scalar_one()
    monkeypatch.setattr(demo_seed, "_PEOPLE", demo_seed._PEOPLE[:2])
    monkeypatch.setattr(demo_seed, "_seed_journeys", AsyncMock())
    await demo_seed._seed_database(service_session, tenant_id, uid)
    names = set(
        (
            await service_session.execute(
                text("SELECT name FROM crm.contacts WHERE tenant_id=:t"),
                {"t": tenant_id},
            )
        )
        .scalars()
        .all()
    )
    assert "Keep Me" in names
    assert "Amelia Hart" in names
    await tenant_data.clear_demo_book(service_session, tenant_id)
    remaining = list(
        (
            await service_session.execute(
                text("SELECT id, name FROM crm.contacts WHERE tenant_id=:t"),
                {"t": tenant_id},
            )
        ).all()
    )
    assert remaining == [(kept_id, "Keep Me")]
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM platform.accounts WHERE tenant_id=:t"),
            {"t": tenant_id},
        )
    ).scalar_one() == 0


@pytest.mark.asyncio
async def test_demo_seed_is_reconciled_and_marks_every_link(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, uid, _alias = await _tenant(service_session)
    await service_session.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id,provider,enabled,config) "
            "VALUES (:t,'morgan-lloyd',true,'{}'), (:t,'seccl',true,'{}')"
        ),
        {"t": tenant_id},
    )
    await service_session.execute(
        text(
            "INSERT INTO platform.products (tenant_id,product_type,name,code,enabled) "
            "VALUES (:t,'GIA','Demo GIA','GIA',true),"
            "       (:t,'ISA','Demo ISA','ISA',true),"
            "       (:t,'SIPP','Demo SIPP','SIPP',true),"
            "       (:t,'TPOFFBOND','Demo Offshore Bond','TPOFFBOND',false)"
        ),
        {"t": tenant_id},
    )
    user_id = (
        await service_session.execute(
            text(
                "INSERT INTO auth.users "
                " (tenant_id,email,first_name,last_name,enabled,type,is_adviser) "
                "VALUES (:t,:email,'Demo','Adviser',true,ARRAY['staff']::text[],true) "
                "RETURNING id"
            ),
            {"t": tenant_id, "email": f"adviser-{uid}@example.test"},
        )
    ).scalar_one()
    assert user_id
    monkeypatch.setattr(demo_seed, "_PEOPLE", demo_seed._PEOPLE[:3])
    journeys = AsyncMock()
    monkeypatch.setattr(demo_seed, "_seed_journeys", journeys)

    counts, lake = await demo_seed._seed_database(service_session, tenant_id, uid)

    assert counts == {
        "clients": 3,
        "accounts": 7,
        "transactions": 652,
        "valuations": 791,
        "positions": 254,
        "transfer_providers": 6,
    }
    mobiles = (
        (
            await service_session.execute(
                text("SELECT mobile FROM crm.contacts WHERE tenant_id=:t ORDER BY id"),
                {"t": tenant_id},
            )
        )
        .scalars()
        .all()
    )
    assert mobiles == ["+44 7700 900000", "+44 7700 900001", "+44 7700 900002"]
    account_values = dict(
        (
            await service_session.execute(
                text(
                    "SELECT l.external_id, a.value::float8 FROM platform.accounts a "
                    "JOIN platform.links l ON l.entity_type='account' AND l.entity_id=a.id "
                    "WHERE a.tenant_id=:t"
                ),
                {"t": tenant_id},
            )
        ).all()
    )
    for external_id, expected in account_values.items():
        assert sum(
            p["market_value"] for p in lake["positions"] if p["account_external_id"] == external_id
        ) == pytest.approx(expected, abs=0.01)
        latest = max(
            (row for row in lake["valuations"] if row["account_external_id"] == external_id),
            key=lambda row: row["as_of_date"],
        )
        assert latest["market_value"] == pytest.approx(expected, abs=0.01)
    # Every seeded account must be addressable by its provider id, so the count follows the
    # accounts rather than restating a figure that goes stale whenever the book shape changes.
    assert (
        await service_session.execute(
            text(
                "SELECT count(*) FROM platform.links "
                "WHERE tenant_id=:t AND data->>'seeded_demo'='true'"
            ),
            {"t": tenant_id},
        )
    ).scalar_one() == counts["accounts"]
    assert set(
        (
            await service_session.execute(
                text("SELECT DISTINCT provider::text FROM platform.links WHERE tenant_id=:t"),
                {"t": tenant_id},
            )
        ).scalars()
    ) == {"seccl"}
    journeys.assert_awaited_once()
    journey_contacts = journeys.await_args.args[2]
    journey_accounts = journeys.await_args.args[3]
    assert all(
        any(account_contact == contact_id for _, account_contact, _ in journey_accounts)
        for contact_id in journey_contacts
    )
    transaction_rows = (
        (
            await service_session.execute(
                text(
                    "SELECT t.type::text AS type, t.source_type, t.source_subtype, t.data,"
                    " p.product_type::text AS product_type "
                    "FROM platform.transactions t "
                    "JOIN platform.accounts a ON a.id=t.account_id "
                    "JOIN platform.products p ON p.id=a.product_id "
                    "WHERE t.tenant_id=:t"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    assert all(row["source_subtype"] != "Demo movement" for row in transaction_rows)
    transfers = [row for row in transaction_rows if row["type"] == "transfer_in"]
    assert transfers
    assert {row["product_type"] for row in transfers} == {"SIPP"}
    assert all(row["data"]["previous_provider_name"] for row in transfers)
    providers = (
        (
            await service_session.execute(
                text(
                    "SELECT name, products FROM platform.transfer_providers "
                    "WHERE tenant_id=:t AND data->>'seeded_demo'='true' ORDER BY name"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    assert [row["name"] for row in providers] == sorted(
        provider["name"] for provider in demo_seed._TRANSFER_PROVIDERS
    )
    assert all("SIPP" in row["products"] for row in providers)
    mirror_contact = (
        (
            await service_session.execute(
                text("SELECT id FROM crm.contacts WHERE tenant_id=:t ORDER BY id LIMIT 1"),
                {"t": tenant_id},
            )
        )
        .scalars()
        .one()
    )
    mirror_accounts = (
        (
            await service_session.execute(
                text(
                    "SELECT a.id, a.name, a.value::float8 AS value, a.performance,"
                    " p.product_type::text AS product_type "
                    "FROM platform.accounts a JOIN platform.products p ON p.id=a.product_id "
                    "WHERE a.contact_id=:c ORDER BY p.product_type::text"
                ),
                {"c": mirror_contact},
            )
        )
        .mappings()
        .all()
    )
    assert {row["product_type"] for row in mirror_accounts} == {
        "GIA",
        "ISA",
        "SIPP",
        "TPOFFBOND",
    }
    assert {row["name"] for row in mirror_accounts} == {
        "Retirement Portfolio",
        "Medium-Term ISA",
        "Short-Term Investment Account",
        "International Investment Bond",
    }
    assert sum(len(row["performance"]) for row in mirror_accounts) == 79
    mirror_ids = [row["id"] for row in mirror_accounts]
    assert (
        await service_session.execute(
            text("SELECT count(*) FROM platform.transactions WHERE account_id=ANY(:ids)"),
            {"ids": mirror_ids},
        )
    ).scalar_one() == 536
    assert (
        len(
            [
                row
                for row in lake["positions"]
                if str(row["account_external_id"]).startswith(f"demo-{uid}-1-")
            ]
        )
        == 157
    )
    assert all(
        isinstance(row["book_cost"], int | float) and row["book_cost"] > 0
        for row in lake["positions"]
    )
    assert all(isinstance(row["unrealised_gain"], int | float) for row in lake["positions"])
    activity: dict[str, set[str]] = {}
    for row in lake["transactions"]:
        account_id = str(row["account_external_id"])
        key = str(row["isin"]) if row.get("isin") else f"cash:{row.get('currency') or 'GBP'}"
        activity.setdefault(account_id, set()).add(key)
    for row in lake["positions"]:
        key = str(row["isin"]) if row.get("isin") else f"cash:{row.get('currency') or 'GBP'}"
        assert key in activity[str(row["account_external_id"])]
    latest_total = (
        await service_session.execute(
            text(
                "SELECT sum(v.market_value)::float8 FROM platform.valuations v "
                "WHERE v.account_id=ANY(:ids) AND v.as_of_date=("
                " SELECT max(v2.as_of_date) FROM platform.valuations v2 "
                " WHERE v2.account_id=ANY(:ids))"
            ),
            {"ids": mirror_ids},
        )
    ).scalar_one()
    assert latest_total == pytest.approx(sum(row["value"] for row in mirror_accounts), abs=0.01)


def test_demo_seed_grants_the_reader_before_writing_the_lake() -> None:
    """The writer attach creates the catalogue schema and does not grant lake_reader.

    Without USAGE the portal shows the account values and an empty holdings list.
    """
    import inspect

    source = inspect.getsource(demo_seed.seed)
    assert source.index("ensure_tenant_catalog") < source.index("_write_lake")


def test_demo_seed_prefers_a_custodian_over_an_administrator() -> None:
    """Graphene enables Morgan Lloyd; taking the first tenant_platforms row hid the client book."""
    assert demo_seed._book_provider(["morgan-lloyd", "seccl"]) == "seccl"
    assert demo_seed._book_provider(["morgan-lloyd", "sei"]) == "sei"
    assert demo_seed._book_provider(["sei", "seccl"]) == "seccl"
    assert demo_seed._book_provider(["morgan-lloyd"]) == "morgan-lloyd"
    with pytest.raises(ValueError, match="enabled provider"):
        demo_seed._book_provider([])


def test_every_demo_employment_status_matches_the_contact_contract() -> None:
    allowed = {
        "employed",
        "self_employed",
        "retired",
        "unemployed",
        "education",
        "care_under_16s",
        "care_over_16",
        "other",
    }

    assert {
        employment.lower().replace(" ", "_")
        for *_identity, employment, _occupation in demo_seed._PEOPLE
    } <= allowed


def test_demo_source_book_is_anonymised_and_keeps_the_complete_shapes() -> None:
    book = demo_seed._source_book()
    accounts = {account["key"]: account for account in book["accounts"]}

    assert book["version"] == 2
    assert set(accounts) == {"retirement", "medium_term", "short_term", "international"}
    assert sum(len(account["positions"]) for account in accounts.values()) == 165
    assert sum(len(demo_seed._held_positions(account)) for account in accounts.values()) == 157
    assert sum(len(account["transactions"]) for account in accounts.values()) == 471
    assert sum(len(account["valuations"]) for account in accounts.values()) == 452
    assert {len(account["valuations"]) for account in accounts.values()} == {113}
    return_dates = {
        point["date"] for account in accounts.values() for point in account["performance"]
    }
    assert len(return_dates) == 21
    serialised = json.dumps(book)
    assert "Barry" not in serialised
    assert "Clarke" not in serialised
    assert "MSP000" not in serialised
    fixture = json.loads(
        Path(demo_seed.__file__).with_name("demo_data").joinpath("client_book.json").read_text()
    )
    held = [
        position
        for account in fixture["accounts"]
        for position in account["positions"]
        if Decimal(str(position["value"])) != 0
    ]
    assert held
    assert all(
        isinstance(position.get("book_cost"), int | float) and position["book_cost"] > 0
        for position in held
    )
    assert any(position.get("unrealised_gain") for position in held)


def test_demo_holdings_have_cost_gain_and_a_ledger_row() -> None:
    """The ISA/GIA extracts have holdings and no transactions; the holdings extract has no cost.

    Those three figures are now on the fixture (and stamped again on load) so the lake writes
    numeric columns rather than NULL. Every current holding also gets at least one movement,
    including on abstract clients that only keep a slice of the source ledger.
    """
    equity_gains = 0
    for account in demo_seed._source_book()["accounts"]:
        held = demo_seed._held_positions(account)
        assert held
        assert all(Decimal(str(position["value"])) != 0 for position in held)
        for position in held:
            cost, gain, pct = demo_seed._position_economics(position, account)
            assert cost > 0
            assert position["book_cost"] == float(cost)
            assert position["unrealised_gain"] == float(gain)
            assert position["unrealised_gain_percent"] == float(pct)
            if demo_seed._is_cash_position(position):
                assert cost == Decimal(str(position["value"]))
                assert gain == 0
                assert pct == 0
            else:
                assert cost + gain == Decimal(str(position["value"]))
                if gain != 0:
                    equity_gains += 1
        for client_index in (0, 1):
            covered = {
                demo_seed._activity_key(movement)
                for movement in demo_seed._activity_for_account(account, client_index)
            }
            for position in held:
                assert demo_seed._position_activity_key(position) in covered
    assert equity_gains > 0


@pytest.mark.asyncio
async def test_demo_application_uses_the_live_application_shape(
    service_session: AsyncSession,
) -> None:
    tenant_id, _uid, _alias = await _tenant(service_session)

    application_id = await demo_seed._create_demo_application(service_session, tenant_id, "sei")

    assert (
        await service_session.execute(
            text("SELECT status || ':' || custody::text FROM crm.applications WHERE id=:id"),
            {"id": application_id},
        )
    ).scalar_one() == "draft:onshore"


def test_object_reset_preserves_only_tenant_branding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeS3:
        deleted: list[str] = []

        def list_objects_v2(self, **_kwargs: object) -> dict[str, object]:
            return {
                "Contents": [
                    {"Key": "files/tenant/logo.svg"},
                    {"Key": "files/client/document.pdf"},
                    {"Key": "datalake/platform/positions.parquet"},
                ],
                "IsTruncated": False,
            }

        def delete_objects(self, **kwargs: object) -> dict[str, object]:
            delete = kwargs["Delete"]
            assert isinstance(delete, dict)
            self.deleted.extend(str(item["Key"]) for item in delete["Objects"])
            return {}

    fake = FakeS3()
    monkeypatch.setattr(storage, "client_for", lambda *_args: fake)
    deleted = storage._delete_objects_except(
        "tenant-bucket", ("files/tenant/",), endpoint="http://storage.test"
    )

    assert deleted == 2
    assert fake.deleted == [
        "files/client/document.pdf",
        "datalake/platform/positions.parquet",
    ]
