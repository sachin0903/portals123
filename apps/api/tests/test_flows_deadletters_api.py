"""HTTP-layer tests for the flow-engine hygiene surface (needs Docker).

``GET /flows/dead-letters`` + its replay shipped with **no test**, which is how the route ended up
unreachable: it was declared after ``GET /flows/{uid}``, so every request landed in ``get_flow`` and
404'd and the operator page rendered its error banner forever. ``tests/test_flows_routes.py`` locks
the route order; these lock what the endpoints actually do — including tenant scoping, which matters
because they run elevated (``firm_session``) over service_role-only queue tables.

Also covers ``PATCH /flows/{uid}``'s archive/restore verbs (untested when they shipped).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.db import FirmContext
from app.permissions import FLOWS_READ, FLOWS_WRITE
from app.routers import flows as api


def _principal(*perms: Any) -> Principal:
    return Principal(
        user_id="wos_staff",
        organisation_id="org_1",
        role="adviser",
        permissions=[p.slug if hasattr(p, "slug") else str(p) for p in perms],
    )


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


async def _tenant(sess: AsyncSession, label: str) -> int:
    s = uuid.uuid4().hex[:8]
    return int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"{label}_{s}",
            a=f"{label}-{s}",
            n=f"{label} {s}",
        )
    )


async def _dead_outbox(sess: AsyncSession, tenant: int, *, title: str, error: str) -> int:
    return int(
        await _scalar(
            sess,
            "INSERT INTO flows.outbox (tenant_id, event_type, payload, status, attempts, "
            "  max_attempts, last_error, last_attempt) "
            "VALUES (:t, 'task.requested', CAST(:p AS jsonb), 'dead_letter', 8, 8, :e, now()) "
            "RETURNING id",
            t=tenant,
            p=json.dumps({"spec": {"title": title}}),
            e=error,
        )
    )


async def _failed_render(sess: AsyncSession, tenant: int, *, name: str) -> int:
    doc = await _scalar(
        sess,
        "INSERT INTO documents.documents (uid, tenant_id, document_type, source, name, status) "
        "VALUES (:u, :t, 'suitability', 'generated', :n, 'failed') RETURNING id",
        u=f"doc_{uuid.uuid4().hex}",
        t=tenant,
        n=name,
    )
    return int(
        await _scalar(
            sess,
            "INSERT INTO documents.render_jobs (tenant_id, document_id, status, attempts, "
            "  max_attempts, last_error) VALUES (:t, :d, 'failed', 3, 3, 'boom') RETURNING id",
            t=tenant,
            d=doc,
        )
    )


def _ctx(sess: AsyncSession, tenant: int) -> FirmContext:
    return FirmContext(session=sess, tenant_id=tenant, principal=_principal(FLOWS_WRITE))


# ==================================================================================================
# dead letters
# ==================================================================================================
@pytest.mark.asyncio
async def test_dead_letters_lists_both_kinds_for_this_tenant_only(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    mine = await _tenant(sess, "dl")
    theirs = await _tenant(sess, "dl-other")
    outbox_id = await _dead_outbox(
        sess, mine, title="Chase the ID", error="no such role 'complince'"
    )
    render_id = await _failed_render(sess, mine, name="Suitability pack")
    await _dead_outbox(sess, theirs, title="Not yours", error="nope")
    await _failed_render(sess, theirs, name="Not yours either")

    rows = await api.list_dead_letters(principal=_principal(FLOWS_WRITE), ctx=_ctx(sess, mine))

    by_kind = {r.kind: r for r in rows}
    assert set(by_kind) == {"outbox", "render"}
    assert by_kind["outbox"].id == outbox_id
    assert by_kind["outbox"].summary == "Chase the ID"
    assert "complince" in str(by_kind["outbox"].last_error)
    assert by_kind["render"].id == render_id
    assert by_kind["render"].summary == "Suitability pack"
    assert all("Not yours" != r.summary for r in rows)


@pytest.mark.asyncio
async def test_dead_letters_needs_flows_write(service_session: AsyncSession) -> None:
    sess = service_session
    mine = await _tenant(sess, "dl-perm")
    with pytest.raises(HTTPException) as exc:
        await api.list_dead_letters(principal=_principal(FLOWS_READ), ctx=_ctx(sess, mine))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_replaying_an_outbox_dead_letter_requeues_it_with_a_fresh_budget(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    mine = await _tenant(sess, "dl-replay")
    row_id = await _dead_outbox(sess, mine, title="Chase the ID", error="no such role")

    out = await api.replay_dead_letter(
        "outbox", row_id, principal=_principal(FLOWS_WRITE), ctx=_ctx(sess, mine)
    )

    assert out == {"ok": True, "id": row_id}
    row = (
        (
            await sess.execute(
                text("SELECT status, attempts, last_error FROM flows.outbox WHERE id = :i"),
                {"i": row_id},
            )
        )
        .mappings()
        .one()
    )
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert row["last_error"] is None


@pytest.mark.asyncio
async def test_replaying_a_render_dead_letter_requeues_the_job(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    mine = await _tenant(sess, "dl-render")
    job = await _failed_render(sess, mine, name="Suitability pack")

    await api.replay_dead_letter(
        "render", job, principal=_principal(FLOWS_WRITE), ctx=_ctx(sess, mine)
    )

    row = (
        (
            await sess.execute(
                text(
                    "SELECT status, attempts, last_error, claimed_at "
                    "FROM documents.render_jobs WHERE id = :i"
                ),
                {"i": job},
            )
        )
        .mappings()
        .one()
    )
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert row["claimed_at"] is None


@pytest.mark.asyncio
async def test_another_tenants_dead_letter_cannot_be_replayed(
    service_session: AsyncSession,
) -> None:
    sess = service_session
    mine = await _tenant(sess, "dl-a")
    theirs = await _tenant(sess, "dl-b")
    row_id = await _dead_outbox(sess, theirs, title="Theirs", error="nope")
    with pytest.raises(HTTPException) as exc:
        await api.replay_dead_letter(
            "outbox", row_id, principal=_principal(FLOWS_WRITE), ctx=_ctx(sess, mine)
        )
    assert exc.value.status_code == 404
    assert await _scalar(sess, "SELECT status FROM flows.outbox WHERE id = :i", i=row_id) == (
        "dead_letter"
    )


@pytest.mark.asyncio
async def test_an_unknown_dead_letter_kind_is_404(service_session: AsyncSession) -> None:
    sess = service_session
    mine = await _tenant(sess, "dl-kind")
    with pytest.raises(HTTPException) as exc:
        await api.replay_dead_letter(
            "wat", 1, principal=_principal(FLOWS_WRITE), ctx=_ctx(sess, mine)
        )
    assert exc.value.status_code == 404


# ==================================================================================================
# archive / restore a flow definition
# ==================================================================================================
async def _flow(sess: AsyncSession, tenant: int, *, published: bool) -> str:
    s = uuid.uuid4().hex[:8]
    uid = f"flw_{s}"
    flow_id = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
            "VALUES (:u, :t, :c, 'Onboarding', :st) RETURNING id",
            u=uid,
            t=tenant,
            c=f"onb_{s}",
            st="published" if published else "draft",
        )
    )
    vid = int(
        await _scalar(
            sess,
            "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
            "  definition) VALUES (:u, :t, :f, 1, :st, '{}'::jsonb) RETURNING id",
            u=f"flv_{s}",
            t=tenant,
            f=flow_id,
            st="published" if published else "draft",
        )
    )
    if published:
        await sess.execute(
            text("UPDATE flows.flows SET current_published_version_id = :v WHERE id = :i"),
            {"v": vid, "i": flow_id},
        )
    return uid


@pytest.mark.asyncio
async def test_a_flow_can_be_archived_and_restored(service_session: AsyncSession) -> None:
    sess = service_session
    tenant = await _tenant(sess, "arc")
    uid = await _flow(sess, tenant, published=True)

    archived = await api.update_flow(
        uid, api.UpdateFlow(status="archived"), _principal(FLOWS_WRITE), sess
    )
    assert archived.status == "archived"

    restored = await api.update_flow(
        uid, api.UpdateFlow(status="published"), _principal(FLOWS_WRITE), sess
    )
    assert restored.status == "published"


@pytest.mark.asyncio
async def test_restoring_a_flow_with_no_published_version_is_refused(
    service_session: AsyncSession,
) -> None:
    """Otherwise a 'published' flow with nothing to run from appears in the New-journey picker."""
    sess = service_session
    tenant = await _tenant(sess, "arc-none")
    uid = await _flow(sess, tenant, published=False)
    with pytest.raises(HTTPException) as exc:
        await api.update_flow(
            uid, api.UpdateFlow(status="published"), _principal(FLOWS_WRITE), sess
        )
    assert exc.value.status_code == 422
    assert "publish a version" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_list_flows_refuses_an_unknown_status(service_session: AsyncSession) -> None:
    sess = service_session
    with pytest.raises(HTTPException) as exc:
        await api.list_flows(flow_status="binned", principal=_principal(FLOWS_READ), session=sess)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_an_unknown_flow_status_is_refused(service_session: AsyncSession) -> None:
    sess = service_session
    tenant = await _tenant(sess, "arc-bad")
    uid = await _flow(sess, tenant, published=True)
    with pytest.raises(HTTPException) as exc:
        await api.update_flow(uid, api.UpdateFlow(status="binned"), _principal(FLOWS_WRITE), sess)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_archiving_needs_flows_write(service_session: AsyncSession) -> None:
    sess = service_session
    tenant = await _tenant(sess, "arc-perm")
    uid = await _flow(sess, tenant, published=True)
    with pytest.raises(HTTPException) as exc:
        await api.update_flow(uid, api.UpdateFlow(status="archived"), _principal(FLOWS_READ), sess)
    assert exc.value.status_code == 403
