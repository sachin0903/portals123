"""A PUBLISHED email-template version's content is immutable — enforced by `0062`, not commented.

`0042_email_templates.sql:37` claimed "Immutable once status = 'published'" with nothing behind it.
Both roles hold UPDATE, and the published row is the one the SEND path resolves through
`email.templates.current_published_version_id`, so an in-place edit changes what a client receives
while skipping the version roll and the publish gates.

Pinned here: the three CONTENT columns (`schema`/`subject`/`preheader` — exactly what
`render_published_email` reads) are refused on a published row from both roles; the state machine
(publish, retire-to-archived, idempotent re-publish) and `metadata` are deliberately untouched; the
real service paths still work; and the losing save is a 409, not a 500.
"""

import importlib.util
import json
import types
import uuid
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.email import templates_service as svc
from tests import email_support

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "0062_email_version_immutable.py"
)

CONTENT = {"html": "<p>Hello {{ client.first_name }}</p>", "css": ".x{color:#0f766e}"}
OTHER_CONTENT = {"html": "<p>Rewritten behind your back</p>", "css": ""}

# 23001 (restrict_violation) surfaces as psycopg's IntegrityError, so the API maps it to 409.
RESTRICT_VIOLATION = "23001"


@pytest.fixture
def firm(super_engine: Engine) -> dict[str, str]:
    return email_support.make_firm(
        super_engine, prefix="i", name="IFirm", first_name="Ida", last_name="Author"
    )


@pytest_asyncio.fixture
async def firm_session(db: dict[str, str], firm: dict[str, str]):
    """The real firm-router path (db.py::tenant_session): `authenticated` + the staffer's claims.
    The trigger function is not SECURITY DEFINER, so it must fire for this role too."""
    opener, engine = email_support.session_opener(db, email_support.firm_claims(firm))
    yield opener
    await engine.dispose()


# ── helpers ───────────────────────────────────────────────────────────────────


