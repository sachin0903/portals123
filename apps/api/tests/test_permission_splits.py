"""The six permission splits (July 2026) — registry, endpoint re-gates, backfill, pages diff.

Each split takes a surface off a coarser parent permission and gives it its own slug:

* ``instructions:read``/``instructions:write`` ← ``contacts:write`` (money movement — and the
  read fixes the read-behind-write: LISTING instructions used to demand the write slug);
* ``documents:send`` ← ``documents:write`` (client-facing release: send / request create /
  request-item reassign; drafting — upload/generate/rename/archive — stays ``write``);
* ``illustrations:write`` ← ``documents:write`` (both generators call a paid external engine);
* ``forms:submit`` ← ``forms:write`` (the response plane; response LISTING also leaves
  ``forms:read`` so form-definition viewers stop reading client submissions);
* ``pages:write`` ← ``branding:write`` (portal composition — a DIFF gate on the branding PUTs,
  so a colour save that echoes the stored pages never trips it);
* ``platforms:query`` ← ``platforms:read`` (the lake SQL console, operations portal).

What is pinned here, per split: the OLD parent slug alone now 403s on the re-gated endpoint,
the NEW slug passes the gate (proven by reaching the handler's own domain response — 404/422/
empty list — rather than 403), and migration ``0042`` grants each new slug to every existing
holder of its parent (exercised by re-running the idempotent SQL against seeded roles).
"""

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import branding as branding_mod
from app import permissions, rbac
from app.auth import Principal, get_current_principal
from app.branding import Branding
from app.db import FirmContext, admin_session
from app.main import app
from app.permissions import satisfies

API_ROOT = Path(__file__).resolve().parent.parent


# ---- registry pins ----------------------------------------------------------------------------


def test_new_slugs_are_registered_with_the_right_portals() -> None:
    by_slug = {p.slug: p for p in permissions.ALL}
    platform = (
        "instructions:read",
        "instructions:write",
        "documents:send",
        "illustrations:write",
        "forms:submit",
        "pages:write",
    )
    for slug in platform:
        assert by_slug[slug].portal == "platform", slug
        # platform-portal ⇒ grantable to firm roles (NOT operations-reserved)
        assert slug not in rbac.OPERATIONS_ONLY, slug
    # The console permission is operations-portal BY DECLARATION, which is what keeps it off
    # every firm-scoped role (assert_grantable refuses OPERATIONS_ONLY to firms).
    assert by_slug["platforms:query"].portal == "operations"
    assert "platforms:query" in rbac.OPERATIONS_ONLY


def test_write_implies_read_within_instructions_and_nowhere_across() -> None:
    assert satisfies({"instructions:write"}, "instructions:read")
    assert not satisfies({"instructions:read"}, "instructions:write")
    # the implication never crosses to a sibling action or another resource — the reason the
    # 0042 backfill grants platforms:query to platforms:write holders explicitly
    assert not satisfies({"platforms:write"}, "platforms:query")
    assert not satisfies({"documents:write"}, "documents:send")
    assert not satisfies({"forms:write"}, "forms:submit")
    assert not satisfies({"contacts:write"}, "instructions:read")
    assert not satisfies({"contacts:write"}, "contacts:sync_morgan_lloyd")
    assert not satisfies({"branding:write"}, "pages:write")


# ---- the endpoint re-gates ---------------------------------------------------------------------
#
# Handler-level, with a hand-built context (the test_permissions.py idiom): the 403 must name the
# NEW slug, and the new slug must get PAST the gate — asserted by reaching the handler's own
# domain response (404/422/[]), which a 403 never is.


def _ctx(session: AsyncSession, perms: list[str]) -> FirmContext:
    return FirmContext(
        session=session,
        tenant_id=999_999_999,  # nothing real: every pass-case asserts the domain 404/[] shape
        principal=Principal(user_id="user_split", organisation_id="org_split", permissions=perms),
    )


def _principal(perms: list[str]) -> Principal:
    return Principal(user_id="user_split", organisation_id="org_split", permissions=perms)


def _assert_403(exc_info: pytest.ExceptionInfo[HTTPException], slug: str) -> None:
    assert exc_info.value.status_code == 403
    assert slug in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_instructions_list_regates_to_instructions_read(
    service_session: AsyncSession,
) -> None:
    from app.routers import firm

    # the parent alone — yesterday's key — no longer opens the door
    with pytest.raises(HTTPException) as exc:
        await firm.list_instructions(contact_id=1, ctx=_ctx(service_session, ["contacts:write"]))
    _assert_403(exc, "instructions:read")
    # the new read slug passes (unknown contact in a nothing-tenant ⇒ empty list, not 403)
    out = await firm.list_instructions(
        contact_id=1, ctx=_ctx(service_session, ["instructions:read"])
    )
    assert out == []


