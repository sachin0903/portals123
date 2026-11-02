"""Model-tenant email templates — the shared service + the admin authoring door.

`load_defaults` seeds the built-in defaults as published v1s (idempotent); `clone_published` copies
one tenant's published templates into another (idempotent, never overwriting a code the destination
already has); `provisioning.clone_model_email_templates` is the creation-time guard; and the admin
door resolves its scope by TARGET — the MODEL tenant needs `model:write`, so a `tenants:write`
operator cannot reach the preset every new firm inherits.

Service functions take explicit tenant_id + actor, so they run under `service_session`; the admin
endpoints resolve the actor from claims, so those run under service_role WITH claims published
(mirroring `db.admin_session`).
"""

import json
import types
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, text

from app import provisioning
from app.auth import Principal
from app.email import catalog as email_catalog
from app.email import defaults as email_defaults
from app.email import templates_service as svc
from app.email import transport as email_mod
from app.email.render import render_published_email
from app.permissions import MODEL_WRITE, TENANTS_WRITE
from app.routers import admin
from tests import email_support

# Every catalogue code that ships a default — what load_defaults should create.
_CODES_WITH_DEFAULT = [
    c.code for c in email_catalog.KNOWN_CODES if email_defaults.default_for(c.code)
]


def _mk_tenant(conn: object, *, is_model: bool = False) -> int:
    s = uuid.uuid4().hex[:8]
    return int(
        conn.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO auth.tenants (uid, alias, name, is_model) "
                "VALUES (:uid, :alias, :name, :m) RETURNING id"
            ),
            {"uid": f"mten_{s}", "alias": f"mfirm-{s}", "name": f"MFirm {s}", "m": is_model},
        ).scalar_one()
    )


def _mk_user(conn: object) -> str:
    s = uuid.uuid4().hex[:8]
    return str(
        conn.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO auth.users "
                "  (uid, email, workos_user_id, first_name, last_name, enabled) "
                "VALUES (:uid, :email, :wos, 'Olly', 'Ops', true) RETURNING id"
            ),
            {"uid": f"musr_{s}", "email": f"ops_{s}@e.test", "wos": f"mwos_{s}"},
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_load_defaults_seeds_published_and_is_idempotent(
    super_engine: Engine, service_session
) -> None:
    with super_engine.connect() as c:
        tenant = _mk_tenant(c)
        actor = _mk_user(c)

    created = await svc.load_defaults(service_session, tenant, actor)
    assert created == len(_CODES_WITH_DEFAULT)

    rows = await svc.list_templates(service_session, tenant)
    assert {r["code"] for r in rows} == set(_CODES_WITH_DEFAULT)
    # Every seeded template is PUBLISHED (so a clone/render resolves it), not left a draft.
    assert all(r["current_version_status"] == "published" for r in rows)

    # A published template resolves on the send path (fail-safe fallback is bypassed once present).
    rendered = await render_published_email(
        service_session,
        tenant_id=tenant,
        code="invite",
        context={"brand": {"name": "Acme"}, "portal_label": "Client Portal"},
    )
    assert rendered is not None and rendered.html

    # Idempotent: a second load creates nothing new.
    assert await svc.load_defaults(service_session, tenant, actor) == 0


@pytest.mark.asyncio
async def test_load_defaults_seeds_the_shipped_preview_text_line(
    super_engine: Engine, service_session
) -> None:
    """The preset is cloned into every new firm, so an empty preheader here is an empty inbox
    preview line for every firm that inherits it — and it would ALSO undo the shipped default's own
    preview line, since publishing an override switches the fallback off. Seeding "" made the preset
    strictly worse than sending nothing at all."""
    with super_engine.connect() as c:
        tenant = _mk_tenant(c)
        actor = _mk_user(c)
    await svc.load_defaults(service_session, tenant, actor)

    for code in _CODES_WITH_DEFAULT:
        default = email_defaults.default_for(code)
        assert default is not None
        rendered = await render_published_email(
            service_session,
            tenant_id=tenant,
            code=code,
            context={"brand": {"name": "Acme"}, "portal_label": "Client Portal"},
        )
        assert rendered is not None, code
        assert rendered.preheader == default.preheader, (
            f"{code}: the seeded preset's preheader is {rendered.preheader!r}, not the shipped "
            f"{default.preheader!r}"
        )
        assert default.preheader in rendered.html, (
            f"{code}: the seeded preheader never reaches the delivered HTML"
        )


