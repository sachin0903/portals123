"""The platform-integrations aggregator (app/platforms.py): promoted-relation naming,
console query guards, graceful degradation when the daemon/lake are unconfigured, the
daemon snapshot merge, and the registry reads against real Postgres."""

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import platforms, providers
from app.config import settings

# ---- pure helpers ----


def test_promoted_relation_is_always_unprefixed() -> None:
    """interlace promotes its default environment unprefixed, and that default is production —
    the only environment the API reads. A `dev__` prefix is what made the fan-out and the row
    counts read relations nothing had ever promoted to, so there is no longer a setting for it.
    """
    assert platforms.promoted_relation("raw.contacts") == "raw.contacts"
    assert platforms.promoted_relation("platform.transfers") == "platform.transfers"
    # A model with no schema prefix lands in `main`.
    assert platforms.promoted_relation("contacts") == "main.contacts"


def test_no_environment_setting_remains() -> None:
    """Guards the removal: a reintroduced DATALAKE_ENV would silently re-prefix every relation."""
    assert not hasattr(settings, "datalake_env")


def test_relation_bindings_map_placeholders_to_relations() -> None:
    """Placeholders are the model's last path segment, bound to its promoted relation."""
    assert platforms._relation_bindings(("ref.dsrs", "ref.dsr_assignments")) == [
        {"dsrs": "ref.dsrs", "dsr_assignments": "ref.dsr_assignments"}
    ]


def test_option_templates_bind_every_placeholder() -> None:
    """Each option query's placeholders must match the models it declares, or the
    format() at read time raises KeyError and the picker silently empties."""
    for provider, groups in platforms._OPTION_SOURCES.items():
        for group, (models, template) in groups.items():
            binding = platforms._relation_bindings(models)[0]
            formatted = template.format(**binding)  # raises KeyError on a mismatch
            assert "{" not in formatted, f"{provider}.{group} has an unbound placeholder"


def test_sei_option_groups_include_enrolment_templates() -> None:
    """The products-page picker reads these; dropping a group empties the dropdown
    without a type error, so the names are pinned. Firms stay in the payload because
    the enablement card still picks one; the products sheet filters templates by the
    tenant's already-enabled firm_id and never lists firms."""
    assert set(platforms._OPTION_SOURCES["sei"]) >= {
        "firms",
        "subfirms",
        "dsr_teams",
        "account_templates",
        "client_templates",
    }
    account_sql = platforms._OPTION_SOURCES["sei"]["account_templates"][1]
    assert "scope = 'account'" in account_sql
    client_sql = platforms._OPTION_SOURCES["sei"]["client_templates"][1]
    assert "scope = 'client'" in client_sql


def test_query_guards_reject_non_select() -> None:
    for bad in ("", "  -- just a comment", "drop table x", "attach 'x' as y", "set a=1"):
        with pytest.raises(ValueError):
            platforms._query_sync("morgan-lloyd", bad, 10)
    with pytest.raises(ValueError):
        platforms._query_sync("morgan-lloyd", "select 1; select 2", 10)


def test_query_requires_lake_configured() -> None:
    # Guards pass, but no catalogue DSN (hermetic default) => unavailable, not a crash.
    with pytest.raises(platforms.PlatformUnavailable):
        platforms._query_sync("morgan-lloyd", "select 1", 10)


async def test_trigger_sync_requires_service() -> None:
    with pytest.raises(platforms.PlatformUnavailable):
        await platforms.trigger_sync("morgan-lloyd")


