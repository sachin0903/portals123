"""Tenant WorkOS environment registration + encrypted secret handling (app.environments).

Runs against the real testcontainer DB via the service_role session (the admin_session path).
Proves: config upsert, secrets are stored as ciphertext (never plaintext), and the per-host
lookup returns the decrypted config the shared portal consumes.
"""

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import environments
from app.config import settings


class _FakeWorkOS:
    """Records org creations, supports list (for find-or-create), returns deterministic ids."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self._orgs: list[SimpleNamespace] = []
        self.organizations = SimpleNamespace(
            create_organization=self._create, list_organizations=self._list
        )

    async def _create(self, *, name: str) -> SimpleNamespace:
        self.created.append(name)
        oid = f"org_{name.replace(' ', '_').lower()}_{len(self.created)}"
        org = SimpleNamespace(id=oid, name=name)
        self._orgs.append(org)
        return org

    async def _list(self) -> SimpleNamespace:
        orgs = list(self._orgs)

        async def auto_paging_iter():  # noqa: ANN202
            for org in orgs:
                yield org

        return SimpleNamespace(auto_paging_iter=auto_paging_iter)


COOKIE_PW = "a-cookie-password-at-least-32-bytes!!"  # noqa: S105 - test fixture, not a real secret


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())


@pytest_asyncio.fixture
async def tenant_id(service_session: AsyncSession) -> int:
    # Unique per test: service_session doesn't truncate, so fixed ids would collide.
    suffix = uuid.uuid4().hex[:10]
    row = await service_session.execute(
        text(
            "INSERT INTO auth.tenants (uid, alias, name, workos_project_id) "
            "VALUES (:uid, :alias, 'Firm Env', :proj) RETURNING id"
        ),
        {"uid": f"ten_{suffix}", "alias": f"firm-{suffix}", "proj": f"project_{suffix}"},
    )
    return int(row.scalar_one())


@pytest.mark.asyncio
async def test_register_and_fetch_env_config(service_session: AsyncSession, tenant_id: int) -> None:
    env = await environments.set_environment(
        service_session,
        tenant_id=tenant_id,
        workos_client_id="client_prod",
        issuer="https://api.workos.com/user_management/client_prod",
        client_portal_host="app.firm-env.com",
    )
    await environments.set_environment_secrets(
        service_session,
        tenant_environment_id=env["id"],
        workos_api_key="sk_live_env_key",  # noqa: S106 - test fixture, not a real secret
        cookie_password=COOKIE_PW,
    )
    # App-only portal (0107): the host's portal signs in through its OWN application — the app
    # client id paired with the app's own secret.
    await environments.set_environment_applications(
        service_session, tenant_id=tenant_id, client_portal_client_id="app_client"
    )
    await environments.set_environment_app_secrets(
        service_session,
        tenant_id=tenant_id,
        client_portal_secret="sk_app_client",  # noqa: S106 - test fixture, not a real secret
    )

    # Stored as ciphertext, never plaintext.
    for column in ("workos_api_key_ct", "client_portal_secret_ct"):
        ct = (
            await service_session.execute(
                text(
                    f"SELECT {column} FROM auth.environment_secrets "  # noqa: S608 - fixed 2-value loop, not user input
                    "WHERE tenant_environment_id = :e"
                ),
                {"e": env["id"]},
            )
        ).scalar_one()
        assert ct not in ("sk_live_env_key", "sk_app_client")

    # Every session authenticates into the ONE firm org (one-firm-org, 0096).
    await service_session.execute(
        text("UPDATE auth.tenant_environments SET firm_org_id = 'org_firm' WHERE id = :e"),
        {"e": env["id"]},
    )

    # The portal-facing lookup returns the decrypted config for the host.
    config = await environments.env_config_by_host(service_session, "app.firm-env.com")
    assert config is not None
    assert config["tenant_id"] == tenant_id
    assert config["workos_client_id"] == "client_prod"
    assert config["app_client_id"] == "app_client"  # the matched host's portal APPLICATION
    # The key the portal authenticates with is the host portal's APP secret — never the env key.
    assert config["workos_api_key"] == "sk_app_client"
    assert config["cookie_password"] == COOKIE_PW
    assert config["organization_id"] == "org_firm"  # the one firm org


@pytest.mark.asyncio
async def test_env_config_has_no_env_key_fallback(
    service_session: AsyncSession, tenant_id: int
) -> None:
    """A portal whose app secret isn't stored gets ``workos_api_key = None`` — surfaced by the
    login as "temporarily unavailable" rather than silently signing in with the environment key."""
    env = await environments.set_environment(
        service_session,
        tenant_id=tenant_id,
        workos_client_id="client_nofall",
        issuer="https://api.workos.com/user_management/client_nofall",
        client_portal_host="nofall.firm-env.com",
    )
    await environments.set_environment_secrets(
        service_session,
        tenant_environment_id=env["id"],
        workos_api_key="sk_live_env_only",  # noqa: S106 - test fixture, not a real secret
        cookie_password=COOKIE_PW,
    )
    config = await environments.env_config_by_host(service_session, "nofall.firm-env.com")
    assert config is not None
    assert config["workos_api_key"] is None  # no env-key fallback
    assert config["app_client_id"] is None  # no app registered either


@pytest.mark.asyncio
async def test_unknown_host_returns_none(service_session: AsyncSession) -> None:
    assert await environments.env_config_by_host(service_session, "nope.example.com") is None


def test_local_host_alias_pairs_compose_and_vite_ports() -> None:
    assert environments.local_host_alias("localhost:3000") == "localhost:5173"
    assert environments.local_host_alias("localhost:5173") == "localhost:3000"
    assert environments.local_host_alias("localhost:3001") == "localhost:5175"
    assert environments.local_host_alias("localhost:5175") == "localhost:3001"
    assert environments.local_host_alias("localhost:3002") == "localhost:5174"
    assert environments.local_host_alias("127.0.0.1:3000") == "127.0.0.1:5173"
    assert environments.local_host_alias("app.firm.com") == "app.firm.com"
    assert environments.local_host_alias("localhost:8080") == "localhost:8080"


@pytest.mark.asyncio
async def test_env_config_by_host_accepts_local_compose_ports(
    service_session: AsyncSession, tenant_id: int
) -> None:
    """``localhost:3000`` resolves a row stored as ``localhost:5173``."""
    await service_session.execute(
        text(
            "DELETE FROM auth.tenant_environments "
            "WHERE client_portal_host IN ('localhost:5173', 'localhost:3000') "
            "   OR platform_host IN ('localhost:5175', 'localhost:3001') "
            "   OR operations_host IN ('localhost:5174', 'localhost:3002')"
        )
    )
    await environments.set_environment(
        service_session,
        tenant_id=tenant_id,
        workos_client_id="client_compose_alias",
        issuer="https://api.workos.com/user_management/client_compose_alias",
        client_portal_host="localhost:5173",
        platform_host="localhost:5175",
        operations_host="localhost:5174",
    )
    client = await environments.env_config_by_host(service_session, "localhost:3000")
    platform = await environments.env_config_by_host(service_session, "localhost:3001")
    ops = await environments.env_config_by_host(service_session, "localhost:3002")
    vite = await environments.env_config_by_host(service_session, "localhost:5173")
    assert client is not None and platform is not None and ops is not None and vite is not None
    assert client["portal"] == "client"
    assert platform["portal"] == "platform"
    assert ops["portal"] == "operations"
    assert vite["tenant_id"] == client["tenant_id"] == tenant_id


@pytest.mark.asyncio
async def test_env_config_serves_defaults_when_stored_theme_is_unparseable(
    service_session: AsyncSession, tenant_id: int
) -> None:
    """WorkOS keys still return; branding degrades."""
    host = f"theme-miss.{uuid.uuid4().hex[:8]}.example.com"
    await environments.set_environment(
        service_session,
        tenant_id=tenant_id,
        workos_client_id="client_theme_miss",
        client_portal_host=host,
    )
    await service_session.execute(
        text("UPDATE auth.tenants SET theme = CAST(:th AS jsonb) WHERE id = :t"),
        {
            "t": tenant_id,
            "th": '{"v": 2, "client": {"logo_url": "javascript:alert(1)"}}',
        },
    )
    cfg = await environments.env_config_by_host(service_session, host)
    assert cfg is not None
    assert cfg["workos_client_id"] == "client_theme_miss"
    assert cfg["branding"]["logo_url"] is None


@pytest.mark.asyncio
async def test_env_config_withholds_a_large_inline_logo_from_the_shell(
    service_session: AsyncSession, tenant_id: int
) -> None:
    """The payload a portal receives never carries a large data-URI logo: decoding one on the main
    thread was the staff shell's Largest Contentful Paint. It arrives as None (the shell falls
    back to the built-in mark); a small inline icon and a proxy path pass; and the EDITOR's own
    read still sees the stored value, so a save from the studio does not erase the firm's logo."""
    import base64
    import os
    from io import BytesIO

    from PIL import Image

    from app import branding, shell_images

    host = f"inline-logo.{uuid.uuid4().hex[:8]}.example.com"
    await environments.set_environment(
        service_session,
        tenant_id=tenant_id,
        workos_client_id="client_inline_logo",
        platform_host=host,
    )
    # A 600×200 noisy RGBA PNG: hundreds of KB (well over the byte bound, under branding's own
    # 1.4M-character data-URI cap) and over the pixel bound too.
    out = BytesIO()
    Image.frombytes("RGBA", (600, 200), os.urandom(600 * 200 * 4)).save(out, "PNG")
    big = f"data:image/png;base64,{base64.b64encode(out.getvalue()).decode('ascii')}"
    assert len(out.getvalue()) > shell_images.MAX_INLINE_SHELL_BYTES
    small = "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode("ascii")
    proxied = "/_assets/logo-dark-0123456789abcdef.png"
    await service_session.execute(
        text("UPDATE auth.tenants SET theme = CAST(:th AS jsonb) WHERE id = :t"),
        {
            "t": tenant_id,
            "th": branding.Theme(
                platform=branding.Branding(logo_url=big, icon_url=small, logo_dark_url=proxied)
            ).model_dump_json(),
        },
    )
    cfg = await environments.env_config_by_host(service_session, host)
    assert cfg is not None and cfg["portal"] == "platform"
    assert cfg["branding"]["logo_url"] is None
    assert cfg["branding"]["icon_url"] == small
    assert cfg["branding"]["logo_dark_url"] == proxied

    editor = await branding.get_branding(service_session, tenant_id, "platform")
    assert editor["branding"]["logo_url"] == big


