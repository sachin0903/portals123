"""Platform-integrations cockpit — what the admin /platforms pages render (docs/storage.md §9).

Aggregates three planes per provider (Morgan Lloyd first):
  - the provider's **interlace daemon** (`interlace serve`) over its service API:
    models + column lineage + SQL, the promotion plan, run queue and event log;
  - the **core warehouse** (the provider's catalogue schema in the `datalakes` DB):
    row counts per promoted relation, plus a read-only SQL console — a lake_reader
    READ_ONLY DuckDB attach, mirroring `app/lake` but on the core catalogue;
  - the **portals DB registry**: each firm's enablement + connection config
    (`platform.tenant_platforms`; `platform.providers` folded into it in 0133)
    and per-firm enablements (`platform.tenant_platforms`).

Everything degrades: an unreachable daemon or unconfigured lake yields a partial
overview (`service.status = "down"`, counts absent) — the page must render while
the stack is half-up.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import cron, providers
from app.config import settings
from app.lake import core as lake_core
from app.listing import DEFAULT_LIMIT, clean_many
from app.providers import promoted_relation

logger = logging.getLogger(__name__)

# Connect stays short so a daemon that is actually down fails fast. The read budget is
# what staging and production blow: GET /plan diffs every model against the warehouse,
# and GET /runs correlates each row with a large event log. Five seconds marked that
# as "the data service is down" while the event stream — which has no read deadline —
# still said live updates were connected.
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=5.0, write=10.0, pool=10.0)
_SNAPSHOT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
# POST /reset drops every Interlace-owned view and snapshot schema, then answers.
# The same five-second read budget abandoned that call, so Rebuild appeared to do
# nothing: the wipe never got to report, and the page stayed on the timeout.
_RESET_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
# A startup apply (SEI's first transaction pull) can run for minutes. Writes are refused
# with 503 until it finishes; the caller waits this long, then retries the same POST.
_STARTUP_DETAIL = "startup apply is still running"
_STARTUP_POLL_S = 2.0
_STARTUP_WAIT_S = 900.0
# Enough history for the Runs tab / event feed without paging.
_RUNS_SHOWN = 20
_EVENTS_SHOWN = 60
_SERVICE_TTL_S = 10.0

# The overview used to issue five collection requests and then one request per model on every
# click. Tenant models make that N large (a hundred-plus for SEI), and the daemon serves them
# one at a time. Model SQL and column lineage are immutable for a fingerprint, so they are
# cached — and they are not on the overview at all. The first paint reads the five lists.
# Models and lineage fetch details afterwards. The TTL is short because runs and events move.
_service_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_service_locks: dict[str, asyncio.Lock] = {}
_summary_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_summary_locks: dict[str, asyncio.Lock] = {}
_model_detail_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
_detail_locks: dict[str, asyncio.Lock] = {}


class PlatformUnavailable(RuntimeError):
    """The provider has no configured pipeline service, or it can't be reached."""


# Headline metrics (clients, AUM, per firm) are one attach and two scans. Per-model row counts
# are a separate read — they only feed Models and Lineage — and share this TTL. The console and
# record previews always read live.
_WAREHOUSE_TTL_S = 60.0
_warehouse_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_dashboard_locks: dict[str, asyncio.Lock] = {}
_catalogue_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_catalogue_locks: dict[str, asyncio.Lock] = {}


def service_url(provider: str) -> str:
    """Base URL of the provider's interlace daemon; blank when not configured.

    DERIVED from the provider code (0131) rather than held as a third dict over the same three
    custodians: `morgan-lloyd` -> `interlace_morgan_lloyd_url` -> `INTERLACE_MORGAN_LLOYD_URL`.
    The settings field name and the env var were already that substitution written out by hand, so
    a fourth custodian needed an edit here, an edit on `Settings`, and an edit in three compose
    files — and forgetting the first of those returns "" from a `.get`, which reads as "the
    pipeline service is not configured" rather than as a missing case.
    """
    attribute = f"interlace_{provider.replace('-', '_')}_url"
    return str(getattr(settings, attribute, "") or "")


async def _service_get(base: str, path: str, client: httpx.AsyncClient) -> Any:
    resp = await client.get(f"{base.rstrip('/')}{path}")
    resp.raise_for_status()
    return resp.json()


def _service_liveness(body: Any) -> str:
    """How the index card reads ``GET /health``.

    interlace 2.8.7 returns ``starting`` on HTTP 200 while the startup apply runs.
    Treating that 200 as up told an operator the write buttons would succeed.
    """
    status = body.get("status") if isinstance(body, dict) else None
    if status == "starting":
        return "starting"
    if status == "error":
        return "down"
    return "up"


def _exc_text(exc: BaseException) -> str:
    """What an operator should read. httpx timeouts stringify to nothing, so the page
    was showing the class name ``ReadTimeout``."""
    if isinstance(exc, httpx.TimeoutException):
        return "the pipeline did not answer in time"
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _optional_list(provider: str, path: str, value: Any, default: Any) -> Any:
    """A list the overview can render without. The model list is not one of these."""
    if isinstance(value, BaseException):
        logger.warning(
            "platform service %s unavailable for %s: %s",
            path,
            provider,
            _exc_text(value),
        )
        return default
    return value


def _down(exc: BaseException | None = None, *, configured: bool = True) -> dict[str, Any]:
    if not configured:
        return {"status": "down", "error": "pipeline service not configured"}
    if exc is None:
        return {"status": "down", "error": "unavailable"}
    return {"status": "down", "error": _exc_text(exc)}


async def _collect_service(provider: str) -> dict[str, Any]:
    """The daemon's lists: models, plan, runs, events, streams.

    Not the per-model detail. That is one HTTP round trip per model and the daemon answers them
    serially; the overview does not render SQL or column lineage, so it must not wait on them.
    """
    base = service_url(provider)
    if not base:
        return _down(configured=False)
    paths = ("/models", "/plan", "/runs", "/events", "/streams")
    try:
        async with httpx.AsyncClient(timeout=_SNAPSHOT_TIMEOUT) as client:
            # One slow list must not discard the lists that already answered. GET /plan
            # is the usual one: it diffs the whole project. The model list is the
            # snapshot; without it the page has nothing to render.
            fetched = await asyncio.gather(
                *(_service_get(base, path, client) for path in paths),
                return_exceptions=True,
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "platform service snapshot failed for %s: %s",
            provider,
            _exc_text(exc),
        )
        return _down(exc)
    by_path = dict(zip(paths, fetched, strict=True))
    raw_models = by_path["/models"]
    if isinstance(raw_models, BaseException):
        logger.warning(
            "platform service snapshot failed for %s: %s",
            provider,
            _exc_text(raw_models),
        )
        return _down(raw_models)
    plan_body = _optional_list(provider, "/plan", by_path["/plan"], {"changes": []})
    runs = _optional_list(provider, "/runs", by_path["/runs"], [])
    events = _optional_list(provider, "/events", by_path["/events"], [])
    streams = _optional_list(provider, "/streams", by_path["/streams"], [])
    try:
        models = [dict(model) for model in raw_models]
        for model in models:
            model["columns"] = {}
            model["sql"] = None
        changes = plan_body.get("changes", []) if isinstance(plan_body, dict) else []
        return {
            "status": "up",
            "models": models,
            "plan": {c["name"]: c for c in changes},
            "runs": sorted(
                runs,
                key=lambda r: (str(r.get("enqueued_at") or ""), int(r.get("id") or 0)),
                reverse=True,
            )[:_RUNS_SHOWN],
            # Full history too: the overview graph spans weeks, so 20 rows is not enough.
            "all_runs": runs,
            "events": sorted(events, key=lambda e: e["seq"], reverse=True)[:_EVENTS_SHOWN],
            "streams": streams,
        }
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "platform service snapshot failed for %s: %s",
            provider,
            _exc_text(exc),
        )
        return _down(exc)


def _detail_key(provider: str, model: dict[str, Any]) -> tuple[str, str, str]:
    return (provider, str(model["name"]), str(model.get("fingerprint") or ""))


async def _attach_model_details(provider: str, models: list[dict[str, Any]]) -> None:
    """Fill `columns` and `sql` on `models` from the fingerprint cache, fetching the rest.

    Mutates the dicts it is given. Callers pass copies when the list is a cached summary.
    One flight per provider: two catalogue loads otherwise each walk every model.
    """
    lock = _detail_locks.setdefault(provider, asyncio.Lock())
    async with lock:
        await _attach_model_details_locked(provider, models)