@pytest.mark.asyncio
async def test_instruction_create_and_cancel_regate_to_instructions_write(
    service_session: AsyncSession,
) -> None:
    from app.routers import firm

    body = firm.CreateInstruction(kind="expected_in", account_id=1, amount=100.0)
    with pytest.raises(HTTPException) as exc:
        await firm.create_instruction(
            contact_uid="con_none",
            contact_id=1,
            body=body,
            ctx=_ctx(service_session, ["contacts:write"]),
        )
    _assert_403(exc, "instructions:write")
    with pytest.raises(HTTPException) as exc:
        await firm.create_instruction(
            contact_uid="con_none",
            contact_id=1,
            body=body,
            ctx=_ctx(service_session, ["instructions:write"]),
        )
    assert exc.value.status_code == 404  # past the gate: unknown account

    with pytest.raises(HTTPException) as exc:
        await firm.cancel_instruction(uid="ins_none", ctx=_ctx(service_session, ["contacts:write"]))
    _assert_403(exc, "instructions:write")
    with pytest.raises(HTTPException) as exc:
        await firm.cancel_instruction(
            uid="ins_none", ctx=_ctx(service_session, ["instructions:write"])
        )
    assert exc.value.status_code == 404  # past the gate: no pending instruction


@pytest.mark.asyncio
async def test_bank_account_and_model_puts_regate_to_instructions_write(
    service_session: AsyncSession,
) -> None:
    from app.routers import crm

    bank = crm.SetBankAccount(name="A", sort_code="12-34-56", account_number="12345678")
    with pytest.raises(HTTPException) as exc:
        await crm.set_contact_bank_account(
            contact_uid="con_none",
            contact_id=1,
            body=bank,
            ctx=_ctx(service_session, ["contacts:write"]),
        )
    _assert_403(exc, "instructions:write")
    with pytest.raises(HTTPException) as exc:
        await crm.set_contact_bank_account(
            contact_uid="con_none",
            contact_id=1,
            body=bank,
            ctx=_ctx(service_session, ["instructions:write"]),
        )
    assert exc.value.status_code == 404  # past the gate: unknown contact

    model = crm.SetAccountModel(model_id=None)
    with pytest.raises(HTTPException) as exc:
        await crm.set_account_model(
            account_uid="acc_none",
            account_id=1,
            body=model,
            ctx=_ctx(service_session, ["contacts:write"]),
        )
    _assert_403(exc, "instructions:write")
    with pytest.raises(HTTPException) as exc:
        await crm.set_account_model(
            account_uid="acc_none",
            account_id=1,
            body=model,
            ctx=_ctx(service_session, ["instructions:write"]),
        )
    assert exc.value.status_code == 404  # past the gate: unknown account

    fee = crm.SetAccountFeePackages(fee_package_ids=[])
    with pytest.raises(HTTPException) as exc:
        await crm.set_account_fee_packages(
            account_uid="acc_none",
            account_id=1,
            body=fee,
            ctx=_ctx(service_session, ["contacts:write"]),
        )
    _assert_403(exc, "instructions:write")
    with pytest.raises(HTTPException) as exc:
        await crm.set_account_fee_packages(
            account_uid="acc_none",
            account_id=1,
            body=fee,
            ctx=_ctx(service_session, ["instructions:write"]),
        )
    assert exc.value.status_code == 404  # past the gate: unknown account

    movements = crm.SetAccountRegularMovements(payments=[], withdrawals=[])
    with pytest.raises(HTTPException) as exc:
        await crm.set_account_regular_movements(
            account_uid="acc_none",
            account_id=1,
            body=movements,
            ctx=_ctx(service_session, ["contacts:write"]),
        )
    _assert_403(exc, "instructions:write")
    with pytest.raises(HTTPException) as exc:
        await crm.set_account_regular_movements(
            account_uid="acc_none",
            account_id=1,
            body=movements,
            ctx=_ctx(service_session, ["instructions:write"]),
        )
    assert exc.value.status_code == 404  # past the gate: unknown account


@pytest.mark.asyncio
async def test_drawdown_session_regates_to_instructions_write(
    service_session: AsyncSession,
) -> None:
    from app.routers import crm

    with pytest.raises(HTTPException) as exc:
        await crm.account_drawdown_session(
            account_id=1, principal=_principal(["contacts:write"]), session=service_session
        )
    _assert_403(exc, "instructions:write")
    with pytest.raises(HTTPException) as exc:
        await crm.account_drawdown_session(
            account_id=999_999_999,
            principal=_principal(["instructions:write"]),
            session=service_session,
        )
    assert exc.value.status_code == 404  # past the gate: unknown account


