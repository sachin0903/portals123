"""The firm self-service notification-rule endpoints (``/firm/notification-rules``).

The security-sensitive surface: a firm may author and manage its OWN rules and edit an ops rule
only when Graphene marked it ``firm_editable``, may never delete an ops rule, and may never reach
another tenant's rules. Every write needs ``notifications:write``. These assert that guard
directly against a real Postgres, since it is the boundary between a firm and Graphene's control.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.db import FirmContext
from app.notifications import rules_service as notif_rules
from app.routers import firm as firm_router

pytestmark = pytest.mark.asyncio


def _ctx(session: AsyncSession, tenant_id: int, *perms: str) -> FirmContext:
    """The context ``firm_session`` yields for a firm principal holding ``perms``."""
    return FirmContext(
        session=session,
        tenant_id=tenant_id,
        principal=Principal(user_id="u_fn", organisation_id="o_fn", permissions=list(perms)),
    )


async def _tenant(session: AsyncSession, uid: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :u, :u) RETURNING id"
                ),
                {"u": uid},
            )
        ).scalar_one()
    )


async def _ops_rule(
    session: AsyncSession, tenant_id: int, *, firm_editable: bool
) -> dict[str, object]:
    """A Graphene-authored rule on the firm's tenant, with the given firm_editable guard."""
    return await notif_rules.create_rule(
        session,
        tenant_id,
        authored_by="graphene",
        firm_editable=firm_editable,
        trigger_key="document.shared",
        audience="adviser",
        title="An adviser notice",
    )


async def test_firm_creates_and_manages_its_own_rule(service_session: AsyncSession) -> None:
    firm = await _tenant(service_session, f"fnr_own_{uuid.uuid4().hex[:8]}")
    mgr = _ctx(service_session, firm, "notifications:write")

    created = await firm_router.firm_create_notification_rule(
        firm_router.FirmRuleCreate(
            trigger_key="document.shared", audience="adviser", title="Heads up"
        ),
        ctx=mgr,
    )
    # A firm's own rule is firm-authored and always firm-editable (not the firm's guard to set).
    assert created["authored_by"] == "firm"
    assert created["firm_editable"] is True

    listed = await firm_router.firm_list_notification_rules(ctx=mgr)
    assert any(r["uid"] == created["uid"] for r in listed)

    updated = await firm_router.firm_update_notification_rule(
        str(created["uid"]), firm_router.FirmRuleUpdate(title="Reworded"), ctx=mgr
    )
    assert updated["title"] == "Reworded"

    assert (await firm_router.firm_delete_notification_rule(str(created["uid"]), ctx=mgr))["ok"]


async def test_firm_update_persists_mandatory(service_session: AsyncSession) -> None:
    """A firm toggling Mandatory must PERSIST — the update model has to carry `mandatory`, or the
    builder switch silently reverts on reload (the adviser-portal bug: FirmRuleUpdate had dropped
    it while FirmRuleCreate + the admin update kept it)."""
    firm = await _tenant(service_session, f"fnr_mand_{uuid.uuid4().hex[:8]}")
    mgr = _ctx(service_session, firm, "notifications:write")

    created = await firm_router.firm_create_notification_rule(
        firm_router.FirmRuleCreate(
            trigger_key="document.shared", audience="adviser", title="Heads up", mandatory=False
        ),
        ctx=mgr,
    )
    assert created["mandatory"] is False

    on = await firm_router.firm_update_notification_rule(
        str(created["uid"]), firm_router.FirmRuleUpdate(mandatory=True), ctx=mgr
    )
    assert on["mandatory"] is True  # stuck (was silently dropped before)

    off = await firm_router.firm_update_notification_rule(
        str(created["uid"]), firm_router.FirmRuleUpdate(mandatory=False), ctx=mgr
    )
    assert off["mandatory"] is False


async def test_firm_may_edit_but_not_delete_an_editable_ops_rule(
    service_session: AsyncSession,
) -> None:
    firm = await _tenant(service_session, f"fnr_edit_{uuid.uuid4().hex[:8]}")
    rule = await _ops_rule(service_session, firm, firm_editable=True)
    mgr = _ctx(service_session, firm, "notifications:write")

    # Editable ⇒ a firm may reword it.
    updated = await firm_router.firm_update_notification_rule(
        str(rule["uid"]), firm_router.FirmRuleUpdate(title="Firm tweak"), ctx=mgr
    )
    assert updated["title"] == "Firm tweak"

    # But deleting a Graphene-authored rule is never the firm's to do.
    with pytest.raises(HTTPException) as exc:
        await firm_router.firm_delete_notification_rule(str(rule["uid"]), ctx=mgr)
    assert exc.value.status_code == 403


async def test_firm_cannot_touch_a_locked_ops_rule(service_session: AsyncSession) -> None:
    firm = await _tenant(service_session, f"fnr_lock_{uuid.uuid4().hex[:8]}")
    rule = await _ops_rule(service_session, firm, firm_editable=False)
    mgr = _ctx(service_session, firm, "notifications:write")

    with pytest.raises(HTTPException) as edit_exc:
        await firm_router.firm_update_notification_rule(
            str(rule["uid"]), firm_router.FirmRuleUpdate(title="nope"), ctx=mgr
        )
    assert edit_exc.value.status_code == 403


async def test_firm_cannot_reach_another_tenants_rule(service_session: AsyncSession) -> None:
    firm = await _tenant(service_session, f"fnr_a_{uuid.uuid4().hex[:8]}")
    other = await _tenant(service_session, f"fnr_b_{uuid.uuid4().hex[:8]}")
    others_rule = await _ops_rule(service_session, other, firm_editable=True)
    mgr = _ctx(service_session, firm, "notifications:write")

    # A rule on another tenant reads as absent — 404, not 403 (never confirm it exists).
    with pytest.raises(HTTPException) as exc:
        await firm_router.firm_update_notification_rule(
            str(others_rule["uid"]), firm_router.FirmRuleUpdate(title="x"), ctx=mgr
        )
    assert exc.value.status_code == 404


async def test_firm_write_needs_the_manage_permission(service_session: AsyncSession) -> None:
    firm = await _tenant(service_session, f"fnr_perm_{uuid.uuid4().hex[:8]}")
    reader = _ctx(service_session, firm, "notifications:read")  # read only

    with pytest.raises(HTTPException) as exc:
        await firm_router.firm_create_notification_rule(
            firm_router.FirmRuleCreate(
                trigger_key="document.shared", audience="adviser", title="denied"
            ),
            ctx=reader,
        )
    assert exc.value.status_code == 403


async def test_firm_rejects_an_invalid_trigger_audience_pair(service_session: AsyncSession) -> None:
    firm = await _tenant(service_session, f"fnr_inv_{uuid.uuid4().hex[:8]}")
    mgr = _ctx(service_session, firm, "notifications:write")

    with pytest.raises(HTTPException) as exc:
        await firm_router.firm_create_notification_rule(
            firm_router.FirmRuleCreate(
                trigger_key="document.shared", audience="assignee", title="wrong audience"
            ),
            ctx=mgr,
        )
    assert exc.value.status_code == 422