# ---- rebuild (the state-db / catalogue disagreement recovery) ----


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    """Records the daemon calls a rebuild makes, in order."""

    def __init__(self, *, drop_status: int = 200, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self._drop_status = drop_status
        self._fail_on = fail_on

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def _maybe_fail(self, path: str) -> None:
        if self._fail_on and self._fail_on in path:
            raise httpx.ConnectError("daemon down")

    async def delete(self, url: str, params: Any = None) -> _FakeResponse:
        self.calls.append(("DELETE", url, params))
        self._maybe_fail(url)
        return _FakeResponse({"views": 43}, self._drop_status)

    async def post(self, url: str, json: Any = None) -> _FakeResponse:
        self.calls.append(("POST", url, json))
        self._maybe_fail(url)
        return _FakeResponse({"ok": True})


@pytest.fixture
def daemon(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(platforms, "service_url", lambda _p: "http://daemon:8100")

    def _install(client: _FakeClient) -> _FakeClient:
        monkeypatch.setattr(platforms.httpx, "AsyncClient", lambda **_: client)
        return client

    return _install


async def test_a_tenant_refresh_selects_that_firms_models_and_nothing_else(daemon: Any) -> None:
    """`book_sink` tags every per-tenant model `tenant:{uid}`, so one selector covers the whole
    chain — slice, stage, merge — for one firm. Sending no selector would re-run every firm's."""
    client = daemon(_FakeClient())
    await platforms.trigger_tenant_refresh("seccl", "4c76b77a1095c246")
    method, url, body = client.calls[-1]
    assert (method, url.rsplit("/", 1)[-1]) == ("POST", "runs")
    assert body == {"selectors": ["tag:tenant:4c76b77a1095c246"]}


async def test_a_tenant_refresh_reports_the_daemons_reason(daemon: Any) -> None:
    """A 400's body is the reason. httpx's own message only names the status and the URL."""
    request = httpx.Request("POST", "http://daemon:8100/runs")
    response = httpx.Response(
        400,
        json={"detail": "tag 'tenant:ten_x' matches no models"},
        request=request,
    )

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, url: str, json: Any = None) -> httpx.Response:
            return response

    daemon(_Client())
    with pytest.raises(platforms.PlatformUnavailable, match="matches no models"):
        await platforms.trigger_tenant_refresh("seccl", "ten_x")


async def test_a_tenant_refresh_needs_the_daemon_configured() -> None:
    """Unconfigured is not "nothing to do": a refresh that quietly no-ops reads as a firm whose
    data never changes."""
    with pytest.raises(platforms.PlatformUnavailable):
        await platforms.trigger_tenant_refresh("seccl", "abc")


async def test_rebuild_resets_before_it_runs(daemon: Any) -> None:
    """`gc` reclaims only snapshots nothing references, so collecting first leaves every stale
    table claimed. `POST /reset` drops the views and the snapshot schemas, then the run rebuilds."""
    client = daemon(_FakeClient())
    await platforms.rebuild("seccl")
    verbs = [(method, url.rsplit("/", 1)[-1].split("?")[0]) for method, url, _ in client.calls]
    assert verbs == [("POST", "reset"), ("POST", "runs")]


async def test_rebuild_confirms_the_wipe(daemon: Any) -> None:
    # The daemon refuses to reset without it — correct for every other caller.
    client = daemon(_FakeClient())
    await platforms.rebuild("seccl")
    assert client.calls[0][2] == {"confirm": True}
    assert client.calls[1][2] == {"selectors": []}


async def test_a_half_finished_rebuild_reports_what_completed(daemon: Any) -> None:
    """A reset that landed but never enqueued a run has emptied the warehouse. The operator has
    to see that, rather than a generic 'daemon down'."""
    daemon(_FakeClient(fail_on="/runs"))
    with pytest.raises(platforms.PlatformUnavailable) as caught:
        await platforms.rebuild("seccl")
    assert "reset" in str(caught.value)


async def test_a_dead_daemon_ends_the_event_stream(daemon: Any) -> None:
    """The browser reconnects on its own. A refused connection must not escape the
    stream as a traceback — the route turns this into a closed response."""

    class _Down:
        async def __aenter__(self) -> "_Down":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def stream(self, _method: str, _url: str) -> None:
            raise httpx.ConnectError("All connection attempts failed")

    daemon(_Down())
    with pytest.raises(platforms.PlatformUnavailable):
        async for _chunk in platforms.iter_event_stream("seccl"):
            pass


async def test_cancel_run_posts_to_that_run(daemon: Any) -> None:
    client = daemon(_FakeClient())
    await platforms.cancel_run("seccl", 14)
    method, url, body = client.calls[-1]
    assert method == "POST"
    assert url.endswith("/runs/14/cancel")
    assert body is None


async def test_rebuild_requires_a_configured_service() -> None:
    with pytest.raises(platforms.PlatformUnavailable):
        await platforms.rebuild("morgan-lloyd")


def _daemon_response(status_code: int, payload: dict[str, Any], path: str) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("POST", f"http://daemon:8100{path}"),
    )


