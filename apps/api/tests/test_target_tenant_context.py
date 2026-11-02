"""Generic operations access to a tenant's platform-business workspace."""

import ast
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import db as db_mod
from app import live_push
from app.auth import Principal, get_current_principal
from app.config import settings
from app.main import app
from tests.conftest import api_routes


def test_alembic_has_a_single_head() -> None:
    """One head, whichever revision currently holds it.

    THE INVARIANT IS THE COUNT, not the name. Two heads is what happens when two branches add a
    migration off the same parent, and alembic then refuses to upgrade to an ambiguous `head` —
    which fails not as one error but as every database-backed test in the suite erroring before it
    runs. This test earns its place by catching exactly that, and it has: twice on this branch.

    It used to assert the head WAS a named revision, which made it fail for any branch that
    legitimately added a migration — so the signal it exists to give was indistinguishable from
    "someone appended to the graph", and the fix each time was to edit the expected name rather
    than to look at why.
    """
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    assert len(heads) == 1, f"the migration graph has forked: {sorted(heads)}"


def test_renamed_transfer_facts_revision_is_still_locatable() -> None:
    """A database stamped with the original id must still be able to `upgrade head`.

    Transfer-facts was applied as `0173_transfer_pension_facts` then committed as 0176 when
    0173–0175 landed on main. Alembic looks the stamped string up in the script directory; if
    that id disappears, `api:db-migrate` fails before any SQL runs. The stub + 0178 merge are
    what keep it on the graph — deleting either re-creates the error this pins.
    """
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("0173_transfer_pension_facts")
    assert revision is not None
    assert revision.down_revision == "0172_instruction_fee_type"


def test_renamed_account_role_revision_is_still_locatable() -> None:
    """A database stamped ``0236_account_role_party`` must still be able to ``upgrade head``.

    The party role was applied under that id, then committed as 0243 once 0235–0242 had
    taken the numbers. The stub and the 0244 merge are what keep the old id on the graph.
    """
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    stub = scripts.get_revision("0236_account_role_party")
    assert stub is not None
    assert stub.down_revision == "0234_txn_public_id"
    merge = scripts.get_revision("0244_merge_account_party")
    assert merge is not None
    assert set(merge.down_revision) == {"0243_account_role_party", "0236_account_role_party"}


def test_uncommitted_integrations_tenant_revision_is_still_locatable() -> None:
    """A database stamped ``0246_integrations_tenant`` must still be able to ``upgrade head``.

    Dev was migrated under that id on 9 Oct from a revision that was never committed, while
    0245 and 0246_contact_name_from_parts landed on main afterwards. The stub and the 0247
    merge are what keep the old id on the graph; neither carries the DDL.
    """
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    stub = scripts.get_revision("0246_integrations_tenant")
    assert stub is not None
    assert stub.down_revision == "0244_merge_account_party"
    merge = scripts.get_revision("0247_merge_integrations")
    assert merge is not None
    assert set(merge.down_revision) == {
        "0246_contact_name_from_parts",
        "0246_integrations_tenant",
    }


def test_integrations_tenant_ddl_follows_main() -> None:
    """The real DDL (the enum member, then `firm_id`'s retype) is a linear tail off main's head,
    not a chain hung off the old stamped ids -- same class of rename as the SIPP drawdown case
    above. It was applied to dev as `0245_intelliflo_provider`/`0246_integrations_tenant`, off
    `0244_merge_account_party` directly; main's own `0245`/`0246` on that same parent is what
    collided (the stub this file's companion test pins). Refiled as `0272`/`0273`, directly off
    main's head at the time they landed (`0271_employer_bank_account`), with no further merge
    needed: the stub/merge already reduced the graph to one head before these two are added.

    Deliberately NOT asserting 0273 is `get_heads()` here -- that's the exact anti-pattern
    `test_alembic_has_a_single_head` above moved away from (a later migration legitimately
    extends the chain past it); the single-head invariant is that test's job, by count, not this
    one's, by name.
    """
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    enum_member = scripts.get_revision("0272_intelliflo_provider")
    assert enum_member is not None
    assert enum_member.down_revision == "0271_employer_bank_account"
    firm_id = scripts.get_revision("0273_integrations_tenant")
    assert firm_id is not None
    assert firm_id.down_revision == "0272_intelliflo_provider"


