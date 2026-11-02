"""The persist half of the outgoing-call audit.

Written against a real Postgres because the properties that matter are the table's, not
Python's: append-only enforced by the grant, an INSERT that cannot be blocked by tenant scoping,
and a row that survives a body full of things `json` cannot encode.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from api_log import ApiCall
from sqlalchemy import Engine, text

from app.api_log import ApiLog, _as_json
from tests.conftest import api_routes


@pytest.fixture
def audit_factory(db: dict[str, str]):  # type: ignore[no-untyped-def]
    """An ApiLog writing to the test container rather than the app's global engine."""
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # The APP role with SET LOCAL ROLE service_role -- exactly what app.db.system_transaction
    # does in production. Using the superuser here would bypass grants and hide a missing one:
    # `service_role` was left off audit.api_log, every INSERT failed, and the drain's error
    # handler swallowed it. A test on the superuser engine passed throughout.
    url = db["app"].replace("postgresql://", "postgresql+psycopg://")
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def factory():  # type: ignore[no-untyped-def]
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            yield session

    yield lambda: ApiLog(session_factory=factory)
    asyncio.run(engine.dispose())


class TestSerialisation:
    """`_as_json` is the only thing between a vendor payload and a failed INSERT."""

    def test_a_decimal_or_date_does_not_lose_the_row(self) -> None:
        # A money field is a Decimal and a start date is a date; neither is JSON-encodable.
        # Losing the evidence to a TypeError would be the worst possible trade.
        out = _as_json({"amount": Decimal("185000.00"), "start": date(2026, 8, 3)})

        assert out is not None
        assert "185000.00" in out
        assert "2026-08-03" in out

    def test_a_scalar_body_is_wrapped(self) -> None:
        # jsonb accepts a bare scalar, but querying one is awkward and inconsistent with every
        # other row, so a non-object body gets a key.
        assert _as_json("plain text") == '{"value": "plain text"}'

    def test_an_unserialisable_body_still_produces_a_row(self) -> None:
        class Opaque:
            def __repr__(self) -> str:
                raise RuntimeError("even repr fails")

        # `default=str` handles most things; this proves the last-resort path keeps a row.
        out = _as_json({"weird": object()})
        assert out is not None and "weird" in out

    def test_none_stays_none(self) -> None:
        assert _as_json(None) is None


def _call(**kw: object) -> ApiCall:
    base: dict[str, object] = {
        "provider": "seccl",
        "method": "POST",
        "url": "https://api.test/portfoliotransactiongroup",
        "operation": "portfoliotransactiongroup",
        "ok": True,
        "status_code": 200,
        "request": {"transferValue": 1000},
        "response": {"id": "abc"},
        "duration_ms": 12,
    }
    base.update(kw)
    return ApiCall(**base)  # type: ignore[arg-type]


class TestTable:
    def test_a_call_is_written_and_readable(self, super_engine: Engine, audit_factory) -> None:  # type: ignore[no-untyped-def]
        audit = audit_factory()

        async def run() -> None:
            await audit.start()
            audit.recorder()(_call())
            await audit.stop()

        asyncio.run(run())

        with super_engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT provider, operation, ok, status_code, request, response "
                        "FROM audit.api_log ORDER BY id DESC LIMIT 1"
                    )
                )
                .mappings()
                .one()
            )
        assert row["provider"] == "seccl"
        assert row["operation"] == "portfoliotransactiongroup"
        assert row["ok"] is True
        assert row["request"] == {"transferValue": 1000}

    def test_a_failed_call_with_no_response_is_still_recorded(  # type: ignore[no-untyped-def]
        self, super_engine: Engine, audit_factory
    ) -> None:
        # The row an incident is reconstructed from: no status, no body, a reason.
        audit = audit_factory()

        async def run() -> None:
            await audit.start()
            audit.recorder()(
                _call(ok=False, status_code=None, response=None, error="ConnectError: no route")
            )
            await audit.stop()

        asyncio.run(run())

        with super_engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT ok, status_code, response, error FROM audit.api_log "
                        "ORDER BY id DESC LIMIT 1"
                    )
                )
                .mappings()
                .one()
            )
        assert row["ok"] is False
        assert row["status_code"] is None
        assert row["response"] is None
        assert "no route" in row["error"]

    def test_the_table_is_append_only_for_the_request_role(self, super_engine: Engine) -> None:
        """Evidence that can be edited is not evidence. Enforced by the grant, not a trigger.

        Asserted on the GRANT rather than by attempting an UPDATE as `authenticated`. The `app`
        login is a member of both `authenticated` and `service_role`, and `service_role` holds
        ALL on this table, so privilege inheritance makes a runtime `SET ROLE` check unreliable
        -- it passed before service_role was correctly granted and stopped passing after, while
        the property being tested never changed. The catalogue is the unambiguous statement.
        """
        with super_engine.connect() as conn:
            granted = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT privilege_type FROM information_schema.role_table_grants "
                        " WHERE table_schema = 'audit' AND table_name = 'api_log' "
                        "   AND grantee = 'authenticated'"
                    )
                )
            }

        assert granted == {"SELECT", "INSERT"}, f"authenticated should not hold {granted}"

    def test_an_insert_is_not_blocked_by_tenant_scoping(self, app_engine: Engine) -> None:
        """The INSERT policy is WITH CHECK (true) on purpose.

        A sync worker runs with no claims at all, and a cross-tenant admin action's affected
        tenant differs from the actor's. Evidence-writing must not fail for either reason.
        """
        with app_engine.connect() as conn:
            conn.execute(text("SET ROLE authenticated"))
            # No request.jwt.claims set: tenant_lineage() is empty.
            # tenant_id NULL, which is the real case for a platform-wide call (a token refresh,
            # a reference-data pull). With no claims set, tenant_lineage() is empty, so a
            # scoping-restrictive INSERT policy would reject even this.
            conn.execute(
                text(
                    "INSERT INTO audit.api_log (tenant_id, provider, ok) "
                    "VALUES (NULL, 'sei', false)"
                )
            )
            conn.commit()


