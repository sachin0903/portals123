"""Intelliflo Tenants API endpoint."""

import logging

import requests

from intelliflo_sync.providers.intelliflo import config

logger = logging.getLogger(__name__)


class TenantsEndpoint:
    def __init__(self, auth_headers: dict):
        self._headers = {
            "x-api-key": config.API_KEY,
            "Content-Type": "application/json",
            **auth_headers,
        }

    def get_tenant(self, tenant_id: str) -> dict:
        """The tenant's full record."""
        url = f"{config.API_BASE_URL}/v2/tenants/{tenant_id}"
        logger.info("Fetching tenant %s", tenant_id)
        response = requests.get(url, headers=self._headers, timeout=300)
        response.raise_for_status()
        return response.json()