@pytest.mark.asyncio
async def test_clone_published_copies_and_is_idempotent(
    super_engine: Engine, service_session
) -> None:
    with super_engine.connect() as c:
        src = _mk_tenant(c)
        dst = _mk_tenant(c)
        actor = _mk_user(c)

    await svc.load_defaults(service_session, src, actor)
    cloned = await svc.clone_published(
        service_session, src_tenant_id=src, dst_tenant_id=dst, actor_id=actor
    )
    assert cloned == len(_CODES_WITH_DEFAULT)

    dst_rows = await svc.list_templates(service_session, dst)
    assert {r["code"] for r in dst_rows} == set(_CODES_WITH_DEFAULT)
    assert all(r["current_version_status"] == "published" for r in dst_rows)

    # Idempotent: cloning again copies nothing (every code already present).
    assert (
        await svc.clone_published(
            service_session, src_tenant_id=src, dst_tenant_id=dst, actor_id=actor
        )
        == 0
    )


@pytest.mark.asyncio
async def test_clone_never_overwrites_a_customised_code(
    super_engine: Engine, service_session
) -> None:
    with super_engine.connect() as c:
        src = _mk_tenant(c)
        dst = _mk_tenant(c)
        actor = _mk_user(c)

    await svc.load_defaults(service_session, src, actor)
    # The destination already has its OWN 'invite' template — the clone must leave it untouched.
    own = await svc.create_template(
        service_session,
        dst,
        actor,
        code="invite",
        name="Firm invite",
        description=None,
        category="transactional",
        scope="firm",
    )
    cloned = await svc.clone_published(
        service_session, src_tenant_id=src, dst_tenant_id=dst, actor_id=actor
    )
    assert cloned == len(_CODES_WITH_DEFAULT) - 1  # 'invite' skipped
    invites = [r for r in await svc.list_templates(service_session, dst) if r["code"] == "invite"]
    assert len(invites) == 1 and invites[0]["uid"] == own["uid"]  # the firm's own, not a clone


@pytest.mark.asyncio
async def test_clone_model_guard_returns_zero_without_actor(
    super_engine: Engine, service_session
) -> None:
    # provisioning.clone_model_email_templates resolves auth.uid() itself; with no claims published
    # there is no operator to attribute created_by to, so it must no-op (fallback stays in force)
    # rather than fail — service_session sets service_role but publishes no claims.
    with super_engine.connect() as c:
        tenant = _mk_tenant(c)
    assert await provisioning.clone_model_email_templates(service_session, tenant) == 0


@pytest.mark.asyncio
async def test_admin_scope_requires_model_write_for_the_model_tenant(
    super_engine: Engine, service_session
) -> None:
    model_id = (
        await service_session.execute(text("SELECT id FROM auth.tenants WHERE is_model LIMIT 1"))
    ).scalar_one()
    with super_engine.connect() as c:
        firm = _mk_tenant(c)

    # The gate resolves by target: model tenant → model:write, a firm → tenants:write.
    assert await admin._tenant_scope_permission(service_session, int(model_id), write=True) == (
        MODEL_WRITE
    )
    assert await admin._tenant_scope_permission(service_session, firm, write=True) == TENANTS_WRITE


@asynccontextmanager
async def _ops_session(db: dict[str, str], user_uuid: str, perms: list[str]) -> AsyncIterator:
    """service_role WITH claims (mirrors db.admin_session), so the admin endpoints can resolve the
    acting operator (auth.uid()) for created_by."""
    opener, engine = email_support.session_opener(
        db, email_support.ops_claims(user_uuid, perms), role="service_role"
    )
    try:
        async with opener() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_author_a_firm_requires_tenants_write(
    super_engine: Engine, db: dict[str, str]
) -> None:
    with super_engine.connect() as c:
        firm = _mk_tenant(c)
        actor = _mk_user(c)

    body = admin.CreateEmailTemplate(code="invite", name="Invite", category="transactional")
    # Without tenants:write → 403 at the scope gate (before any operator resolution / write).
    async with _ops_session(db, actor, perms=[]) as session:
        with pytest.raises(HTTPException) as ei:
            await admin.admin_create_email_template(
                tenant_id=firm,
                body=body,
                session=session,
                principal=Principal(user_id="w", organisation_id="o", role="ops", permissions=[]),
            )
        assert ei.value.status_code == 403

    # With tenants:write → created, attributed to the operator.
    async with _ops_session(db, actor, perms=["tenants:write"]) as session:
        out = await admin.admin_create_email_template(
            tenant_id=firm,
            body=body,
            session=session,
            principal=Principal(
                user_id="w", organisation_id="o", role="ops", permissions=["tenants:write"]
            ),
        )
        assert out.code == "invite" and out.created_by == "Olly Ops"


