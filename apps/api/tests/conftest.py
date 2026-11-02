"""Shared test fixtures.

The RLS tests need a real Postgres 18 (proving isolation can't be done against a mock),
so we spin an ephemeral container via testcontainers, bootstrap the roles as a superuser,
and run the migrations as the `migrator` role -- exactly the production split. Tests that
exercise RLS connect as the `app` role and `SET ROLE authenticated`, mirroring the request
path. If Docker is unavailable the DB-backed tests FAIL rather than skip -- a green gate that
silently omitted the tenant-isolation proof is worse than a red one. Set
ALLOW_SKIP_DB_TESTS=1 to skip locally without Docker; CI must never set it.
"""

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import Request

from app.ids import ALPHABET, ID_LENGTH

# On Windows the default ProactorEventLoop can't drive psycopg's async connection (see
# app/__main__). The DB fixtures run one-off async coroutines via asyncio.run, so pin the
# selector loop here.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# BELOW the event-loop pin above, not with the top imports: this reaches `app.db` and so pulls
# in sqlalchemy + psycopg, and the pin exists because the DB fixtures run one-off coroutines
# through `asyncio.run` — importing the async stack on the wrong side of it is the Windows
# ProactorEventLoop trap this repo has already lost time to.
from app.comms import ledger as _comms_ledger

API_ROOT = Path(__file__).resolve().parent.parent  # apps/api

# THERE IS NO PLATFORM ROW TO MINT ANY MORE (0131).
#
# `platform.platforms` held one row per provider and existed to be a foreign-key target; the
# columns that pointed at it now hold `platform.provider_code` directly, so a fixture that needed
# "the seccl platform id" needs the string `seccl`. This constant survives as that string so the
# ~20 call sites read the same way, and because the SELECT keeps their `.scalar_one()` shape
# working -- a statement rather than a literal, since the fixtures execute it.
#
# It is a SELECT and no longer an INSERT, which is the point: the vocabulary is closed and lives
# in `app/providers.py::PROVIDERS`, so there is nothing for a test to seed and nothing for it to
# forget to seed. Call sites that still pass an unused `n` are harmless -- text() binds only what
# the statement names.
SECCL_PLATFORM_SQL = "SELECT 'seccl'::platform.provider_code"

# The isolation proof must not be able to go missing behind a green gate.
_ALLOW_SKIP = os.environ.get("ALLOW_SKIP_DB_TESTS") == "1"


@pytest.fixture(autouse=True)
def _no_live_postcoder(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key in ``.env.local`` must not send a fixture bank account to Postcoder.

    A blank key is the product's own skip: the directory is not asked, and the save proceeds.
    A test that wants the directory sets the key itself.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "postcoder_api_key", "")


def assert_public_id(value: object) -> None:
    """Assert a value is a platform public id: 12 characters of app/ids.py's alphabet, no prefix.

    Every uid assertion in the suite goes through here so the shape is stated ONCE -- these used to
    be `startswith("con_")` checks, which passed for any string wearing the right prefix (including
    another tenant's id) and said nothing about the rest of it.
    """
    assert isinstance(value, str), f"not a public id: {value!r}"
    assert len(value) == ID_LENGTH, f"public id should be {ID_LENGTH} chars: {value!r}"
    assert set(value) <= set(ALPHABET), f"public id has characters outside the alphabet: {value!r}"


def _unavailable(reason: str) -> None:
    if _ALLOW_SKIP:
        pytest.skip(reason)
    pytest.fail(f"{reason} -- set ALLOW_SKIP_DB_TESTS=1 to skip locally without Docker")


# Captured at import, before `_comms_ledger_silenced` replaces it. Tests that assert the
# Activity feed (or any other reader of `comms.messages`) opt in via `live_comms_ledger`.
_REAL_LEDGER_RECORD = _comms_ledger.record


