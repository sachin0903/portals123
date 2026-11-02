"""Register the local dev tenant's WorkOS environment, so per-host resolution works again.

`bootstrap_dev_tenant.py` restores the *identity* mirror (tenant + user + role + memberships).
This restores the *routing* half that a `pnpm db:reset` also wipes: the `auth.tenant_environments`
row mapping each portal host to its WorkOS project, that environment's encrypted keys, the ONE
firm org, and the three portal APPLICATIONS' credentials (app-only portal classification, 0105 —
a portal signs in through its own application, and without the app ids + secrets stored, login on
that host reads as 503 misconfigured).

Idempotent, including against WorkOS: the firm org is matched by name before being created, so
re-running never leaves duplicate organisations behind. Re-running for the SAME tenant is the
normal case and simply re-claims its project.

It refuses one thing: registering a WorkOS project that another tenant already holds. The keys here
come from the API's own settings, so running this under a second `DEV_TENANT_ALIAS` would otherwise
point two tenants at one project — which the mirror cannot represent (`auth.users.workos_user_id` is
globally unique), and whose symptom is two event pollers dragging every login between two firms
until they collide. A second tenant needs its own project.

Run:  PYTHONPATH=. uv run --directory apps/api python scripts/bootstrap_dev_environment.py
Env overrides: DEV_TENANT_ALIAS, DEV_CLIENT_PORTAL_HOST, DEV_PLATFORM_HOST, DEV_OPERATIONS_HOST.
App credentials: DEV_CLIENT_PORTAL_APP_ID/_SECRET, DEV_PLATFORM_APP_ID/_SECRET,
DEV_OPERATIONS_APP_ID/_SECRET (or the same keys in apps/api/.env(.local)).

The WorkOS api key and client id come from the API's own settings; the cookie password from
`WORKOS_COOKIE_PASSWORD` (apps/api/.env). All must match what the portals use, since the portal
seals its session cookie with the value this stores.
"""

import asyncio
import os
import sys

from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.environments import (
    core_workos_client,
    ensure_firm_org,
    project_claimed_by,
    set_environment,
    set_environment_app_secrets,
    set_environment_applications,
    set_environment_secrets,
)

TENANT_ALIAS = os.environ.get("DEV_TENANT_ALIAS", "graphene")
# Host headers moon :dev sees. Compose :3000–:3002 are aliases in env_config_by_host.
CLIENT_PORTAL_HOST = os.environ.get("DEV_CLIENT_PORTAL_HOST", "localhost:5173")
PLATFORM_HOST = os.environ.get("DEV_PLATFORM_HOST", "localhost:5175")
OPERATIONS_HOST = os.environ.get("DEV_OPERATIONS_HOST", "localhost:5174")

# iron-session's floor; a shorter value only fails at *seal* time, which surfaces as a baffling
# "invalid code" on the MFA step (see pkgs/auth COOKIE_PASSWORD_MIN_LENGTH).
COOKIE_PASSWORD_MIN_LENGTH = 32


def _dotenv() -> dict[str, str | None]:
    """Settings ignores unknown keys, so read the API's dotenv files directly (same precedence as
    pydantic-settings: .env then .env.local, with a real environment variable winning)."""
    return {**dotenv_values(".env"), **dotenv_values(".env.local")}


def from_env(name: str, files: dict[str, str | None]) -> str:
    return os.environ.get(name) or files.get(name) or ""


