"""Where a new client takes its Seccl node, and where an adviser records that choice."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import adviser_orgs, provisioning
from app.actions.attempt import OperationError
from app.adviser_orgs import chosen_seccl_node, enrolment_employee_id, provider_org_options

pytestmark = pytest.mark.asyncio


async def _firm(session: AsyncSession) -> int:
    suffix = uuid.uuid4().hex[:8]
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:u, :a, 'Adviser orgs') RETURNING id"
                ),
                {"u": f"ten_{suffix}", "a": f"orgs-{suffix}"},
            )
        ).scalar_one()
    )


async def _contact(session: AsyncSession, tenant_id: int) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :u, 'individual', 'Ada', 'Lovelace') RETURNING id"
                ),
                {"t": tenant_id, "u": uuid.uuid4().hex[:12]},
            )
        ).scalar_one()
    )


async def _node(session: AsyncSession, tenant_id: int, key: str, name: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO platform.nodes (tenant_id, provider, key, type, name) "
                    "VALUES (:t, 'seccl', :k, 'node', :n) RETURNING id"
                ),
                {"t": tenant_id, "k": key, "n": name},
            )
        ).scalar_one()
    )


async def _enable(
    session: AsyncSession,
    tenant_id: int,
    provider: str,
    ids: list[str],
    *,
    enabled: bool = True,
) -> None:
    field = "node_ids" if provider == "seccl" else "dsr_ids"
    await session.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id, provider, config, enabled) "
            "VALUES (:t, :p, CAST(CAST(:config AS text) AS jsonb), :on)"
        ),
        {"t": tenant_id, "p": provider, "config": json.dumps({field: ids}), "on": enabled},
    )


async def _adviser(
    session: AsyncSession, tenant_id: int, contact_id: int, *, seccl: str | None = None
) -> str:
    user_id = str(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.users "
                    "  (tenant_id, email, first_name, enabled, type, is_adviser, provider_orgs) "
                    "VALUES (:t, :e, 'Ann', true, '{staff}', true, CAST(:orgs AS jsonb)) "
                    "RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "e": f"ann-{uuid.uuid4().hex[:8]}@orgs.test",
                    "orgs": json.dumps({"seccl": seccl} if seccl else {}),
                },
            )
        ).scalar_one()
    )
    await session.execute(
        text(
            "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
            "VALUES (:t, :c, CAST(:u AS uuid), 'adviser')"
        ),
        {"t": tenant_id, "c": contact_id, "u": user_id},
    )
    return user_id


async def test_a_client_already_on_the_platform_keeps_their_node(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _firm(service_session)
    contact_id = await _contact(service_session, tenant_id)
    node_id = await _node(service_session, tenant_id, "21", "North")
    await service_session.execute(
        text("UPDATE crm.contacts SET node_id = :n WHERE id = :c"),
        {"n": node_id, "c": contact_id},
    )
    await _enable(service_session, tenant_id, "seccl", ["21", "22"])
    await _adviser(service_session, tenant_id, contact_id, seccl="22")
    assert await chosen_seccl_node(service_session, tenant_id=tenant_id, contact_id=contact_id) == (
        "21",
        "",
    )


async def test_a_new_client_uses_the_adviser_node_when_the_firm_has_several(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _firm(service_session)
    contact_id = await _contact(service_session, tenant_id)
    await _enable(service_session, tenant_id, "seccl", ["21", "22"])
    await _adviser(service_session, tenant_id, contact_id, seccl="22")
    assert await chosen_seccl_node(service_session, tenant_id=tenant_id, contact_id=contact_id) == (
        "22",
        "",
    )


async def test_a_firm_with_one_node_still_opens_when_the_adviser_has_not_chosen(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _firm(service_session)
    contact_id = await _contact(service_session, tenant_id)
    await _enable(service_session, tenant_id, "seccl", ["21"])
    key, gap = await chosen_seccl_node(service_session, tenant_id=tenant_id, contact_id=contact_id)
    assert key == "21"
    assert gap == ""


async def test_several_nodes_and_no_adviser_choice_names_the_team_record(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _firm(service_session)
    contact_id = await _contact(service_session, tenant_id)
    await _enable(service_session, tenant_id, "seccl", ["21", "22"])
    _key, gap = await chosen_seccl_node(service_session, tenant_id=tenant_id, contact_id=contact_id)
    assert "team record" in gap
    with pytest.raises(OperationError, match="team record"):
        from app.adviser_orgs import seccl_node_for_create

        await seccl_node_for_create(service_session, tenant_id=tenant_id, contact_id=contact_id)


async def test_the_dropdown_is_the_firms_enabled_nodes_with_their_names(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _firm(service_session)
    await _node(service_session, tenant_id, "21", "North")
    await _node(service_session, tenant_id, "99", "Not enabled")
    await _enable(service_session, tenant_id, "seccl", ["21", "40"])
    await _enable(service_session, tenant_id, "sei", ["59282"])
    options = await provider_org_options(service_session, tenant_id=tenant_id)
    assert options["seccl"] == [
        {"value": "21", "label": "North"},
        {"value": "40", "label": "40"},
    ]
    assert options["sei"] == []


async def test_a_switched_off_custodian_is_not_offered(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _firm(service_session)
    await _node(service_session, tenant_id, "21", "North")
    await _enable(service_session, tenant_id, "seccl", ["21"], enabled=False)
    await _enable(service_session, tenant_id, "sei", ["59282"])
    options = await provider_org_options(service_session, tenant_id=tenant_id)
    assert options["seccl"] == []
    assert options["sei"] == []


async def test_the_sei_dropdown_is_the_books_employees_by_seis_name(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = await _firm(service_session)
    await service_session.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id, provider, config, enabled) "
            "VALUES (:t, 'sei', CAST(CAST(:config AS text) AS jsonb), true)"
        ),
        {"t": tenant_id, "config": json.dumps({"firm_id": "3108", "dsr_ids": ["200148"]})},
    )
    await service_session.execute(
        text(
            "INSERT INTO platform.nodes (tenant_id, provider, key, type, name) VALUES "
            "(:t, 'sei', '200148', 'team', 'Alpha Investment Office'), "
            "(:t, 'sei', '58304024', 'team', 'Fiorenzo, Krampitz')"
        ),
        {"t": tenant_id},
    )
    await service_session.execute(
        text(
            "INSERT INTO auth.users "
            "  (tenant_id, email, first_name, last_name, enabled, type, provider_refs) "
            "VALUES (:t, :e, 'Stefano', 'Del Federico', true, '{staff}', CAST(:refs AS jsonb))"
        ),
        {
            "t": tenant_id,
            "e": f"stefano-{uuid.uuid4().hex[:8]}@orgs.test",
            "refs": json.dumps({"sei": "99999999"}),
        },
    )

    class _Catalogue:
        def close(self) -> None:
            return None

    monkeypatch.setattr(adviser_orgs.lake_core, "connect", lambda _provider: _Catalogue())
    monkeypatch.setattr(
        "app.integrations.sei.enrol.employees_for_picker",
        lambda _conn, firm: (
            [
                ("58304063", "Llacuna, Decormier"),
                ("58304024", "Fiorenzo, Krampitz"),
                ("58560716", None),
            ]
            if firm == "3108"
            else []
        ),
    )
    options = await provider_org_options(service_session, tenant_id=tenant_id)
    assert options["sei"] == [
        {"value": "58304024", "label": "58304024 · Fiorenzo, Krampitz"},
        {"value": "58304063", "label": "58304063 · Llacuna, Decormier"},
        {"value": "58560716", "label": "58560716"},
    ]
    assert all("Stefano" not in item["label"] for item in options["sei"])
    assert all("Alpha" not in item["label"] for item in options["sei"])
    user_id = str(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.users (tenant_id, email, enabled, type) "
                    "VALUES (:t, :e, true, '{staff}') RETURNING id"
                ),
                {"t": tenant_id, "e": f"sam-{uuid.uuid4().hex[:8]}@orgs.test"},
            )
        ).scalar_one()
    )
    saved = await provisioning.update_member_profile(
        service_session,
        tenant_id=tenant_id,
        user_id=user_id,
        fields={"sei_dsr": "58304024"},
    )
    assert saved["sei_dsr"] == "58304024"
    with pytest.raises(ValueError, match="SEI employee"):
        await provisioning.update_member_profile(
            service_session,
            tenant_id=tenant_id,
            user_id=user_id,
            fields={"sei_dsr": "200148"},
        )
    with pytest.raises(ValueError, match="SEI employee"):
        await provisioning.update_member_profile(
            service_session,
            tenant_id=tenant_id,
            user_id=user_id,
            fields={"sei_dsr": "99999999"},
        )


async def test_the_dropdown_uses_the_thin_file_only_when_the_full_file_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Catalogue:
        def execute(self, sql: str, params: list[str]) -> _Catalogue:
            assert "grantee = 'employee'" in sql
            assert params == ["3108"]
            return self

        def fetchall(self) -> list[tuple[str, str | None]]:
            return [("58304024", "Fiorenzo, Krampitz")]

        def close(self) -> None:
            return None

    monkeypatch.setattr(adviser_orgs.lake_core, "connect", lambda _provider: _Catalogue())
    monkeypatch.setattr(
        "app.integrations.sei.enrol.employees_for_picker", lambda _conn, _firm: None
    )
    assert adviser_orgs.catalogue_employees("3108") == [("58304024", "Fiorenzo, Krampitz")]


async def test_a_switched_off_sei_firm_does_not_offer_its_teams(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _firm(service_session)
    await service_session.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id, provider, config, enabled) "
            "VALUES (:t, 'sei', CAST(CAST(:config AS text) AS jsonb), false)"
        ),
        {"t": tenant_id, "config": json.dumps({"firm_id": "3116"})},
    )
    await service_session.execute(
        text(
            "INSERT INTO platform.nodes (tenant_id, provider, key, type, name) "
            "VALUES (:t, 'sei', '704899', 'team', 'Chester Rose')"
        ),
        {"t": tenant_id},
    )
    options = await provider_org_options(service_session, tenant_id=tenant_id)
    assert options["sei"] == []


async def test_the_profile_save_merges_one_custodian_and_refuses_one_outside_the_firm(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = await _firm(service_session)
    await _enable(service_session, tenant_id, "seccl", ["21"])
    await _enable(service_session, tenant_id, "sei", ["59282"])
    await service_session.execute(
        text(
            "UPDATE platform.tenant_platforms "
            "SET config = config || CAST(CAST(:extra AS text) AS jsonb) "
            "WHERE tenant_id = :t AND provider = 'sei'"
        ),
        {"t": tenant_id, "extra": json.dumps({"firm_id": "3108"})},
    )
    monkeypatch.setattr(adviser_orgs, "catalogue_employees", lambda _firm: [("59282", None)])
    user_id = str(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.users (tenant_id, email, enabled, type) "
                    "VALUES (:t, :e, true, '{staff}') RETURNING id"
                ),
                {"t": tenant_id, "e": f"sam-{uuid.uuid4().hex[:8]}@orgs.test"},
            )
        ).scalar_one()
    )
    out = await provisioning.update_member_profile(
        service_session,
        tenant_id=tenant_id,
        user_id=user_id,
        fields={"seccl_node": "21"},
    )
    assert out["seccl_node"] == "21"
    assert out["sei_dsr"] is None

    out = await provisioning.update_member_profile(
        service_session,
        tenant_id=tenant_id,
        user_id=user_id,
        fields={"sei_dsr": "59282"},
    )
    assert out["seccl_node"] == "21"
    assert out["sei_dsr"] == "59282"

    out = await provisioning.update_member_profile(
        service_session,
        tenant_id=tenant_id,
        user_id=user_id,
        fields={"job_title": "Partner"},
    )
    assert out["seccl_node"] == "21"
    assert out["sei_dsr"] == "59282"

    out = await provisioning.update_member_profile(
        service_session,
        tenant_id=tenant_id,
        user_id=user_id,
        fields={"seccl_node": ""},
    )
    assert out["seccl_node"] is None
    assert out["sei_dsr"] == "59282"

    with pytest.raises(ValueError, match="not one this firm has enabled"):
        await provisioning.update_member_profile(
            service_session,
            tenant_id=tenant_id,
            user_id=user_id,
            fields={"seccl_node": "99"},
        )


async def _user_with_sei(session: AsyncSession, tenant_id: int, employee_id: str) -> str:
    return str(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.users "
                    "  (tenant_id, email, first_name, enabled, type, is_adviser, provider_refs) "
                    "VALUES (:t, :e, 'Ann', true, '{staff}', true, CAST(:refs AS jsonb)) "
                    "RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "e": f"ann-{uuid.uuid4().hex[:8]}@orgs.test",
                    "refs": json.dumps({"sei": employee_id}),
                },
            )
        ).scalar_one()
    )


async def test_enrolment_uses_the_journey_adviser_when_the_client_has_none(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _firm(service_session)
    contact_id = await _contact(service_session, tenant_id)
    adviser = await _user_with_sei(service_session, tenant_id, "58494889")
    application_uid = uuid.uuid4().hex[:12]
    await service_session.execute(
        text(
            "INSERT INTO crm.applications (tenant_id, uid, status, adviser_id) "
            "VALUES (:t, :u, 'draft', CAST(:a AS uuid))"
        ),
        {"t": tenant_id, "u": application_uid, "a": adviser},
    )
    assert (
        await enrolment_employee_id(
            service_session,
            tenant_id=tenant_id,
            contact_id=contact_id,
            application_uid=application_uid,
        )
        == "58494889"
    )


async def test_the_clients_lead_adviser_wins_over_the_journey_adviser(
    service_session: AsyncSession,
) -> None:
    tenant_id = await _firm(service_session)
    contact_id = await _contact(service_session, tenant_id)
    lead = await _user_with_sei(service_session, tenant_id, "11111111")
    journey = await _user_with_sei(service_session, tenant_id, "22222222")
    await service_session.execute(
        text(
            "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
            "VALUES (:t, :c, CAST(:u AS uuid), 'adviser')"
        ),
        {"t": tenant_id, "c": contact_id, "u": lead},
    )
    application_uid = uuid.uuid4().hex[:12]
    await service_session.execute(
        text(
            "INSERT INTO crm.applications (tenant_id, uid, status, adviser_id) "
            "VALUES (:t, :u, 'draft', CAST(:a AS uuid))"
        ),
        {"t": tenant_id, "u": application_uid, "a": journey},
    )
    assert (
        await enrolment_employee_id(
            service_session,
            tenant_id=tenant_id,
            contact_id=contact_id,
            application_uid=application_uid,
        )
        == "11111111"
    )
