"""Tenant sync: group settings → distinct tenants → /v2/tenants/<id> → integrations.platforms."""

from unittest.mock import MagicMock

import pytest

from intelliflo_sync.providers.intelliflo import config, tenant_sync
from intelliflo_sync.providers.intelliflo.endpoints.installed_apps import InstalledAppsEndpoint
from intelliflo_sync.providers.intelliflo.endpoints.tenants import TenantsEndpoint

ITEMS = [
    {"group": {"id": 10}, "tenant": {"id": 111}},
    {"group": {"id": 11}, "tenant": {"id": 111}},  # same tenant, second group: first one wins
    {"group": {"id": 20}, "tenant": {"id": 222}},
    {"group": {"id": 30}},  # no tenant: ignored
]


def _response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def test_distinct_tenants_with_their_group():
    assert tenant_sync.tenants_from_group_settings(ITEMS) == {"111": "10", "222": "20"}


def test_list_group_settings_and_get_tenant_urls(monkeypatch):
    urls = []  # both modules share the one requests module, so patch requests.get once

    def fake_get(url, **kw):
        urls.append(url)
        return _response({"items": ITEMS, "count": 4} if "group_settings" in url else {"id": 111, "name": "Acme"})

    monkeypatch.setattr("intelliflo_sync.providers.intelliflo.endpoints.tenants.requests.get", fake_get)
    assert InstalledAppsEndpoint({}).list_group_settings("app1") == ITEMS
    assert TenantsEndpoint({}).get_tenant("111") == {"id": 111, "name": "Acme"}
    assert urls[0].endswith("/v2/installed_apps/app1/group_settings")
    assert urls[1].endswith("/v2/tenants/111")


def _patch_sync(monkeypatch, tenant_error_for=None):
    monkeypatch.setattr(config, "APP_ID", "app1")
    monkeypatch.setattr(tenant_sync, "_make_auth", lambda: MagicMock(get_headers=lambda: {}))
    monkeypatch.setattr(InstalledAppsEndpoint, "list_group_settings", lambda self, app_id: ITEMS)

    def get_tenant(self, tenant_id):
        if tenant_id == tenant_error_for:
            raise RuntimeError("boom")
        return {"id": int(tenant_id), "name": f"Tenant {tenant_id}"}

    monkeypatch.setattr(TenantsEndpoint, "get_tenant", get_tenant)
    saved = []
    monkeypatch.setattr(tenant_sync.iflo_db, "upsert_tenant", lambda **kw: saved.append(kw) or "1")
    return saved


def test_sync_upserts_each_tenant_with_its_response(monkeypatch):
    saved = _patch_sync(monkeypatch)
    assert tenant_sync.sync_tenants() == {"tenants": 2, "synced": 2, "failed": []}
    assert saved[0] == {
        "tenant_id": "111",
        "tenant_name": "Tenant 111",
        "app_id": "app1",
        "group_id": "10",
        "application_data": {"id": 111, "name": "Tenant 111"},
        "provider_id": config.DEFAULT_PROVIDER_ID,
        "provider_name": config.DEFAULT_PROVIDER_NAME,
    }
    assert saved[1]["tenant_id"] == "222" and saved[1]["group_id"] == "20"


def test_sync_continues_after_a_failed_tenant(monkeypatch):
    saved = _patch_sync(monkeypatch, tenant_error_for="111")
    assert tenant_sync.sync_tenants() == {"tenants": 2, "synced": 1, "failed": ["111"]}
    assert [s["tenant_id"] for s in saved] == ["222"]


def test_sync_requires_app_id(monkeypatch):
    monkeypatch.setattr(config, "APP_ID", "")
    monkeypatch.setattr(config, "missing_credentials", lambda: [])
    with pytest.raises(ValueError, match="INTELLIFLO_APP_ID"):
        tenant_sync._make_auth()


class _Cursor:
    def __init__(self, existing):
        self.existing, self.calls = existing, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        last = self.calls[-1][0]
        return (7,) if last.startswith(("INSERT", "UPDATE")) or self.existing else None


def _upsert(monkeypatch, existing):
    cur = _Cursor(existing)
    conn = MagicMock(cursor=lambda: cur)
    monkeypatch.setattr(tenant_sync.iflo_db, "_get_pg_connection", lambda: conn)
    tenant_sync.iflo_db.upsert_tenant("111", "T", "app1", "10", {}, provider_id="5261", provider_name="Graphene")
    return cur.calls[-1]


def test_a_new_tenant_row_starts_with_the_default_provider(monkeypatch):
    sql, params = _upsert(monkeypatch, existing=False)
    assert sql.startswith("INSERT") and "provider_id, provider_name" in sql
    assert params[-2:] == ("5261", "Graphene")


def test_an_existing_row_gets_the_default_only_where_its_provider_is_blank(monkeypatch):
    sql, params = _upsert(monkeypatch, existing=True)
    assert sql.startswith("UPDATE")
    assert "provider_id = COALESCE(NULLIF(provider_id, ''), %s)" in sql
    assert "provider_name = COALESCE(NULLIF(provider_name, ''), %s)" in sql
    assert params[-3:-1] == ("5261", "Graphene") and params[-1] == 7


def test_the_token_request_names_no_tenant(monkeypatch):
    monkeypatch.setattr(config, "APP_ID", "app1")
    monkeypatch.setattr(config, "missing_credentials", lambda: [])
    assert tenant_sync._make_auth()._extra_data == {}