async def main() -> None:
    files = _dotenv()
    password = from_env("WORKOS_COOKIE_PASSWORD", files)
    apps = {
        portal: (
            from_env(f"DEV_{prefix}_APP_ID", files),
            from_env(f"DEV_{prefix}_APP_SECRET", files),
        )
        for portal, prefix in (
            ("client", "CLIENT_PORTAL"),
            ("platform", "PLATFORM"),
            ("operations", "OPERATIONS"),
        )
    }
    missing = [
        name
        for name, value in (
            ("WORKOS_API_KEY", settings.workos_api_key),
            ("WORKOS_CLIENT_ID", settings.workos_client_id),
            ("SECRETS_MASTER_KEY", settings.secrets_master_key),
            ("WORKOS_COOKIE_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        sys.exit(f"Missing in apps/api/.env(.local): {', '.join(missing)}")
    if len(password) < COOKIE_PASSWORD_MIN_LENGTH:
        sys.exit(
            f"WORKOS_COOKIE_PASSWORD is shorter than {COOKIE_PASSWORD_MIN_LENGTH} characters; "
            "the portal cannot seal a session with it."
        )
    unconfigured = [p for p, (app_id, secret) in apps.items() if not (app_id and secret)]
    if unconfigured:
        print(
            f"NOTE: no app id+secret for: {', '.join(unconfigured)} — those portals will read as "
            "misconfigured (503) at sign-in until DEV_*_APP_ID/_SECRET are set and this re-runs."
        )

    engine = create_async_engine(settings.migration_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))  # BYPASSRLS for provisioning

        tenant_id = (
            await s.execute(
                text("SELECT id FROM auth.tenants WHERE alias = :alias"),
                {"alias": TENANT_ALIAS},
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            sys.exit(
                f"No tenant with alias '{TENANT_ALIAS}'. Run scripts/bootstrap_dev_tenant.py first."
            )

        # 0. One WorkOS project, one tenant. This script hands out `settings.workos_client_id` —
        #    the single project in apps/api/.env — so running it under a second DEV_TENANT_ALIAS
        #    silently points two tenants at one project. That cannot work, and it fails in a way
        #    nobody reads as a bootstrap mistake: `auth.users.workos_user_id` is globally unique, so
        #    each environment's event drain re-homes every mirrored login into its own tenant, and
        #    the tug-of-war eventually collides on `users_tenant_email_key` and wedges a poller.
        #    Refusing here costs a re-run; discovering it later cost a day (see app/events.py).
        claimed = await project_claimed_by(
            s, settings.workos_client_id, except_tenant_id=int(tenant_id)
        )
        if claimed is not None:
            other_id, other_name = claimed
            sys.exit(
                f"WorkOS project {settings.workos_client_id} is already registered to tenant "
                f"'{other_name}' (id {other_id}); tenant '{TENANT_ALIAS}' (id {tenant_id}) cannot "
                "share it.\n"
                "A project belongs to one tenant (docs/identity-and-access.md §0) and the mirror "
                "enforces that whether or not you mean it to — auth.users.workos_user_id is "
                "globally unique, so both environments would fight over every login.\n"
                "Either give this tenant its own WorkOS project (its own WORKOS_CLIENT_ID + "
                f"WORKOS_API_KEY in apps/api/.env), or run with DEV_TENANT_ALIAS pointing at "
                f"tenant '{other_name}', which already owns this one."
            )

        # 1. Routing config. issuer stays NULL so tenant_registry.default_issuer() derives it from
        #    the client id — only a custom AuthKit domain needs it set explicitly.
        env = await set_environment(
            s,
            tenant_id=int(tenant_id),
            workos_client_id=settings.workos_client_id,
            issuer=settings.workos_issuer or None,
            client_portal_host=CLIENT_PORTAL_HOST,
            platform_host=PLATFORM_HOST,
            operations_host=OPERATIONS_HOST,
        )

        # 2. Keys (encrypted with SECRETS_MASTER_KEY) — the env key is for provisioning calls; the
        #    cookie password seals portal sessions.
        await set_environment_secrets(
            s,
            tenant_environment_id=env["id"],
            workos_api_key=settings.workos_api_key,
            cookie_password=password,
        )

        # 3. The ONE firm org — every member's authentication home (portals are applications).
        workos = core_workos_client()
        firm_org = await ensure_firm_org(s, workos, tenant_environment_id=env["id"])

        # 4. The portal applications' credentials — what each host's sign-in actually uses.
        await set_environment_applications(
            s,
            tenant_id=int(tenant_id),
            client_portal_client_id=apps["client"][0] or None,
            platform_client_id=apps["platform"][0] or None,
            operations_client_id=apps["operations"][0] or None,
        )
        await set_environment_app_secrets(
            s,
            tenant_id=int(tenant_id),
            client_portal_secret=apps["client"][1] or None,
            platform_secret=apps["platform"][1] or None,
            operations_secret=apps["operations"][1] or None,
        )

    await engine.dispose()
    print(
        f"Registered environment {env['id']} (stage '{env['stage']}') for tenant "
        f"'{TENANT_ALIAS}' (id {tenant_id}).\n"
        f"  firm org       {firm_org}\n"
        f"  client portal  {CLIENT_PORTAL_HOST} (app: {apps['client'][0] or 'UNSET'})\n"
        f"  platform       {PLATFORM_HOST} (app: {apps['platform'][0] or 'UNSET'})\n"
        f"  operations     {OPERATIONS_HOST} (app: {apps['operations'][0] or 'UNSET'})\n"
        "  keys stored (WorkOS api key + cookie password + app secrets, encrypted)."
    )


if __name__ == "__main__":
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