def _migration_module() -> types.ModuleType:
    """Load the revision by PATH (its module name starts with a digit). Reading `_DOWN` from the
    real file rather than restating it is the point: a copy keeps passing after the downgrade
    breaks."""
    spec = importlib.util.spec_from_file_location("_rev_0062", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(engine: Engine, firm: dict[str, str], status: str) -> tuple[str, str]:
    """A COMMITTED template + v1 in `status`, as the superuser: both callers (the downgrade
    probe, the interleaved publish) run on a SEPARATE connection and could not see an uncommitted
    row. Returns (template uid, version uid)."""
    s = uuid.uuid4().hex[:8]
    with engine.connect() as c:
        template_id = int(
            c.execute(
                text(
                    "INSERT INTO email.templates (uid, tenant_id, code, name, created_by) "
                    "VALUES (:uid, :t, :code, 'Seeded', :actor) RETURNING id"
                ),
                {
                    "uid": f"eml_{s}",
                    "t": int(firm["tenant_id"]),
                    "code": f"seed_{s}",
                    "actor": firm["user_uuid"],
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO email.template_versions "
                "  (uid, tenant_id, template_id, version_number, status, subject, preheader, "
                "   schema, created_by, published_by, published_at) "
                "VALUES (:uid, :t, :tid, 1, CAST(:status AS email.template_status), "
                "  'Seeded subject', 'Seeded preheader', CAST(:schema AS jsonb), :actor, :pub, "
                "  CASE WHEN :live THEN now() ELSE NULL END)"
            ),
            {
                "uid": f"emlv_{s}",
                "t": int(firm["tenant_id"]),
                "tid": template_id,
                "status": status,
                "live": status == "published",
                "schema": json.dumps(CONTENT),
                "actor": firm["user_uuid"],
                "pub": firm["user_uuid"] if status == "published" else None,
            },
        )
    return f"eml_{s}", f"emlv_{s}"


async def _create(session: AsyncSession, firm: dict[str, str]) -> str:
    tpl = await svc.create_template(
        session,
        int(firm["tenant_id"]),
        firm["user_uuid"],
        code=f"immut_{uuid.uuid4().hex[:6]}",
        name="Immutable",
        category="notification",
        description=None,
        scope="firm",
    )
    return str(tpl["uid"])


async def _save(
    session: AsyncSession,
    firm: dict[str, str],
    tpl_uid: str,
    *,
    subject: str = "Live subject",
    preheader: str = "Live preheader",
    schema: dict[str, str] = CONTENT,
) -> dict[str, object]:
    return await svc.save_version(
        session,
        int(firm["tenant_id"]),
        firm["user_uuid"],
        tpl_uid,
        subject=subject,
        preheader=preheader,
        schema=schema,
        scope="firm",
    )


async def _published(session: AsyncSession, firm: dict[str, str]) -> tuple[str, str]:
    """Author and publish through the REAL service. Also covers draft → published: the trigger
    keys on OLD.status, so publishing passes while the row is still a draft."""
    tpl_uid = await _create(session, firm)
    saved = await _save(session, firm, tpl_uid)
    published = await svc.publish_version(
        session, int(firm["tenant_id"]), firm["user_uuid"], tpl_uid, saved["uid"], scope="firm"
    )
    assert published["status"] == "published" and published["published_at"] is not None
    return tpl_uid, str(published["uid"])


async def _expect_refusal(session: AsyncSession, sql: str, params: dict[str, object]) -> str:
    """Demand the trigger's refusal in a savepoint. Asserts SQLSTATE **and** message — "it
    raised" would also pass on a typo, an RLS denial or an unrelated constraint."""
    with pytest.raises(IntegrityError) as excinfo:
        async with session.begin_nested():
            await session.execute(text(sql), params)
    orig = excinfo.value.orig
    assert getattr(orig, "sqlstate", None) == RESTRICT_VIOLATION, f"wrong SQLSTATE: {orig!r}"
    message = str(orig)
    assert "is published and its content is immutable" in message
    assert "Save a new draft version instead" in message  # the HINT reaches the client
    return message


async def _content_of(session: AsyncSession, vid: str) -> dict[str, object]:
    result = await session.execute(
        text(
            "SELECT subject, preheader, schema, status::text AS status "
            "FROM email.template_versions WHERE uid = :v"
        ),
        {"v": vid},
    )
    return dict(result.mappings().one())


# ── the guard: a published row's CONTENT ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sql", "column", "unchanged"),
    [
        ("SET schema = CAST(:x AS jsonb)", "schema", CONTENT),
        ("SET subject = :x", "subject", "Live subject"),
        ("SET preheader = :x", "preheader", "Live preheader"),
        (
            "SET schema = CAST(:x AS jsonb), subject = 'T', preheader = 'T'",
            "all three",
            CONTENT,
        ),
    ],
)
async def test_editing_a_published_versions_content_is_rejected(
    service_session: AsyncSession,
    firm: dict[str, str],
    sql: str,
    column: str,
    unchanged: object,
) -> None:
    _, vid = await _published(service_session, firm)
    value = json.dumps(OTHER_CONTENT) if "jsonb" in sql else "Tampered"
    message = await _expect_refusal(
        service_session,
        f"UPDATE email.template_versions {sql} WHERE uid = :v",
        {"x": value, "v": vid},
    )
    assert vid in message  # the refusal names the offending version
    key = "schema" if unchanged == CONTENT else column
    assert (await _content_of(service_session, vid))[key] == unchanged


@pytest.mark.asyncio
async def test_the_firms_own_role_cannot_edit_a_published_version_either(
    firm_session, firm: dict[str, str]
) -> None:
    """`authenticated` holds UPDATE (0042:84) and the trigger is not SECURITY DEFINER, so it
    must fire for the firm portal too — while its legitimate save/publish/roll path keeps working
    under RLS (the trigger and the policies have to coexist)."""
    async with firm_session() as session:
        tpl_uid, vid = await _published(session, firm)
        await _expect_refusal(
            session,
            "UPDATE email.template_versions SET subject = 'Tampered by the firm' WHERE uid = :v",
            {"v": vid},
        )
        assert (await _content_of(session, vid))["subject"] == "Live subject"
        v2 = await svc.save_version(
            session,
            int(firm["tenant_id"]),
            firm["user_uuid"],
            tpl_uid,
            subject="RLS v2",
            preheader="",
            schema=OTHER_CONTENT,
            scope="firm",
        )
        assert v2["version_number"] == 2


# ── the carve-out: everything that is NOT content ─────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("what", "sql"),
    [
        ("retire to archived", "SET status = 'archived'"),
        (
            "idempotent re-publish",
            "SET status = 'published', published_at = now(), authored_by_scope = 'operations'",
        ),
        # `metadata` is unguarded deliberately: nothing reads it, publish writes it after the flip.
        ("metadata", 'SET metadata = CAST(\'{"note":"x"}\' AS jsonb)'),
    ],
)
async def test_non_content_updates_on_a_published_row_still_succeed(
    service_session: AsyncSession, firm: dict[str, str], what: str, sql: str
) -> None:
    _, vid = await _published(service_session, firm)
    await service_session.execute(
        text(f"UPDATE email.template_versions {sql} WHERE uid = :v"), {"v": vid}
    )
    assert (await _content_of(service_session, vid))["subject"] == "Live subject", what