@pytest.mark.asyncio
async def test_document_send_and_requests_regate_to_documents_send(
    service_session: AsyncSession,
) -> None:
    from app.routers import documents as docs

    send = docs.SendDocument(contact_ids=[1])
    with pytest.raises(HTTPException) as exc:
        await docs.send_document(
            uid="doc_none",
            body=send,
            principal=_principal(["documents:write"]),
            session=service_session,
        )
    _assert_403(exc, "documents:send")
    with pytest.raises(HTTPException) as exc:
        await docs.send_document(
            uid="doc_none",
            body=send,
            principal=_principal(["documents:send"]),
            session=service_session,
        )
    # Past the gate. The refusal that follows is the tenant one, not the permission one: send now
    # resolves the caller's tenant before calling app/documents/service.py (the service takes the
    # tenant explicitly, because the engine calls it too and has no ambient identity), and this
    # synthetic session has none. On a real request tenant_session always does, and the next
    # refusal is the service's own "Document not found".
    assert exc.value.status_code == 403 and "tenant" in str(exc.value.detail).lower()

    req = docs.CreateRequest(contact_id=1, items=[])
    with pytest.raises(HTTPException) as exc:
        await docs.create_request(
            body=req, principal=_principal(["documents:write"]), session=service_session
        )
    _assert_403(exc, "documents:send")
    with pytest.raises(HTTPException) as exc:
        await docs.create_request(
            body=req, principal=_principal(["documents:send"]), session=service_session
        )
    # Past the gate, refused for the tenant instead — same reason as send above.
    assert exc.value.status_code == 403 and "tenant" in str(exc.value.detail).lower()

    reassign = docs.ReassignItem(target_document_uid="doc_none")
    with pytest.raises(HTTPException) as exc:
        await docs.reassign_request_item(
            req_uid="req_none",
            item_uid="itm_none",
            body=reassign,
            principal=_principal(["documents:write"]),
            session=service_session,
        )
    _assert_403(exc, "documents:send")
    with pytest.raises(HTTPException) as exc:
        await docs.reassign_request_item(
            req_uid="req_none",
            item_uid="itm_none",
            body=reassign,
            principal=_principal(["documents:send"]),
            session=service_session,
        )
    assert exc.value.status_code == 404  # past the gate: unknown item


@pytest.mark.asyncio
async def test_document_drafting_stays_on_documents_write(service_session: AsyncSession) -> None:
    """The split must not creep: rename/archive are internal drafting, and documents:send alone
    must NOT unlock them (send is release, not editing)."""
    from app.routers import documents as docs

    with pytest.raises(HTTPException) as exc:
        await docs.archive_document(
            uid="doc_none", principal=_principal(["documents:send"]), session=service_session
        )
    _assert_403(exc, "documents:write")


@pytest.mark.asyncio
async def test_both_illustration_generators_regate_to_illustrations_write(
    service_session: AsyncSession,
) -> None:
    from app.routers import illustrations as ill

    pension = ill.IllustrationInput()
    with pytest.raises(HTTPException) as exc:
        await ill.create_illustration(
            contact_id=1,
            body=pension,
            principal=_principal(["documents:write"]),
            session=service_session,
        )
    _assert_403(exc, "illustrations:write")
    with pytest.raises(HTTPException) as exc:
        await ill.create_illustration(
            contact_id=999_999_999,
            body=pension,
            principal=_principal(["illustrations:write"]),
            session=service_session,
        )
    assert exc.value.status_code == 404  # past the gate: unknown client

    investment = ill.InvestmentIllustrationInput()
    with pytest.raises(HTTPException) as exc:
        await ill.create_investment_illustration(
            contact_id=1,
            body=investment,
            principal=_principal(["documents:write"]),
            session=service_session,
        )
    _assert_403(exc, "illustrations:write")
    with pytest.raises(HTTPException) as exc:
        await ill.create_investment_illustration(
            contact_id=999_999_999,
            body=investment,
            principal=_principal(["illustrations:write"]),
            session=service_session,
        )
    assert exc.value.status_code == 404  # past the gate: unknown client