async def _attach_model_details_locked(provider: str, models: list[dict[str, Any]]) -> None:
    base = service_url(provider)
    if not base:
        return
    missing = [model for model in models if _detail_key(provider, model) not in _model_detail_cache]
    details: list[Any] = []
    if missing:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                details = list(
                    await asyncio.gather(
                        *(
                            _service_get(base, f"/models/{model['name']}", client)
                            for model in missing
                        ),
                        return_exceptions=True,
                    )
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "platform model details unavailable for %s: %s",
                provider,
                str(exc) or exc.__class__.__name__,
            )
            details = []
            missing = []
    for model, detail in zip(missing, details, strict=True):
        if isinstance(detail, BaseException):
            logger.warning(
                "platform model detail unavailable for %s/%s: %s",
                provider,
                model["name"],
                str(detail) or detail.__class__.__name__,
            )
            continue
        _model_detail_cache[_detail_key(provider, model)] = dict(detail)
    current = {_detail_key(provider, model) for model in models}
    for stale in [key for key in _model_detail_cache if key[0] == provider and key not in current]:
        del _model_detail_cache[stale]
    for model in models:
        detail = _model_detail_cache.get(_detail_key(provider, model), {})
        model["columns"] = detail.get("columns", {})
        model["sql"] = detail.get("sql")


async def _service_snapshot_uncached(provider: str) -> dict[str, Any]:
    """Lists plus per-model lineage and SQL. `status: down` when the daemon is unreachable."""
    snapshot = await _collect_service(provider)
    if snapshot["status"] == "up":
        await _attach_model_details(provider, snapshot["models"])
    return snapshot