class TestSource:
    """`source` — which half of the platform made the call (0073).

    The column exists because an adviser's action and a 3am cron pull were indistinguishable,
    and it cannot be recovered after the fact: both halves call the same operations, and a null
    `user_id` means "a sync" and "a token refresh on the request path" equally.
    """

    def test_it_defaults_to_app(self, app_engine: Engine) -> None:
        """A row written by anything that does not state a source is the request path — which is
        what every pre-0073 row would have been, had any ever been written."""
        with app_engine.connect() as conn:
            conn.execute(text("SET ROLE service_role"))
            source = conn.execute(
                text(
                    "INSERT INTO audit.api_log (provider, ok) VALUES ('sei', true) RETURNING source"
                )
            ).scalar_one()
            conn.rollback()
        assert source == "app"

    def test_an_unknown_source_is_refused(self, app_engine: Engine) -> None:
        """A typo must not become a third, invisible category that no filter ever selects."""
        from sqlalchemy.exc import IntegrityError

        with app_engine.connect() as conn:
            conn.execute(text("SET ROLE service_role"))
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO audit.api_log (provider, ok, source) "
                        "VALUES ('sei', true, 'daemon')"
                    )
                )
            conn.rollback()

    def test_the_check_matches_the_python_literal(self) -> None:
        """`api_log.Source` and the CHECK are two spellings of one vocabulary. A value added to
        one and not the other fails at INSERT, in a swallowed background drain."""
        from typing import get_args

        from api_log import Source

        assert set(get_args(Source)) == {"app", "sync"}

    def test_book_writer_can_write_but_only_append(self, super_engine: Engine) -> None:
        """The sync daemons write their own rows as `book_writer` (0062's narrow login).

        Asserted on the catalogue for the same reason the `authenticated` grant is: privilege
        inheritance makes a runtime attempt an unreliable statement of what was granted.
        """
        with super_engine.connect() as conn:
            granted = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT privilege_type FROM information_schema.role_table_grants "
                        " WHERE table_schema = 'audit' AND table_name = 'api_log' "
                        "   AND grantee = 'book_writer'"
                    )
                )
            }
        assert granted == {"INSERT"}, f"book_writer should hold INSERT alone, not {granted}"

    def test_book_writer_can_actually_insert(self, super_engine: Engine) -> None:
        """The grant read back from the catalogue is not the same claim as the INSERT working.

        This is the test that earns its place: the table grant alone was not enough, because
        0062 never gave book_writer USAGE on the `audit` schema, so every insert failed with
        "permission denied for schema audit" -- into a drain that swallows and logs. Exactly the
        shape of 0047's missing service_role grant, which produced an audit trail that recorded
        its own failure and stayed empty.

        On the SUPERUSER engine because it is the only one that can `SET ROLE book_writer`: the
        `app` login is not a member of it, which is the isolation 0062 was built for. The role
        is still exercised for real -- SET ROLE drops the superuser's own rights for the
        statement, so a missing grant fails here exactly as it would in the daemon.
        """
        with super_engine.connect() as conn:
            conn.execute(text("SET ROLE book_writer"))
            # No RETURNING, and that is a rule rather than a style choice: RETURNING requires
            # SELECT on the columns it names, which book_writer deliberately does not hold. A
            # recorder written with `RETURNING id` would fail every insert. `book_sink.api_log`
            # uses a plain executemany for this reason.
            # A distinctive operation, because the container is shared across this module and
            # a plain `provider = 'seccl'` would also match other tests' rows.
            conn.execute(
                text(
                    "INSERT INTO audit.api_log (provider, transport, ok, source, operation) "
                    "VALUES ('seccl', 'http', true, 'sync', 'book_writer_grant_probe')"
                )
            )
            conn.execute(text("RESET ROLE"))
            written = conn.execute(
                text("SELECT source FROM audit.api_log WHERE operation = 'book_writer_grant_probe'")
            ).scalar_one()
            conn.rollback()
        # It landed, so the identity sequence advanced with no sequence grant either --
        # GENERATED AS IDENTITY is advanced internally, where a `serial` would have needed one.
        assert written == "sync"