@pytest.mark.asyncio
async def test_operations_tenant_and_firm_org_routing(
    service_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tenant_id: int
) -> None:
    """The ops tenant is the one whose env uses the core client id; its ONE firm org resolves via
    the core client (from settings, not a stored key) — one-firm-org, portals are applications."""
    monkeypatch.setattr(settings, "workos_client_id", "core_client_xyz")
    monkeypatch.setattr(settings, "workos_api_key", "sk_core")
    await service_session.execute(
        text(
            "INSERT INTO auth.tenant_environments "
            "  (tenant_id, stage, workos_client_id, firm_org_id) "
            "VALUES (:t, 'production', 'core_client_xyz', 'org_graphene')"
        ),
        {"t": tenant_id},
    )
    assert await environments.operations_tenant_id(service_session) == tenant_id
    org_id, client = await environments.firm_org_and_client(service_session, tenant_id=tenant_id)
    assert org_id == "org_graphene"
    assert client.client_id == "core_client_xyz"  # the core client, not a tenant key


@pytest.mark.asyncio
async def test_ensure_firm_org_creates_one_org_and_is_idempotent(
    service_session: AsyncSession, tenant_id: int
) -> None:
    """One-firm-org (0096): the SINGLE org every member authenticates into, named after the firm.
    Which portals they may use is `auth.organisation_memberships.portal`, never a second org."""
    env = await environments.set_environment(
        service_session,
        tenant_id=tenant_id,
        workos_client_id="client_p",
        issuer="https://api.workos.com/user_management/client_p",
        client_portal_host="ensure.firm-env.com",
    )
    workos = _FakeWorkOS()
    org_id = await environments.ensure_firm_org(
        service_session, workos, tenant_environment_id=env["id"]
    )
    assert org_id
    assert workos.created == ["Firm Env"]  # the firm's own name, not a portal label

    # Idempotent: a second call creates nothing new and returns the same id.
    again = await environments.ensure_firm_org(
        service_session, workos, tenant_environment_id=env["id"]
    )
    assert again == org_id
    assert workos.created == ["Firm Env"]

    # Idempotent against WorkOS too: even if the DB loses the id (a reset) while the WorkOS
    # org persists, it reuses it by name rather than creating a duplicate.
    await service_session.execute(
        text("UPDATE auth.tenant_environments SET firm_org_id = NULL WHERE id = :id"),
        {"id": env["id"]},
    )
    reused = await environments.ensure_firm_org(
        service_session, workos, tenant_environment_id=env["id"]
    )
    assert reused == org_id  # same id reused
    assert workos.created == ["Firm Env"]  # nothing new created