@pytest.fixture(autouse=True)
def _comms_ledger_silenced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the outbound-email ledger out of the developer's own database.

    ``app.comms.ledger.record`` deliberately opens its OWN ``system_transaction`` so a ledger row
    survives the rollback of a caller whose mail has already gone (see its module header). The
    consequence for tests is the trap ``documents/service.py::queue_client_email`` already
    documents: that connection comes from the module-level ``app.db.SessionLocal``, i.e.
    ``settings.database_url`` — the DEV database — and not from the testcontainer the rest of the
    suite runs against. So every test that drove ``send_email`` was writing real rows into
    whatever database the developer happened to be pointing at, where they then showed up in the
    Activity feed as invites to `a.real.client@someadviserfirm.co.uk`.

    Silenced for the whole suite rather than patched in the nine modules that happen to send
    today: the hazard belongs to the writer, not to its callers, and a new test that sends must not
    have to know this. ``test_comms_ledger.py`` overrides it where the recording is the thing under
    test, and ``test_comms_rls.py`` / ``test_comms_stream.py`` insert their rows directly. Tests
    that need the real writer pointed at the testcontainer request ``live_comms_ledger``.
    """

    async def _no_write(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(_comms_ledger, "record", _no_write)


@pytest.fixture(autouse=True)
def _outbound_email_allowlist_is_the_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Do not let a send read the developer's saved allowlist.

    ``refresh_allowlist`` opens ``system_transaction``, which uses the process-wide engine — the
    database in ``settings.database_url``, not the testcontainer. A test that sends mail would
    otherwise adopt whatever list the developer saved in admin, and a failed read would touch that
    database at all. The env default is what these tests are about. A test that wants a saved list
    calls ``set_runtime_allowlist`` and never the database.
    """
    from app.email import transport as email_mod

    email_mod.clear_runtime_allowlist()

    async def _no_refresh() -> None:
        return None

    monkeypatch.setattr(email_mod, "refresh_allowlist", _no_refresh)
    yield
    email_mod.clear_runtime_allowlist()


@pytest_asyncio.fixture
async def live_comms_ledger(
    db: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    """Opt-in: ``ledger.record`` writes into the testcontainer, not the developer's Activity feed.

    ``record`` opens ``app.db.system_transaction``, which uses the process-wide ``SessionLocal``
    bound to ``settings.database_url``. Point that maker at the same Postgres the rest of the
    suite uses, then restore the real writer the autouse fixture silenced.
    """
    from app import db as db_mod

    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "SessionLocal", maker)
    monkeypatch.setattr(_comms_ledger, "record", _REAL_LEDGER_RECORD)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
def _resend_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite off the real Resend API, whatever the developer's ``.env.local`` says.

    The non-production guard (``EMAIL_ALLOWLIST``) is the deployment's control and NOT a test
    harness. It ships on, but a checkout that sets ``EMAIL_NONPROD_GUARD=false`` — or a recipient
    on the default ``@grapheneplatforms.com`` list — still leaves a REAL ``RESEND_API_KEY`` as the
    only thing between ``send_email`` and ``api.resend.com``. Observed, while the guard was opt-in:
    running the suite with ``EMAIL_NONPROD_GUARD=false`` posted invites for fixture addresses to the
    live API, which Resend ACCEPTED (they bounce afterwards, against our own sending domain's
    reputation). The tell was `test_admin_offboarding` failing on ``email_delivered is False``,
    which is that test noticing the mail had genuinely gone.

    So the credential is blanked here, the same hermeticity ``_storage_unconfigured`` gives S3 and
    the lake, and for the same reason: a test must not depend on which secrets happen to be on the
    machine. Tests that need a configured sender set the field themselves (``test_email_send_guard``
    monkeypatches ``re_test_key`` and stubs the transport) — an autouse fixture runs first, so
    their own value still wins.

    THE WEBHOOK SECRET IS BLANKED TOO, and that is about behaviour rather than about reaching the
    network. ``comms.poll`` reads it to decide whether to leave a new message to the webhook for
    ``WEBHOOK_GRACE_SECONDS`` before speaking for it, so with a developer's real secret present
    every poll test inserts a row the scan then correctly declines to look at. Which happened:
    ``Settings.model_config`` names ``.env``/``.env.local`` by RELATIVE path, so the suite loads
    them when run from ``apps/api`` — which is exactly what ``moon run api:test`` does — and not
    when run from the workspace root. Eight poll tests passed one way and failed the other with
    nothing in the diff to explain it. A test that needs the grace switched on sets the secret
    itself, as ``test_comms_poll`` does.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "resend_api_key", "")
    monkeypatch.setattr(settings, "resend_webhook_secret", "")
    # Same hermeticity for identity and screening. `_idv_provider` reads the
    # ComplyCube key at seed time and, when it is set, publish verify_identity to the client. A
    # developer's `.env.local` then makes the staff walkthrough fail (`the manual route raised its
    # documents`) while CI, which has no key, passes. The same key would turn the worker's event
    # loop and the ops routes on against the real provider. Tests that need a configured client
    # set the key themselves.
    monkeypatch.setattr(settings, "complycube_api_key", "")
    monkeypatch.setattr(settings, "complycube_webhook_secret", "")
    monkeypatch.setattr(settings, "complycube_allow_live_key", False)