class TestAdminRoutes:
    """The API log's routes, and the ordering trap they sit in."""

    def test_api_log_is_not_shadowed_by_the_provider_route(self) -> None:
        """`/admin/platforms/api-calls` must not resolve as provider='api-calls'.

        FastAPI matches in declaration order, so a literal segment that could also read as a
        `{provider}` value has to be declared first. Reordering the module silently turns this
        endpoint into a platform overview for a platform called "api-calls".
        """
        from app.main import app

        paths = [
            r.path for r in api_routes(app) if getattr(r, "path", "").startswith("/admin/platforms")
        ]
        literal = paths.index("/admin/platforms/api-calls")
        templated = paths.index("/admin/platforms/{provider}/overview")

        assert literal < templated

    def test_the_three_log_routes_exist(self) -> None:
        from app.main import app

        paths = {getattr(r, "path", "") for r in api_routes(app)}
        assert "/admin/platforms/api-calls" in paths
        assert "/admin/platforms/api-calls/summary" in paths
        # `{call_uid}`, not `{call_id}` (0114_api_log_uid): a call is opened by its uid, and the
        # ordering assertion above is what keeps `summary` reachable now that the dynamic segment
        # is a string rather than an int that would 422 on it.
        assert "/admin/platforms/api-calls/{call_uid}" in paths


class TestQuery:
    """Filtering, and the one projection decision that matters."""

    def _seed(self, super_engine: Engine) -> None:
        with super_engine.connect() as conn:
            # The container is session-scoped, so earlier tests have already written rows.
            # Totals are the assertion here, so start from a known table. Superuser can delete;
            # the append-only grant restricts `authenticated`, which is the role that matters.
            conn.execute(text("DELETE FROM audit.api_log"))
            conn.execute(
                text(
                    "INSERT INTO audit.api_log "
                    "  (provider, service, operation, ok, status_code, request, response) VALUES "
                    "  ('seccl', NULL, 'portfoliotransactiongroup', true, 201, "
                    '   \'{"big": "payload"}\', \'{"id": "x"}\'), '
                    "  ('morgan-lloyd', 'ctc', 'run_quote', false, 200, "
                    '   \'{"xml": "..."}\', \'{"xml": "error"}\')'
                )
            )

    def test_a_provider_filter_narrows_and_totals_agree(
        self, super_engine: Engine, db: dict[str, str]
    ) -> None:
        from app import platforms as platforms_mod

        self._seed(super_engine)

        async def run() -> dict[str, object]:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            engine = create_async_engine(
                db["super"].replace("postgresql://", "postgresql+psycopg://")
            )
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                out = await platforms_mod.api_calls(session, provider="seccl")
            await engine.dispose()
            return out

        page = asyncio.run(run())

        assert page["total"] == 1
        assert [r["provider"] for r in page["items"]] == ["seccl"]
        # The console prints the name, not the slug, in its provider filter and column.
        assert page["items"][0]["provider_name"] == "Seccl"
        # Bodies are deliberately absent from the list projection: a request body can be a whole
        # CTC1 XML document, and 50 of them would make a table render megabytes.
        assert "request" not in page["items"][0]
        assert "response" not in page["items"][0]

    def test_the_failures_filter_finds_a_200_that_failed(
        self, super_engine: Engine, db: dict[str, str]
    ) -> None:
        # The CTC engine answers 200 with an HTML error page, so `ok` is the only usable filter.
        from app import platforms as platforms_mod

        self._seed(super_engine)

        async def run() -> dict[str, object]:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            engine = create_async_engine(
                db["super"].replace("postgresql://", "postgresql+psycopg://")
            )
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                out = await platforms_mod.api_calls(session, ok=False)
            await engine.dispose()
            return out

        page = asyncio.run(run())

        assert page["total"] == 1
        assert page["items"][0]["status_code"] == 200
        assert page["items"][0]["service"] == "ctc"