@pytest.mark.asyncio
async def test_env_workos_client_requires_stored_key(
    service_session: AsyncSession, tenant_id: int
) -> None:
    env = await environments.set_environment(
        service_session,
        tenant_id=tenant_id,
        workos_client_id="client_s",
        issuer="https://api.workos.com/user_management/client_s",
        client_portal_host="nokey.firm-env.com",
    )
    with pytest.raises(LookupError):
        await environments.env_workos_client(service_session, env["id"])
    # once keys are set, it builds a client
    await environments.set_environment_secrets(
        service_session,
        tenant_environment_id=env["id"],
        workos_api_key="sk_live_x",  # noqa: S106 - test fixture, not a real secret
        cookie_password=COOKIE_PW,
    )
    client = await environments.env_workos_client(service_session, env["id"])
    assert client is not None


@pytest.mark.asyncio
async def test_list_environments_flags_secrets_without_exposing_them(
    service_session: AsyncSession, tenant_id: int
) -> None:
    staging = await environments.set_environment(
        service_session,
        tenant_id=tenant_id,
        workos_client_id="client_staging",
        issuer="https://api.workos.com/user_management/client_staging",
        client_portal_host="staging.firm-env.com",
    )
    rows = await environments.list_environments(service_session, tenant_id)
    # One config per tenant per instance — stamped with the instance stage.
    assert [r["stage"] for r in rows] == [settings.app_stage]
    assert rows[0]["has_secrets"] is False  # none set yet
    assert rows[0]["firm_org_id"] is None  # the one org, not per-portal columns
    # Per-portal APPLICATION secret presence — the onboarding checklist's tick source.
    assert rows[0]["has_client_portal_secret"] is False
    assert rows[0]["has_platform_secret"] is False
    assert rows[0]["has_operations_secret"] is False
    assert "workos_api_key_ct" not in rows[0]  # secrets never surface here

    await environments.set_environment_secrets(
        service_session,
        tenant_environment_id=staging["id"],
        workos_api_key="sk_live_staging",  # noqa: S106 - test fixture, not a real secret
        cookie_password=COOKIE_PW,
    )
    await environments.set_environment_app_secrets(
        service_session,
        tenant_id=tenant_id,
        platform_secret="sk_app_staff",  # noqa: S106 - test fixture, not a real secret
    )
    rows = await environments.list_environments(service_session, tenant_id)
    assert rows[0]["has_secrets"] is True
    assert rows[0]["has_platform_secret"] is True
    assert rows[0]["has_client_portal_secret"] is False  # one portal's secret, not the others'
    assert "platform_secret_ct" not in rows[0]