@pytest.fixture(autouse=True)
def _reset_core_catalogue_pool() -> Iterator[None]:
    """A pooled core connection must not leak from one test into the next."""
    from app.lake import core as lake_core

    lake_core.reset_pool()
    yield
    lake_core.reset_pool()


@pytest.fixture(autouse=True)
def _storage_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic: settings read .env/.env.local, so a developer's local S3 creds
    would otherwise make provision_tenant reach a real object store mid-test (the same
    trap as the seccl .env webhook secret). Tests that need storage configured set the
    fields they need explicitly."""
    from app.config import settings

    for field in (
        "s3_endpoint_url",
        "s3_bucket",
        "s3_access_key_id",
        "s3_secret_access_key",
        "s3_public_base_url",
        "s3_acl",
        "s3_kind",
        "datalake_database_url",
        "datalake_catalog_dsn",
        "datalake_oltp_dsn",
        "interlace_morgan_lloyd_url",
        "interlace_seccl_url",
    ):
        monkeypatch.setattr(settings, field, "")


def _url(container: object, user: str, password: str) -> str:
    host = container.get_container_host_ip()  # type: ignore[attr-defined]
    port = container.get_exposed_port(5432)  # type: ignore[attr-defined]
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/portals"


@pytest.fixture(scope="session")
def _pg() -> Iterator[object]:
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError as exc:  # pragma: no cover
        _unavailable(f"testcontainers not installed: {exc}")

    container = PostgresContainer(
        "postgres:18",
        username="postgres",
        password="postgres",  # noqa: S106 - ephemeral throwaway test container
        dbname="portals",
    )
    try:
        container.start()
    except Exception as exc:  # pragma: no cover - Docker not available in this env
        _unavailable(f"Postgres testcontainer unavailable (Docker not running?): {exc}")
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def db(_pg: object) -> dict[str, str]:
    """Bootstrap roles (as superuser) + apply migrations (as migrator). Returns the
    per-role connection URLs."""
    super_url = _url(_pg, "postgres", "postgres")
    migrator_url = _url(_pg, "migrator", "migrator")
    app_url = _url(_pg, "app", "app")

    # 1) Superuser-tier bootstrap (roles + migrator grants), exactly as an environment
    # runs it. AUTOCOMMIT so CREATE ROLE/GRANT don't wrap. 03-datalakes.sql is skipped:
    # it uses psql meta-commands (\gexec/\connect) and the suite doesn't need the
    # datalakes DB (lake tests that want its roles create them).
    bootstrap = API_ROOT / "migrations" / "bootstrap"
    su = create_engine(super_url, isolation_level="AUTOCOMMIT")
    with su.connect() as conn:
        # encoding is explicit because these files carry UTF-8 multi-byte characters and the
        # Path.read_text default is the PLATFORM encoding -- cp1252 on Windows -- under which the
        # em dashes decode to mojibake and an undefined byte raises outright, so every DB-backed
        # test errored at fixture setup. Invisible on Linux CI, which is why it survived.
        conn.exec_driver_sql((bootstrap / "01-roles.sql").read_text(encoding="utf-8"))
        conn.exec_driver_sql((bootstrap / "02-grants.sql").read_text(encoding="utf-8"))
    su.dispose()

    # 2) Migrations, as the elevated migrator role (URL injected into env.py).
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", migrator_url)
    command.upgrade(cfg, "head")

    # 3) Sync the canonical permission catalogue (app/permissions.py) — the app does this on
    # startup; tests need it present too (it's no longer seeded in SQL). The reference
    # catalogues are loaded the same way the API lifespan loads them, from THIS database.
    # Left empty, the first request opens system_transaction() against the process DSN,
    # which in CI is localhost and is not this container.
    from app import countries, currencies
    from app.permissions import sync_permissions

    async def _seed_permissions() -> None:
        currencies.clear()
        countries.clear()
        engine = create_async_engine(app_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            await sync_permissions(session)
            await currencies.load(session)
            await countries.load(session)
        await engine.dispose()

    asyncio.run(_seed_permissions())

    return {"super": super_url, "app": app_url, "migrator": migrator_url}


@pytest.fixture
def super_engine(db: dict[str, str]) -> Iterator[Engine]:
    """Superuser engine -- bypasses RLS; used only to seed test data."""
    engine = create_engine(db["super"], isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture
def app_engine(db: dict[str, str]) -> Iterator[Engine]:
    """App-role engine -- the RLS-bound request role."""
    engine = create_engine(db["app"])
    yield engine
    engine.dispose()


@pytest_asyncio.fixture
async def service_session(db: dict[str, str]) -> "AsyncSession":
    """An async session acting as service_role (BYPASSRLS) -- mirrors the admin_session
    provisioning path. Used to exercise app.provisioning end-to-end. No truncate: the
    provisioning tests use unique aliases/emails and tenant-scoped assertions, so they are
    order-independent and must not wipe the seeded role templates (a CASCADE from
    auth.tenants would)."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session
    await engine.dispose()