# The SIPP-drawdown feature chain was applied as 0179–0186, then as 0188–0195.
# The DDL is 0197–0204, off main's head. Same class of rename as transfer-facts above.
_SIPP_DRAWDOWN_STUB_IDS = (
    "0179_outbound_transactions",
    "0180_contacts_sync_morgan_lloyd",
    "0181_account_open_reference",
    "0182_provider_documents",
    "0183_provider_document_names",
    "0184_sipp_scheme_admin",
    "0185_provider_document_dates",
    "0186_isa_inherit_gbp",
    "0188_outbound_transactions",
    "0189_contacts_sync_morgan_lloyd",
    "0190_account_open_reference",
    "0191_provider_documents",
    "0192_provider_document_names",
    "0193_sipp_scheme_admin",
    "0194_provider_document_dates",
    "0195_isa_inherit_gbp",
)


def test_sipp_drawdown_revisions_follow_main() -> None:
    """Feature DDL is a linear tail off current main, not a chain that reuses main's numbers."""
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    outbound = scripts.get_revision("0197_outbound_transactions")
    assert outbound is not None
    assert outbound.down_revision == "0191_merge_transfers"
    merge = scripts.get_revision("0205_merge_sipp_drawdown")
    assert merge is not None
    assert set(merge.down_revision) == {"0204_isa_inherit_gbp", "0196_merge_sipp_drawdown"}
    stubs = scripts.get_revision("0196_merge_sipp_drawdown")
    assert stubs is not None
    assert set(stubs.down_revision) == {"0195_isa_inherit_gbp", "0186_isa_inherit_gbp"}


def test_sipp_drawdown_renamed_revision_ids_remain_locatable() -> None:
    """A database stamped with 0179–0186 must still be able to `upgrade head`."""
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    for old_id in _SIPP_DRAWDOWN_STUB_IDS:
        revision = scripts.get_revision(old_id)
        assert revision is not None, old_id
    outbound_stub = scripts.get_revision("0179_outbound_transactions")
    assert outbound_stub is not None
    assert set(outbound_stub.down_revision) == {
        "0187_provider_columns",
        "0178_merge_transfer_facts",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "permissions", "dependency"),
    [
        ("GET", ["tenant_portal:read"], db_mod.firm_book_session),
        ("PUT", ["tenant_portal:write"], db_mod.firm_book_write_session),
    ],
)
async def test_complete_workspace_can_reuse_legacy_book_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    permissions: list[str],
    dependency: Callable[..., AsyncIterator[db_mod.FirmContext]],
) -> None:
    """Operate routes reuse established account/instruction handlers without requiring book:*."""
    principal = Principal(
        user_id="operator",
        organisation_id="org",
        role="operations",
        permissions=permissions,
        client_id="operations-app",
    )
    request = Request({"type": "http", "method": method, "path": "/", "headers": []})
    expected = db_mod.FirmContext(session=object(), tenant_id=42, principal=principal)  # type: ignore[arg-type]
    called: list[str] = []

    async def target(
        _request: Request, tenant: str | None, _principal: Principal
    ) -> AsyncIterator[db_mod.FirmContext]:
        called.append(str(tenant))
        yield expected

    monkeypatch.setattr(db_mod, "target_firm_session", target)
    yielded = [ctx async for ctx in dependency(request, "ten_target", principal)]
    assert yielded == [expected]
    assert called == ["ten_target"]