@pytest.mark.asyncio
async def test_form_responses_regate_to_forms_submit(service_session: AsyncSession) -> None:
    from app.routers import forms

    body = forms.FormResponseIn(answers={"a": 1})
    # POST: the authoring slug no longer records responses
    with pytest.raises(HTTPException) as exc:
        await forms.create_form_response(
            uid="frm_none",
            body=body,
            principal=_principal(["forms:write"]),
            session=service_session,
        )
    _assert_403(exc, "forms:submit")
    with pytest.raises(HTTPException) as exc:
        await forms.create_form_response(
            uid="frm_none",
            body=body,
            principal=_principal(["forms:submit"]),
            session=service_session,
        )
    assert exc.value.status_code == 404  # past the gate: unknown form

    # LIST: form-definition viewers (forms:read) stop reading client submissions…
    with pytest.raises(HTTPException) as exc:
        await forms.list_form_responses(
            uid="frm_none", principal=_principal(["forms:read"]), session=service_session
        )
    assert exc.value.status_code == 403
    # …while the response plane (forms:submit) and the client-paperwork readers
    # (documents:read) both still can.
    for slug in ("forms:submit", "documents:read"):
        with pytest.raises(HTTPException) as exc:
            await forms.list_form_responses(
                uid="frm_none", principal=_principal([slug]), session=service_session
            )
        assert exc.value.status_code == 404, slug  # past the gate: unknown form


# ---- the platforms Query console, on the wire ---------------------------------------------------
#
# The gate is a Depends (`requires(PLATFORMS_QUERY)`), so a direct handler call would skip it —
# these go through the real ASGI app with `admin_session` overridden (the test_permissions.py
# wire idiom).


@pytest_asyncio.fixture
async def admin_wire(service_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def _session() -> AsyncIterator[AsyncSession]:
        yield service_session

    app.dependency_overrides[admin_session] = _session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(admin_session, None)
        app.dependency_overrides.pop(get_current_principal, None)


@pytest.mark.asyncio
async def test_platform_query_regates_to_platforms_query(admin_wire: AsyncClient) -> None:
    # platforms:read (yesterday's gate) — and even platforms:write — no longer reach the console
    for held in (["platforms:read"], ["platforms:write"]):
        app.dependency_overrides[get_current_principal] = lambda held=held: _principal(held)
        res = await admin_wire.post("/admin/platforms/morgan-lloyd/query", json={"sql": "select 1"})
        assert res.status_code == 403, (held, res.text)
        assert "platforms:query" in res.text

    # the new slug passes the gate: the refusal left is the unconfigured lake (503), not authz
    app.dependency_overrides[get_current_principal] = lambda: _principal(["platforms:query"])
    res = await admin_wire.post("/admin/platforms/morgan-lloyd/query", json={"sql": "select 1"})
    assert res.status_code == 503, res.text


# ---- pages:write — the branding diff gate --------------------------------------------------------


def _pages(label: str) -> list[dict[str, object]]:
    return [
        {
            "slug": "home",
            "label": label,
            "icon": "house",
            "nav": True,
            "layouts": {"default": [{"type": "accounts", "w": 12, "h": 2}]},
        }
    ]


def test_pages_changed_is_a_canonical_diff() -> None:
    """The pure rule the endpoints gate on: composition changes trip it, echoes and cosmetic
    changes don't, and None↔concrete counts as a change in both directions."""
    plain = Branding()
    with_pages = Branding.model_validate({"pages": _pages("Home")})

    assert not branding_mod.pages_changed(plain, Branding())  # nothing → nothing
    # a colour-only change with no pages on either side
    assert not branding_mod.pages_changed(
        plain, Branding.model_validate({"colors": {"light": {"primary": "#123456"}}})
    )
    # the studio echo: the same pages payload re-parsed dumps identically after validation
    assert not branding_mod.pages_changed(
        with_pages, Branding.model_validate({"pages": _pages("Home")})
    )
    # …and a colour change that carries the stored pages verbatim is still no pages change
    assert not branding_mod.pages_changed(
        with_pages,
        Branding.model_validate(
            {"pages": _pages("Home"), "colors": {"light": {"primary": "#123456"}}}
        ),
    )
    # real composition changes
    assert branding_mod.pages_changed(plain, with_pages)  # materialising the defaults
    assert branding_mod.pages_changed(with_pages, plain)  # re-tracking the defaults
    assert branding_mod.pages_changed(
        with_pages, Branding.model_validate({"pages": _pages("Renamed")})
    )


async def _seed_tenant(session: AsyncSession, alias: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, 'Pages Firm') "
                    "ON CONFLICT (uid) DO UPDATE SET name = auth.tenants.name RETURNING id"
                ),
                {"u": f"ten_{alias}", "a": alias},
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_firm_branding_put_gates_pages_changes_on_pages_write(
    service_session: AsyncSession,
) -> None:
    from app.routers import firm

    tid = await _seed_tenant(service_session, f"pages-{uuid.uuid4().hex[:8]}")

    def ctx(perms: list[str]) -> FirmContext:
        return FirmContext(session=service_session, tenant_id=tid, principal=_principal(perms))

    # branding:write alone: cosmetic saves fine…
    colours = Branding.model_validate({"colors": {"light": {"primary": "#336699"}}})
    await firm.set_branding(body=firm.SetBranding(branding=colours), ctx=ctx(["branding:write"]))
    # …but a pages change is refused, and nothing was persisted
    with_pages = Branding.model_validate({"pages": _pages("Home")})
    with pytest.raises(HTTPException) as exc:
        await firm.set_branding(
            body=firm.SetBranding(branding=with_pages), ctx=ctx(["branding:write"])
        )
    _assert_403(exc, "pages:write")
    stored = await branding_mod.stored_branding(service_session, tid, "client")
    assert stored.pages is None

    # with pages:write the composition lands
    await firm.set_branding(
        body=firm.SetBranding(branding=with_pages), ctx=ctx(["branding:write", "pages:write"])
    )
    stored = await branding_mod.stored_branding(service_session, tid, "client")
    assert stored.pages is not None and stored.pages[0].label == "Home"

    # the echo rule end to end: a cosmetic edit that resubmits the stored pages untouched saves
    # on branding:write alone (the studio always sends the whole branding object)
    echo = Branding.model_validate(
        {"pages": _pages("Home"), "colors": {"light": {"primary": "#00274a"}}}
    )
    await firm.set_branding(body=firm.SetBranding(branding=echo), ctx=ctx(["branding:write"]))
    stored = await branding_mod.stored_branding(service_session, tid, "client")
    assert stored.colors.light is not None and stored.colors.light.primary == "#00274a"

    # …and CLEARING the pages (re-tracking platform defaults) is a composition change too
    with pytest.raises(HTTPException) as exc:
        await firm.set_branding(
            body=firm.SetBranding(branding=Branding()), ctx=ctx(["branding:write"])
        )
    _assert_403(exc, "pages:write")