@contextmanager
def principal_conn(app_engine: Engine, claims: dict[str, object]) -> Iterator[Connection]:
    """A connection acting as a request principal: `SET ROLE authenticated` + the given
    pre-resolved claims published to `request.jwt.claims` (mirrors db.py::tenant_session)."""
    with app_engine.connect() as conn:
        conn.execute(text("SET ROLE authenticated"))
        conn.execute(
            text("SELECT set_config('request.jwt.claims', :c, false)"),
            {"c": json.dumps(claims)},
        )
        yield conn


def fake_request() -> Request:
    """A minimal real `Request`, for tests that call `app.db`'s session dependencies
    (`tenant_session`/`firm_session`/`admin_session`/`book_session`) directly rather than through
    FastAPI's own injection -- they now require one (`resolve_claims_cached` caches on
    `request.state`). A fresh call each time, since the cache is deliberately per-request: reusing
    one `Request` object across calls in a test would accidentally exercise the cache-HIT path
    when the test means to exercise two independent requests."""
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def api_routes(app: object) -> list[Any]:
    """Every `APIRoute` the app serves, flattened.

    `app.routes` STOPPED being a flat list of routes in FastAPI 0.141: `include_router` no longer
    copies a router's routes onto the app, it appends one opaque wrapper per included router (28 of
    them here, beside 4 plain `Route`s). Nine test modules introspected the flat list — to prove a
    router is mounted, that a trigger resolves to a live route, that a mutating route authenticates
    — and every one of them silently found nothing rather than failing loudly, because "no route
    matched" and "the route is not registered" look identical from the outside.

    The app itself was never affected: `/search` still answers 401 and `/openapi.json` still lists
    279 paths. Only the introspection was.

    Written STRUCTURALLY rather than against the wrapper's name (`_IncludedRouter`, private and
    version-specific): anything exposing a nested router is descended into, and only `APIRoute`s
    come back. That holds for the old flat shape too, so it need not know which version it is on.
    """
    from fastapi.routing import APIRoute

    found: list[Any] = []

    def walk(routes: Iterable[Any], depth: int = 0) -> None:
        if depth > 10:  # a cycle would otherwise hang the suite rather than fail it
            return
        for route in routes:
            if isinstance(route, APIRoute):
                found.append(route)
            elif nested := getattr(route, "original_router", None):
                walk(getattr(nested, "routes", ()), depth + 1)
            elif inner := getattr(route, "routes", None):
                walk(inner, depth + 1)

    walk(getattr(app, "routes", ()))
    return found


@pytest.fixture(scope="session")
def _baton_truncate_engine(db):
    """A superuser engine (bypasses RLS/grants) used to wipe baton state between tests."""
    eng = create_engine(db["super"], isolation_level="AUTOCOMMIT")
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _baton_live_on_testcontainer(db, monkeypatch, _baton_truncate_engine):
    """Baton is the live actions engine; bind its process-wide engine to the testcontainer so the
    doors and the worker act on the test database (not the dev DSN ``app.db`` defaults to).

    The testcontainer DB is session-scoped and shared, so a prior test's undrained SCHEDULED runs
    would be claimed by this test's worker drain (the worker scans the whole DB, not one tenant).
    Wipe baton state first so each test's drain only sees its own runs."""
    from app.actions import baton_engine

    with _baton_truncate_engine.connect() as conn:
        conn.exec_driver_sql(
            "TRUNCATE baton.runs, baton.attempts, baton.events, baton.subjects "
            "RESTART IDENTITY CASCADE"
        )
    eng = create_async_engine(db["app"])
    monkeypatch.setattr(baton_engine, "_db_engine", eng)
    baton_engine.get_engine.cache_clear()
    yield
    baton_engine.get_engine.cache_clear()


@pytest.fixture
def clear_io_plan_links(super_engine: Engine) -> None:
    """Delete every Intelliflo account link before a test.

    An Intelliflo plan can be linked to ONE account across every tenant (0278,
    `links_intelliflo_plan_uq`), and the tests that exercise plan links share one database and
    reuse plan ids (`p1`, `PLAN-...`). A link an earlier test left behind would refuse the next,
    so those modules use this.
    """
    with super_engine.connect() as c:
        c.execute(
            text(
                "DELETE FROM platform.links "
                "WHERE provider = 'intelliflo' AND entity_type = 'account'"
            )
        )