# ── the race the trigger exposes, and its HTTP translation ────────────────────


class _InterleavingSession:
    """An `AsyncSession` proxy that runs a callback once, right after a chosen statement.

    `save_version` decides in-place-vs-roll from an UNLOCKED read of the head version's status, so a
    concurrent publish can commit in the window before the UPDATE. Widening it by hand is the only
    deterministic way to exercise that."""

    def __init__(self, inner: AsyncSession, marker: str, hook: object) -> None:
        self._inner = inner
        self._marker = marker
        self._hook = hook
        self.fired = False

    async def execute(self, statement: object, *args: object, **kwargs: object) -> object:
        result = await self._inner.execute(statement, *args, **kwargs)  # type: ignore[arg-type]
        if not self.fired and self._marker in str(statement):
            self.fired = True
            self._hook()  # type: ignore[operator]
        return result

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


@pytest.mark.asyncio
async def test_a_publish_racing_a_save_raises_publishedimmutable_not_integrityerror(
    service_session: AsyncSession, firm: dict[str, str], super_engine: Engine
) -> None:
    """THE 500 THIS FIXES. The trigger correctly refuses a save that lands on a just-published
    row, but an uncaught IntegrityError says "we broke" when the truth is "somebody published while
    you were typing"."""
    tpl_uid, vid = _seed(super_engine, firm, "draft")

    def _publish_behind_our_back() -> None:
        """A status-only flip — what a colleague clicking Publish in another tab commits."""
        with super_engine.connect() as c2:
            c2.execute(
                text(
                    "UPDATE email.template_versions "
                    "SET status = 'published', published_at = now(), published_by = :actor "
                    "WHERE uid = :v"
                ),
                {"actor": firm["user_uuid"], "v": vid},
            )

    proxy = _InterleavingSession(
        service_session, "ORDER BY version_number DESC LIMIT 1", _publish_behind_our_back
    )

    with pytest.raises(svc.PublishedImmutable):
        await svc.save_version(
            cast(AsyncSession, proxy),
            int(firm["tenant_id"]),
            firm["user_uuid"],
            tpl_uid,
            subject="Saved over the top",
            preheader="Saved over the top",
            schema=OTHER_CONTENT,
            scope="firm",
        )
    assert proxy.fired, "the interleave never ran — this test would be vacuous"

    with super_engine.connect() as c:
        row = (
            c.execute(
                text(
                    "SELECT subject, schema, status::text AS status "
                    "FROM email.template_versions WHERE uid = :v"
                ),
                {"v": vid},
            )
            .mappings()
            .one()
        )
    assert row["status"] == "published"
    assert row["subject"] == "Seeded subject"
    assert row["schema"] == CONTENT