# ---- one WorkOS project, one tenant ----------------------------------------------------------


async def _a_tenant(session: AsyncSession, name: str) -> int:
    suffix = uuid.uuid4().hex[:10]
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) "
                    "VALUES (:uid, :alias, :name) RETURNING id"
                ),
                {"uid": f"ten_{suffix}", "alias": f"claim-{suffix}", "name": name},
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_project_claimed_by_names_the_other_tenant(service_session: AsyncSession) -> None:
    """The bootstrap script's guard. Two tenants on one WorkOS project is unrepresentable —
    `auth.users.workos_user_id` is globally unique, so both event drains fight over every login
    (see app/events.py). The answer has to NAME the holder: a script that only said "refused"
    would leave you guessing which alias to re-run under."""
    first = await _a_tenant(service_session, "First Firm")
    second = await _a_tenant(service_session, "Second Firm")
    client_id = f"client_{uuid.uuid4().hex[:10]}"
    await environments.set_environment(service_session, tenant_id=first, workos_client_id=client_id)

    claimed = await environments.project_claimed_by(
        service_session, client_id, except_tenant_id=second
    )
    assert claimed == (first, "First Firm")


@pytest.mark.asyncio
async def test_a_tenant_re_claiming_its_own_project_is_not_a_conflict(
    service_session: AsyncSession,
) -> None:
    """Re-running the bootstrap for the SAME tenant is the normal case and must stay idempotent —
    the exemption is why this takes `except_tenant_id` rather than just counting rows."""
    only = await _a_tenant(service_session, "Sole Firm")
    client_id = f"client_{uuid.uuid4().hex[:10]}"
    await environments.set_environment(service_session, tenant_id=only, workos_client_id=client_id)

    assert (
        await environments.project_claimed_by(service_session, client_id, except_tenant_id=only)
        is None
    )


@pytest.mark.asyncio
async def test_an_unregistered_project_is_free(service_session: AsyncSession) -> None:
    tid = await _a_tenant(service_session, "New Firm")
    assert (
        await environments.project_claimed_by(
            service_session, f"client_{uuid.uuid4().hex[:10]}", except_tenant_id=tid
        )
        is None
    )
