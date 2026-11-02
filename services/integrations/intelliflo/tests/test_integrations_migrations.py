"""Integration migrations: local-only seeds, FE-schema SQL.

No schema-migration tests here at all any more. `scripts/pg/*` (Postgres schema creation) was
dropped in the port from sei_reports — the `integrations` schema is now owned by
`apps/api/migrations`. `scripts/duck/*` (the SEI reporting-lake views) is gone too, for a
different reason: `shared/lake.py` now attaches read-only to services/sei's own `platform.*`/
`raw.*` catalogue, and this service never creates objects there — it's `lake_reader`, not the
catalogue's writer. What remains is what this service still owns: the local-only seed tooling
(`intelliflo-seed`).
"""

import re
from pathlib import Path

import pytest

from intelliflo_sync.migrations import seed
from intelliflo_sync.providers.intelliflo import contract as iflo_contract
from intelliflo_sync.shared import config, contract
from intelliflo_sync.shared import db as shared_db

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "src/intelliflo_sync/migrations"


def _sql(path: Path) -> str:
    """The file's SQL without comments, collapsed to single spaces."""
    text = re.sub(r"--[^\n]*", "", path.read_text())
    return re.sub(r"\s+", " ", text)


# ── seeds are local-only ─────────────────────────────────────────────────────


def test_seeds_are_kept_out_of_the_images():
    repo_root = ROOT.parents[2]  # services/integrations/intelliflo -> services/integrations -> services -> repo root
    ignore = (repo_root / ".dockerignore").read_text()
    assert "**/migrations/*/seeds/local.yaml" in ignore
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "seed" not in dockerfile.lower()


def test_only_the_seed_script_imports_the_seed_module():
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        if path.name == "seed.py" and path.parent.name == "migrations":
            continue
        imports_seed = re.search(
            r"^\s*(?:from intelliflo_sync\.migrations import seed|"
            r"from intelliflo_sync\.migrations\.seed import|import intelliflo_sync\.migrations\.seed)",
            path.read_text(),
            re.MULTILINE,
        )
        if imports_seed:
            offenders.append(str(path))
    assert offenders == []


def test_pyproject_exposes_the_seed_script():
    assert 'intelliflo-seed = "intelliflo_sync.migrations.seed:run"' in (ROOT / "pyproject.toml").read_text()


def test_seed_refuses_outside_local(monkeypatch):
    monkeypatch.setattr(config, "PG_HOST", "localhost")
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(SystemExit, match="APP_ENV"):
        seed.check_local()
    monkeypatch.setenv("APP_ENV", "dev")
    with pytest.raises(SystemExit, match="APP_ENV"):
        seed.check_local()
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(config, "PG_HOST", "db.dev.example.com")
    with pytest.raises(SystemExit, match="PG_HOST"):
        seed.check_local()


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_seed_allows_local(monkeypatch, host):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(config, "PG_HOST", host)
    seed.check_local()


def test_seed_files_skip_underscore_scripts_and_are_ordered():
    names = [p.name for p in seed.seed_files()]
    assert names == sorted(names) and names[0] == "002_platform.sql"
    assert not any(n.startswith("_") for n in names)
    assert [p.name for p in seed.seed_files(["_reset.sql"])] == ["_reset.sql"]


def test_seeds_contain_no_ddl():
    for path in seed.SEEDS_DIR.glob("*.sql"):
        assert not re.search(r"\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|TYPE|SCHEMA)\b", _sql(path), re.IGNORECASE), (
            path.name
        )


def test_seed_stops_with_the_fix_when_the_schema_is_incomplete(monkeypatch):
    class Cur:
        def execute(self, *a, **kw):
            pass

    monkeypatch.setattr(contract, "missing_columns", lambda cur, required: {"integrations.platforms": ["group_id"]})
    with pytest.raises(SystemExit, match="Run apps/api's migrations first"):
        seed.require_schema(Cur())


def _yaml(tmp_path, body):
    path = tmp_path / "local.yaml"
    path.write_text(body)
    return path


BASE = """
tenant_id: "12551"
firm_id: "3106"
provider_id: "5261"
provider_name: "Graphene"
app_id: "89c73fd"
"""


