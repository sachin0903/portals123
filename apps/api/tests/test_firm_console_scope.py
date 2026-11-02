"""The per-firm console shows ONE firm's records, or it shows none and says why.

Two ways it did not. Both read as ordinary pages, which is what made them worth a test rather
than a fix:

* **A blank enablement config served every firm's records.** `_slice_clause` returned no WHERE at
  all when a firm had no slice keys, reasoning that an empty table reads as "this custodian holds
  nothing for us" and that the page states the firm's keys beside the tables. But `raw.contacts`
  is every contact in the Dynamics ORG, the mitigation was UI copy on one page, and the property
  being relied on was an API one: `GET /admin/platforms/morgan-lloyd/firms/{uid}/entities/contacts`
  handed another firm's clients to any `platforms:read` holder.
* **A DISABLED enablement still served rows.** `_firm_slice` selected `tp.enabled` and never read
  it, so the switch that disconnects a firm from a custodian disconnected nothing here.

Both answer 409 rather than 404 or an empty table: the firm IS connected and the data IS
reachable — it is the enablement that cannot say which rows are theirs. That is a conflict with
configuration, and it is fixable by an operator who is told about it.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import platform_entities as pe
from app.routers import admin


async def _firm(session: AsyncSession, *, config: str, enabled: bool) -> str:
    """A tenant with a Morgan Lloyd enablement, returning its uid."""
    uid = (
        await session.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name)"
                " VALUES ('ten_' || encode(gen_random_bytes(8), 'hex'),"
                "         'scope-' || encode(gen_random_bytes(4), 'hex'), 'Scoped Firm')"
                " RETURNING uid"
            )
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id, provider, config, enabled)"
            " SELECT id, 'morgan-lloyd', CAST(:c AS jsonb), :e FROM auth.tenants WHERE uid = :uid"
        ),
        {"c": config, "e": enabled, "uid": uid},
    )
    return str(uid)


@pytest.mark.asyncio
async def test_a_configured_firm_resolves_its_slice_keys(service_session: AsyncSession) -> None:
    uid = await _firm(service_session, config='{"tenant_ids": ["dyn-a", "dyn-b"]}', enabled=True)
    _tenant_id, keys = await admin._firm_slice(service_session, "morgan-lloyd", uid)
    assert keys == ["dyn-a", "dyn-b"]


@pytest.mark.asyncio
async def test_a_disabled_enablement_serves_nothing(service_session: AsyncSession) -> None:
    """Distinguished from "never connected" (404), because turning it back on is a different act
    from configuring it for the first time."""
    uid = await _firm(service_session, config='{"tenant_ids": ["dyn-a"]}', enabled=False)
    with pytest.raises(HTTPException) as caught:
        await admin._firm_slice(service_session, "morgan-lloyd", uid)
    assert caught.value.status_code == 409
    assert "disabled" in str(caught.value.detail)


@pytest.mark.asyncio
async def test_a_firm_never_connected_is_a_404(service_session: AsyncSession) -> None:
    uid = (
        await service_session.execute(
            text(
                "INSERT INTO auth.tenants (uid, alias, name)"
                " VALUES ('ten_' || encode(gen_random_bytes(8), 'hex'),"
                "         'none-' || encode(gen_random_bytes(4), 'hex'), 'Unconnected')"
                " RETURNING uid"
            )
        )
    ).scalar_one()
    with pytest.raises(HTTPException) as caught:
        await admin._firm_slice(service_session, "morgan-lloyd", str(uid))
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_a_blank_config_cannot_narrow_a_shared_relation(
    service_session: AsyncSession,
) -> None:
    """The leak, at the layer that leaked. `_firm_slice` resolves no keys, and every Morgan Lloyd
    lake entity then refuses rather than selecting the whole org."""
    uid = await _firm(service_session, config="{}", enabled=True)
    _tenant_id, keys = await admin._firm_slice(service_session, "morgan-lloyd", uid)
    assert keys == []

    shared = [e for e in pe.entities_for("morgan-lloyd") if e.source == "lake"]
    assert shared, "morgan_lloyd should declare lake entities"
    for entity in shared:
        assert not pe.is_narrowable(entity, keys), entity.key
        with pytest.raises(pe.Unnarrowable):
            pe._slice_clause(entity, keys)


@pytest.mark.asyncio
async def test_the_outbound_tabs_are_scoped_by_tenant_not_by_slice_key(
    service_session: AsyncSession,
) -> None:
    """The three `audit.api_log` tabs carry `slice_column=None`, so the refusal above must not
    catch them: they are narrowed by a bound `tenant_id` in the query, which a blank vendor config
    has no bearing on."""
    uid = await _firm(service_session, config="{}", enabled=True)
    tenant_id, keys = await admin._firm_slice(service_session, "morgan-lloyd", uid)
    assert tenant_id > 0 and keys == []

    outbound = [e for e in pe.entities_for("morgan-lloyd") if e.source == "outbound"]
    assert outbound, "morgan_lloyd should declare outbound entities"
    for entity in outbound:
        assert pe.is_narrowable(entity, keys), entity.key