@pytest.fixture
def tenant_context_seed(super_engine: Engine) -> dict[str, str]:
    suffix = uuid.uuid4().hex[:8]
    result = {"suffix": suffix, "core_client_id": f"target_core_{suffix}"}
    with super_engine.connect() as connection:

        def tenant(tag: str) -> int:
            return int(
                connection.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) VALUES (:uid, :alias, :name) "
                        "RETURNING id"
                    ),
                    {
                        "uid": f"target_{tag}_{suffix}",
                        "alias": f"target-{tag}-{suffix}",
                        "name": f"Target {tag}",
                    },
                ).scalar_one()
            )

        def user(tenant_id: int, tag: str, portal: str) -> tuple[str, str]:
            user_id = str(
                connection.execute(
                    text(
                        "INSERT INTO auth.users "
                        "  (uid, tenant_id, email, workos_user_id, first_name, enabled) "
                        "VALUES (:uid, :tenant, :email, :wos, :name, true) RETURNING id"
                    ),
                    {
                        "uid": f"target_user_{tag}_{suffix}",
                        "tenant": tenant_id,
                        "email": f"{tag}-{suffix}@target.test",
                        "wos": f"target_wos_{tag}_{suffix}",
                        "name": tag,
                    },
                ).scalar_one()
            )
            connection.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (CAST(:user_id AS uuid), :tenant, :portal)"
                ),
                {"user_id": user_id, "tenant": tenant_id, "portal": portal},
            )
            return user_id, f"target_wos_{tag}_{suffix}"

        ops_id = tenant("ops")
        connection.execute(
            text(
                "INSERT INTO auth.tenant_environments "
                "  (tenant_id, stage, workos_client_id, operations_client_id) "
                "VALUES (:tenant, 'production', :core, :app)"
            ),
            {
                "tenant": ops_id,
                "core": result["core_client_id"],
                "app": f"target_ops_app_{suffix}",
            },
        )
        ops_uuid, ops_wos = user(ops_id, "ops", "operations")

        alpha_id = tenant("alpha")
        connection.execute(
            text(
                "INSERT INTO auth.tenant_environments "
                "  (tenant_id, stage, workos_client_id, platform_client_id) "
                "VALUES (:tenant, 'production', :core, :app)"
            ),
            {
                "tenant": alpha_id,
                "core": f"target_alpha_core_{suffix}",
                "app": f"target_platform_app_{suffix}",
            },
        )
        _staff_uuid, staff_wos = user(alpha_id, "staff", "platform")
        beta_id = tenant("beta")
        for tag, tenant_id in (("alpha", alpha_id), ("beta", beta_id)):
            connection.execute(
                text(
                    "INSERT INTO documents.forms "
                    "  (uid, tenant_id, code, name, entity_type) "
                    "VALUES (:uid, :tenant, :code, :name, 'applicant')"
                ),
                {
                    "uid": f"target_form_{tag}_{suffix}",
                    "tenant": tenant_id,
                    "code": f"target_{tag}_{suffix}",
                    "name": f"Target Form {tag} {suffix}",
                },
            )
        connection.commit()

    result.update(
        ops_id=str(ops_id),
        ops_uuid=ops_uuid,
        ops_wos=ops_wos,
        ops_app=f"target_ops_app_{suffix}",
        alpha_id=str(alpha_id),
        alpha_uid=f"target_alpha_{suffix}",
        staff_wos=staff_wos,
        staff_app=f"target_platform_app_{suffix}",
        beta_id=str(beta_id),
        beta_uid=f"target_beta_{suffix}",
    )
    return result