async def _cached_service(
    provider: str,
    cache: dict[str, tuple[float, dict[str, Any]]],
    locks: dict[str, asyncio.Lock],
    load: Callable[[str], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Single-flight TTL cache. A `down` result is not stored — an outage should recover on
    the next request, while a healthy snapshot may lag for the TTL."""
    cached = cache.get(provider)
    if cached and time.monotonic() - cached[0] < _SERVICE_TTL_S:
        return cached[1]
    lock = locks.setdefault(provider, asyncio.Lock())
    async with lock:
        cached = cache.get(provider)
        if cached and time.monotonic() - cached[0] < _SERVICE_TTL_S:
            return cached[1]
        snapshot = await load(provider)
        if snapshot["status"] == "up":
            cache[provider] = (time.monotonic(), snapshot)
        for stale in [key for key in cache if key not in providers.PROVIDERS]:
            del cache[stale]
            locks.pop(stale, None)
        return snapshot


async def _service_snapshot(provider: str) -> dict[str, Any]:
    """Lists plus per-model details. Used when a caller really needs SQL and column lineage.

    The overview does not. Three app replicas and several operators can otherwise fan the same
    N+1 request set into the singleton pipeline daemon; the lock collapses concurrent misses
    in one API process, and the fingerprint cache fetches details only for changed models.
    """
    return await _cached_service(
        provider, _service_cache, _service_locks, _service_snapshot_uncached
    )


async def _service_summary(provider: str) -> dict[str, Any]:
    """The five daemon lists, without a request per model. What the overview waits on."""
    return await _cached_service(provider, _summary_cache, _summary_locks, _collect_service)


def forget_service(provider: str) -> None:
    """Make an operator-triggered run visible immediately rather than after the snapshot TTL."""
    _service_cache.pop(provider, None)
    _summary_cache.pop(provider, None)


# ---- core-warehouse reads (lake_reader, READ_ONLY) ----


def _core_connect_sync(provider: str) -> Any:
    """Check out the provider's core catalogue, READ_ONLY as lake_reader.

    Delegates to the pool in `app.lake.core`. `CoreUnavailable` is re-raised as
    `PlatformUnavailable` so every caller in this module keeps catching one exception.
    The connection belongs to this thread until :func:`lake_core.checkin`.
    """
    try:
        return lake_core.checkout(provider)
    except lake_core.CoreUnavailable as exc:
        raise PlatformUnavailable(str(exc)) from exc


@contextmanager
def _owned_core(provider: str, conn: Any | None = None) -> Iterator[Any]:
    """A caller-supplied connection, or a pooled one discarded if the body raises.

    A stubbed ``_core_connect_sync`` was never checked out; ``checkin`` then closes it,
    which is what these callers used to do in a ``finally``.
    """
    if conn is not None:
        yield conn
        return
    borrowed = _core_connect_sync(provider)
    discard = False
    try:
        yield borrowed
    except BaseException:
        discard = True
        raise
    finally:
        lake_core.checkin(borrowed, discard=discard)


def _relation_bindings(models: tuple[str, ...]) -> list[dict[str, str]]:
    """One template binding: placeholder -> promoted relation.

    Placeholders are the model's last path segment (``ref.firms`` -> ``firms``). This used to
    return several candidate spellings because a catalogue might hold either the prefixed or
    the bare form; relations are unconditionally bare now, so there is one.
    """
    return [{name.rpartition(".")[2]: promoted_relation(name) for name in models}]


# Headline figures per provider: which promoted relation counts as "clients", and where a
# portfolio value lives. A provider with no holdings layer (Morgan Lloyd is scheme admin, not
# custodian) simply has no AUM — the card renders "—" rather than a misleading zero.
# Relations are `platform.*` for every provider — the
# consumption layer is Graphene-canonical, so "clients" is spelt the same whichever
# custodian it came from.
_METRIC_SOURCES: dict[str, dict[str, tuple[str, str] | tuple[str]]] = {
    "seccl": {"clients": ("platform.clients",), "aum": ("platform.positions", "current_value")},
    "morgan-lloyd": {"clients": ("platform.contacts",)},
    # SEI's holdings value is `market_value`; `current_value` is Seccl's spelling and
    # naming it here left the SEI AUM figure permanently blank.
    "sei": {"clients": ("platform.clients",), "aum": ("platform.positions", "market_value")},
}


# How a provider's consumption layer identifies the slice a row belongs to, and where the
# human-readable name of that slice lives. `listish` marks a column Seccl sends as either a
# scalar or a JSON array (`node_id` is `["9"]` on clients but `41` on positions) — the same
# quirk `book_sink.predicate` handles.
_SLICE: dict[str, dict[str, Any]] = {
    "seccl": {
        "column": "node_id",
        "listish": True,
        "names": ("platform.nodes", "key", "name"),
    },
    "morgan-lloyd": {"column": "ml_tenant_id", "listish": False, "names": None},
    "sei": {"column": "firm_id", "listish": False, "names": None},
}


def _slice_expr(column: str, listish: bool) -> str:
    """SQL yielding one row per slice id for a row, unnesting the scalar-or-list shape."""
    if not listish:
        return f"CAST({column} AS VARCHAR)"
    return (
        f"unnest(coalesce(try_cast(CAST({column} AS VARCHAR) AS VARCHAR[]), "
        f"[CAST({column} AS VARCHAR)]))"
    )


def _per_slice_sync(provider: str, conn: Any = None) -> dict[str, Any]:
    """Clients and AUM grouped by slice id, plus slice names.

    **One query per metric, not one per tenant.** Aggregating by the slice column and summing in
    Python keeps the page cost flat as firms are added — a per-tenant query would be O(tenants)
    DuckDB round trips on every page view, each re-scanning the same relation.
    """
    config = _SLICE.get(provider)
    sources = _METRIC_SOURCES.get(provider, {})
    if not config or not sources:
        return {}
    expr = _slice_expr(config["column"], config["listish"])
    with _owned_core(provider, conn) as conn:
        out: dict[str, Any] = {"clients": {}, "aum": {}, "names": {}}
        clients = sources.get("clients")
        if clients:
            try:
                rows = conn.execute(
                    f"SELECT {expr} AS slice_id, count(*) AS n "  # noqa: S608
                    f"FROM {promoted_relation(clients[0])} GROUP BY 1"
                ).fetchall()
                out["clients"] = {str(s): int(n) for s, n in rows if s is not None}
            except Exception as exc:  # noqa: BLE001
                logger.debug("per-slice clients unavailable for %s: %s", provider, exc)
        aum = sources.get("aum")
        if aum and len(aum) == 2:
            try:
                rows = conn.execute(
                    f"SELECT {expr} AS slice_id, sum({aum[1]}) AS v "  # noqa: S608
                    f"FROM {promoted_relation(aum[0])} GROUP BY 1"
                ).fetchall()
                out["aum"] = {str(s): float(v or 0) for s, v in rows if s is not None}
            except Exception as exc:  # noqa: BLE001
                logger.debug("per-slice aum unavailable for %s: %s", provider, exc)
        names = config["names"]
        if names:
            relation, id_column, name_column = names
            try:
                rows = conn.execute(
                    f"SELECT CAST({id_column} AS VARCHAR), {name_column} "  # noqa: S608
                    f"FROM {promoted_relation(relation)}"
                ).fetchall()
                out["names"] = {str(i): str(n) for i, n in rows if i is not None}
            except Exception as exc:  # noqa: BLE001
                logger.debug("slice names unavailable for %s: %s", provider, exc)
        return out


def tenant_slice_metrics(
    firms: list[dict[str, Any]], per_slice: dict[str, Any], provider: str
) -> list[dict[str, Any]]:
    """Attach clients / AUM / resolved slice names to each firm's enablement row.

    A firm holds several slice ids (a Seccl firm owns several nodes), so its figures are the sum
    over the ids it holds. Ids the warehouse doesn't know about are still listed by id — an
    enablement referencing a node that no longer exists should be visible, not silently dropped.
    """
    clients = per_slice.get("clients") or {}
    aum = per_slice.get("aum") or {}
    names = per_slice.get("names") or {}
    keys = providers.slice_config_keys(provider)

    enriched: list[dict[str, Any]] = []
    for firm in firms:
        config = firm.get("config") or {}
        ids: list[str] = []
        for key in keys:
            value = config.get(key)
            if isinstance(value, list | tuple):
                ids.extend(str(v) for v in value)
            elif value is not None:
                ids.append(str(value))
        has_any = any(i in clients or i in aum for i in ids)
        enriched.append(
            {
                **firm,
                "slices": [{"id": i, "name": names.get(i)} for i in ids],
                # None, not 0, when the warehouse knows nothing about any of this firm's ids:
                # "not promoted yet" must not render as "no clients".
                "clients": sum(clients.get(i, 0) for i in ids) if has_any else None,
                "aum": sum(aum.get(i, 0.0) for i in ids) if has_any else None,
            }
        )
    return enriched


def _metrics_sync(provider: str, conn: Any = None) -> dict[str, Any]:
    """Client count + AUM from the provider's consumption layer.

    Read on the warehouse connection rather than derived from `counts`, because AUM is a SUM and
    the client count should be of the consumption relation specifically, not whatever the daemon
    happens to list. A missing relation yields None, not zero: "not promoted yet" and "no clients"
    must not look the same.
    """
    sources = _METRIC_SOURCES.get(provider, {})
    if not sources:
        return {}
    with _owned_core(provider, conn) as conn:
        metrics: dict[str, Any] = {}
        clients = sources.get("clients")
        if clients:
            try:
                row = conn.execute(
                    f"SELECT count(*) FROM {promoted_relation(clients[0])}"  # noqa: S608
                ).fetchone()
                metrics["clients"] = int(row[0]) if row else 0
            except Exception as exc:  # noqa: BLE001 — not promoted yet; the card renders "—"
                logger.debug("clients metric unavailable for %s: %s", provider, exc)
        aum = sources.get("aum")
        if aum and len(aum) == 2:
            try:
                row = conn.execute(
                    f"SELECT sum({aum[1]}) FROM {promoted_relation(aum[0])}"  # noqa: S608
                ).fetchone()
                metrics["aum"] = float(row[0]) if row and row[0] is not None else 0.0
            except Exception as exc:  # noqa: BLE001
                logger.debug("aum metric unavailable for %s: %s", provider, exc)
        return metrics


def runs_per_day(runs: list[dict[str, Any]], days: int = 14) -> list[dict[str, Any]]:
    """Run counts per day for the overview's column graph, oldest first.

    Every day in the window is emitted, including empty ones — a bar chart with the gaps missing
    reads as "busy every day" when it was actually idle for a week.
    """
    today = datetime.now(UTC).date()
    window = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    counts = dict.fromkeys(window, 0)
    failed = dict.fromkeys(window, 0)
    for run in runs:
        stamp = run.get("enqueued_at")
        if not isinstance(stamp, str):
            continue
        try:
            day = datetime.fromisoformat(stamp).date()
        except ValueError:
            continue
        if day in counts:
            counts[day] += 1
            if run.get("state") == "failed":
                failed[day] += 1
    return [{"date": day.isoformat(), "runs": counts[day], "failed": failed[day]} for day in window]


# Model names are interpolated into SQL. They come from the daemon, but a name that is not a
# plain identifier must not become executable SQL.
_SQL_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _promoted_sql(model_name: str) -> tuple[str, str] | None:
    """`(schema.table, '"schema"."table"')` for a model, or None when it is not an identifier."""
    if not model_name or any(not _SQL_IDENT.fullmatch(part) for part in model_name.split(".")):
        return None
    relation = promoted_relation(model_name)
    schema, dot, table = relation.partition(".")
    if not dot or not _SQL_IDENT.fullmatch(schema) or not _SQL_IDENT.fullmatch(table):
        return None
    return f"{schema}.{table}", f'"{schema}"."{table}"'


def _warehouse_sync(provider: str, model_names: list[str], conn: Any = None) -> dict[str, Any]:
    """Row counts + column schemas per model's promoted relation.

    The daemon's compiled graph only knows lineage for SQL models, so the warehouse is the
    source of truth for what a (Python-extracted) relation actually contains. Missing relations
    (not yet promoted) are skipped, not errors.

    One catalogue listing and one column listing, then a single `UNION ALL` of the counts.
    A count and a `DESCRIBE` per model was a query round trip per relation — about a second and
    a half on SEI's catalogue, on top of the attach.
    """
    with _owned_core(provider, conn) as conn:
        present = {
            f"{schema}.{table}"
            for schema, table in conn.execute(
                "SELECT table_schema, table_name FROM information_schema.tables"
            ).fetchall()
        }
        pairs: list[tuple[str, str, str]] = []
        for name in model_names:
            promoted = _promoted_sql(name)
            if promoted is None or promoted[0] not in present:
                continue
            pairs.append((name, promoted[0], promoted[1]))
        schemas = _schemas_for(conn, pairs)
        try:
            counts = _counts_together(conn, pairs)
        except Exception as exc:  # noqa: BLE001 — one broken view must not blank the rest
            logger.warning("batched platform counts failed for %s: %s", provider, exc)
            counts = _counts_each(conn, pairs)
        return {"counts": counts, "schemas": schemas}


def _schemas_for(conn: Any, pairs: list[tuple[str, str, str]]) -> dict[str, list[list[str]]]:
    if not pairs:
        return {}
    rows = conn.execute(
        "SELECT table_schema, table_name, column_name, data_type "
        "FROM information_schema.columns "
        "ORDER BY table_schema, table_name, ordinal_position"
    ).fetchall()
    by_relation: dict[str, list[list[str]]] = {}
    for schema, table, column, data_type in rows:
        by_relation.setdefault(f"{schema}.{table}", []).append([str(column), str(data_type)])
    return {name: by_relation[bare] for name, bare, _quoted in pairs if bare in by_relation}


def _counts_together(conn: Any, pairs: list[tuple[str, str, str]]) -> dict[str, int]:
    if not pairs:
        return {}
    parts = []
    for name, _bare, quoted in pairs:
        literal = "'" + name.replace("'", "''") + "'"
        parts.append(f"SELECT {literal} AS name, count(*)::BIGINT AS n FROM {quoted}")  # noqa: S608
    return {str(name): int(n) for name, n in conn.execute(" UNION ALL ".join(parts)).fetchall()}


def _counts_each(conn: Any, pairs: list[tuple[str, str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, _bare, quoted in pairs:
        try:
            row = conn.execute(f"SELECT count(*) FROM {quoted}").fetchone()  # noqa: S608
        except Exception:  # noqa: BLE001, S112 — this relation is unreadable; the others still are
            continue
        counts[name] = int(row[0]) if row else 0
    return counts


def _dashboard_reads_sync(provider: str) -> dict[str, Any]:
    """Clients, AUM and per-firm figures, over one attach.

    Per-model counts used to ride along. They scan every promoted relation, and the overview
    does not show them.
    """
    with _owned_core(provider) as conn:
        return {
            "metrics": _metrics_sync(provider, conn),
            "per_slice": _per_slice_sync(provider, conn),
        }


def _remember(
    cache: dict[str, tuple[float, dict[str, Any]]], provider: str, reads: dict[str, Any]
) -> None:
    """Cache one provider's warehouse observation, bounded by the provider registry."""
    cache[provider] = (time.monotonic(), reads)
    for stale in [key for key in cache if key not in providers.PROVIDERS]:
        del cache[stale]


def _remember_warehouse(provider: str, reads: dict[str, Any]) -> None:
    """Cache the overview's headline warehouse reads.

    The cache was an unbounded process-local dict with no eviction — harmless at three providers
    and wrong as a habit. It is keyed by provider, so the bound is the registry's size.
    """
    _remember(_warehouse_cache, provider, reads)


def forget_warehouse(provider: str) -> None:
    """Drop cached warehouse observations for a provider.

    Called after a sync or a rebuild: both change exactly what this caches, and leaving it meant
    an operator pressed Rebuild and then read pre-rebuild figures for up to a minute with
    nothing saying so.
    """
    _warehouse_cache.pop(provider, None)
    _catalogue_cache.pop(provider, None)
    _catalogue_locks.pop(provider, None)


# ---------------------------------------------------------------------------------------------
# Ingestion — how a custodian's data actually arrives, which differs per provider and is the one
# part of the pipeline nothing surfaced.
#
# Only SEI has an ingest LEDGER, and that is a fact about the transport rather than a gap: its
# nightly PGP-encrypted SFTP drop is the head of its own DAG (`raw.files`, which all 47 report
# models depend on), so every file's arrival, trailer verification and decryption is a row. Seccl
# arrives by webhook and API poll — its streams and runs already say so — and Morgan Lloyd is
# poll-only, with nothing to fetch and therefore nothing to record.
#
# Stated per provider rather than probed, so a provider with no ledger reports WHY instead of
# rendering an empty table that reads as "nothing arrived last night".
_INGEST: dict[str, dict[str, Any]] = {
    "sei": {
        "kind": "sftp",
        "relation": "raw.files",
        "note": (
            "SEI writes a set of pipe-delimited, PGP-encrypted reports to SFTP each night. The "
            "pull is the head of this DAG, not a side cron: every raw model depends on it, so a "
            "run cannot read a drop it has not fetched."
        ),
    },
    "seccl": {
        "kind": "webhooks",
        "relation": None,
        "note": (
            "Seccl pushes webhooks to the gateway and is polled for the entities that carry no "
            "change signal. There is no file to fetch, so arrival is visible as stream lag and "
            "run history rather than as a per-file ledger."
        ),
    },
    "morgan-lloyd": {
        "kind": "api",
        "relation": None,
        "note": (
            "Morgan Lloyd is read on a schedule over Dataverse OData and custom actions. Nothing "
            "is delivered to us, so there is no ingest ledger — run history is the record."
        ),
    },
}

# Newest first, and bounded: a drop is ~46 files a night, so this is a fortnight of history.
_INGEST_LIMIT = 700


def _ingestion_sync(provider: str) -> dict[str, Any]:
    """The per-file drop ledger, newest first, for a provider that has one.

    Reads the relation directly rather than through `platform_entities`: this is not a firm's
    data. A drop is platform-wide — one file carries every firm's records — so there is no slice
    to narrow by and nothing here belongs to a tenant.
    """
    declared = _INGEST.get(provider)
    if declared is None or not declared["relation"]:
        return {"files": [], "available": False}
    relation = promoted_relation(str(declared["relation"]))
    with _owned_core(provider) as conn:
        try:
            columns = [str(row[0]) for row in conn.execute(f"DESCRIBE {relation}").fetchall()]
        except Exception as exc:  # noqa: BLE001 — an unbuilt model is a state, not a crash
            # The distinction this whole module exists for: "the pull has never run" is not
            # "last night's drop was empty".
            return {"files": [], "available": False, "reason": str(exc).splitlines()[0][:200]}
        wanted = [
            c
            for c in (
                "filename",
                "report",
                "drop_date",
                "status",
                "bytes",
                "trailer_rows",
                "rows_read",
                "error",
                "processed_at",
            )
            if c in columns
        ]
        order = "processed_at" if "processed_at" in columns else wanted[0]
        selected = ", ".join(f'"{c}"' for c in wanted)
        rows = conn.execute(
            f'SELECT {selected} FROM {relation} ORDER BY "{order}" DESC LIMIT ?',  # noqa: S608
            [_INGEST_LIMIT],
        ).fetchall()
        files = [dict(zip(wanted, row, strict=True)) for row in rows]
        return {"files": files, "available": True}


async def ingestion(provider: str) -> dict[str, Any]:
    """How this custodian's data arrives, and — where it is a delivery — whether it did.

    THE SURFACE THAT DID NOT EXIST. SEI's drop ledger records, per file, whether it synced, was
    already present, or FAILED, with SEI's own `Total Number of Records:` trailer checked against
    what was read — so a truncated drop fails that file rather than landing short. None of it
    reached a screen: `transport: 'sftp'` was a badge on a card, and a night where every file
    failed looked exactly like a night with no data.
    """
    declared = _INGEST.get(provider, {"kind": "unknown", "relation": None, "note": ""})
    body: dict[str, Any] = {
        "kind": declared["kind"],
        "note": declared["note"],
        "files": [],
        "available": False,
    }
    if not declared["relation"]:
        return body
    try:
        body.update(await asyncio.to_thread(_ingestion_sync, provider))
    except PlatformUnavailable as exc:
        body["reason"] = str(exc)
    return body


_SELECT_ONLY = re.compile(r"^\s*(select|with|describe|summarize)\b", re.IGNORECASE)


def _scan_sql(sql: str) -> tuple[str, str]:
    """(cleaned, masked): ``cleaned`` is the sql with ``--`` line comments removed —
    but only OUTSIDE string literals, so a literal like ``'%--%'`` survives; ``masked``
    additionally blanks literal bodies, for structural checks (``;``, leading keyword)."""
    cleaned: list[str] = []
    masked: list[str] = []
    i, n = 0, len(sql)
    in_str = False
    while i < n:
        ch = sql[i]
        if in_str:
            cleaned.append(ch)
            if ch == "'" and i + 1 < n and sql[i + 1] == "'":  # escaped '' stays in-string
                cleaned.append("'")
                masked.append("  ")  # two source chars -> keep masked/cleaned aligned
                i += 1
            elif ch == "'":
                masked.append("'")
                in_str = False
            else:
                masked.append(" ")
        elif ch == "'":
            in_str = True
            cleaned.append(ch)
            masked.append(ch)
        elif ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                i += 1
            cleaned.append(" ")
            masked.append(" ")
            continue
        else:
            cleaned.append(ch)
            masked.append(ch)
        i += 1
    return "".join(cleaned), "".join(masked)


def _query_sync(provider: str, sql: str, limit: int) -> dict[str, Any]:
    """Run one read-only statement against the provider's core catalogue. Defence in depth:
    the attach is READ_ONLY as lake_reader AND only a single SELECT, WITH, DESCRIBE or
    SUMMARIZE statement is accepted (no ATTACH/INSTALL/SET escapes)."""
    cleaned, masked = _scan_sql(sql)
    cleaned, masked = cleaned.strip(), masked.strip()
    while cleaned.endswith(";") and masked.endswith(";"):
        cleaned, masked = cleaned[:-1].rstrip(), masked[:-1].rstrip()
    if not cleaned:
        raise ValueError("empty query")
    if ";" in masked:
        raise ValueError("one statement per query")
    if not _SELECT_ONLY.match(masked):
        raise ValueError(
            "read-only console: only SELECT, WITH, DESCRIBE and SUMMARIZE queries are accepted"
        )
    with _owned_core(provider) as conn:
        started = time.perf_counter()
        cursor = conn.execute(cleaned)
        rows = cursor.fetchmany(limit)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        columns = [d[0] for d in cursor.description or []]

        def cell(value: Any) -> Any:
            if value is None or isinstance(value, (bool, int, float, str)):
                return value
            return str(value)

        return {
            "columns": columns,
            "rows": [[cell(v) for v in row] for row in rows],
            "rowCount": len(rows),
            "elapsedMs": elapsed_ms,
        }


async def query(provider: str, sql: str, limit: int) -> dict[str, Any]:
    return await asyncio.to_thread(_query_sync, provider, sql, limit)


def _detail_of(body: Any) -> str:
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str) and detail:
            return detail
    return ""


def _response_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:
        return ""
    return _detail_of(body)


def _startup_waiting(response: httpx.Response) -> bool:
    """interlace 2.8.7 answers a write with this while the startup apply is still running."""
    return response.status_code == 503 and _response_detail(response) == _STARTUP_DETAIL


async def _wait_for_startup(client: httpx.AsyncClient, base: str, deadline: float) -> None:
    """Poll ``GET /health`` until the startup apply finishes or the wait runs out."""
    root = base.rstrip("/")
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PlatformUnavailable(_STARTUP_DETAIL)
        try:
            resp = await client.get(f"{root}/health")
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPError as exc:
            raise PlatformUnavailable(_service_error(exc)) from exc
        status = body.get("status") if isinstance(body, dict) else None
        if status == "ok":
            return
        if status == "error":
            raise PlatformUnavailable(_detail_of(body) or "startup apply failed")
        await asyncio.sleep(min(_STARTUP_POLL_S, remaining))


async def _post_daemon(
    client: httpx.AsyncClient, base: str, path: str, payload: Any | None = None
) -> Any:
    """POST one daemon write, retrying it once the startup apply has finished.

    A 503 with ``startup apply is still running`` is the daemon accepting the
    connection and refusing the write. Waiting for health, then posting the same
    body, is what gets the real result. Any other failure is the caller's.
    """
    url = f"{base.rstrip('/')}{path}"
    deadline = time.monotonic() + _STARTUP_WAIT_S
    waited = False
    while True:
        if payload is None:
            resp = await client.post(url)
        else:
            resp = await client.post(url, json=payload)
        if not _startup_waiting(resp):
            resp.raise_for_status()
            return resp.json()
        # One retry, after health reports ok. A second 503 is the daemon's answer.
        if waited:
            raise PlatformUnavailable(_STARTUP_DETAIL)
        await _wait_for_startup(client, base, deadline)
        waited = True


async def trigger_sync(provider: str) -> dict[str, Any]:
    """Enqueue a full pipeline run on the provider's daemon (POST /runs)."""
    forget_warehouse(provider)
    forget_service(provider)
    base = service_url(provider)
    if not base:
        raise PlatformUnavailable("pipeline service not configured")
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            body = await _post_daemon(client, base, "/runs", {"selectors": []})
            return dict(body)
    except httpx.HTTPError as exc:
        raise PlatformUnavailable(_service_error(exc)) from exc


async def merge_rejects(
    session: AsyncSession, provider: str, *, tenant_id: int | None = None, limit: int = 200
) -> dict[str, Any]:
    """What the latest import could not write, and why.

    THE LEDGER HAD NO READER, which made it decoration. Its entire justification is that a firm with
    no rows and a firm whose rows were all refused look identical from every surface — the reasons
    are the only thing that tells the two apart (`docs/provider-pipeline.md` §5) — and that argument
    only holds if somebody can see them.

    THE LATEST IMPORT, not the lifetime of the table. A reject is a statement about one run, and the
    table keeps every run. Select's fifteen Cash Transfers were refused once, mapped, and written an
    hour later; summing the table still listed them. The window is the newest run of that firm and
    entity: refusals at or before its `run_at`, and after the run before it.

    POOLED BY REASON. 117 transactions refused for one unmapped type is ONE thing to act on, not
    117; the raw rows are still there behind `recent`, because chasing a specific refusal back to
    source needs the provider's own key.
    """
    pooled = (
        (
            await session.execute(
                text(
                    """
                    WITH runs AS (
                        SELECT tenant_id, entity, run_at, rejected,
                               lag(run_at) OVER (
                                   PARTITION BY tenant_id, entity ORDER BY run_at
                               ) AS prev_at
                          FROM platform.merge_runs
                         WHERE provider = :provider
                           AND (CAST(:tenant_id AS int) IS NULL OR tenant_id = :tenant_id)
                    ),
                    latest AS (
                        SELECT DISTINCT ON (tenant_id, entity)
                               tenant_id, entity, run_at, rejected, prev_at
                          FROM runs
                         ORDER BY tenant_id, entity, run_at DESC
                    )
                    SELECT r.entity, r.reason, count(*) AS rejected,
                           max(r.occurred_at) AS last_seen,
                           count(DISTINCT r.tenant_id) AS tenants,
                           min(t.name) AS tenant_name
                      FROM platform.merge_rejects r
                      JOIN latest l
                        ON l.tenant_id = r.tenant_id
                       AND l.entity = r.entity
                       AND l.rejected > 0
                       AND r.occurred_at <= l.run_at
                       AND (l.prev_at IS NULL OR r.occurred_at > l.prev_at)
                      LEFT JOIN auth.tenants t ON t.id = r.tenant_id
                     WHERE r.provider = :provider
                     GROUP BY r.entity, r.reason
                     ORDER BY count(*) DESC, max(r.occurred_at) DESC
                     LIMIT :limit
                    """
                ),
                {"provider": provider, "tenant_id": tenant_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    recent = (
        (
            await session.execute(
                text(
                    """
                    WITH runs AS (
                        SELECT tenant_id, entity, run_at, rejected,
                               lag(run_at) OVER (
                                   PARTITION BY tenant_id, entity ORDER BY run_at
                               ) AS prev_at
                          FROM platform.merge_runs
                         WHERE provider = :provider
                           AND (CAST(:tenant_id AS int) IS NULL OR tenant_id = :tenant_id)
                    ),
                    latest AS (
                        SELECT DISTINCT ON (tenant_id, entity)
                               tenant_id, entity, run_at, rejected, prev_at
                          FROM runs
                         ORDER BY tenant_id, entity, run_at DESC
                    )
                    SELECT r.entity, r.reason, r.row_key, r.occurred_at, t.name AS tenant_name
                      FROM platform.merge_rejects r
                      JOIN latest l
                        ON l.tenant_id = r.tenant_id
                       AND l.entity = r.entity
                       AND l.rejected > 0
                       AND r.occurred_at <= l.run_at
                       AND (l.prev_at IS NULL OR r.occurred_at > l.prev_at)
                      LEFT JOIN auth.tenants t ON t.id = r.tenant_id
                     WHERE r.provider = :provider
                     ORDER BY r.id DESC
                     LIMIT :limit
                    """
                ),
                {"provider": provider, "tenant_id": tenant_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return {
        "reasons": [dict(row) for row in pooled],
        "recent": [dict(row) for row in recent],
        # The total across the pooled groups, not a second count(*): a page showing "12 reasons"
        # beside a total that disagreed with them would send someone looking for the difference.
        "rejected": sum(int(row["rejected"]) for row in pooled),
    }


async def merge_runs(
    session: AsyncSession, provider: str, *, tenant_id: int | None = None, limit: int = 25
) -> dict[str, Any]:
    """What the merge DID, run by run — the other half of the answer `merge_rejects` starts.

    Rejects say what was refused. This says whether the merge ran at all, for which firms, and how
    much it wrote — which is the question an operator actually opens the page with, and the one
    nothing could answer: `book_sink` builds this ledger on every run and published it only as
    `book_{uid}.merge_ledger`, a warehouse relation holding ONE run and reachable only from the SQL
    console.

    It also carries the counted OBSERVATIONS that are deliberately not rejects — "no allocations
    published", "unmodelled model wrapper: SSAS" — which by design appear in no other table.

    Grouped by RUN, newest first, with the entity rows nested. A flat list of entity rows would
    make "the last run wrote nothing" and "the last run did not happen" look the same again.
    """
    rows = (
        (
            await session.execute(
                text(
                    """
                    SELECT r.run_id, r.tenant_id, r.entity, r.created, r.updated, r.rejected,
                           r.reasons, r.note, r.run_at, t.name AS tenant_name, t.uid AS tenant_uid
                      FROM platform.merge_runs r
                      LEFT JOIN auth.tenants t ON t.id = r.tenant_id
                     WHERE r.provider = :provider
                       AND (CAST(:tenant_id AS int) IS NULL OR r.tenant_id = :tenant_id)
                       AND r.run_id IN (
                             SELECT run_id FROM platform.merge_runs
                              WHERE provider = :provider
                                AND (CAST(:tenant_id AS int) IS NULL OR tenant_id = :tenant_id)
                              GROUP BY run_id
                              ORDER BY max(run_at) DESC
                              LIMIT :limit
                           )
                     ORDER BY r.run_at DESC, r.id
                    """
                ),
                {"provider": provider, "tenant_id": tenant_id, "limit": limit},
            )
        )
        .mappings()
        .all()
    )

    runs: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["run_id"])
        run = index.get(key)
        if run is None:
            run = {
                "run_id": key,
                "tenant_uid": row["tenant_uid"],
                "tenant_name": row["tenant_name"],
                "run_at": row["run_at"],
                "created": 0,
                "updated": 0,
                "rejected": 0,
                "failed": 0,
                "entities": [],
            }
            index[key] = run
            runs.append(run)
        entity = {
            "entity": row["entity"],
            "created": int(row["created"]),
            "updated": int(row["updated"]),
            "rejected": int(row["rejected"]),
            "reasons": dict(row["reasons"] or {}),
            "note": row["note"],
        }
        run["entities"].append(entity)
        run["created"] += entity["created"]
        run["updated"] += entity["updated"]
        run["rejected"] += entity["rejected"]
        # A `note` on an entity means that entity raised and the walk carried on. Counted at the
        # run level because "15 entities merged, 1 failed" is the headline, not a row to hunt for.
        if entity["note"]:
            run["failed"] += 1
    return {"runs": runs}


async def trigger_tenant_refresh(provider: str, tenant_uid: str | None = None) -> dict[str, Any]:
    """Enqueue a run of ONE tenant's models on the provider's daemon — or of every tenant's.

    `book_sink` tags every model it registers for a firm `tenant:{uid}`, and interlace's selector
    grammar takes `tag:` — so this is the whole of a per-firm refresh: filter, stage, merge, for
    that firm only. It also tags them all plainly `tenant`, so `tenant_uid=None` means every firm's
    chain and nothing upstream of it: the distinction the deleted `POST /admin/fanout/run` used to
    draw between "re-project the slices" and "sync the provider", now that both live in one DAG.

    The daemon recompiles when the enabled-firm set changes, so a firm attached since
    startup is in the graph by the time this POST is handled. A tag that still matches
    nothing — the firm has no active bucket, or it is not enabled — is a 400, and the
    daemon's `detail` is the message surfaced here. `models` is what the run selected.
    """
    forget_warehouse(provider)
    forget_service(provider)
    base = service_url(provider)
    if not base:
        raise PlatformUnavailable("pipeline service not configured")
    selector = f"tag:tenant:{tenant_uid}" if tenant_uid else "tag:tenant"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            body = await _post_daemon(client, base, "/runs", {"selectors": [selector]})
            return dict(body)
    except httpx.HTTPError as exc:
        raise PlatformUnavailable(_service_error(exc)) from exc


def _service_error(exc: httpx.HTTPError) -> str:
    """The daemon's own `detail` when it has one.

    httpx's default is `Client error '400 Bad Request' for url '…/runs'`, which names the
    status and hides the reason — an unknown `tag:tenant:…` says which tag missed.
    A startup 503's detail is ``startup apply is still running``.
    """
    response = getattr(exc, "response", None)
    if response is not None:
        detail = _response_detail(response)
        if detail:
            return detail
    return _exc_text(exc)


async def rebuild(provider: str) -> dict[str, Any]:
    """Reset Interlace-owned state, then re-run every model.

    THE RECOVERY FOR A STATE DB THAT NO LONGER MATCHES ITS WAREHOUSE. interlace records what it
    has built in a SQLite state db beside the project; the tables themselves live in the DuckLake
    catalogue. Anything that empties the catalogue without telling interlace — a restored
    Postgres, a hand-dropped schema — leaves the two disagreeing, and the daemon then
    reads a model it believes it built:

        Catalog Error: Table with name client_products__6b9736c5ca246de1 does not exist!
        Did you mean "client_products__6b9736c5ca246de1__stage"?

    `POST /reset` is interlace's own wipe: environment views, snapshot schemas, runs and the
    stream log. It also drops the ``streams`` landing schema. The daemon's model import puts
    those tables back before this returns, because the overview and the scheduler read them.
    External table and file destinations are not dropped, and terminal models stay recorded so
    the next run does not re-deliver into them. `gc` alone does not do this — it keeps every
    snapshot the environment still references.

    The run is enqueued after the reset. Not idempotent in cost — it rebuilds everything, which
    for SEI is a full re-read of the drop — so it is a button an operator presses, never
    something a failure path retries.
    """
    forget_warehouse(provider)
    forget_service(provider)
    base = service_url(provider)
    if not base:
        raise PlatformUnavailable("pipeline service not configured")
    steps: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=_RESET_TIMEOUT) as client:
            # confirm is the daemon's guard, the same role `force` used to play on env drop.
            steps["reset"] = await _post_daemon(client, base, "/reset", {"confirm": True})
            steps["run"] = await _post_daemon(client, base, "/runs", {"selectors": []})
    except httpx.HTTPError as exc:
        raise PlatformUnavailable(_rebuild_failure(_service_error(exc), steps)) from exc
    except PlatformUnavailable as exc:
        # The startup wait gave up, or health reported the apply had failed. A reset that
        # already landed still has to be named: the warehouse is empty.
        raise PlatformUnavailable(_rebuild_failure(str(exc), steps)) from exc
    return steps


def _rebuild_failure(detail: str, steps: dict[str, Any]) -> str:
    """Name a rebuild that stopped, including which step had already landed.

    An empty ``steps`` means the reset never happened. Appending ``completed: {}``
    there used to be how a refused connection was reported; a 503 during the
    startup apply has a body, and that body is the message.
    """
    if not steps:
        return detail
    return f"{detail}; completed: {steps}"


async def cancel_run(provider: str, run_id: int) -> dict[str, Any]:
    """Ask the daemon to cancel one run. Queued cancels immediately; running stops at the next
    heartbeat."""
    base = service_url(provider)
    if not base:
        raise PlatformUnavailable("pipeline service not configured")
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            body = await _post_daemon(client, base, f"/runs/{run_id}/cancel")
            return dict(body)
    except httpx.HTTPError as exc:
        raise PlatformUnavailable(_service_error(exc)) from exc


async def iter_event_stream(provider: str, after: int = 0) -> AsyncIterator[bytes]:
    """Proxy the daemon's SSE feed (`GET /events/stream`).

    One upstream connection per admin reader. `after` is the last sequence the page already
    applied, so a reconnect replays only what it missed. This generator only copies bytes,
    including the blank lines that frame each event.
    """
    base = service_url(provider)
    if not base:
        raise PlatformUnavailable("pipeline service not configured")
    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    url = f"{base.rstrip('/')}/events/stream"
    if after > 0:
        url = f"{url}?after={after}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    yield chunk
    except httpx.HTTPError as exc:
        # A daemon that is down, or that drops the socket, is the page reconnecting —
        # not an error in this process. Raised before or between yields so the route
        # can end the response without an ASGI traceback.
        raise PlatformUnavailable(_service_error(exc)) from exc


# ---- enablement option lists (the tenant Platforms dropdowns) ----

# What each provider's slice-filter dropdowns offer, and the promoted relations each
# is read from. These are the *live* upstream ids, not the hand-registered
# `platform.providers` rows: a firm's SEI subfirms and DSR teams change
# whenever SEI reorganises, and nobody is going to retype 434 DSR teams.
#
# Each query yields (value, label, hint, parent). `parent` is what the UI cascades
# on — pick a firm, and only that firm's subfirms and DSR teams remain selectable.
# Templates reference their relations by the model's last path segment, e.g.
# `ref.firms` -> `{firms}`.
_OPTION_SOURCES: dict[str, dict[str, tuple[tuple[str, ...], str]]] = {
    "sei": {
        "firms": (
            ("ref.firms",),
            "SELECT CAST(firm_id AS VARCHAR) AS value, firm_name AS label, "
            "CAST(firm_id AS VARCHAR) AS hint, NULL AS parent FROM {firms} "
            "WHERE firm_id IS NOT NULL ORDER BY firm_name",
        ),
        "subfirms": (
            ("ref.subfirms",),
            "SELECT CAST(subfirm_id AS VARCHAR) AS value, subfirm_name AS label, "
            "CAST(subfirm_id AS VARCHAR) AS hint, CAST(firm_id AS VARCHAR) AS parent "
            "FROM {subfirms} WHERE subfirm_id IS NOT NULL ORDER BY subfirm_name",
        ),
        # The DSR type is the whole point of this list. An account carries several DSR
        # grants of different types (~3.5 on average), and only the "IFA Firm" /
        # "Adviser Firm" ones identify an advisory firm — the others (SEI Custody,
        # Legal Entity, Client Service Team) are operational segregation shared by
        # every account in the firm. So the type rides in the hint and the
        # firm-identifying teams sort first, or an operator cannot tell which of 434
        # teams is the one that means "this tenant's book".
        # Reads platform.dsr_teams, which already aggregates the types, resolves
        # identifies_firm and counts the accounts each team covers — this used to
        # rebuild all of that here over `ref.dsrs` and `ref.dsr_assignments`, two models
        # that no longer exist (they became platform.dsr_teams and platform.dsr_grants).
        # Their tables are still sitting in the catalogue unreferenced, so the picker
        # kept working while quietly serving whatever those orphans last held.
        "dsr_teams": (
            ("platform.dsr_teams",),
            "SELECT CAST(dsr_team_id AS VARCHAR) AS value, dsr_team_name AS label, "
            "       concat_ws(' · ', CAST(dsr_team_id AS VARCHAR), "
            "                 array_to_string(dsr_types, ' / ')) AS hint, "
            "       CAST(firm_id AS VARCHAR) AS parent "
            "FROM {dsr_teams} WHERE dsr_team_id IS NOT NULL "
            # Most-covering advisory firms first: with 400+ teams in a firm, an operator
            # cannot otherwise tell which one means "this tenant's book".
            "ORDER BY identifies_firm DESC, account_count DESC, dsr_team_name",
        ),
        # Enrolment templates are NOT a slice filter — they are the ids POST
        # /accountservice/accounts requires, which the SFTP drop cannot supply. Same
        # picker shape as the others (`parent` = owning firm) so the products page can
        # keep only THIS tenant's templates: the firm is already on
        # `tenant_platforms.config.firm_id`, and is not chosen per product.
        # `scope` is what the model stamps (`client` / `account`); account templates
        # are per shelf product, client templates are one per firm.
        "account_templates": (
            ("ref.enrollment_templates",),
            "SELECT CAST(template_id AS VARCHAR) AS value, "
            "       coalesce(template_name, CAST(template_id AS VARCHAR)) AS label, "
            "       concat_ws(' · ', CAST(template_id AS VARCHAR), template_type_desc) AS hint, "
            "       CAST(firm_id AS VARCHAR) AS parent "
            "FROM {enrollment_templates} "
            "WHERE template_id IS NOT NULL AND scope = 'account' "
            "ORDER BY template_name, template_id",
        ),
        "client_templates": (
            ("ref.enrollment_templates",),
            "SELECT CAST(template_id AS VARCHAR) AS value, "
            "       coalesce(template_name, CAST(template_id AS VARCHAR)) AS label, "
            "       concat_ws(' · ', CAST(template_id AS VARCHAR), template_type_desc) AS hint, "
            "       CAST(firm_id AS VARCHAR) AS parent "
            "FROM {enrollment_templates} "
            "WHERE template_id IS NOT NULL AND scope = 'client' "
            "ORDER BY template_name, template_id",
        ),
    },
    "seccl": {
        # Read from the CONFORMED layer, not `raw_nodes`: conforming Seccl renamed that model
        # (raw.nodes) and superseded it with platform.nodes, so this picker was quietly
        # returning nothing and the card fell back to manual id entry.
        #
        # `parent` is the node's immediate parent rather than its firm, which is what makes the
        # list cascade down a real hierarchy — lineage carries the whole ancestry, and the
        # depth rides in the hint so a long list reads as a tree.
        "nodes": (
            ("platform.nodes",),
            # Ordered and indented BY LINEAGE, so the list reads as the hierarchy it is: a
            # parent is immediately followed by its children. `lineage` is the root->leaf
            # ancestry, so sorting on it is a pre-order walk of the tree — but sorted as raw
            # strings '12' would precede '4', so each element is zero-padded for the sort only.
            #
            # The indent is non-breaking spaces rather than plain ones: a picker renders labels
            # in HTML, where leading whitespace collapses and the indent would vanish.
            "SELECT key AS value, "
            "repeat(chr(160) || chr(160), greatest(len(lineage) - 1, 0)) "
            "  || coalesce(name, key) AS label, "
            "coalesce(type_raw, '') AS hint, "
            "parent_key AS parent "
            "FROM {nodes} WHERE key IS NOT NULL "
            "ORDER BY list_transform(lineage, x -> lpad(x, 12, '0'))",
        ),
    },
}


def _options_sync(provider: str) -> dict[str, list[dict[str, str | None]]]:
    """Read a provider's option lists from the core warehouse (lake_reader, READ_ONLY).

    A relation that isn't promoted yet is returned as an empty list rather than an
    error: a provider can be configured before its models have ever run, and the
    card falls back to manual id entry in that case.
    """
    groups = _OPTION_SOURCES.get(provider)
    if not groups:
        return {}
    with _owned_core(provider) as conn:
        result: dict[str, list[dict[str, str | None]]] = {}
        for group, (models, template) in groups.items():
            rows: list[Any] = []
            for binding in _relation_bindings(models):
                try:
                    rows = conn.execute(template.format(**binding)).fetchall()
                    break
                except Exception:  # noqa: BLE001, S112 — try the next spelling
                    continue
            else:
                # Not promoted under any spelling: the models have not run yet.
                result[group] = []
                continue
            result[group] = [
                {
                    "value": str(value),
                    "label": str(label) if label is not None else str(value),
                    "hint": str(hint) if hint else None,
                    "parent": str(parent) if parent else None,
                }
                for value, label, hint, parent in rows
            ]
        return result


async def provider_options(
    provider: str, session: AsyncSession | None = None
) -> dict[str, list[dict[str, str | None]]]:
    """Selectable options for a provider's enablement card and the products-page
    enrolment-template picker.

    MORGAN LLOYD OFFERS NONE, and that is the honest answer (0133). Its slice key is the
    connection's Microsoft tenant id, which used to be registered separately in
    `platform.providers` so this could list them — but there was never anything to DISCOVER: a
    tenant id the sync has never seen is exactly the one being enabled, so the "options" were only
    ever the ids somebody had already typed into a different form. Registering it and enabling a
    firm on it are one step now, and the card falls back to manual entry, which is what it does
    for every list this function cannot fill.
    """
    if provider == "morgan-lloyd":
        return {"tenants": []}
    if provider not in _OPTION_SOURCES:
        return {}
    try:
        return await asyncio.to_thread(_options_sync, provider)
    except PlatformUnavailable:
        # No catalogue configured (a bare dev box): the card degrades to manual entry.
        return {group: [] for group in _OPTION_SOURCES[provider]}


# Which config key each provider's options answer for. `firm_id` is scalar and the rest are lists;
# `validate_config` has already established that, so this only has to say WHERE the values live.
_CONFIG_OPTION_GROUP: dict[str, dict[str, str]] = {
    "morgan-lloyd": {"tenant_ids": "tenants"},
    "seccl": {"node_ids": "nodes"},
    "sei": {"firm_id": "firms", "subfirm_ids": "subfirms", "dsr_ids": "dsr_teams"},
}


async def unknown_slice_ids(
    provider: str, config: dict[str, Any], session: AsyncSession | None = None
) -> dict[str, list[str]]:
    """Configured ids that match nothing the provider actually offers, per config key.

    `providers.validate_config` checks the SHAPE — the right keys, holding non-empty strings. It
    cannot check that `node_ids: ["12"]` names a node that exists, and a mistyped id saves clean,
    projects an empty slice, and reads as "this firm has no data with this custodian" — the
    failure mode the whole pipeline keeps producing. The values are knowable: the same options the
    enablement picker is populated from.

    AN EMPTY OPTION LIST IS NOT A REFUSAL, and this is the constraint that shapes the whole
    function. A picker whose model has not been promoted yet returns nothing and degrades to
    manual id entry — deliberately, because a provider can be configured before its models have
    ever run (docs/provider-pipeline.md §6a; SEI's DSR list read 0 of 503 for exactly this
    reason). Refusing then would block the legitimate first enablement of every new custodian, and
    could only be fixed by editing code. So an unavailable list yields no complaint at all.
    """
    groups = _CONFIG_OPTION_GROUP.get(provider, {})
    if not groups:
        return {}
    offered = await provider_options(provider, session)
    unknown: dict[str, list[str]] = {}
    for key, group in groups.items():
        values = config.get(key)
        if values is None:
            continue
        known = {str(o["value"]) for o in offered.get(group, []) if o.get("value") is not None}
        if not known:
            continue
        wanted = [str(v) for v in (values if isinstance(values, list | tuple) else [values])]
        missing = [v for v in wanted if v not in known]
        if missing:
            unknown[key] = missing
    return unknown


# ---- portals-DB registries ----


async def firm_enablements(session: AsyncSession, provider: str) -> list[dict[str, Any]]:
    """Every firm's enablement of this provider (any state), with the tenant's identity —
    the platform page's firms table."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT tp.tenant_id, t.uid, t.name, t.alias, tp.config, tp.enabled, "
                    "       tp.last_fanout_at "
                    "FROM platform.tenant_platforms tp "
                    "JOIN auth.tenants t ON t.id = tp.tenant_id "
                    "WHERE tp.provider = :p ORDER BY t.name"
                ),
                {"p": provider},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def connectable_tenants(session: AsyncSession, provider: str) -> list[dict[str, Any]]:
    """Firms with no enablement row for this provider yet — the "Add tenant" picker.

    `firm_enablements` only sees firms that already have a row, so a platform with nothing
    connected would offer nothing to connect. Excludes the model tenant, which is a template.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT t.id AS tenant_id, t.uid, t.name, t.alias "
                    "FROM auth.tenants t "
                    "WHERE t.enabled AND NOT COALESCE(t.is_model, false) "
                    "  AND NOT EXISTS (SELECT 1 FROM platform.tenant_platforms tp "
                    "                   WHERE tp.tenant_id = t.id AND tp.provider = :p) "
                    "ORDER BY t.name"
                ),
                {"p": provider},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def _cached_dashboard(provider: str) -> dict[str, Any]:
    """Headline warehouse figures, single-flight and TTL-cached. A lake outage returns empty
    metrics rather than failing the page."""
    cached = _warehouse_cache.get(provider)
    if cached and time.monotonic() - cached[0] < _WAREHOUSE_TTL_S:
        return cached[1]
    lock = _dashboard_locks.setdefault(provider, asyncio.Lock())
    async with lock:
        cached = _warehouse_cache.get(provider)
        if cached and time.monotonic() - cached[0] < _WAREHOUSE_TTL_S:
            return cached[1]
        try:
            reads = await asyncio.to_thread(_dashboard_reads_sync, provider)
        except Exception as exc:  # noqa: BLE001 — lake down must not blank the page
            logger.warning("platform warehouse read unavailable for %s: %s", provider, exc)
            return {"metrics": {}, "per_slice": {}}
        _remember_warehouse(provider, reads)
        return reads


async def _cached_catalogue_warehouse(provider: str, names: list[str]) -> dict[str, Any]:
    cached = _catalogue_cache.get(provider)
    if cached and time.monotonic() - cached[0] < _WAREHOUSE_TTL_S:
        return cached[1]
    lock = _catalogue_locks.setdefault(provider, asyncio.Lock())
    async with lock:
        cached = _catalogue_cache.get(provider)
        if cached and time.monotonic() - cached[0] < _WAREHOUSE_TTL_S:
            return cached[1]
        try:
            reads = await asyncio.to_thread(_warehouse_sync, provider, names)
        except Exception as exc:  # noqa: BLE001 — counts missing, the model list still renders
            logger.warning("platform catalogue counts unavailable for %s: %s", provider, exc)
            return {"counts": {}, "schemas": {}}
        _remember(_catalogue_cache, provider, reads)
        return reads


async def overview(session: AsyncSession, provider: str) -> dict[str, Any]:
    """What the platform overview paints: daemon lists, headline metrics, firm enablements.

    Per-model SQL, column lineage and row counts are `catalogue`. They are a request per model
    plus a scan per relation, and this page does not render them. The headline warehouse read
    starts immediately, overlapping the daemon, because it does not need the model list.
    """
    # The daemon read overlaps the DB reads, but the two DB reads must be sequential: an
    # AsyncSession allows only one in-flight operation at a time. The warehouse read starts
    # only when a daemon is configured — an unconfigured provider has nothing to attach, and
    # the registry test for that case must not open a catalogue.
    service_task = asyncio.ensure_future(_service_summary(provider))
    dashboard_task = (
        asyncio.ensure_future(_cached_dashboard(provider)) if service_url(provider) else None
    )
    try:
        firms = await firm_enablements(session, provider)
        candidates = await connectable_tenants(session, provider)
    except BaseException:
        service_task.cancel()
        if dashboard_task is not None:
            dashboard_task.cancel()
        raise
    service = await service_task
    reads = await dashboard_task if dashboard_task is not None else {"metrics": {}, "per_slice": {}}
    metrics = dict(reads.get("metrics") or {})
    per_slice = reads.get("per_slice") or {}
    # Per-firm figures come from the same grouped read, so adding firms costs no extra queries.
    firms = tenant_slice_metrics(firms, per_slice, provider)

    all_runs = service.get("all_runs", service.get("runs", []))
    schedules = [
        (m.get("schedule") or {}).get("cron", "")
        for m in service.get("models", [])
        if (m.get("schedule") or {}).get("cron")
    ]
    metrics.update(
        {
            # `firms` is the flat enablement list (any state); the card counts the live ones.
            "tenants": sum(1 for f in firms if f.get("enabled")),
            "runs_per_day": runs_per_day(all_runs),
            "last_run_at": max(
                (r["enqueued_at"] for r in all_runs if r.get("enqueued_at")), default=None
            ),
            "next_run_at": (
                fire.isoformat()
                if (fire := cron.earliest_next_fire(schedules, datetime.now(UTC)))
                else None
            ),
        }
    )

    return {
        "provider": provider,
        # How this custodian presents — name, role, description, transports, KPI labels. From
        # `providers.PROVIDER_META`, the same registry `sync_platforms` seeds the database from,
        # so a fourth custodian is one edit rather than seven.
        "meta": providers.meta(provider),
        "service": service,
        # Kept, empty. Row counts and column schemas moved to `catalogue` so opening a platform
        # does not scan every relation. The console still reads these keys.
        "counts": {},
        "schemas": {},
        # EMPTY SINCE 0133, and kept as a key rather than removed: `platform.providers` is
        # gone — a firm's connection is one `tenant_platforms` row, which is what `firms`
        # below already carries — and an absent key would have the console render
        # `undefined` where it renders an empty list today.
        "sources": [],
        "firms": firms,
        "candidates": candidates,
        "metrics": metrics,
    }


async def catalogue(provider: str) -> dict[str, Any]:
    """Column lineage, SQL, and warehouse row counts — what Models and Lineage render.

    The model list is the overview's summary (already cached when the operator just opened
    the platform). Detail fetches and the count scan then run together: neither needs the
    other's answer, and doing them in series was the slow half of opening a platform.
    """
    summary = await _service_summary(provider)
    if summary["status"] != "up":
        return {
            "status": "down",
            "error": summary.get("error", ""),
            "models": [],
            "counts": {},
            "schemas": {},
        }
    models = [dict(model) for model in summary["models"]]
    names = [str(model["name"]) for model in models]
    gathered = await asyncio.gather(
        _attach_model_details(provider, models),
        _cached_catalogue_warehouse(provider, names),
    )
    warehouse = gathered[1]
    return {
        "status": "up",
        "models": [
            {"name": model["name"], "columns": model.get("columns") or {}, "sql": model.get("sql")}
            for model in models
        ],
        "counts": warehouse["counts"],
        "schemas": warehouse["schemas"],
    }


async def index(session: AsyncSession) -> list[dict[str, Any]]:
    """The /platforms index card data: who each provider is, is its daemon up, how many firms.

    IT CARRIES ITS OWN METADATA NOW. The index used to render from a checked-in fixture module
    (`lib/platforms/data/*.ts`) whose header said "no API yet": every card's description and both
    its headline KPIs were invented, sitting directly beneath a live green status badge — Seccl
    read "Webhook events today 12.4k", SEI "Rows loaded 1.2M / Quarantined 12", Morgan Lloyd
    "Outbound queue depth 7". Nothing marked them as concept data.

    What it deliberately does NOT carry is a headline figure per provider. Row counts come from the
    warehouse, which is a per-provider DuckDB attach — three of them, on an index page, to show two
    numbers each. `enabled_firms` is a cheap, true fact from this database. Client count and AUM
    are the overview; per-model counts are the catalogue request, after that page is on screen.
    """

    async def health(provider: str) -> str:
        base = service_url(provider)
        if not base:
            return "unconfigured"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                body = await _service_get(base, "/health", client)
            return _service_liveness(body)
        except httpx.HTTPError:
            return "down"

    counts_result = await session.execute(
        text(
            "SELECT provider, count(*) FROM platform.tenant_platforms "
            "WHERE enabled GROUP BY provider"
        )
    )
    enabled: dict[str, int] = {str(p): int(c) for p, c in counts_result.all()}
    statuses = await asyncio.gather(*(health(p) for p in providers.PROVIDERS))
    return [
        {
            "provider": p,
            "meta": providers.meta(p),
            "service": s,
            "enabled_firms": int(enabled.get(p, 0)),
        }
        for p, s in zip(providers.PROVIDERS, statuses, strict=True)
    ]


# --- the outgoing API-call log -------------------------------------------------------------
#
# Admin > Platforms > API. Reads audit.api_log, which every vendor client writes to from its
# single HTTP choke point (docs/provider-routing.md). Read-only by construction: the table
# grants SELECT and INSERT only.

_CALL_COLUMNS = (
    # `uid` is what the console opens a call by; `id` stays for the sort order and the paging
    # cursor, which is what a bigint on an append-only log is good at.
    "id, uid, created_at, tenant_id, user_id, source, provider, service, transport, method, url, "
    "operation, status_code, ok, error, duration_ms, entity_type, entity_id"
)

# Guarded rather than passed through: `source` is interpolated into no SQL (it is a bound
# parameter like every other filter), but an unknown value would silently match nothing and read
# as "the sync made no calls", which is the wrong answer to show an operator during an incident.
_SOURCES = ("app", "sync")


async def api_calls(
    session: AsyncSession,
    *,
    provider: str | Sequence[str] | None = None,
    ok: bool | None = None,
    tenant_id: int | None = None,
    operation: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """A page of outgoing calls, newest first, with the total for paging.

    Bodies are NOT in the list projection. A request body can be a whole CTC1 XML document or a
    1,600-row provider register; sending them for 50 rows to render a table would make the
    response megabytes for data nobody reads until they open one. `api_call` fetches them.
    """
    where = ["true"]
    params: dict[str, Any] = {"limit": max(1, min(limit, 200)), "offset": max(0, offset)}
    chosen_providers = clean_many(provider)
    if chosen_providers:
        where.append("provider = ANY(CAST(:provider AS text[]))")
        params["provider"] = chosen_providers
    if ok is not None:
        where.append("ok = :ok")
        params["ok"] = ok
    if tenant_id is not None:
        where.append("tenant_id = :tenant_id")
        params["tenant_id"] = tenant_id
    if operation:
        where.append("operation = :operation")
        params["operation"] = operation
    if source:
        if source not in _SOURCES:
            raise ValueError(f"unknown source {source!r}; expected one of {', '.join(_SOURCES)}")
        where.append("source = :source")
        params["source"] = source
    if q:
        where.append("(url ILIKE :q OR operation ILIKE :q OR error ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    clause = " AND ".join(where)

    total = (
        await session.execute(
            text(f"SELECT count(*) FROM audit.api_log WHERE {clause}"),  # noqa: S608
            params,
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                # S608: interpolation is `_CALL_COLUMNS` (a module constant) and `clause`
                # (built above only from fixed literals -- every user value is a bound
                # parameter). No caller input reaches the SQL text.
                text(  # noqa: S608
                    f"SELECT {_CALL_COLUMNS} FROM audit.api_log WHERE {clause} "  # noqa: S608
                    "ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return {"items": [_with_provider_name(r) for r in rows], "total": int(total)}


async def api_call(session: AsyncSession, call_uid: str) -> dict[str, Any] | None:
    """One call including its request and response bodies, addressed by its uid (0114)."""
    row = (
        (
            await session.execute(
                text(
                    f"SELECT {_CALL_COLUMNS}, request, response "  # noqa: S608 - module constant
                    "FROM audit.api_log WHERE uid = :uid"
                ),
                {"uid": call_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    return _with_provider_name(row) if row else None


async def api_call_summary(session: AsyncSession) -> list[dict[str, Any]]:
    """Per provider/service/source: volume, failures and latency over the last 24 hours.

    The filter strip needs counts to be useful, and "how many failed" is the question the page
    exists to answer. p95 rather than a mean: one 30-second timeout hides in a mean of hundreds.

    Grouped by `source` as well, because the two halves have completely different shapes -- a
    nightly sync is thousands of calls at machine latency, an operator's action is a handful --
    and pooling them makes both figures meaningless: the sync's volume buries the app's, while
    the app's failures vanish into the sync's p95.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT provider, service, source, count(*) AS calls, "
                    "       count(*) FILTER (WHERE NOT ok) AS failures, "
                    "       cast(percentile_disc(0.95) WITHIN GROUP (ORDER BY duration_ms) AS int) "
                    "         AS p95_ms, "
                    "       max(created_at) AS last_call_at "
                    "  FROM audit.api_log "
                    " WHERE created_at > now() - interval '24 hours' "
                    " GROUP BY provider, service, source ORDER BY provider, service, source"
                )
            )
        )
        .mappings()
        .all()
    )
    return [_with_provider_name(r) for r in rows]


def _with_provider_name(row: Any) -> dict[str, Any]:
    """An api_log row with the provider's display name, so the console prints "SEI", not `sei`."""
    return {**dict(row), "provider_name": providers.meta(str(row["provider"]))["name"]}