def test_load_params_defaults_and_json(tmp_path):
    params = seed.load_params(
        _yaml(
            tmp_path,
            BASE
            + """
accounts:
  - {account_number: "A1", policy_number: "P1", plan_id: 7}
""",
        )
    )
    assert params["tenant_id"] == "12551" and params["group_id"] is None and params["graphene_tenant_id"] == 3
    assert "dsr_codes" not in params and "advisors" not in params  # DSRs come from platform.tenant_platforms
    assert '"plan_id": "7"' in params["accounts"]


@pytest.mark.parametrize("missing", ["tenant_id", "firm_id", "provider_id", "provider_name", "app_id"])
def test_load_params_requires_platform_fields(tmp_path, missing):
    body = "\n".join(line for line in BASE.splitlines() if not line.startswith(missing + ":"))
    with pytest.raises(SystemExit, match=missing):
        seed.load_params(_yaml(tmp_path, body))


def test_load_params_rejects_placeholder_accounts(tmp_path):
    with pytest.raises(SystemExit, match="account_number, policy_number and plan_id"):
        seed.load_params(
            _yaml(
                tmp_path,
                BASE
                + """
accounts:
  - {account_number: "REPLACE-WITH-SEI-ACCOUNT-NUMBER",
     policy_number: "REPLACE-WITH-IO-POLICY-NUMBER", plan_id: "REPLACE-WITH-IO-PLAN-ID"}
""",
            )
        )


def test_load_params_missing_file(tmp_path):
    with pytest.raises(SystemExit, match="local.example.yaml"):
        seed.load_params(tmp_path / "nope.yaml")


def test_example_config_needs_filling_in():
    with pytest.raises(SystemExit, match="account_number, policy_number and plan_id"):
        seed.load_params(seed.SEEDS_DIR / "local.example.yaml")


# ── SQL against the FE (portals) schema ──────────────────────────────────────


class _Cursor:
    def __init__(self, results):
        self.executed, self._results = [], list(results)

    def execute(self, sql, params=None):
        self.executed.append((re.sub(r"\s+", " ", sql).strip(), params))

    def fetchone(self):
        return self._results.pop(0)

    def fetchall(self):
        return self._results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, results):
        self.cur, self.committed = _Cursor(results), False

    def cursor(self, *a, **kw):
        return self.cur

    def commit(self):
        self.committed = True

    def close(self):
        pass


def test_features_come_from_platform_features_feature_code(monkeypatch):
    conn = _Conn([[("Transactions",), ("Valuations",)]])
    monkeypatch.setattr(shared_db, "_get_connection", lambda: conn)
    assert shared_db.fetch_integration_features("14") == ["Transactions", "Valuations"]
    sql = conn.cur.executed[0][0]
    assert "feature_code" in sql and "FROM integrations.platform_features" in sql and "feature_id" not in sql


def test_upsert_integration_inserts_when_no_row_for_platform_and_tenant(monkeypatch):
    conn = _Conn([None, (7,)])
    monkeypatch.setattr(shared_db, "_get_connection", lambda: conn)
    assert (
        shared_db.upsert_integration(
            None, "intelliflo", "12551", "IO", {"a": 1}, enabled=False, installed=True, app_id="89c73fd"
        )
        == "7"
    )
    select, insert = conn.cur.executed
    assert "WHERE platform_name = %s AND tenant_id = %s" in select[0] and select[1] == ("intelliflo", "12551")
    assert insert[0].startswith("INSERT INTO integrations.platforms") and "ON CONFLICT" not in insert[0]
    assert conn.committed


def test_upsert_integration_updates_existing_row_and_keeps_firm_and_app_id(monkeypatch):
    conn = _Conn([(14, None), (14,)])
    monkeypatch.setattr(shared_db, "_get_connection", lambda: conn)
    assert (
        shared_db.upsert_integration(None, "intelliflo", "12551", "IO", {"a": 1}, enabled=False, installed=False)
        == "14"
    )
    update = conn.cur.executed[1][0]
    assert update.startswith("UPDATE integrations.platforms") and "firm_id = COALESCE(firm_id, %s)" in update
    assert "app_id = COALESCE(%s, app_id)" in update and "ON CONFLICT" not in update


# ── the contract check (read-only) ───────────────────────────────────────────


class _CatalogCursor:
    """Answers the pg_attribute query from {table: [columns]}; an unlisted table has no columns."""

    def __init__(self, tables):
        self.tables, self._rows = tables, []

    def execute(self, sql, params=None):
        self._rows = [(c,) for c in self.tables.get(params[0], [])]

    def fetchall(self):
        return self._rows