@pytest.mark.asyncio
async def test_admin_load_defaults_into_the_model_needs_model_write(
    super_engine: Engine, db: dict[str, str]
) -> None:
    model_id = None
    with super_engine.connect() as c:
        model_id = int(
            c.execute(text("SELECT id FROM auth.tenants WHERE is_model LIMIT 1")).scalar_one()
        )
        actor = _mk_user(c)

    # A tenants:write operator cannot seed the MODEL preset — it resolves to model:write. Fails at
    # the gate, so nothing is written to the shared model tenant.
    async with _ops_session(db, actor, perms=["tenants:write"]) as session:
        with pytest.raises(HTTPException) as ei:
            await admin.admin_email_templates_load_defaults(
                tenant_id=model_id,
                session=session,
                principal=Principal(
                    user_id="w", organisation_id="o", role="ops", permissions=["tenants:write"]
                ),
            )
        assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_send_test_mails_the_acting_operator_and_needs_write(
    super_engine: Engine, db: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The only way to test the MODEL preset, which has no logins. Two properties: behind the target
    tenant's WRITE scope, and the recipient is the ACTING OPERATOR'S own address, never a parameter.
    The shared helper's full behaviour is pinned in tests/test_email_send_test.py; this is the admin
    door's own wiring."""
    with super_engine.connect() as c:
        firm = _mk_tenant(c)
        actor = _mk_user(c)
        operator_email = str(
            c.execute(text("SELECT email FROM auth.users WHERE id = :u"), {"u": actor}).scalar_one()
        )

    payloads: list[dict[str, object]] = []
    email_mod.clear_caches()
    monkeypatch.setattr(email_mod.settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(email_mod.settings, "app_stage", "dev")
    monkeypatch.setattr(email_mod.settings, "email_allowlist", ["@e.test"])
    monkeypatch.setattr(
        email_mod, "httpx", types.SimpleNamespace(AsyncClient=email_support.FakeClient(payloads))
    )

    body = admin.SendTestEmail(
        **{"subject": "Hi {{ first_name }}", "html": "<p>hi</p>", "to": "client@elsewhere.test"}
    )

    # Create something to test, as an operator who may write.
    ops = Principal(user_id="w", organisation_id="o", role="ops", permissions=["tenants:write"])
    async with _ops_session(db, actor, perms=["tenants:write"]) as session:
        created = await admin.admin_create_email_template(
            tenant_id=firm,
            body=admin.CreateEmailTemplate(code="invite", name="Invite"),
            session=session,
            principal=ops,
        )

    # Read-only scope cannot send: it spends the firm's email reputation.
    async with _ops_session(db, actor, perms=["tenants:read"]) as session:
        with pytest.raises(HTTPException) as ei:
            await admin.admin_send_test_email_template(
                tenant_id=firm,
                uid=created.uid,
                body=body,
                session=session,
                principal=Principal(
                    user_id="w", organisation_id="o", role="ops", permissions=["tenants:read"]
                ),
            )
        assert ei.value.status_code == 403
    assert payloads == []

    async with _ops_session(db, actor, perms=["tenants:write"]) as session:
        out = await admin.admin_send_test_email_template(
            tenant_id=firm, uid=created.uid, body=body, session=session, principal=ops
        )
    assert out.status == "delivered"
    assert out.to == operator_email
    assert [p["to"] for p in payloads] == [[operator_email]]
    assert "elsewhere.test" not in json.dumps(payloads)
    assert out.subject.startswith("[TEST] ")
    email_mod.clear_caches()