@pytest.mark.asyncio
async def test_admin_branding_put_gates_pages_changes_on_pages_write(
    service_session: AsyncSession,
) -> None:
    """The same diff gate on the Graphene-admin PUT: tenants:write reaches the endpoint (that
    dependency is wiring, exercised elsewhere) but recomposing a tenant's portal additionally
    needs pages:write — `system` bypasses inside require_permission as everywhere."""
    from app.routers import admin

    tid = await _seed_tenant(service_session, f"pages-adm-{uuid.uuid4().hex[:8]}")
    with_pages = Branding.model_validate({"pages": _pages("Home")})

    with pytest.raises(HTTPException) as exc:
        await admin.set_branding(
            tenant_id=tid,
            body=admin.SetBranding(branding=with_pages),
            portal="client",
            session=service_session,
            _p=_principal(["tenants:write"]),
        )
    _assert_403(exc, "pages:write")

    out = await admin.set_branding(
        tenant_id=tid,
        body=admin.SetBranding(branding=with_pages),
        portal="client",
        session=service_session,
        _p=_principal(["tenants:write", "pages:write"]),
    )
    assert out["portal"] == "client"
    # cosmetic-only stays tenants:write (echoing the now-stored pages back untouched)
    echo = Branding.model_validate(
        {"pages": _pages("Home"), "colors": {"light": {"primary": "#101010"}}}
    )
    await admin.set_branding(
        tenant_id=tid,
        body=admin.SetBranding(branding=echo),
        portal="client",
        session=service_session,
        _p=_principal(["tenants:write"]),
    )


# ---- migration 0042: the grant backfill ---------------------------------------------------------


def _apply(db: dict[str, str], *filenames: str) -> None:
    """Re-apply migration SQL as the migrator role (the model_preset idiom from
    test_schema_invariants.py — both files are idempotent, and earlier RLS tests may have
    truncated auth.tenants CASCADE, taking the migration-seeded grants with it)."""
    engine = create_engine(db["migrator"])
    try:
        for filename in filenames:
            sql = (API_ROOT / "migrations" / "sql" / filename).read_text(encoding="utf-8")
            with engine.begin() as conn:
                conn.exec_driver_sql(sql)
    finally:
        engine.dispose()


