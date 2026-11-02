"""Intelliflo integration: config comes from integrations.platforms, DSR filtering."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from intelliflo_sync.providers.intelliflo import config
from intelliflo_sync.providers.intelliflo import contract as iflo_contract
from intelliflo_sync.providers.intelliflo import db as iflo_db
from intelliflo_sync.providers.intelliflo import integration as iflo_integration

PLATFORM = {
    "platform_id": "p-1",
    "tenant_id": "12551",
    "internal_firm_id": "3",
    "firm_id": "3106",
    "dsr_codes": ["409663", "409855"],
    "provider_id": "5261",
}


# ── run(): platform config guard ─────────────────────────────────────────────


def test_run_raises_when_platform_missing(monkeypatch):
    monkeypatch.setattr(iflo_db, "get_platform_config", lambda tenant_id: None)
    with pytest.raises(ValueError, match="No platform found"):
        iflo_integration.IntellifloIntegration().run("12551")


@pytest.mark.parametrize("missing", ["firm_id", "provider_id"])
def test_run_raises_when_platform_field_empty(monkeypatch, missing):
    platform = {**PLATFORM, missing: ""}
    monkeypatch.setattr(iflo_db, "get_platform_config", lambda tenant_id: platform)
    with pytest.raises(ValueError, match=missing):
        iflo_integration.IntellifloIntegration().run("12551")


# ── firm id and DSR codes come from platform.tenant_platforms ────────────────


@pytest.mark.parametrize(
    "cfg, expected",
    [
        ({"firm_id": "3106", "dsr_codes": ["409663", " 409855 "]}, ("3106", ["409663", "409855"])),
        ({"firm_id": 3106, "dsr_ids": "409663, 409855"}, ("3106", ["409663", "409855"])),
        ({"firm_id": "3106", "dsr_id": "409663"}, ("3106", ["409663"])),
        ({"firm_id": "3106", "dsr_codes": []}, ("3106", [])),
        ({}, ("", [])),
        (None, ("", [])),
    ],
)
def test_firm_and_dsrs_from_tenant_platform_config(cfg, expected):
    assert iflo_db._firm_and_dsrs(cfg) == expected


# `dsr_ids` is the canonical key (the admin SEI card writes it; the SEI sync's slice reads only it).
# The older names still narrow, as a fallback, but never ahead of `dsr_ids` and never silently.


def test_dsr_ids_wins_over_a_stale_legacy_key(caplog):
    cfg = {"firm_id": "3106", "dsr_ids": ["704718"], "dsr_codes": ["D001"]}

    with caplog.at_level("WARNING", logger=iflo_db.logger.name):
        assert iflo_db._firm_and_dsrs(cfg) == ("3106", ["704718"])

    # The stale value is named, so it can be found and removed.
    assert "dsr_codes" in caplog.text and "D001" in caplog.text


def test_a_legacy_key_alone_still_narrows_but_is_flagged(caplog):
    with caplog.at_level("WARNING", logger=iflo_db.logger.name):
        assert iflo_db._firm_and_dsrs({"firm_id": "3106", "dsr_codes": ["704718"]}) == (
            "3106",
            ["704718"],
        )

    assert "legacy key 'dsr_codes'" in caplog.text


def test_an_empty_dsr_ids_falls_back_to_the_legacy_key(caplog):
    """`[]` means "not set", the same as absent, so a populated legacy list still applies."""
    with caplog.at_level("WARNING", logger=iflo_db.logger.name):
        assert iflo_db._firm_and_dsrs({"firm_id": "3106", "dsr_ids": [], "dsr_codes": ["1"]}) == (
            "3106",
            ["1"],
        )


def test_dsr_ids_alone_and_agreeing_keys_do_not_warn(caplog):
    with caplog.at_level("WARNING", logger=iflo_db.logger.name):
        assert iflo_db._firm_and_dsrs({"firm_id": "3106", "dsr_ids": ["704718"]})[1] == ["704718"]
        # The same teams under both names is redundant, not contradictory.
        assert iflo_db._firm_and_dsrs({"firm_id": "3106", "dsr_ids": ["704718"], "dsr_codes": ["704718"]})[1] == [
            "704718"
        ]

    assert caplog.text == ""


# No `lake` fixture / DSR-filtering tests for Valuations here any more: `get_valuations_for_firm`
# now reads the shared SEI datalake's `raw.*`/`platform.*` layer — see
# test_get_valuations_for_firm.py for that coverage, and providers/intelliflo/db.py's docstring +
# the README's "Datalake migration status" for the mapping.


# ── webhook: app id ──────────────────────────────────────────────────────────


def test_webhook_app_id_read_from_payload_id():
    from intelliflo_sync.providers.intelliflo.webhook import _app_id

    assert _app_id({"event": "AppInstalled", "payload": {"id": "89c73fd", "name": "App"}}) == "89c73fd"
    assert _app_id({"event": "AppInstalled", "payload": {"id": 123}}) == "123"


def test_webhook_app_id_missing():
    from intelliflo_sync.providers.intelliflo.webhook import _app_id

    assert _app_id({"event": "AppInstalled"}) is None
    assert _app_id({"payload": "x"}) is None
    assert _app_id({"payload": {}}) is None


# ── installed apps ─────────────────────────────────────────────────────────

from intelliflo_sync.providers.intelliflo.endpoints.installed_apps import InstalledAppsEndpoint

GROUP_SETTINGS = {
    "items": [{"id": "group:36955", "appId": "4970224", "group": {"id": 36955, "href": "x"}, "tenant": {"id": 12551}}],
    "count": 1,
}


def _fake_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def test_installed_apps_get_group_id(monkeypatch):
    called = {}

    def fake_get(url, headers=None, timeout=None):
        called["url"] = url
        return _fake_response(GROUP_SETTINGS)

    monkeypatch.setattr("intelliflo_sync.providers.intelliflo.endpoints.installed_apps.requests.get", fake_get)
    assert InstalledAppsEndpoint({}).get_group_id("4970224") == "36955"
    assert called["url"].endswith("/v2/installed_apps/4970224/group_settings")


def test_installed_apps_get_group_id_none_when_empty(monkeypatch):
    monkeypatch.setattr(
        "intelliflo_sync.providers.intelliflo.endpoints.installed_apps.requests.get",
        lambda *a, **kw: _fake_response({"items": [], "count": 0}),
    )
    assert InstalledAppsEndpoint({}).get_group_id("4970224") is None


# ── features come from platform_features ─────────────────────────────────────


def _run_with(monkeypatch, linked, **kwargs):
    from intelliflo_sync.providers.intelliflo import db as iflo_db

    platform = {
        "platform_id": "14",
        "tenant_id": "12551",
        "firm_id": "3106",
        "dsr_codes": [],
        "provider_id": "5261",
        "provider_name": "G",
        "app_id": "a",
        "group_id": "",
    }
    monkeypatch.setattr(iflo_db, "get_platform_config", lambda tenant: platform)
    monkeypatch.setattr(iflo_contract, "blocked_features", lambda features: {})
    # run() fails a feature nothing implements; these tests are about orchestration, so name them known
    # (keeping a real entry where the feature exists).
    for name in ("Valuations", "Transactions", "IncomeStatements", "IncomeStatementItems"):
        monkeypatch.setitem(iflo_contract.FEATURES, name, iflo_contract.FEATURES.get(name, {}))
    asked = []
    monkeypatch.setattr(
        iflo_integration, "fetch_integration_features", lambda platform_id: asked.append(platform_id) or linked
    )
    ran = []
    integration = iflo_integration.IntellifloIntegration()
    monkeypatch.setattr(
        integration, "_run_firm", lambda firm, features: (ran.extend(features) or {f: {} for f in features}, [])
    )
    return integration, ran, asked


def test_there_is_no_built_in_default_feature_list():
    assert not hasattr(config, "DEFAULT_FEATURES") and not hasattr(iflo_integration, "FEATURES")


def test_run_without_features_runs_what_is_linked_in_platform_features(monkeypatch):
    integration, ran, asked = _run_with(monkeypatch, linked=["IncomeStatements", "Valuations"])
    integration.run("12551")
    assert asked == ["14"] and ran == ["Valuations", "IncomeStatements"]


def test_an_explicit_feature_list_is_used_as_given(monkeypatch):
    integration, ran, asked = _run_with(monkeypatch, linked=["Transactions"])
    integration.run("12551", ["Valuations"])
    assert ran == ["Valuations"] and asked == []  # platform_features is not even consulted


def test_run_without_features_and_nothing_linked_refuses(monkeypatch):
    integration, ran, _ = _run_with(monkeypatch, linked=[])
    with pytest.raises(ValueError, match="No features are linked"):
        integration.run("12551")
    assert ran == []


# ── run order ────────────────────────────────────────────────────────────────


def test_features_run_in_dependency_order_whatever_order_they_are_listed():
    order = iflo_integration.IntellifloIntegration.order_features
    alphabetical = [
        "IncomeStatementItems",
        "IncomeStatements",
        "Valuations",
    ]  # how platform_features sorts them
    assert order(alphabetical) == ["Valuations", "IncomeStatements", "IncomeStatementItems"]
    assert order(["IncomeStatementItems", "IncomeStatements", "Valuations"]) == [
        "Valuations",
        "IncomeStatements",
        "IncomeStatementItems",
    ]
    assert order(["Zeta", "Valuations", "Alpha", "Valuations"]) == [
        "Valuations",
        "Alpha",
        "Zeta",
    ]  # unknown last; no duplicates


def test_a_feature_nothing_implements_fails_instead_of_recording_a_success(monkeypatch):
    """Recorded as a success it would mark the data date done, and the day would be skipped for good once
    the feature exists."""
    from intelliflo_sync.shared.base.exceptions import FeatureError

    integration, ran, _ = _run_with(monkeypatch, linked=[])
    with pytest.raises(FeatureError) as raised:
        integration.run("12551", ["NoSuchFeature"])
    assert raised.value.data["failed_features"] == {"NoSuchFeature"}
    assert "NoSuchFeature: not implemented" in str(raised.value)
    assert ran == []


def test_run_passes_the_features_to_run_firm_in_dependency_order(monkeypatch):
    integration, ran, _ = _run_with(monkeypatch, linked=[])
    integration.run("12551", ["IncomeStatementItems", "IncomeStatements", "Valuations"])
    assert ran == ["Valuations", "IncomeStatements", "IncomeStatementItems"]


def test_scheduler_and_main_share_one_run_loop_that_orders_the_features_with_the_provider():
    src = Path(__file__).resolve().parents[1] / "src/intelliflo_sync"
    assert "cls.order_features(features)" in (src / "shared/runner.py").read_text()
    for name in ("scheduler.py", "main.py"):
        source = (src / name).read_text()
        assert "runnable_integrations(" in source and "run_features(" in source, name


# ── per-feature cron ─────────────────────────────────────────────────────────

from intelliflo_sync.shared.schedule import group_by_cron

DEFAULT_CRON = iflo_integration.IntellifloIntegration.cron


def test_feature_cron_env_names(monkeypatch):
    for env in (
        "INTELLIFLO_VALUATIONS_CRON",
        "INTELLIFLO_INCOME_STATEMENTS_CRON",
        "INTELLIFLO_INCOME_STATEMENT_ITEMS_CRON",
    ):
        monkeypatch.delenv(env, raising=False)
    assert [config.feature_cron(f) for f in ("Valuations", "IncomeStatements", "IncomeStatementItems")] == [
        None,
        None,
        None,
    ]
    monkeypatch.setenv("INTELLIFLO_VALUATIONS_CRON", "0 7 * * 1-5")
    monkeypatch.setenv("INTELLIFLO_INCOME_STATEMENTS_CRON", " 0 8 1-3 * 1-5 ")
    monkeypatch.setenv("INTELLIFLO_INCOME_STATEMENT_ITEMS_CRON", "0 9 * * 1-5")
    assert config.feature_cron("Valuations") == "0 7 * * 1-5"
    assert config.feature_cron("IncomeStatements") == "0 8 1-3 * 1-5"  # trimmed
    assert config.feature_cron("IncomeStatementItems") == "0 9 * * 1-5"


def test_a_feature_without_its_own_cron_uses_the_default(monkeypatch):
    monkeypatch.delenv("INTELLIFLO_VALUATIONS_CRON", raising=False)
    monkeypatch.setenv("INTELLIFLO_INCOME_STATEMENTS_CRON", "0 8 1 * *")
    cron_for = iflo_integration.IntellifloIntegration.cron_for
    assert cron_for("Valuations") == DEFAULT_CRON
    assert cron_for("IncomeStatements") == "0 8 1 * *"


def test_features_sharing_a_cron_run_together_in_order_others_are_independent(monkeypatch):
    order = iflo_integration.IntellifloIntegration.order_features
    features = order(["IncomeStatementItems", "IncomeStatements", "Valuations"])
    monkeypatch.delenv("INTELLIFLO_VALUATIONS_CRON", raising=False)
    monkeypatch.delenv("INTELLIFLO_INCOME_STATEMENTS_CRON", raising=False)
    monkeypatch.delenv("INTELLIFLO_INCOME_STATEMENT_ITEMS_CRON", raising=False)
    cron_for = iflo_integration.IntellifloIntegration.cron_for

    # nothing configured: one job, in the required order
    assert group_by_cron(features, cron_for) == {
        DEFAULT_CRON: ["Valuations", "IncomeStatements", "IncomeStatementItems"]
    }

    # all three on their own schedule: three independent jobs
    monkeypatch.setenv("INTELLIFLO_VALUATIONS_CRON", "0 7 * * 1-5")
    monkeypatch.setenv("INTELLIFLO_INCOME_STATEMENTS_CRON", "0 8 1-3 * 1-5")
    monkeypatch.setenv("INTELLIFLO_INCOME_STATEMENT_ITEMS_CRON", "0 9 * * 1-5")
    assert group_by_cron(features, cron_for) == {
        "0 7 * * 1-5": ["Valuations"],
        "0 8 1-3 * 1-5": ["IncomeStatements"],
        "0 9 * * 1-5": ["IncomeStatementItems"],
    }

    # statements and items on the same schedule stay in order, valuations apart
    monkeypatch.setenv("INTELLIFLO_INCOME_STATEMENT_ITEMS_CRON", "0 8 1-3 * 1-5")
    assert group_by_cron(features, cron_for) == {
        "0 7 * * 1-5": ["Valuations"],
        "0 8 1-3 * 1-5": ["IncomeStatements", "IncomeStatementItems"],
    }


def test_scheduler_schedules_one_job_per_distinct_cron():
    source = (Path(__file__).resolve().parents[1] / "src/intelliflo_sync/scheduler.py").read_text()
    assert "group_by_cron(features, cls.cron_for)" in source


def _scheduled_jobs(monkeypatch, linked):
    """Run scheduler.run() against a fake APScheduler and return [(job name, cron field summary)]."""
    from intelliflo_sync import scheduler
    from intelliflo_sync.shared import runner

    jobs = []

    class FakeScheduler:
        def add_job(self, func, trigger, id=None, name=None, **kw):
            jobs.append({"id": id, "name": name, "trigger": str(trigger)})

        def get_jobs(self):
            return jobs

        def start(self):
            pass

    row = {
        "id": 14,
        "name": "IO tenant",
        "tenant_id": "12551",
        "provider": "intelliflo",
        "installed": True,
        "enabled": True,
        "status": "approved",
    }
    monkeypatch.setattr(scheduler, "BlockingScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler.iflo_contract, "check_core_or_exit", lambda: None)
    monkeypatch.setattr(scheduler.iflo_contract, "warn_blocked_features", lambda: None)
    monkeypatch.setattr(scheduler, "fetch_all_integrations", lambda: [row])
    monkeypatch.setattr(runner, "fetch_integration_features", lambda platform_id: list(linked))
    monkeypatch.setattr(runner.config, "RUN_PROVIDER", None)
    monkeypatch.setattr(runner.config, "RUN_FEATURE", None)
    scheduler.run()
    return [j for j in jobs if "advisors" not in (j["name"] or "")]


ALPHABETICAL = ["IncomeStatementItems", "IncomeStatements", "Valuations"]  # how platform_features sorts them


def test_scheduler_with_no_feature_crons_schedules_one_job_in_the_required_order(monkeypatch):
    for env in (
        "INTELLIFLO_VALUATIONS_CRON",
        "INTELLIFLO_INCOME_STATEMENTS_CRON",
        "INTELLIFLO_INCOME_STATEMENT_ITEMS_CRON",
    ):
        monkeypatch.delenv(env, raising=False)
    jobs = _scheduled_jobs(monkeypatch, ALPHABETICAL)
    assert len(jobs) == 1
    assert jobs[0]["name"].endswith("['Valuations', 'IncomeStatements', 'IncomeStatementItems']")


def test_scheduler_schedules_each_feature_independently_when_each_has_its_own_cron(monkeypatch):
    monkeypatch.setenv("INTELLIFLO_VALUATIONS_CRON", "0 7 * * 1-5")
    monkeypatch.setenv("INTELLIFLO_INCOME_STATEMENTS_CRON", "0 8 1-3 * 1-5")
    monkeypatch.setenv("INTELLIFLO_INCOME_STATEMENT_ITEMS_CRON", "0 9 * * 1-5")
    jobs = _scheduled_jobs(monkeypatch, ALPHABETICAL)
    assert sorted(j["name"].split(" ", 2)[2] for j in jobs) == [
        "['IncomeStatementItems']",
        "['IncomeStatements']",
        "['Valuations']",
    ]
    assert len({j["id"] for j in jobs}) == 3  # distinct job ids
    by_feature = {j["name"].split(" ", 2)[2]: j["trigger"] for j in jobs}
    assert "hour='7'" in by_feature["['Valuations']"] and "day='1-3'" in by_feature["['IncomeStatements']"]


def test_scheduler_skips_a_feature_with_an_invalid_cron_and_keeps_the_rest(monkeypatch):
    monkeypatch.setenv("INTELLIFLO_INCOME_STATEMENTS_CRON", "not a cron")
    monkeypatch.delenv("INTELLIFLO_VALUATIONS_CRON", raising=False)
    monkeypatch.delenv("INTELLIFLO_INCOME_STATEMENT_ITEMS_CRON", raising=False)
    jobs = _scheduled_jobs(monkeypatch, ALPHABETICAL)
    assert len(jobs) == 1 and "IncomeStatements'," not in jobs[0]["name"]
    assert jobs[0]["name"].endswith("['Valuations', 'IncomeStatementItems']")


# ── no hardcoded credentials ─────────────────────────────────────────────────

import re as _re

CONFIG_SOURCE = Path(__file__).resolve().parents[1] / "src/intelliflo_sync/providers/intelliflo/config.py"


def test_config_source_has_no_credentials_in_it():
    """No `IntellifloSettings` field whose name reads as a secret, key or client id carries a
    non-empty default (config.py has no `os.getenv` calls to regex-match any more — it's a
    pydantic-settings model — so this inspects the model's fields instead)."""
    offenders = [
        (name, field.default)
        for name, field in config.IntellifloSettings.model_fields.items()
        if _re.search(r"(?:[A-Z_]*(?:SECRET|KEY|CLIENT_ID))[A-Z_]*$", name.upper())
        and isinstance(field.default, str)
        and field.default
    ]
    assert offenders == [], f"credential defaults in IntellifloSettings: {offenders}"
    # no passwords or secrets left behind in comments
    text = CONFIG_SOURCE.read_text()
    commented = [
        line
        for line in text.splitlines()
        if line.lstrip().startswith("#") and _re.search(r"(?i)password|secret\s*=", line)
    ]
    assert commented == []


def test_missing_credentials_names_what_is_unset(monkeypatch):
    monkeypatch.setattr(config, "CLIENT_ID", "id")
    monkeypatch.setattr(config, "CLIENT_SECRET", "")
    monkeypatch.setattr(config, "API_KEY", "")
    assert config.missing_credentials() == ["INTELLIFLO_CLIENT_SECRET", "INTELLIFLO_API_KEY"]
    monkeypatch.setattr(config, "CLIENT_SECRET", "s")
    monkeypatch.setattr(config, "API_KEY", "k")
    assert config.missing_credentials() == []


def test_auth_is_refused_with_a_clear_message_when_credentials_are_missing(monkeypatch):
    monkeypatch.setattr(config, "CLIENT_ID", "")
    monkeypatch.setattr(config, "CLIENT_SECRET", "")
    monkeypatch.setattr(config, "API_KEY", "k")
    with pytest.raises(ValueError, match="INTELLIFLO_CLIENT_ID, INTELLIFLO_CLIENT_SECRET"):
        iflo_integration.IntellifloIntegration()._make_auth("12551")


def test_webhook_registration_is_skipped_when_credentials_or_secret_are_missing(monkeypatch, caplog):
    from intelliflo_sync.providers.intelliflo import webhook

    monkeypatch.setattr(config, "WEBHOOK_CALLBACK_URL", "https://example/callback")
    monkeypatch.setattr(config, "CLIENT_ID", "id")
    monkeypatch.setattr(config, "CLIENT_SECRET", "s")
    monkeypatch.setattr(config, "API_KEY", "k")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "")
    monkeypatch.setattr(webhook.requests, "post", lambda *a, **kw: pytest.fail("must not register"))
    with caplog.at_level("ERROR"):
        webhook.register()
    assert "INTELLIFLO_WEBHOOK_SECRET" in caplog.text