class _ScriptedDaemon:
    """Serves a fixed list of POST and GET responses, in call order."""

    def __init__(
        self, posts: list[httpx.Response], gets: list[httpx.Response] | None = None
    ) -> None:
        self._posts = list(posts)
        self._gets = list(gets or [])
        self.calls: list[tuple[str, str]] = []

    async def __aenter__(self) -> "_ScriptedDaemon":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, json: Any = None) -> httpx.Response:
        self.calls.append(("POST", url))
        return self._posts.pop(0)

    async def get(self, url: str) -> httpx.Response:
        self.calls.append(("GET", url))
        return self._gets.pop(0)


def test_health_starting_is_not_up() -> None:
    """A 200 from ``GET /health`` during the startup apply is not a daemon that can take a write."""
    assert (
        platforms._service_liveness(
            {"status": "starting", "detail": "startup apply is still running"}
        )
        == "starting"
    )
    assert platforms._service_liveness({"status": "ok"}) == "up"
    assert (
        platforms._service_liveness({"status": "error", "detail": "startup apply failed"}) == "down"
    )


async def test_a_sync_during_startup_retries_once_health_is_ok(
    daemon: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first POST is refused while the apply runs. The retry after health is ok is the run."""
    monkeypatch.setattr(platforms, "_STARTUP_POLL_S", 0)
    client = daemon(
        _ScriptedDaemon(
            posts=[
                _daemon_response(503, {"detail": "startup apply is still running"}, "/runs"),
                _daemon_response(200, {"id": 2, "state": "queued"}, "/runs"),
            ],
            gets=[
                _daemon_response(200, {"status": "starting"}, "/health"),
                _daemon_response(200, {"status": "ok"}, "/health"),
            ],
        )
    )
    result = await platforms.trigger_sync("seccl")
    assert result == {"id": 2, "state": "queued"}
    assert [call[0] for call in client.calls] == ["POST", "GET", "GET", "POST"]


async def test_a_rebuild_during_startup_retries_the_reset(
    daemon: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platforms, "_STARTUP_POLL_S", 0)
    client = daemon(
        _ScriptedDaemon(
            posts=[
                _daemon_response(503, {"detail": "startup apply is still running"}, "/reset"),
                _daemon_response(200, {"views": 43}, "/reset"),
                _daemon_response(200, {"id": 9}, "/runs"),
            ],
            gets=[_daemon_response(200, {"status": "ok"}, "/health")],
        )
    )
    steps = await platforms.rebuild("seccl")
    assert steps == {"reset": {"views": 43}, "run": {"id": 9}}
    assert [url.rsplit("/", 1)[-1] for _, url in client.calls] == [
        "reset",
        "health",
        "reset",
        "runs",
    ]


async def test_a_rebuild_still_starting_names_that_and_not_an_empty_completion(
    daemon: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 503 body is the message. ``completed: {}`` was what a refused connection produced."""
    monkeypatch.setattr(platforms, "_STARTUP_WAIT_S", 0)
    daemon(
        _ScriptedDaemon(
            posts=[_daemon_response(503, {"detail": "startup apply is still running"}, "/reset")]
        )
    )
    with pytest.raises(
        platforms.PlatformUnavailable, match="startup apply is still running"
    ) as caught:
        await platforms.rebuild("seccl")
    assert "completed" not in str(caught.value)


async def test_a_refused_connection_does_not_invent_a_completed_step(daemon: Any) -> None:
    daemon(_FakeClient(fail_on="/reset"))
    with pytest.raises(platforms.PlatformUnavailable, match="daemon down") as caught:
        await platforms.rebuild("seccl")
    assert "completed" not in str(caught.value)


# ---- daemon snapshot ----


async def test_snapshot_down_when_unconfigured() -> None:
    snap = await platforms._service_snapshot("morgan-lloyd")
    assert snap["status"] == "down"


async def test_snapshot_merges_and_fingerprint_caches_model_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: dict[str, Any] = {
        "/models": [
            {"name": "raw.contacts", "strategy": "full_merge", "fingerprint": "raw-v1"},
            {"name": "platform.contacts", "strategy": "view", "fingerprint": "platform-v1"},
        ],
        "/plan": {
            "changes": [
                {"name": "platform.contacts", "change_type": "modified", "category": "nonbreaking"}
            ]
        },
        "/runs": [{"id": 1, "state": "succeeded"}, {"id": 2, "state": "running"}],
        "/events": [{"seq": 1, "type": "run.enqueued"}, {"seq": 2, "type": "run.started"}],
        "/streams": [{"name": "clicks", "schema": {"id": "text"}, "head": 3, "watermark": 3}],
        "/models/raw.contacts": {"name": "raw.contacts", "columns": {}, "sql": None},
        "/models/platform.contacts": {
            "name": "platform.contacts",
            "columns": {"contact_id": ["raw.contacts.contactid"]},
            "sql": "select contactid as contact_id from raw.contacts",
        },
    }

    calls: list[str] = []

    async def fake_get(base: str, path: str, client: Any) -> Any:
        calls.append(path)
        return payloads[path]

    monkeypatch.setattr(settings, "interlace_morgan_lloyd_url", "http://interlace.test")
    monkeypatch.setattr(platforms, "_service_get", fake_get)
    platforms.forget_service("morgan-lloyd")
    for key in [key for key in platforms._model_detail_cache if key[0] == "morgan-lloyd"]:
        del platforms._model_detail_cache[key]

    snap = await platforms._service_snapshot_uncached("morgan-lloyd")
    assert snap["status"] == "up"
    promoted = next(m for m in snap["models"] if m["name"] == "platform.contacts")
    assert promoted["columns"] == {"contact_id": ["raw.contacts.contactid"]}
    assert "select" in promoted["sql"]
    assert snap["plan"]["platform.contacts"]["category"] == "nonbreaking"
    assert [r["id"] for r in snap["runs"]] == [2, 1]  # newest first
    assert snap["events"][0]["seq"] == 2
    assert snap["streams"][0]["name"] == "clicks"

    await platforms._service_snapshot_uncached("morgan-lloyd")
    assert calls.count("/models") == 2
    assert calls.count("/models/raw.contacts") == 1
    assert calls.count("/models/platform.contacts") == 1


async def test_a_read_timeout_is_not_reported_as_the_exception_class() -> None:
    """httpx's ReadTimeout stringifies to empty, which the page was printing as the class name."""
    assert platforms._down(httpx.ReadTimeout(""))["error"] == (
        "the pipeline did not answer in time"
    )


async def test_a_slow_plan_does_not_mark_the_service_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /plan diffs the warehouse. Timing that out used to discard /models as well,
    so a daemon whose event stream was still connected read as unavailable."""

    async def fake_get(_base: str, path: str, _client: Any) -> Any:
        if path == "/plan":
            raise httpx.ReadTimeout("")
        if path == "/models":
            return [{"name": "platform.clients", "fingerprint": "abc", "depends_on": []}]
        return []

    monkeypatch.setattr(settings, "interlace_sei_url", "http://interlace.test")
    monkeypatch.setattr(platforms, "_service_get", fake_get)
    platforms.forget_service("sei")

    snap = await platforms._collect_service("sei")
    assert snap["status"] == "up"
    assert snap["plan"] == {}
    assert snap["models"][0]["name"] == "platform.clients"
    assert snap["runs"] == []
    platforms.forget_service("sei")


async def test_rebuild_waits_long_enough_for_the_wipe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Five seconds abandoned POST /reset on a real catalogue, so Rebuild did nothing."""
    seen: list[Any] = []

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            seen.append(kwargs.get("timeout"))

        async def __aenter__(self) -> _FakeClient:
            return _FakeClient()

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(platforms, "service_url", lambda _provider: "http://daemon:8100")
    monkeypatch.setattr(platforms.httpx, "AsyncClient", _Client)
    await platforms.rebuild("seccl")
    timeout = seen[0]
    assert timeout.connect == 10.0
    assert timeout.read == 300.0


async def test_the_overview_snapshot_does_not_fetch_each_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening a platform must not pay one HTTP round trip per model. SEI has over a hundred,
    and the daemon answers them one at a time."""
    calls: list[str] = []

    async def fake_get(_base: str, path: str, _client: Any) -> Any:
        calls.append(path)
        if path == "/models":
            return [{"name": "platform.clients", "fingerprint": "abc", "depends_on": []}]
        if path == "/plan":
            return {"changes": []}
        return []

    monkeypatch.setattr(settings, "interlace_sei_url", "http://interlace.test")
    monkeypatch.setattr(platforms, "_service_get", fake_get)
    platforms.forget_service("sei")

    snap = await platforms._collect_service("sei")
    assert snap["status"] == "up"
    assert snap["models"][0]["columns"] == {}
    assert snap["models"][0]["sql"] is None
    assert calls == ["/models", "/plan", "/runs", "/events", "/streams"]
    platforms.forget_service("sei")


def test_a_model_name_that_is_not_an_identifier_is_not_counted() -> None:
    assert platforms._promoted_sql("platform.clients") == (
        "platform.clients",
        '"platform"."clients"',
    )
    assert platforms._promoted_sql('platform.clients"; drop table') is None


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


def test_row_counts_are_one_query_not_one_per_model() -> None:
    sqls: list[str] = []

    class _Conn:
        def execute(self, sql: str) -> _Rows:
            sqls.append(sql)
            if "information_schema.tables" in sql:
                return _Rows([("platform", "clients")])
            if "information_schema.columns" in sql:
                return _Rows([("platform", "clients", "id", "VARCHAR")])
            if "count(*)::BIGINT" in sql:
                return _Rows([("platform.clients", 4)])
            raise AssertionError(sql)

        def close(self) -> None:
            return None

    body = platforms._warehouse_sync(
        "sei", ["platform.clients", "platform.missing", "bad name"], _Conn()
    )
    assert body["counts"] == {"platform.clients": 4}
    assert body["schemas"]["platform.clients"] == [["id", "VARCHAR"]]
    assert "platform.missing" not in body["counts"]
    assert sum("count(*)::BIGINT" in sql for sql in sqls) == 1


def test_a_failed_count_batch_falls_back_per_relation() -> None:
    class _Conn:
        def execute(self, sql: str) -> _Rows:
            if "information_schema.tables" in sql:
                return _Rows([("platform", "clients"), ("platform", "positions")])
            if "information_schema.columns" in sql:
                return _Rows([])
            if "UNION ALL" in sql:
                raise RuntimeError("one view is broken")
            if '"clients"' in sql:
                return _Rows([(3,)])
            raise RuntimeError("positions unreadable")

        def close(self) -> None:
            return None

    body = platforms._warehouse_sync("sei", ["platform.clients", "platform.positions"], _Conn())
    assert body["counts"] == {"platform.clients": 3}


async def test_overview_overlaps_the_daemon_with_the_warehouse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def summary(_provider: str) -> dict[str, Any]:
        order.append("daemon")
        await asyncio.sleep(0.02)
        order.append("daemon-done")
        return {
            "status": "up",
            "models": [],
            "runs": [],
            "all_runs": [],
            "events": [],
            "streams": [],
        }

    async def dashboard(_provider: str) -> dict[str, Any]:
        order.append("warehouse")
        await asyncio.sleep(0.02)
        order.append("warehouse-done")
        return {"metrics": {"clients": 3}, "per_slice": {}}

    async def firms(_session: Any, _provider: str) -> list[dict[str, Any]]:
        return []

    async def candidates(_session: Any, _provider: str) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(platforms, "service_url", lambda _provider: "http://daemon")
    monkeypatch.setattr(platforms, "_service_summary", summary)
    monkeypatch.setattr(platforms, "_cached_dashboard", dashboard)
    monkeypatch.setattr(platforms, "firm_enablements", firms)
    monkeypatch.setattr(platforms, "connectable_tenants", candidates)

    body = await platforms.overview(None, "sei")  # type: ignore[arg-type]
    assert order.index("warehouse") < order.index("daemon-done")
    assert body["metrics"]["clients"] == 3
    assert body["counts"] == {}
    assert body["schemas"] == {}


async def test_catalogue_fetches_details_alongside_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    async def summary(_provider: str) -> dict[str, Any]:
        return {
            "status": "up",
            "models": [{"name": "platform.clients", "fingerprint": "abc"}],
        }

    async def details(_provider: str, models: list[dict[str, Any]]) -> None:
        started.append("details")
        await asyncio.sleep(0.02)
        started.append("details-done")
        models[0]["columns"] = {"id": ["raw.clients.id"]}
        models[0]["sql"] = "select 1"

    async def warehouse(_provider: str, _names: list[str]) -> dict[str, Any]:
        started.append("counts")
        await asyncio.sleep(0.02)
        return {
            "counts": {"platform.clients": 5},
            "schemas": {"platform.clients": [["id", "VARCHAR"]]},
        }

    monkeypatch.setattr(platforms, "_service_summary", summary)
    monkeypatch.setattr(platforms, "_attach_model_details", details)
    monkeypatch.setattr(platforms, "_cached_catalogue_warehouse", warehouse)

    body = await platforms.catalogue("sei")
    assert started.index("counts") < started.index("details-done")
    assert body["models"][0]["sql"] == "select 1"
    assert body["counts"]["platform.clients"] == 5
    assert body["schemas"]["platform.clients"] == [["id", "VARCHAR"]]


async def test_concurrent_snapshots_collapse_to_one_daemon_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def snapshot(_provider: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"status": "up", "models": []}

    monkeypatch.setattr(platforms, "_service_snapshot_uncached", snapshot)
    platforms.forget_service("seccl")
    results = await asyncio.gather(
        platforms._service_snapshot("seccl"),
        platforms._service_snapshot("seccl"),
        platforms._service_snapshot("seccl"),
    )
    assert calls == 1
    assert all(result["status"] == "up" for result in results)
    platforms.forget_service("seccl")


# ---- registry reads + overview assembly (real Postgres) ----


async def test_overview_degrades_and_carries_registries(
    service_session: AsyncSession,
) -> None:
    alias = f"plat-{uuid.uuid4().hex[:10]}"
    from app import provisioning

    row = await provisioning.provision_tenant(
        service_session, name="Plat Firm", alias=alias, from_model=False
    )
    await providers.set_provider(
        service_session,
        int(row["id"]),
        "morgan-lloyd",
        config={"tenant_ids": ["ms-guid-plat"]},
        enabled=True,
    )

    overview = await platforms.overview(service_session, "morgan-lloyd")
    assert overview["service"]["status"] == "down"  # daemon unconfigured => partial page
    assert overview["counts"] == {}
    assert overview["schemas"] == {}
    # EMPTY SINCE 0133, and the key is kept rather than removed so the console renders an
    # empty list where it used to render one. A firm's connection is its enablement row,
    # which `firms` below already carries.
    assert overview["sources"] == []
    assert any(f["config"].get("tenant_ids") == ["ms-guid-plat"] for f in overview["firms"])
    firm = next(f for f in overview["firms"] if f["alias"] == alias)
    assert firm["enabled"] is True
    assert firm["config"] == {"tenant_ids": ["ms-guid-plat"]}
    assert firm["uid"].startswith("ten_")

    index = await platforms.index(service_session)
    ml = next(e for e in index if e["provider"] == "morgan-lloyd")
    assert ml["service"] == "unconfigured"
    assert ml["enabled_firms"] >= 1


# ---- per-tenant slice metrics ----


def test_slice_expr_unnests_a_scalar_or_list_column() -> None:
    """Seccl sends node_id as `["9"]` on clients but `41` on positions, so the grouping
    expression has to accept both or one relation silently groups by a JSON string."""
    listish = platforms._slice_expr("node_id", True)
    assert "unnest" in listish and "try_cast" in listish
    assert platforms._slice_expr("ml_tenant_id", False) == "CAST(ml_tenant_id AS VARCHAR)"


def test_slice_config_keys_excludes_narrowing_keys() -> None:
    """SEI's subfirm_ids/dsr_ids narrow WITHIN a firm; counting by them would double-count."""
    assert providers.slice_config_keys("seccl") == ("node_ids",)
    assert providers.slice_config_keys("morgan-lloyd") == ("tenant_ids",)
    assert providers.slice_config_keys("sei") == ("firm_id",)
    assert providers.slice_config_keys("nope") == ()


def test_tenant_slice_metrics_sums_over_a_firms_ids() -> None:
    firms = [{"tenant_id": 1, "config": {"node_ids": ["9", "23"]}}]
    per_slice = {
        "clients": {"9": 10, "23": 5, "99": 999},  # 99 belongs to another firm
        "aum": {"9": 1000.0, "23": 500.0},
        "names": {"9": "Node Nine", "23": "Node Twenty-three"},
    }
    [row] = platforms.tenant_slice_metrics(firms, per_slice, "seccl")
    assert row["clients"] == 15  # only this firm's nodes
    assert row["aum"] == 1500.0
    assert row["slices"] == [
        {"id": "9", "name": "Node Nine"},
        {"id": "23", "name": "Node Twenty-three"},
    ]


def test_tenant_slice_metrics_reports_none_not_zero_when_unknown() -> None:
    """A firm whose ids the warehouse has never seen (not promoted yet, or a stale node) must
    render "—", not "0 clients" — the distinction is the whole point of the nullable fields."""
    firms = [{"tenant_id": 1, "config": {"node_ids": ["404"]}}]
    [row] = platforms.tenant_slice_metrics(firms, {"clients": {"9": 10}}, "seccl")
    assert row["clients"] is None
    assert row["aum"] is None
    # The unknown id is still listed, by id — a stale enablement should be visible.
    assert row["slices"] == [{"id": "404", "name": None}]


def test_tenant_slice_metrics_handles_a_scalar_config_value() -> None:
    """SEI's firm_id is a single value, not a list."""
    firms = [{"tenant_id": 7, "config": {"firm_id": "3106"}}]
    [row] = platforms.tenant_slice_metrics(firms, {"clients": {"3106": 42}}, "sei")
    assert row["clients"] == 42
    assert row["slices"] == [{"id": "3106", "name": None}]


def test_tenant_slice_metrics_tolerates_an_empty_config() -> None:
    [row] = platforms.tenant_slice_metrics([{"tenant_id": 1, "config": {}}], {}, "seccl")
    assert row["slices"] == []
    assert row["clients"] is None


class TestIngestion:
    """How a custodian's data arrives — and the distinction the surface exists to draw.

    Only SEI has a per-file ledger, and that is a FACT ABOUT THE TRANSPORT rather than a gap: its
    nightly PGP-encrypted SFTP drop is the head of its own DAG, so every file's arrival, trailer
    check and decryption is a row. Seccl arrives by webhook and poll; Morgan Lloyd is polled. If
    those two rendered an empty file table it would read as "nothing arrived last night", which is
    the same class of wrong answer the whole pipeline keeps producing.
    """

    @pytest.mark.asyncio
    async def test_a_provider_with_no_ledger_says_why_rather_than_showing_nothing(self) -> None:
        for provider in ("seccl", "morgan-lloyd"):
            body = await platforms.ingestion(provider)
            assert body["available"] is False, provider
            assert body["files"] == []
            # The reason is the whole payload: an operator must be able to tell "this provider does
            # not deliver files" from "the delivery failed".
            assert body["note"], provider
            assert body["kind"] in ("webhooks", "api"), provider

    @pytest.mark.asyncio
    async def test_sei_declares_a_file_transport(self) -> None:
        body = await platforms.ingestion("sei")
        assert body["kind"] == "sftp"
        assert body["note"]

    @pytest.mark.asyncio
    async def test_an_unbuilt_ledger_is_not_an_empty_drop(self, monkeypatch) -> None:  # noqa: ANN001
        """The distinction this module exists for, one layer down: "the pull has never run" is not
        "last night's drop was empty"."""

        def _missing(_provider: str) -> Any:
            raise platforms.PlatformUnavailable("catalogue unreachable")

        monkeypatch.setattr(platforms, "_core_connect_sync", _missing)
        body = await platforms.ingestion("sei")
        assert body["available"] is False
        assert body["files"] == []


class TestSliceValueValidation:
    """The enablement's VALUES, not just its shape.

    `providers.validate_config` establishes that `node_ids` is a non-empty list of non-empty
    strings. It cannot know whether `["12"]` names a node that exists — and a mistyped id saves
    clean, projects an empty slice, and reads as "this firm has no data with this custodian",
    which is the failure mode the whole pipeline keeps producing.
    """

    @staticmethod
    def _offering(**groups: list[str]) -> Any:
        async def options(_provider: str, _session: Any = None) -> dict[str, list[dict[str, Any]]]:
            return {group: [{"value": v} for v in values] for group, values in groups.items()}

        return options

    @pytest.mark.asyncio
    async def test_an_id_the_provider_does_not_offer_is_named(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platforms, "provider_options", self._offering(nodes=["1", "2"]))
        unknown = await platforms.unknown_slice_ids("seccl", {"node_ids": ["1", "99"]})
        assert unknown == {"node_ids": ["99"]}

    @pytest.mark.asyncio
    async def test_ids_the_provider_offers_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(platforms, "provider_options", self._offering(nodes=["1", "2"]))
        assert await platforms.unknown_slice_ids("seccl", {"node_ids": ["1", "2"]}) == {}

    @pytest.mark.asyncio
    async def test_an_empty_option_list_refuses_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE CONSTRAINT THAT SHAPES THIS. A picker whose model has not been promoted returns
        nothing and degrades to manual id entry — deliberately, because a provider can be
        configured before its models have ever run (SEI's DSR list read 0 of 503 for exactly this
        reason). Refusing then would block the first enablement of every new custodian, and could
        only be fixed by editing code."""
        monkeypatch.setattr(platforms, "provider_options", self._offering(nodes=[]))
        assert await platforms.unknown_slice_ids("seccl", {"node_ids": ["anything"]}) == {}

    @pytest.mark.asyncio
    async def test_each_sei_axis_is_checked_against_its_own_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SEI narrows on three nested axes and each has its own population; checking a subfirm
        against the firm list would refuse every valid one."""
        monkeypatch.setattr(
            platforms,
            "provider_options",
            self._offering(firms=["3106"], subfirms=["11"], dsr_teams=["77"]),
        )
        unknown = await platforms.unknown_slice_ids(
            "sei", {"firm_id": "3106", "subfirm_ids": ["11", "12"], "dsr_ids": ["77"]}
        )
        assert unknown == {"subfirm_ids": ["12"]}

    @pytest.mark.asyncio
    async def test_a_scalar_key_is_handled_like_a_list_of_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(platforms, "provider_options", self._offering(firms=["3106"]))
        assert await platforms.unknown_slice_ids("sei", {"firm_id": "9999"}) == {
            "firm_id": ["9999"]
        }

    @pytest.mark.asyncio
    async def test_an_absent_optional_key_is_not_a_complaint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Absent means "no narrowing on that axis" — the whole firm — which is a valid config."""
        monkeypatch.setattr(
            platforms, "provider_options", self._offering(firms=["3106"], subfirms=["11"])
        )
        assert await platforms.unknown_slice_ids("sei", {"firm_id": "3106"}) == {}


class TestOverviewWarehouseReads:
    """Headline warehouse questions share one attach. Per-model counts are not among them.

    `_metrics_sync` and `_per_slice_sync` once opened their own in-memory DuckDB each, and a
    third attach counted every relation. The overview asks the two headline questions on one
    attach. Row counts are the catalogue request, which Models and Lineage make after paint.
    """

    @staticmethod
    def _spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
        opened: list[str] = []

        class _Conn:
            def close(self) -> None:
                pass

        def _connect(provider: str) -> Any:
            opened.append(provider)
            return _Conn()

        monkeypatch.setattr(platforms, "_core_connect_sync", _connect)
        monkeypatch.setattr(platforms, "_warehouse_sync", lambda *a, **k: {"counts": {"x": 1}})
        monkeypatch.setattr(platforms, "_metrics_sync", lambda *a, **k: {"clients": 2})
        monkeypatch.setattr(platforms, "_per_slice_sync", lambda *a, **k: {"by_slice": {}})
        return opened

    def test_headline_reads_share_one_attach(self, monkeypatch: pytest.MonkeyPatch) -> None:
        opened = self._spy(monkeypatch)
        reads = platforms._dashboard_reads_sync("seccl")
        assert opened == ["seccl"], "one attach, not one per question"
        assert reads["metrics"]["clients"] == 2
        assert "by_slice" in reads["per_slice"]
        assert "warehouse" not in reads

    def test_the_overview_cache_is_bounded_by_the_registry(self) -> None:
        """It was an unbounded process-local dict with no eviction — harmless at three providers
        and wrong as a habit."""
        platforms._warehouse_cache["not-a-provider"] = (0.0, {})
        platforms._remember_warehouse(
            "seccl",
            {
                "warehouse": {"counts": {}, "schemas": {}},
                "metrics": {},
                "per_slice": {},
            },
        )
        assert "not-a-provider" not in platforms._warehouse_cache
        assert set(platforms._warehouse_cache) <= set(providers.PROVIDERS)
        platforms.forget_warehouse("seccl")

    @pytest.mark.asyncio
    async def test_a_rebuild_drops_the_cached_overview(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rebuild resets Interlace-owned state and re-runs every model — it changes exactly
        what this caches. Leaving it meant an operator pressed Rebuild and then read pre-rebuild
        row counts for up to a minute, with nothing saying so."""
        platforms._remember_warehouse(
            "seccl",
            {
                "metrics": {"clients": 999},
                "per_slice": {},
            },
        )
        platforms._catalogue_cache["seccl"] = (
            0.0,
            {"counts": {"platform.clients": 999}, "schemas": {}},
        )
        monkeypatch.setattr(platforms, "service_url", lambda _p: "")
        with pytest.raises(platforms.PlatformUnavailable):
            await platforms.rebuild("seccl")
        assert "seccl" not in platforms._warehouse_cache
        assert "seccl" not in platforms._catalogue_cache