def test_missing_columns_reports_missing_tables_and_columns():
    cur = _CatalogCursor({"s.a": ["x", "y"], "s.b": ["x"]})
    required = {"s.a": ["x", "y"], "s.b": ["x", "z"], "s.c": ["x"]}
    assert contract.missing_columns(cur, required) == {"s.b": ["z"], "s.c": ["*"]}
    assert contract.describe({"s.b": ["z"], "s.c": ["*"]}) == ["s.b.z", "s.c"]


def test_merge_combines_requirements_without_duplicates():
    assert contract.merge({"t": ["a", "b"]}, {"t": ["b", "c"], "u": ["x"]}) == {"t": ["a", "b", "c"], "u": ["x"]}


def _patch_catalog(monkeypatch, tables):
    class Conn:
        def cursor(self):
            return _Ctx(_CatalogCursor(tables))

        def close(self):
            pass

    class _Ctx:
        def __init__(self, cur):
            self.cur = cur

        def __enter__(self):
            return self.cur

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(shared_db, "_get_connection", lambda: Conn())


def _complete_schema():
    tables = {}
    for requirements in [iflo_contract.CORE, *iflo_contract.FEATURES.values()]:
        for table, columns in requirements.items():
            tables.setdefault(table, [])
            tables[table] = sorted(set(tables[table]) | set(columns))
    return tables


def test_core_check_exits_when_core_tables_are_missing(monkeypatch):
    tables = _complete_schema()
    del tables["integrations.platform_features"]
    _patch_catalog(monkeypatch, tables)
    with pytest.raises(SystemExit):
        iflo_contract.check_core_or_exit()
    _patch_catalog(monkeypatch, _complete_schema())
    iflo_contract.check_core_or_exit()


def test_run_blocks_only_the_feature_with_a_schema_gap(monkeypatch):
    from intelliflo_sync.providers.intelliflo import db as iflo_db
    from intelliflo_sync.providers.intelliflo import integration as iflo_integration
    from intelliflo_sync.shared.base.exceptions import FeatureError

    platform = {
        "platform_id": "1",
        "tenant_id": "12551",
        "firm_id": "3106",
        "dsr_codes": [],
        "provider_id": "5261",
        "provider_name": "G",
        "app_id": "a",
        "group_id": "",
    }
    monkeypatch.setattr(iflo_db, "get_platform_config", lambda tenant: platform)
    for name in ("Valuations", "IncomeStatements"):
        monkeypatch.setitem(iflo_contract.FEATURES, name, iflo_contract.FEATURES.get(name, {}))
    monkeypatch.setattr(
        iflo_contract, "blocked_features", lambda features: {"IncomeStatements": ["integrations.platforms.group_id"]}
    )
    ran = []
    integration = iflo_integration.IntellifloIntegration()
    monkeypatch.setattr(
        integration, "_run_firm", lambda firm, features: (ran.extend(features) or {f: {"ok": 1} for f in features}, [])
    )
    with pytest.raises(FeatureError) as err:
        integration.run("12551", ["Valuations", "IncomeStatements"])
    assert ran == ["Valuations"]
    assert err.value.data["failed_features"] == {"IncomeStatements"}
    assert "schema not ready" in str(err.value) and "group_id" in str(err.value)


def test_upsert_integration_does_not_replace_a_recorded_app_id_with_a_different_one(monkeypatch, caplog):
    conn = _Conn([(14, "89c73fd"), (14,)])
    monkeypatch.setattr(shared_db, "_get_connection", lambda: conn)
    with caplog.at_level("WARNING"):
        shared_db.upsert_integration(
            None, "intelliflo", "12551", "IO", {"a": 1}, enabled=False, installed=True, app_id="evil99"
        )
    update_params = conn.cur.executed[1][1]
    assert "evil99" not in update_params and "ignoring app id" in caplog.text


def test_upsert_integration_records_the_app_id_when_none_is_recorded_or_it_is_the_same(monkeypatch):
    for recorded, incoming in ((None, "89c73fd"), ("", "89c73fd"), ("89c73fd", "89c73fd")):
        conn = _Conn([(14, recorded), (14,)])
        monkeypatch.setattr(shared_db, "_get_connection", lambda conn=conn: conn)
        shared_db.upsert_integration(
            None, "intelliflo", "12551", "IO", {"a": 1}, enabled=False, installed=True, app_id=incoming
        )
        assert "89c73fd" in conn.cur.executed[1][1]