def _grants(super_engine: Engine, tenant_id: int) -> dict[str, set[str]]:
    with super_engine.connect() as c:
        grants: dict[str, set[str]] = {}
        for row in c.execute(
            text(
                "SELECT r.slug AS role, p.slug AS perm FROM auth.role_permissions rp "
                "JOIN auth.roles r ON r.id = rp.role_id "
                "JOIN auth.permissions p ON p.id = rp.permission_id "
                "WHERE r.tenant_id = :t"
            ),
            {"t": tenant_id},
        ).mappings():
            grants.setdefault(row["role"], set()).add(row["perm"])
    return grants


def test_0042_seeds_the_model_preset_mapping(db: dict[str, str], super_engine: Engine) -> None:
    """The survey's model mapping: owner + adviser get the full working set (owner additionally
    pages:write — composing the portal is firm configuration), compliance gains the read the
    split created (see the queue, add nothing), the client role stays empty."""
    _apply(db, "0033_model_role_preset.sql", "0042_permission_splits.sql")
    with super_engine.connect() as c:
        model_id = int(
            c.execute(
                text("SELECT id FROM auth.tenants WHERE is_model ORDER BY id LIMIT 1")
            ).scalar_one()
        )
    grants = _grants(super_engine, model_id)

    working_set = {
        "instructions:read",
        "instructions:write",
        "documents:send",
        "illustrations:write",
        "forms:submit",
    }
    assert working_set | {"pages:write"} <= grants["owner"]
    assert working_set <= grants["adviser"]
    assert "pages:write" not in grants["adviser"]  # composition is firm configuration
    assert "instructions:read" in grants["compliance"]  # the read-behind-write fix, delivered
    assert "instructions:write" not in grants["compliance"]  # oversight still writes nothing
    assert "client" not in grants  # client-portal roles carry no permissions, ever


def test_0042_backfills_every_existing_parent_holder(
    db: dict[str, str], super_engine: Engine
) -> None:
    """The expand contract: a firm role that held a parent slug yesterday holds the split slug
    after the migration (so nobody 403s on Monday), and a role that did NOT hold the parent
    gains nothing (forms:read deliberately does not backfill forms:submit — that narrowing IS
    the split). Applied twice to prove idempotency."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tid = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:u, :a, 'Backfill Firm') RETURNING id"
                ),
                {"u": f"ten_bf_{s}", "a": f"bf-{s}"},
            ).scalar_one()
        )
        roles = {
            # slug: the PARENT permissions the role held before the split landed
            "money-mover": ["contacts:write"],
            "doc-author": ["documents:write"],
            "form-designer": ["forms:read", "forms:write"],
            "form-viewer": ["forms:read"],
            "brander": ["branding:write"],
            "ops-observer": ["platforms:read"],
            "ops-runner": ["platforms:write"],
        }
        for slug, parents in roles.items():
            rid = int(
                c.execute(
                    text(
                        "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                        "VALUES (:t, :s, :s, 'organisation', 'platform') RETURNING id"
                    ),
                    {"t": tid, "s": slug},
                ).scalar_one()
            )
            for parent in parents:
                c.execute(
                    text(
                        "INSERT INTO auth.role_permissions (role_id, permission_id) "
                        "SELECT :r, id FROM auth.permissions WHERE slug = :p"
                    ),
                    {"r": rid, "p": parent},
                )
        c.commit()

    _apply(db, "0042_permission_splits.sql")
    first = _grants(super_engine, tid)

    assert {"instructions:read", "instructions:write"} <= first["money-mover"]
    assert {"documents:send", "illustrations:write"} <= first["doc-author"]
    assert "forms:submit" in first["form-designer"]  # write holders keep the response plane
    assert "forms:submit" not in first["form-viewer"]  # definition viewers lose it — the point
    assert "pages:write" in first["brander"]
    assert "platforms:query" in first["ops-observer"]
    # write⇒read satisfied the console gate before the split, so write-only roles backfill too
    assert "platforms:query" in first["ops-runner"]
    # …and no split slug leaks to a role whose parents don't imply it
    assert "instructions:write" not in first["doc-author"]
    assert "documents:send" not in first["money-mover"]

    _apply(db, "0042_permission_splits.sql")  # idempotent: same grants, no duplicates, no error
    assert _grants(super_engine, tid) == first


# ---- migration 0159: contacts:create -----------------------------------------------------------


def test_0159_seeds_the_model_preset_mapping(db: dict[str, str], super_engine: Engine) -> None:
    """Owner and adviser mint clients; compliance is read-only oversight and must not."""
    _apply(db, "0033_model_role_preset.sql", "0159_contacts_create.sql")
    with super_engine.connect() as c:
        model_id = int(
            c.execute(
                text("SELECT id FROM auth.tenants WHERE is_model ORDER BY id LIMIT 1")
            ).scalar_one()
        )
    grants = _grants(super_engine, model_id)
    assert "contacts:create" in grants["owner"]
    assert "contacts:create" in grants["adviser"]
    assert "contacts:create" not in grants.get("compliance", set())
    assert "client" not in grants


def test_0159_backfills_every_existing_write_holder(
    db: dict[str, str], super_engine: Engine
) -> None:
    """The expand contract: a role that held contacts:write yesterday holds contacts:create
    after the migration, and a role that did not hold write gains nothing. Applied twice to
    prove idempotency."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tid = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:u, :a, 'Create Backfill Firm') RETURNING id"
                ),
                {"u": f"ten_cc_{s}", "a": f"cc-{s}"},
            ).scalar_one()
        )
        roles = {
            "book-editor": ["contacts:write"],
            "book-reader": ["contacts:read"],
            "compliance": ["contacts:read_all"],
        }
        for slug, parents in roles.items():
            rid = int(
                c.execute(
                    text(
                        "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                        "VALUES (:t, :s, :s, 'organisation', 'platform') RETURNING id"
                    ),
                    {"t": tid, "s": slug},
                ).scalar_one()
            )
            for parent in parents:
                c.execute(
                    text(
                        "INSERT INTO auth.role_permissions (role_id, permission_id) "
                        "SELECT :r, id FROM auth.permissions WHERE slug = :p"
                    ),
                    {"r": rid, "p": parent},
                )
        c.commit()

    _apply(db, "0159_contacts_create.sql")
    first = _grants(super_engine, tid)

    assert "contacts:create" in first["book-editor"]
    assert "contacts:create" not in first["book-reader"]
    assert "contacts:create" not in first["compliance"]

    _apply(db, "0159_contacts_create.sql")
    assert _grants(super_engine, tid) == first


