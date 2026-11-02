"""Onboarding account activation pushes the SIPP to its scheme administrator."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import account_enrol, catalogue, providers


async def _tenant(session: AsyncSession) -> int:
    suffix = uuid.uuid4().hex[:8]
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"enr_{suffix}", "a": f"enr-{suffix}", "n": f"Enrol {suffix}"},
            )
        ).scalar_one()
    )


async def _contact(session: AsyncSession, tenant_id: int) -> int:
    suffix = uuid.uuid4().hex[:8]
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO crm.contacts "
                    "  (tenant_id, uid, type, name, first_name, surname, email) "
                    "VALUES (:t, :u, 'individual', :n, 'Ada', 'Nwosu', :e) RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "u": f"c{suffix}0000"[:12],
                    "n": "Ada Nwosu",
                    "e": f"ada.{suffix}@example.com",
                },
            )
        ).scalar_one()
    )


async def _account(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    product_id: int,
    custodian: str = "seccl",
) -> int:
    suffix = uuid.uuid4().hex[:8]
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "  (tenant_id, uid, contact_id, product_id, name, status, custodian) "
                    "VALUES (:t, :u, :c, :p, 'Nwosu SIPP', 'draft', "
                    "        CAST(:custodian AS platform.provider_code)) "
                    "RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "u": f"a{suffix}0000"[:12],
                    "c": contact_id,
                    "p": product_id,
                    "custodian": custodian,
                },
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_activating_a_sipp_syncs_the_scheme_administrator(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Morgan Lloyd is the administrator, not a cash rail: setup_account must mint the
    client/scheme/policy there. Seccl/SEI enrolment stays pending_ops — those write-backs
    are not built, and pretending they ran would stamp a link the lake cannot match."""
    tenant_id = await _tenant(service_session)
    contact_id = await _contact(service_session, tenant_id)
    sipp = await catalogue.upsert_product(
        service_session, tenant_id, {"name": "Personal Pension", "product_type": "SIPP"}
    )
    await providers.set_provider(
        service_session,
        tenant_id,
        "morgan-lloyd",
        config={"tenant_ids": ["g-1"]},
        enabled=True,
    )
    account_id = await _account(
        service_session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        product_id=int(sipp["id"]),
    )

    seen: dict[str, object] = {}

    async def _fake_sync(
        session: object,
        *,
        contact_id: int,
        account_id: int | None = None,
        tenant_id: int | None = None,
    ) -> dict[str, object]:
        # `tenant_id` travels since the sync writes links under the client's own tenant
        # (b939a6b7); the report below still names the client and the account.
        seen.update(contact_id=contact_id, account_id=account_id)
        return {"contact": {"status": "created"}, "accounts": [{"status": "created"}]}

    from app.integrations import morgan_lloyd as ml

    monkeypatch.setattr(ml, "configured", lambda: True)
    monkeypatch.setattr(ml, "sync_client", _fake_sync)

    report = await account_enrol.sync_activated_account(
        service_session, tenant_id=tenant_id, account_id=account_id
    )
    assert seen == {"contact_id": contact_id, "account_id": account_id}
    assert report["administrator"]["status"] == "synced"
    assert report["administrator"]["provider"] == "morgan-lloyd"
    assert report["custodian"]["status"] == "pending_ops"
    assert report["custodian"]["provider"] == "seccl"
    assert report["custodian"]["reason"] == "enrolment_unbuilt"

    data = (
        await service_session.execute(
            text("SELECT data FROM platform.accounts WHERE id = :a"), {"a": account_id}
        )
    ).scalar_one()
    assert data["platform_sync"]["administrator"]["status"] == "synced"
    assert data["platform_sync"]["custodian"]["status"] == "pending_ops"


