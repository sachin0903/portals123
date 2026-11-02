"""Intelliflo Installed Apps API endpoint."""

import logging

import requests

from intelliflo_sync.providers.intelliflo import config

logger = logging.getLogger(__name__)


class InstalledAppsEndpoint:
    def __init__(self, auth_headers: dict, product_provider_id: str = ""):
        self._headers = {
            "x-api-key": config.API_KEY,
            "x-iflo-productProviderId": product_provider_id,
            "Content-Type": "application/json",
            **auth_headers,
        }

    def list_group_settings(self, app_id: str) -> list[dict]:
        """Every group setting of the installed app (one per tenant/group it is installed for)."""
        url = f"{config.API_BASE_URL}/v2/installed_apps/{app_id}/group_settings"
        logger.info("Fetching group settings for installed app %s", app_id)
        response = requests.get(url, headers=self._headers, timeout=300)
        response.raise_for_status()
        body = response.json()
        items = body.get("items", [])
        if body.get("count", len(items)) > len(items):
            logger.warning(
                "Installed app %s: response has %s group settings but only %d returned",
                app_id,
                body.get("count"),
                len(items),
            )
        return items

    def get_group_id(self, app_id: str, tenant_id: str | None = None) -> str | None:
        """Return the id of the group the app is installed for, or None if it has no group settings.

        With `tenant_id`, only a group setting that belongs to that tenant counts; settings for any other
        tenant are ignored.
        """
        url = f"{config.API_BASE_URL}/v2/installed_apps/{app_id}/group_settings"
        logger.info("Fetching group settings for installed app %s", app_id)
        response = requests.get(url, headers=self._headers, timeout=300)
        response.raise_for_status()
        items = response.json().get("items", [])
        if tenant_id is not None:
            items = [i for i in items if str((i.get("tenant") or {}).get("id")) == str(tenant_id)]
        if not items:
            logger.warning(
                "No group settings found for installed app %s%s",
                app_id,
                f" and tenant {tenant_id}" if tenant_id is not None else "",
            )
            return None
        if len(items) > 1:
            logger.warning("Installed app %s has %d group settings — using the first", app_id, len(items))
        group_id = (items[0].get("group") or {}).get("id")
        return str(group_id) if group_id is not None else None