def test_the_conflict_maps_to_409_on_both_surfaces() -> None:
    """`_raise_http` is shared by the firm router and the admin model door. Without it 0062's
    refusal reached the author as a 500 — and the remedy has to be in the message, since no amount
    of retrying the same save fixes a draft that is no longer a draft."""
    from fastapi import HTTPException

    from app.routers.email_templates import _raise_http

    with pytest.raises(HTTPException) as excinfo:
        _raise_http(svc.PublishedImmutable("eml_x"))
    assert excinfo.value.status_code == 409
    detail = str(excinfo.value.detail).lower()
    assert "published" in detail and "reload" in detail


# ── the trigger is actually installed (not just passing by accident) ──────────

_TRIGGER_COUNT = text(
    "SELECT count(*) FROM pg_trigger tg "
    "JOIN pg_class cl ON cl.oid = tg.tgrelid "
    "JOIN pg_namespace ns ON ns.oid = cl.relnamespace "
    "WHERE ns.nspname = 'email' AND cl.relname = 'template_versions' "
    "  AND tg.tgname = 'reject_published_edit'"
)


def test_the_trigger_and_function_exist(super_engine: Engine) -> None:
    """So a migration that stopped shipping the trigger fails HERE rather than making every
    behavioural test above quietly vacuous."""
    with super_engine.connect() as c:
        assert c.execute(_TRIGGER_COUNT).scalar_one() == 1
        body = c.execute(
            text(
                "SELECT prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'email' AND p.proname = 'reject_published_version_edit'"
            )
        ).scalar_one()
    # The guarded set read off the function's own source: a new content column shows up here.
    for column in ("schema", "subject", "preheader"):
        assert f"NEW.{column}" in body
    assert "restrict_violation" in body


def test_the_downgrade_removes_the_guard_and_the_edit_then_succeeds(
    db: dict[str, str], super_engine: Engine, firm: dict[str, str]
) -> None:
    """Two things at once, inside a ROLLED BACK transaction (DDL is transactional in Postgres):
    that `downgrade()` is valid and removes both objects (downgrades are otherwise never executed),
    and NON-VACUITY — the same statement the tests above prove is refused SUCCEEDS once the trigger
    is gone, so those refusals are caused by THIS trigger.

    On the MIGRATOR connection because service_role cannot DROP TRIGGER — but the DML runs under
    `SET ROLE service_role`, because the table is FORCE RLS and the migrator holds no BYPASSRLS
    (attributes are not inherited), so it would match zero rows, fire no trigger, and be vacuous."""
    down_sql = _migration_module()._DOWN
    _tpl, vid = _seed(super_engine, firm, "published")
    tamper = text("UPDATE email.template_versions SET subject = 'After downgrade' WHERE uid = :v")

    migrator = create_engine(db["migrator"])
    try:
        with migrator.connect() as c:
            trans = c.begin()
            try:
                c.execute(text("SET ROLE service_role"))
                assert (
                    c.execute(
                        text("SELECT count(*) FROM email.template_versions WHERE uid = :v"),
                        {"v": vid},
                    ).scalar_one()
                    == 1
                )
                with pytest.raises(IntegrityError) as excinfo:
                    with c.begin_nested():
                        c.execute(tamper, {"v": vid})
                assert getattr(excinfo.value.orig, "sqlstate", None) == RESTRICT_VIOLATION

                c.execute(text("RESET ROLE"))
                c.exec_driver_sql(down_sql)
                assert c.execute(_TRIGGER_COUNT).scalar_one() == 0
                assert (
                    c.execute(
                        text(
                            "SELECT count(*) FROM pg_proc p "
                            "JOIN pg_namespace n ON n.oid = p.pronamespace "
                            "WHERE n.nspname = 'email' "
                            "  AND p.proname = 'reject_published_version_edit'"
                        )
                    ).scalar_one()
                    == 0
                )
                c.execute(text("SET ROLE service_role"))
                assert c.execute(tamper, {"v": vid}).rowcount == 1
            finally:
                trans.rollback()
    finally:
        migrator.dispose()

    with super_engine.connect() as c:
        assert c.execute(_TRIGGER_COUNT).scalar_one() == 1
        assert (
            c.execute(
                text("SELECT subject FROM email.template_versions WHERE uid = :v"), {"v": vid}
            ).scalar_one()
            == "Seeded subject"
        )
