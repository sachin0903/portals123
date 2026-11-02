"""Store a tenant's portal APPLICATION credentials (client ids + secrets) directly.

The admin UI is the normal way to save these — but it needs a working admin login, which itself
needs the operations application's credentials: this script breaks that chicken-and-egg (and
serves fresh environments). Reads the ids/secrets from the environment or apps/api/.env(.local):

    DEV_CLIENT_PORTAL_APP_ID / DEV_CLIENT_PORTAL_APP_SECRET
    DEV_PLATFORM_APP_ID      / DEV_PLATFORM_APP_SECRET
    DEV_OPERATIONS_APP_ID    / DEV_OPERATIONS_APP_SECRET

Writes to EVERY environment row of the tenant (credentials are per-project), encrypting secrets
with SECRETS_MASTER_KEY, and ensures the firm org is stamped on every row. Blank values are
skipped (COALESCE-preserve). Safe to re-run.

Run:  uv run --directory apps/api python scripts/set_app_credentials.py [tenant-alias]
"""

import asyncio
import os
import sys

from dotenv import dotenv_values
from sqlalchemy import text

from app import environments
from app.db import system_transaction

TENANT_ALIAS = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DEV_TENANT_ALIAS", "graphene")


def _from_env(name: str, files: dict[str, str | None]) -> str | None:
    return os.environ.get(name) or files.get(name) or None


async def main() -> None:
    files = {**dotenv_values(".env"), **dotenv_values(".env.local")}
    ids = {
        "client_portal_client_id": _from_env("DEV_CLIENT_PORTAL_APP_ID", files),
        "platform_client_id": _from_env("DEV_PLATFORM_APP_ID", files),
        "operations_client_id": _from_env("DEV_OPERATIONS_APP_ID", files),
    }
    secrets = {
        "client_portal_secret": _from_env("DEV_CLIENT_PORTAL_APP_SECRET", files),
        "platform_secret": _from_env("DEV_PLATFORM_APP_SECRET", files),
        "operations_secret": _from_env("DEV_OPERATIONS_APP_SECRET", files),
    }
    if not any(ids.values()) and not any(secrets.values()):
        sys.exit("No DEV_*_APP_ID / DEV_*_APP_SECRET values found in the env or .env(.local).")

    async with system_transaction() as session:
        tenant_id = (
            await session.execute(
                text("SELECT id FROM auth.tenants WHERE alias = :a"), {"a": TENANT_ALIAS}
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            sys.exit(f"No tenant with alias '{TENANT_ALIAS}'.")
        if any(ids.values()):
            await environments.set_environment_applications(
                session, tenant_id=int(tenant_id), **ids
            )
        if any(secrets.values()):
            await environments.set_environment_app_secrets(
                session, tenant_id=int(tenant_id), **secrets
            )
        # Stamp the firm org across every row (created if the primary row lacks one).
        env_id = await environments.primary_environment_id(session, int(tenant_id))
        workos = await environments.client_for_env(session, env_id)
        firm_org = await environments.ensure_firm_org(session, workos, tenant_environment_id=env_id)
    print(
        f"Tenant '{TENANT_ALIAS}' (id {tenant_id}): app ids "
        f"{[k for k, v in ids.items() if v]}, secrets {[k for k, v in secrets.items() if v]} "
        f"stored on ALL environment rows; firm org {firm_org}."
    )


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