@pytest_asyncio.fixture
async def target_client(
    db: dict[str, str],
    tenant_context_seed: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "SessionLocal", maker)
    monkeypatch.setattr(settings, "workos_client_id", tenant_context_seed["core_client_id"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await engine.dispose()


ActAs = Callable[[str, list[str]], None]


@pytest.fixture
def target_actor(tenant_context_seed: dict[str, str]) -> AsyncIterator[ActAs]:
    def act_as(who: str, permissions: list[str]) -> None:
        seed = tenant_context_seed
        principal = Principal(
            user_id=seed["ops_wos"] if who == "ops" else seed["staff_wos"],
            organisation_id=f"target_org_{seed['suffix']}",
            role="operations" if who == "ops" else "adviser",
            permissions=list(permissions),
            client_id=seed["ops_app"] if who == "ops" else seed["staff_app"],
        )

        async def overridden_principal(request: Request) -> Principal:
            request.state.principal = principal
            return principal

        app.dependency_overrides[get_current_principal] = overridden_principal

    yield act_as
    app.dependency_overrides.pop(get_current_principal, None)


@pytest.mark.asyncio
async def test_target_rls_read_is_isolated_and_domain_permissions_are_visible(
    target_client: AsyncClient, target_actor: ActAs, tenant_context_seed: dict[str, str]
) -> None:
    target_actor("ops", ["tenant_portal:read"])
    response = await target_client.get(
        "/forms", params={"tenant": tenant_context_seed["alpha_uid"]}
    )
    assert response.status_code == 200, response.text
    assert f"Target Form alpha {tenant_context_seed['suffix']}" in response.text
    assert f"Target Form beta {tenant_context_seed['suffix']}" not in response.text


@pytest.mark.asyncio
async def test_target_firm_context_endpoint_uses_selected_tenant(
    target_client: AsyncClient, target_actor: ActAs, tenant_context_seed: dict[str, str]
) -> None:
    target_actor("ops", ["tenant_portal:read"])
    response = await target_client.get(
        "/automations", params={"tenant": tenant_context_seed["alpha_uid"]}
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_workspace_permission_can_resolve_target_identity_without_configure_access(
    target_client: AsyncClient, target_actor: ActAs, tenant_context_seed: dict[str, str]
) -> None:
    seed = tenant_context_seed
    target_actor("ops", ["tenant_portal:read"])
    response = await target_client.get(f"/admin/tenants/{seed['alpha_uid']}")
    assert response.status_code == 200, response.text
    assert response.json()["uid"] == seed["alpha_uid"]

    target_actor("ops", ["audit:read"])
    refused = await target_client.get(f"/admin/tenants/{seed['alpha_uid']}")
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_messaging_ticket_subscribes_to_target_firm_with_read_permission(
    target_client: AsyncClient, target_actor: ActAs, tenant_context_seed: dict[str, str]
) -> None:
    seed = tenant_context_seed
    target_actor("ops", ["tenant_portal:read"])
    response = await target_client.post("/messaging/ticket", params={"tenant": seed["beta_uid"]})
    assert response.status_code == 200, response.text
    assert live_push.redeem_ticket(response.json()["ticket"]) == live_push.staff_key(
        int(seed["beta_id"])
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/forms", "/automations"])
async def test_target_read_requires_tenant_portal_read(
    target_client: AsyncClient,
    target_actor: ActAs,
    tenant_context_seed: dict[str, str],
    path: str,
) -> None:
    target_actor("ops", ["tenants:read"])
    response = await target_client.get(path, params={"tenant": tenant_context_seed["alpha_uid"]})
    assert response.status_code == 403
    assert "tenant_portal:read" in response.text


@pytest.mark.asyncio
async def test_target_write_requires_write_and_records_target_and_real_actor(
    target_client: AsyncClient,
    target_actor: ActAs,
    tenant_context_seed: dict[str, str],
    super_engine: Engine,
) -> None:
    seed = tenant_context_seed
    body = {"code": f"created_{seed['suffix']}", "name": "Created through target context"}
    target_actor("ops", ["tenant_portal:read"])
    refused = await target_client.post("/forms", params={"tenant": seed["alpha_uid"]}, json=body)
    assert refused.status_code == 403
    assert "tenant_portal:write" in refused.text

    target_actor("ops", ["tenant_portal:write"])
    created = await target_client.post("/forms", params={"tenant": seed["alpha_uid"]}, json=body)
    assert created.status_code == 201, created.text
    form_uid = created.json()["uid"]
    with super_engine.connect() as connection:
        form_tenant = connection.execute(
            text("SELECT tenant_id FROM documents.forms WHERE uid = :uid"), {"uid": form_uid}
        ).scalar_one()
        event = connection.execute(
            text(
                "SELECT tenant_id, target_tenant_id, actor_id::text AS actor_id "
                "FROM audit.actions WHERE event_type = 'form.created' "
                "  AND metadata -> 'query' ->> 'tenant' = :tenant "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"tenant": seed["alpha_uid"]},
        ).one()
    assert form_tenant == int(seed["alpha_id"])
    assert event.tenant_id == int(seed["ops_id"])
    assert event.target_tenant_id == int(seed["alpha_id"])
    assert event.actor_id == seed["ops_uuid"]


@pytest.mark.asyncio
async def test_non_operations_and_unknown_targets_are_refused_but_suspended_firms_remain_visible(
    target_client: AsyncClient,
    target_actor: ActAs,
    tenant_context_seed: dict[str, str],
    super_engine: Engine,
) -> None:
    seed = tenant_context_seed
    target_actor("staff", ["tenant_portal:read", "forms:read"])
    non_ops = await target_client.get("/forms", params={"tenant": seed["alpha_uid"]})
    assert non_ops.status_code == 403

    target_actor("ops", ["tenant_portal:read"])
    unknown = await target_client.get("/forms", params={"tenant": f"missing_{seed['suffix']}"})
    assert unknown.status_code == 404
    with super_engine.connect() as connection:
        connection.execute(
            text("UPDATE auth.tenants SET enabled = false WHERE id = :tenant"),
            {"tenant": int(seed["beta_id"])},
        )
        connection.commit()
    suspended = await target_client.get("/forms", params={"tenant": seed["beta_uid"]})
    assert suspended.status_code == 200, suspended.text
    assert f"Target Form beta {seed['suffix']}" in suspended.text


@pytest.mark.asyncio
async def test_no_param_delegates_to_existing_tenant_and_firm_sessions(
    target_client: AsyncClient, target_actor: ActAs, tenant_context_seed: dict[str, str]
) -> None:
    seed = tenant_context_seed
    target_actor("staff", ["forms:read", "automations:read"])
    forms = await target_client.get("/forms")
    assert forms.status_code == 200, forms.text
    assert f"Target Form alpha {seed['suffix']}" in forms.text
    assert f"Target Form beta {seed['suffix']}" not in forms.text
    automations = await target_client.get("/automations")
    assert automations.status_code == 200, automations.text


@pytest.mark.asyncio
async def test_workspace_reads_the_target_firms_own_directory(
    target_client: AsyncClient, target_actor: ActAs, tenant_context_seed: dict[str, str]
) -> None:
    """The assignee/adviser pickers list the NAMED firm's people, not the operator's own.

    This read was refused outright before (`/firm/members` was not target-aware, so an operations
    principal held no `members:read` for it) — which is what emptied the workspace's task,
    journey and client-people pickers.
    """
    seed = tenant_context_seed
    target_actor("ops", ["tenant_portal:read"])
    response = await target_client.get("/firm/members", params={"tenant": seed["alpha_uid"]})
    assert response.status_code == 200, response.text
    emails = {member["email"] for member in response.json()}
    assert f"staff-{seed['suffix']}@target.test" in emails
    assert f"ops-{seed['suffix']}@target.test" not in emails


@pytest.mark.asyncio
async def test_a_uid_in_a_workspace_path_resolves_in_the_target_firm(
    target_client: AsyncClient, target_actor: ActAs, tenant_context_seed: dict[str, str]
) -> None:
    """A path uid must resolve against the NAMED firm, not the operator's own tenant.

    The resolvers in `app.addressing` are separate dependencies, and a resolver declaring a
    different session from its endpoint opens a second one bound to the operator's tenant: the
    firm's own record then 404s. `/forms/{uid}` is the cheap witness (the seed gives alpha and
    beta one form each); `test_uid_resolvers_ride_their_endpoints_own_session` is the general rule.
    """
    seed = tenant_context_seed
    target_actor("ops", ["tenant_portal:read"])
    found = await target_client.get(
        f"/forms/target_form_alpha_{seed['suffix']}", params={"tenant": seed["alpha_uid"]}
    )
    assert found.status_code == 200, found.text
    assert found.json()["code"] == f"target_alpha_{seed['suffix']}"
    # The same uid outside the named firm stays a 404 — the target does not widen the lookup.
    foreign = await target_client.get(
        f"/forms/target_form_beta_{seed['suffix']}", params={"tenant": seed["alpha_uid"]}
    )
    assert foreign.status_code == 404


_SESSION_DEPENDENCIES = {
    "admin_session",
    "book_session",
    "firm_book_session",
    "firm_book_write_session",
    "firm_session",
    "get_session",
    "target_firm_session",
    "target_tenant_read_session",
    "target_tenant_session",
    "tenant_session",
}


def _declared(fn: object) -> tuple[set[str], set[str]]:
    """The (session, resolver) dependency names a callable declares, defaults and Annotated."""
    import inspect
    import typing

    sessions: set[str] = set()
    resolvers: set[str] = set()
    for param in inspect.signature(fn).parameters.values():  # type: ignore[arg-type]
        candidates = [param.default, *typing.get_args(param.annotation)]
        for candidate in candidates:
            dependency = getattr(candidate, "dependency", None)
            name = getattr(dependency, "__name__", None)
            if name is None:
                continue
            (sessions if name in _SESSION_DEPENDENCIES else resolvers).add(name)
    return sessions, resolvers


def test_uid_resolvers_ride_their_endpoints_own_session() -> None:
    """Every uid resolver must declare the same session its endpoints declare.

    FastAPI caches a dependency per request, so agreeing means one session and one scoping.
    DISAGREEING is a whole class of bug with one symptom: where the endpoint's session is
    target-aware, the resolver's second session binds the OPERATOR's tenant, the uid resolves
    against the wrong book, and the workspace answers 404 for a record that is plainly there.
    Cheaper to assert than to rediscover — it cost several tabs of the tenant workspace once.
    """
    import inspect

    from app import addressing

    resolvers = {
        name: _declared(fn)[0]
        for name, fn in vars(addressing).items()
        if inspect.iscoroutinefunction(fn) and _declared(fn)[0]
    }
    mismatched: list[str] = []
    for route in api_routes(app):
        sessions, used = _declared(route.endpoint)
        for name in used & resolvers.keys():
            if not resolvers[name] <= sessions:
                mismatched.append(
                    f"{sorted(route.methods or [])} {route.path}: {name} rides "
                    f"{sorted(resolvers[name])}, endpoint takes {sorted(sessions)}"
                )
    assert not mismatched, "\n".join(mismatched)


RLS_PROXY_ROUTERS = {
    "catalog.py",
    "content.py",
    "crm.py",
    "datasets.py",
    "documents.py",
    "email_templates.py",
    "files.py",
    "forms.py",
    "illustrations.py",
    "ref.py",
    "search.py",
    "tasks.py",
    "templates.py",
}
FIRM_PROXY_ROUTERS = {"applications.py", "automations.py", "flows.py", "messaging.py"}


def test_proxy_capable_router_families_cannot_reimport_bare_sessions() -> None:
    """A static tripwire for the declared whole-router proxy families."""
    routers = Path(__file__).parents[1] / "app" / "routers"
    for filename in RLS_PROXY_ROUTERS | FIRM_PROXY_ROUTERS:
        tree = ast.parse((routers / filename).read_text())
        imports = {
            alias.name: alias.asname
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "app.db"
            for alias in node.names
        }
        if filename in RLS_PROXY_ROUTERS:
            assert imports.get("target_tenant_session") == "tenant_session", filename
            assert "tenant_session" not in imports, filename
        if filename in FIRM_PROXY_ROUTERS:
            assert imports.get("target_firm_session") == "firm_session", filename
            assert "firm_session" not in imports, filename
    contributions = (routers / "contributions.py").read_text()
    assert "Depends(firm_session)" not in contributions
    assert "Depends(target_firm_session)" in contributions


def test_firm_business_surfaces_use_target_context_without_proxying_configuration() -> None:
    source = (Path(__file__).parents[1] / "app" / "routers" / "firm.py").read_text()
    tree = ast.parse(source)
    expected = {
        "list_firm_audit_actions",
        "list_firm_comms_messages",
        "get_firm_comms_message",
        "get_firm_event_registry",
        "firm_notification_catalogue",
        "firm_notification_email_templates",
        "firm_list_notification_rules",
        "firm_create_notification_rule",
        "firm_update_notification_rule",
        "firm_delete_notification_rule",
        "firm_notification_rule_versions",
        "list_firm_data_sources",
    }
    functions = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }
    # The workspace's own pickers read the firm's directory (a task's assignee, a client's
    # advisers), so those READS are target-aware too.
    for name in expected | {"list_members", "get_member_profile", "list_roles"}:
        assert "Depends(target_firm_session)" in functions[name], name
    # Firm ADMINISTRATION stays behind the firm's own door: Graphene provisions people, edits
    # roles and sets branding through `/admin/tenants/{id}/…` under `tenants:write`, and a second
    # route to the same power — reachable with `tenant_portal:write` alone — is not wanted.
    for name in {"invite_member", "create_role", "set_role_permissions", "get_branding"}:
        assert "Depends(firm_session)" in functions[name], name