def test_valuation_client_defaults_to_the_general_client_unless_set(monkeypatch):
    """`_load()`'s valuation-client fallback, exercised directly against a fresh
    `IntellifloSettings` — `_env_file=None` so a developer's real `.env`/`.env.local` can't leak
    into the assertion (the equivalent of the old dotenv-mock-and-reload dance)."""

    def load_with(**env):
        for name in (
            "INTELLIFLO_CLIENT_ID",
            "INTELLIFLO_CLIENT_SECRET",
            "INTELLIFLO_VALUATION_CLIENT_ID",
            "INTELLIFLO_VALUATION_CLIENT_SECRET",
        ):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        settings = config.IntellifloSettings(_env_file=None)
        if settings.valuation_client_id is None:
            settings.valuation_client_id = settings.client_id
        if settings.valuation_client_secret is None:
            settings.valuation_client_secret = settings.client_secret
        return settings.valuation_client_id, settings.valuation_client_secret, settings.client_id

    assert load_with() == ("", "", "")
    assert load_with(INTELLIFLO_CLIENT_ID="gen-id", INTELLIFLO_CLIENT_SECRET="gen-secret")[:2] == (
        "gen-id",
        "gen-secret",
    )
    assert load_with(
        INTELLIFLO_CLIENT_ID="gen-id",
        INTELLIFLO_CLIENT_SECRET="gen-secret",
        INTELLIFLO_VALUATION_CLIENT_ID="val-id",
        INTELLIFLO_VALUATION_CLIENT_SECRET="val-secret",
    )[:2] == ("val-id", "val-secret")


