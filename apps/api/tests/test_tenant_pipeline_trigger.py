"""Enabling a provider asks its daemon to run that firm's chain — and cannot fail the enablement.

`PUT /admin/tenants/{id}/providers/{provider}` used to trigger only the OLD fan-out. Both paths are
live until the old one is deleted (docs/provider-pipeline.md §6), and the new one is what writes the
book for all three custodians now, so enablement drives it too.

The contract worth pinning is what happens when it does NOT work, because both failure shapes look
like success from the outside:

* the daemon is unreachable — the enablement has ALREADY been saved by then, so this must be
  reported, not raised: a 503 would tell the operator the enablement failed when it did not;
* the daemon answers having selected NOTHING. `restart_required` is that case. A tag that
  matches no models is a 400 from the daemon, surfaced as the enablement's reported error
  rather than failing the save.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import platforms as platforms_mod
from app.routers import admin


async def _firm(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name)"
                    " VALUES ('ten_' || encode(gen_random_bytes(8), 'hex'),"
                    "         'pipe-' || encode(gen_random_bytes(4), 'hex'), 'Pipeline Firm')"
                    " RETURNING id"
                )
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_an_unreachable_daemon_is_reported_not_raised(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    firm = await _firm(service_session)

    async def unavailable(provider: str, tenant_uid: str) -> dict[str, Any]:
        raise platforms_mod.PlatformUnavailable("pipeline service not configured")

    monkeypatch.setattr(platforms_mod, "trigger_tenant_refresh", unavailable)
    result = await admin._run_tenant_pipeline(service_session, firm, "seccl")

    assert result["ok"] is False
    assert "pipeline service" in str(result["detail"])


@pytest.mark.asyncio
async def test_a_run_that_selected_nothing_says_a_restart_is_needed(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reason this flag exists: the daemon's 200 is indistinguishable from a real run."""
    firm = await _firm(service_session)

    async def selected_nothing(provider: str, tenant_uid: str) -> dict[str, Any]:
        return {"id": "run-1", "models": []}

    monkeypatch.setattr(platforms_mod, "trigger_tenant_refresh", selected_nothing)
    result = await admin._run_tenant_pipeline(service_session, firm, "seccl")

    assert result["ok"] is True
    assert result["selected"] == 0
    assert result["restart_required"] is True


@pytest.mark.asyncio
async def test_a_real_run_reports_what_it_selected(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    firm = await _firm(service_session)
    uid = (
        await service_session.execute(
            text("SELECT uid FROM auth.tenants WHERE id = :t"), {"t": firm}
        )
    ).scalar_one()
    seen: dict[str, str] = {}

    async def ran(provider: str, tenant_uid: str) -> dict[str, Any]:
        seen["provider"] = provider
        seen["uid"] = tenant_uid
        return {"id": "run-2", "models": ["tenant_x.orgs", "stage_x.orgs", "book_x.merge_ledger"]}

    monkeypatch.setattr(platforms_mod, "trigger_tenant_refresh", ran)
    result = await admin._run_tenant_pipeline(service_session, firm, "sei")

    assert result["selected"] == 3
    assert result["restart_required"] is False
    # The firm is addressed by its UID, which is what `book_sink` tags every model with — passing
    # the serial id would select nothing and look exactly like the restart case above.
    assert seen == {"provider": "sei", "uid": str(uid)}


@pytest.mark.asyncio
async def test_a_tenant_that_does_not_exist_is_reported(service_session: AsyncSession) -> None:
    result = await admin._run_tenant_pipeline(service_session, 10**9, "seccl")
    assert result["ok"] is False