@pytest.mark.asyncio
async def test_activating_a_gia_does_not_push_morgan_lloyd(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GIA has no scheme. Pushing it would 422 at cam_createscheme for a wrapper
    Morgan Lloyd does not administer."""
    tenant_id = await _tenant(service_session)
    contact_id = await _contact(service_session, tenant_id)
    gia = await catalogue.upsert_product(
        service_session, tenant_id, {"name": "General", "product_type": "GIA"}
    )
    await providers.set_provider(
        service_session,
        tenant_id,
        "morgan-lloyd",
        config={"tenant_ids": ["g-1"]},
        enabled=True,
    )
    account_id = await _account(
        service_session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        product_id=int(gia["id"]),
    )
    pushed = AsyncMock()
    from app.integrations import morgan_lloyd as ml

    monkeypatch.setattr(ml, "configured", lambda: True)
    monkeypatch.setattr(ml, "sync_client", pushed)

    report = await account_enrol.sync_activated_account(
        service_session, tenant_id=tenant_id, account_id=account_id
    )
    pushed.assert_not_called()
    assert report["administrator"]["status"] == "skipped"
    assert report["administrator"]["reason"] == "no_administrator"


@pytest.mark.asyncio
async def test_activating_a_third_party_pension_does_not_push_morgan_lloyd(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TPPENSION is a pension for the client record, not a Morgan Lloyd scheme. The old
    attach used `is_pension` and then enrol treated that mapping as 'this pot is ours'."""
    tenant_id = await _tenant(service_session)
    contact_id = await _contact(service_session, tenant_id)
    tpp = await catalogue.upsert_product(
        service_session,
        tenant_id,
        {"name": "Third-party Pension", "product_type": "TPPENSION", "is_pension": True},
    )
    await providers.set_provider(
        service_session,
        tenant_id,
        "morgan-lloyd",
        config={"tenant_ids": ["g-1"]},
        enabled=True,
    )
    await service_session.execute(
        text(
            "INSERT INTO platform.product_platforms "
            "  (tenant_id, product_id, provider, role, enabled) "
            "VALUES (:t, :p, 'morgan-lloyd', 'scheme_administrator', true) "
            "ON CONFLICT (product_id, provider) DO NOTHING"
        ),
        {"t": tenant_id, "p": int(tpp["id"])},
    )
    account_id = await _account(
        service_session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        product_id=int(tpp["id"]),
    )
    pushed = AsyncMock()
    from app.integrations import morgan_lloyd as ml

    monkeypatch.setattr(ml, "configured", lambda: True)
    monkeypatch.setattr(ml, "sync_client", pushed)

    report = await account_enrol.sync_activated_account(
        service_session, tenant_id=tenant_id, account_id=account_id
    )
    pushed.assert_not_called()
    assert report["administrator"]["status"] == "skipped"
    assert report["administrator"]["reason"] == "no_administrator"
    left = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM platform.product_platforms "
                "WHERE product_id = :p AND provider = 'morgan-lloyd'"
            ),
            {"p": int(tpp["id"])},
        )
    ).scalar_one()
    assert int(left) == 0


@pytest.mark.asyncio
async def test_a_missing_field_refusal_does_not_fail_the_enrol(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cam_createcontact 422s on a half-filled fact-find. The journey has already
    activated the book; the report is how staff see what to fix, not a blocked step."""
    tenant_id = await _tenant(service_session)
    contact_id = await _contact(service_session, tenant_id)
    sipp = await catalogue.upsert_product(
        service_session, tenant_id, {"name": "Personal Pension", "product_type": "SIPP"}
    )
    await providers.set_provider(
        service_session,
        tenant_id,
        "morgan-lloyd",
        config={"tenant_ids": ["g-1"]},
        enabled=True,
    )
    account_id = await _account(
        service_session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        product_id=int(sipp["id"]),
    )
    from app.integrations import morgan_lloyd as ml

    async def _refuse(*args: object, **kwargs: object) -> dict[str, object]:
        raise ValueError(
            "Morgan Lloyd requires these details before this client can be synced: ni_number"
        )

    monkeypatch.setattr(ml, "configured", lambda: True)
    monkeypatch.setattr(ml, "sync_client", _refuse)

    report = await account_enrol.sync_activated_account(
        service_session, tenant_id=tenant_id, account_id=account_id
    )
    assert report["administrator"]["status"] == "failed"
    assert "ni_number" in str(report["administrator"]["reason"])