def test_group_lookup_only_accepts_settings_for_the_platforms_tenant(monkeypatch):
    other = {"items": [{"id": "group:1", "group": {"id": 1}, "tenant": {"id": 99999}}], "count": 1}
    monkeypatch.setattr(
        "intelliflo_sync.providers.intelliflo.endpoints.installed_apps.requests.get",
        lambda *a, **kw: _fake_response(other),
    )
    assert InstalledAppsEndpoint({}).get_group_id("89c73fd", tenant_id="12551") is None  # another tenant's: ignored
    assert InstalledAppsEndpoint({}).get_group_id("89c73fd") == "1"  # no tenant given: as before

    mixed = {"items": [{"group": {"id": 1}, "tenant": {"id": 99999}}, {"group": {"id": 2}, "tenant": {"id": 12551}}]}
    monkeypatch.setattr(
        "intelliflo_sync.providers.intelliflo.endpoints.installed_apps.requests.get",
        lambda *a, **kw: _fake_response(mixed),
    )
    assert InstalledAppsEndpoint({}).get_group_id("89c73fd", tenant_id="12551") == "2"
    assert InstalledAppsEndpoint({}).get_group_id("89c73fd", tenant_id=12551) == "2"  # int or str tenant id

    untagged = {"items": [{"group": {"id": 3}}]}
    monkeypatch.setattr(
        "intelliflo_sync.providers.intelliflo.endpoints.installed_apps.requests.get",
        lambda *a, **kw: _fake_response(untagged),
    )
    assert InstalledAppsEndpoint({}).get_group_id("89c73fd", tenant_id="12551") is None  # no tenant on it: not trusted