# ---- migration 0163: listings:export -----------------------------------------------------------


def test_0163_seeds_the_model_preset_mapping(db: dict[str, str], super_engine: Engine) -> None:
    """Owner, adviser and compliance may export listings; the client-portal role stays empty."""
    _apply(db, "0033_model_role_preset.sql", "0163_listings_export.sql")
    with super_engine.connect() as c:
        model_id = int(
            c.execute(
                text("SELECT id FROM auth.tenants WHERE is_model ORDER BY id LIMIT 1")
            ).scalar_one()
        )
    grants = _grants(super_engine, model_id)
    assert "listings:export" in grants["owner"]
    assert "listings:export" in grants["adviser"]
    assert "listings:export" in grants["compliance"]
    assert "client" not in grants


def test_0163_backfills_every_existing_book_reader(
    db: dict[str, str], super_engine: Engine
) -> None:
    """The expand contract: a role that could see a book listing yesterday holds
    listings:export after the migration, and a role that could not (no contacts:* )
    gains nothing. Applied twice to prove idempotency."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tid = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:u, :a, 'Export Backfill Firm') RETURNING id"
                ),
                {"u": f"ten_ex_{s}", "a": f"ex-{s}"},
            ).scalar_one()
        )
        roles = {
            "book-editor": ["contacts:write"],
            "book-reader": ["contacts:read"],
            "compliance": ["contacts:read_all"],
            "doc-author": ["documents:write"],
        }
        for slug, parents in roles.items():
            rid = int(
                c.execute(
                    text(
                        "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                        "VALUES (:t, :s, :s, 'organisation', 'platform') RETURNING id"
                    ),
                    {"t": tid, "s": slug},
                ).scalar_one()
            )
            for parent in parents:
                c.execute(
                    text(
                        "INSERT INTO auth.role_permissions (role_id, permission_id) "
                        "SELECT :r, id FROM auth.permissions WHERE slug = :p"
                    ),
                    {"r": rid, "p": parent},
                )
        c.commit()

    _apply(db, "0163_listings_export.sql")
    first = _grants(super_engine, tid)

    assert "listings:export" in first["book-editor"]
    assert "listings:export" in first["book-reader"]
    assert "listings:export" in first["compliance"]
    assert "listings:export" not in first["doc-author"]

    _apply(db, "0163_listings_export.sql")
    assert _grants(super_engine, tid) == first


# ---- migration 0180: contacts:sync_morgan_lloyd -----------------------------------------------


@pytest.mark.asyncio
async def test_morgan_lloyd_sync_regates_to_contacts_sync_morgan_lloyd(
    service_session: AsyncSession,
) -> None:
    from app.routers import crm

    with pytest.raises(HTTPException) as exc:
        await crm.sync_contact_to_morgan_lloyd(
            contact_id=1, ctx=_ctx(service_session, ["contacts:write"])
        )
    _assert_403(exc, "contacts:sync_morgan_lloyd")
    with pytest.raises(HTTPException) as exc:
        await crm.sync_contact_to_morgan_lloyd(
            contact_id=999_999_999, ctx=_ctx(service_session, ["contacts:sync_morgan_lloyd"])
        )
    assert exc.value.status_code == 404  # past the gate: unknown contact (not 503 unconfigured)

    with pytest.raises(HTTPException) as exc:
        await crm.sync_account_to_morgan_lloyd(
            account_id=1, ctx=_ctx(service_session, ["contacts:write"])
        )
    _assert_403(exc, "contacts:sync_morgan_lloyd")
    with pytest.raises(HTTPException) as exc:
        await crm.sync_account_to_morgan_lloyd(
            account_id=999_999_999, ctx=_ctx(service_session, ["contacts:sync_morgan_lloyd"])
        )
    assert exc.value.status_code == 422  # past the gate: no client on the book

    with pytest.raises(HTTPException) as exc:
        await crm.push_contact_to_morgan_lloyd(
            contact_id=1,
            principal=_principal(["contacts:write"]),
            session=service_session,
        )
    _assert_403(exc, "contacts:sync_morgan_lloyd")
    with pytest.raises(HTTPException) as exc:
        await crm.push_contact_to_morgan_lloyd(
            contact_id=999_999_999,
            principal=_principal(["contacts:sync_morgan_lloyd"]),
            session=service_session,
        )
    assert exc.value.status_code == 404  # past the gate: unknown contact


def test_0189_seeds_the_model_preset_mapping(db: dict[str, str], super_engine: Engine) -> None:
    """Owner and adviser may sync to Morgan Lloyd; compliance is read-only and must not."""
    _apply(db, "0033_model_role_preset.sql", "0198_contacts_sync_morgan_lloyd.sql")
    with super_engine.connect() as c:
        model_id = int(
            c.execute(
                text("SELECT id FROM auth.tenants WHERE is_model ORDER BY id LIMIT 1")
            ).scalar_one()
        )
    grants = _grants(super_engine, model_id)
    assert "contacts:sync_morgan_lloyd" in grants["owner"]
    assert "contacts:sync_morgan_lloyd" in grants["adviser"]
    assert "contacts:sync_morgan_lloyd" not in grants.get("compliance", set())
    assert "client" not in grants


def test_0189_backfills_every_existing_write_holder(
    db: dict[str, str], super_engine: Engine
) -> None:
    """The expand contract: a role that held contacts:write yesterday holds
    contacts:sync_morgan_lloyd after the migration, and a role that did not hold write gains
    nothing. Applied twice to prove idempotency."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tid = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:u, :a, 'Sync Backfill Firm') RETURNING id"
                ),
                {"u": f"ten_sm_{s}", "a": f"sm-{s}"},
            ).scalar_one()
        )
        roles = {
            "book-editor": ["contacts:write"],
            "book-reader": ["contacts:read"],
            "compliance": ["contacts:read_all"],
        }
        for slug, parents in roles.items():
            rid = int(
                c.execute(
                    text(
                        "INSERT INTO auth.roles (tenant_id, slug, name, type, portal) "
                        "VALUES (:t, :s, :s, 'organisation', 'platform') RETURNING id"
                    ),
                    {"t": tid, "s": slug},
                ).scalar_one()
            )
            for parent in parents:
                c.execute(
                    text(
                        "INSERT INTO auth.role_permissions (role_id, permission_id) "
                        "SELECT :r, id FROM auth.permissions WHERE slug = :p"
                    ),
                    {"r": rid, "p": parent},
                )
        c.commit()

    _apply(db, "0198_contacts_sync_morgan_lloyd.sql")
    first = _grants(super_engine, tid)

    assert "contacts:sync_morgan_lloyd" in first["book-editor"]
    assert "contacts:sync_morgan_lloyd" not in first["book-reader"]
    assert "contacts:sync_morgan_lloyd" not in first["compliance"]

    _apply(db, "0198_contacts_sync_morgan_lloyd.sql")
    assert _grants(super_engine, tid) == first


# ---- documents:show (no migration: off by default) ------------------------------------------


def test_documents_show_is_a_firm_permission_no_parent_implies() -> None:
    by_slug = {p.slug: p for p in permissions.ALL}
    assert by_slug["documents:show"].portal == "platform"
    assert "documents:show" not in rbac.OPERATIONS_ONLY
    # write⇒read never reaches a sibling action: neither parent opens the page by itself
    assert not satisfies({"documents:read"}, "documents:show")
    assert not satisfies({"documents:write"}, "documents:show")
