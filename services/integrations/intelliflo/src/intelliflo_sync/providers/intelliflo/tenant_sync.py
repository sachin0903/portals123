"""Sync every tenant the Intelliflo app is installed for into integrations.platforms."""

import logging

from intelliflo_sync.providers.intelliflo import config
from intelliflo_sync.providers.intelliflo import db as iflo_db
from intelliflo_sync.providers.intelliflo.endpoints.installed_apps import InstalledAppsEndpoint
from intelliflo_sync.providers.intelliflo.endpoints.tenants import TenantsEndpoint
from intelliflo_sync.shared.base.auth import OAuth2Auth

logger = logging.getLogger(__name__)


def _make_auth() -> OAuth2Auth:
    # No tenant_id in the token request: this job discovers the tenants, so it has none to name, and the
    # app's own credentials are enough to read its group settings (checked against Intelliflo).
    missing = config.missing_credentials() + ([] if config.APP_ID else ["INTELLIFLO_APP_ID"])
    if missing:
        raise ValueError(f"Intelliflo tenant sync is not configured — set {', '.join(missing)}")
    return OAuth2Auth(
        token_url=config.TOKEN_URL,
        client_id=config.CLIENT_ID,
        client_secret=config.CLIENT_SECRET,
        grant_type=config.GRANT_TYPE,
        scope=config.SCOPE,
    )


def tenants_from_group_settings(items: list[dict]) -> dict[str, str | None]:
    """Distinct tenant id → its group id (the first one, if a tenant has several) from group settings."""
    tenants: dict[str, str | None] = {}
    for item in items:
        tenant_id = (item.get("tenant") or {}).get("id")
        if tenant_id is None:
            continue
        tenant_id = str(tenant_id)
        group_id = (item.get("group") or {}).get("id")
        group_id = str(group_id) if group_id is not None else None
        if tenant_id not in tenants:
            tenants[tenant_id] = group_id
        elif group_id and tenants[tenant_id] not in (None, group_id):
            logger.warning(
                "Tenant %s has several groups — keeping %s, ignoring %s",
                tenant_id,
                tenants[tenant_id],
                group_id,
            )
        elif group_id and tenants[tenant_id] is None:
            tenants[tenant_id] = group_id
    return tenants


def sync_tenants() -> dict:
    """Fetch the app's group settings, then each distinct tenant, and upsert them into integrations.platforms."""
    auth_headers = _make_auth().get_headers()
    items = InstalledAppsEndpoint(auth_headers).list_group_settings(config.APP_ID)
    tenants = tenants_from_group_settings(items)
    logger.info("Tenant sync: %d group setting(s), %d distinct tenant(s)", len(items), len(tenants))

    endpoint = TenantsEndpoint(auth_headers)
    synced, failed = 0, []
    for tenant_id, group_id in tenants.items():
        try:
            tenant = endpoint.get_tenant(tenant_id)
            iflo_db.upsert_tenant(
                tenant_id=tenant_id,
                tenant_name=str(tenant.get("name") or "").strip() or tenant_id,
                app_id=config.APP_ID,
                group_id=group_id,
                application_data=tenant,
                provider_id=config.DEFAULT_PROVIDER_ID,
                provider_name=config.DEFAULT_PROVIDER_NAME,
            )
            synced += 1
        except Exception as e:
            logger.error("Tenant sync failed for tenant %s: %s", tenant_id, e, exc_info=True)
            failed.append(tenant_id)
    result = {"tenants": len(tenants), "synced": synced, "failed": failed}
    logger.info("Tenant sync complete: %s", result)
    return result
