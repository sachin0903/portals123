"""Router-function-level tests for the `integrations` schema (0231_integrations_schema.sql) --
the SEI custodian/software platform approval dashboard.

Mirrors test_lookthrough.py's style: call the router's async functions directly against a real
Postgres session, rather than through an HTTP client. `service_session` (service_role, no
published claims) covers everything that doesn't need to resolve "who is calling" server-side;
`actor_session` (below) additionally publishes `request.jwt.claims` with a real `auth.users` row's
id, mirroring what `admin_session` does for a live request, so `update_integration`'s
`SELECT auth.uid()` resolves to someone real.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import Principal
from app.routers import integrations as router

READ = ["integrations:read"]
WRITE = ["integrations:write"]


def _principal(perms: list[str]) -> Principal:
    return Principal(user_id="wos_ops", organisation_id=None, role="ops", permissions=perms)


@pytest.fixture
def firm_tenant_id(super_engine: Engine) -> int:
    """A real `auth.tenants` row -- `firm_id` is a real FK to it now (0210), so a test can no
    longer hand it an arbitrary SEI firm code string."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        return int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"integten_{s}", "a": f"integ-{s}", "n": f"Integ Tenant {s}"},
            ).scalar_one()
        )


@pytest.fixture
def operator_id(super_engine: Engine) -> str:
    """A real `auth.users` row with no tenant -- a Graphene operator, per email_support.py's
    `with_operator` pattern. Needed as a real FK target for `actioned_by`/`updated_by`."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        return str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, email, workos_user_id, first_name, last_name, "
                    "  enabled) "
                    "VALUES (:uid, :email, :wos, 'Olly', 'Ops', true) RETURNING id"
                ),
                {"uid": f"opsusr_{s}", "email": f"ops_{s}@graphene.test", "wos": f"wos_{s}"},
            ).scalar_one()
        )


@pytest_asyncio.fixture
async def actor_session(db: dict[str, str], operator_id: str) -> AsyncIterator[AsyncSession]:
    """`service_session`, plus published `request.jwt.claims` naming `operator_id` as the acting
    user -- mirrors `admin_session`'s own claims publication, so `auth.uid()` resolves for real."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"),
            {"c": json.dumps({"user_uuid": operator_id})},
        )
        yield session
    await engine.dispose()


async def _insert_platform(
    session: AsyncSession,
    *,
    uid: str,
    firm_id: int | None = None,
    platform_name: str = "SEI Wealth Platform",
    tenant_id: str | None = None,
    tenant_name: str = "Tenant One",
    status: str = "pending",
    enabled: bool = False,
    installed: bool = False,
    justification: str | None = None,
) -> int:
    # (firm_id, platform_name, tenant_id) is UNIQUE, and `service_session` commits its rows for
    # the rest of the test run -- default tenant_id to something derived from `uid` (always
    # unique per call in this suite) rather than a fixed literal, so tests never collide.
    tenant_id = tenant_id or f"tenant-{uid}"
    result = await session.execute(
        text(
            """INSERT INTO integrations.platforms
                   (uid, firm_id, platform_name, tenant_id, tenant_name, status,
                    enabled, installed, justification)
               VALUES (:uid, :firm_id, :platform_name, :tenant_id, :tenant_name,
                       CAST(:status AS integrations.platform_status),
                       :enabled, :installed, :justification)
               RETURNING id"""
        ),
        {
            "uid": uid,
            "firm_id": firm_id,
            "platform_name": platform_name,
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "status": status,
            "enabled": enabled,
            "installed": installed,
            "justification": justification,
        },
    )
    return int(result.scalar_one())


async def _add_feature(session: AsyncSession, platform_id: int, feature_code: str) -> None:
    await session.execute(
        text(
            "INSERT INTO integrations.platform_features (platform_id, feature_code) VALUES (:p, :f)"
        ),
        {"p": platform_id, "f": feature_code},
    )


async def _feature_ids(session: AsyncSession, platform_id: int) -> set[str]:
    result = await session.execute(
        text("SELECT feature_code FROM integrations.platform_features WHERE platform_id = :p"),
        {"p": platform_id},
    )
    return set(result.scalars().all())


# ============================================================================
# list / detail
# ============================================================================


@pytest.mark.asyncio
async def test_list_aliases_platform_name_and_resolves_the_firm_tenant_name(
    service_session: AsyncSession, firm_tenant_id: int
) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    platform_id = await _insert_platform(
        service_session, uid=uid, firm_id=firm_tenant_id, platform_name="SEI Custodian"
    )
    await _add_feature(service_session, platform_id, "Valuations")
    await _add_feature(service_session, platform_id, "IncomeStatements")

    rows = await router.list_integrations(principal=_principal(READ), session=service_session)
    row = next(r for r in rows if r.uid == uid)
    tenant_name = (
        await service_session.execute(
            text("SELECT name FROM auth.tenants WHERE id = :id"), {"id": firm_tenant_id}
        )
    ).scalar_one()

    assert row.platform == "SEI Custodian"  # the legacy list-endpoint alias bug, fixed
    assert set(row.feature_ids) == {"Valuations", "IncomeStatements"}
    assert row.firm_id == firm_tenant_id
    assert row.firm_description == tenant_name


@pytest.mark.asyncio
async def test_list_carries_the_rejection_reason_not_just_the_detail_endpoint(
    service_session: AsyncSession,
) -> None:
    """`rejection_reason` must be on the LIST row too, not detail-only -- the edit sheet opens
    from either the list's row-click or the detail page's header and needs it either way to seed
    its Reason field with what's already stored, so an operator editing an already-rejected
    integration's firm/features/enabled can save without being forced to retype a reason that
    already exists."""
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    await _insert_platform(
        service_session, uid=uid, status="rejected", justification="Not onboarded yet"
    )

    rows = await router.list_integrations(principal=_principal(READ), session=service_session)
    row = next(r for r in rows if r.uid == uid)
    assert row.rejection_reason == "Not onboarded yet"


@pytest.mark.asyncio
async def test_detail_aliases_and_offers_every_tenant_as_a_firm_option(
    service_session: AsyncSession, firm_tenant_id: int
) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    platform_id = await _insert_platform(
        service_session,
        uid=uid,
        firm_id=firm_tenant_id,
        platform_name="SEI Custodian",
        status="rejected",
        justification="Not onboarded yet",
    )
    await _add_feature(service_session, platform_id, "Valuations")

    detail = await router.get_integration(uid, principal=_principal(READ), session=service_session)

    assert detail.integration.platform == "SEI Custodian"
    assert detail.integration.feature_ids == ["Valuations"]
    assert detail.integration.rejection_reason == "Not onboarded yet"
    assert detail.integration.created_date is not None
    assert detail.integration.updated_date is not None
    assert any(f.code == "Valuations" for f in detail.features)
    assert any(t.id == firm_tenant_id for t in detail.firms)


@pytest.mark.asyncio
async def test_detail_last_run_is_none_when_the_integration_has_never_run(
    service_session: AsyncSession,
) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    await _insert_platform(service_session, uid=uid)
    detail = await router.get_integration(uid, principal=_principal(READ), session=service_session)
    assert detail.last_run is None


@pytest.mark.asyncio
async def test_detail_last_run_is_true_latest_independent_of_runs_tab_paging(
    service_session: AsyncSession,
) -> None:
    """`last_run` must reflect this integration's actual most recent run, never whatever the
    separate Job Runs tab happens to be showing -- the bug this guards against: the frontend used
    to read `data.runs[0]`, the first row of THAT paged/searched list, which changes on page 2 or
    under a search filter even though the integration's real latest run has not changed."""
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    platform_id = await _insert_platform(service_session, uid=uid)
    await _insert_run(service_session, platform_id=platform_id, feature="Valuations", status=True)
    await _insert_run(service_session, platform_id=platform_id, feature="Transactions", status=True)
    await _insert_run(
        service_session,
        platform_id=platform_id,
        feature="IncomeStatementItems",
        status=False,
        error_message="boom",
    )

    detail = await router.get_integration(uid, principal=_principal(READ), session=service_session)
    assert detail.last_run is not None
    assert detail.last_run.feature == "IncomeStatementItems"

    # A Job Runs tab search matching only an EARLIER run must not change what `last_run` reports --
    # the detail endpoint takes no page/search of its own.
    filtered = await router.list_integration_runs(
        uid, search="Valuations", principal=_principal(READ), session=service_session
    )
    assert [r.feature for r in filtered.items] == ["Valuations"]
    detail_again = await router.get_integration(
        uid, principal=_principal(READ), session=service_session
    )
    assert detail_again.last_run is not None
    assert detail_again.last_run.feature == "IncomeStatementItems"


@pytest.mark.asyncio
async def test_get_integration_404_for_unknown_uid(service_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await router.get_integration(
            "does-not-exist", principal=_principal(READ), session=service_session
        )
    assert exc_info.value.status_code == 404


# ============================================================================
# update
# ============================================================================


@pytest.mark.asyncio
async def test_update_resolves_actioned_by_server_side_never_from_body(
    actor_session: AsyncSession, operator_id: str, firm_tenant_id: int
) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    await _insert_platform(actor_session, uid=uid, firm_id=firm_tenant_id)

    body = router.UpdatePlatformIntegration(
        firm_id=firm_tenant_id,
        status="approved",
        enabled=True,
        features=[],
        reason=None,
        # A hostile client trying to forge who approved this -- there is no such field on the
        # model, so pydantic drops it; the assertions below prove the DB agrees.
        **{"actioned_by": "00000000-0000-0000-0000-000000000000"},
    )
    updated = await router.update_integration(
        uid, body, principal=_principal(WRITE), session=actor_session
    )

    assert updated.approved_by == operator_id
    assert updated.approved_by != "00000000-0000-0000-0000-000000000000"
    db_value = (
        await actor_session.execute(
            text("SELECT actioned_by::text FROM integrations.platforms WHERE uid = :uid"),
            {"uid": uid},
        )
    ).scalar_one()
    assert db_value == operator_id


@pytest.mark.asyncio
async def test_update_round_trips_firm_id_to_a_real_tenant(
    actor_session: AsyncSession, firm_tenant_id: int
) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    await _insert_platform(actor_session, uid=uid, firm_id=None)

    body = router.UpdatePlatformIntegration(
        firm_id=firm_tenant_id, status="pending", enabled=False, features=[]
    )
    updated = await router.update_integration(
        uid, body, principal=_principal(WRITE), session=actor_session
    )

    assert updated.firm_id == firm_tenant_id
    db_value = (
        await actor_session.execute(
            text("SELECT firm_id FROM integrations.platforms WHERE uid = :uid"), {"uid": uid}
        )
    ).scalar_one()
    assert db_value == firm_tenant_id


@pytest.mark.asyncio
async def test_reject_with_blank_reason_422s(
    actor_session: AsyncSession, firm_tenant_id: int
) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    await _insert_platform(actor_session, uid=uid, firm_id=firm_tenant_id)

    body = router.UpdatePlatformIntegration(
        firm_id=firm_tenant_id, status="rejected", enabled=False, features=[], reason="   "
    )
    with pytest.raises(HTTPException) as exc_info:
        await router.update_integration(
            uid, body, principal=_principal(WRITE), session=actor_session
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_reject_with_a_real_reason_succeeds(
    actor_session: AsyncSession, firm_tenant_id: int
) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    await _insert_platform(actor_session, uid=uid, firm_id=firm_tenant_id)

    body = router.UpdatePlatformIntegration(
        firm_id=firm_tenant_id,
        status="rejected",
        enabled=False,
        features=[],
        reason="Not eligible",
    )
    updated = await router.update_integration(
        uid, body, principal=_principal(WRITE), session=actor_session
    )
    assert updated.status == "rejected"
    assert updated.rejection_reason == "Not eligible"


@pytest.mark.asyncio
async def test_update_fully_replaces_feature_membership_and_reads_back_from_db(
    actor_session: AsyncSession, firm_tenant_id: int
) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    platform_id = await _insert_platform(actor_session, uid=uid, firm_id=firm_tenant_id)
    await _add_feature(actor_session, platform_id, "Valuations")
    await _add_feature(actor_session, platform_id, "IncomeStatements")

    body = router.UpdatePlatformIntegration(
        firm_id=firm_tenant_id,
        status="approved",
        enabled=True,
        features=["Transactions", "IncomeStatementItems"],
    )
    updated = await router.update_integration(
        uid, body, principal=_principal(WRITE), session=actor_session
    )

    assert set(updated.feature_ids) == {"Transactions", "IncomeStatementItems"}
    assert await _feature_ids(actor_session, platform_id) == {
        "Transactions",
        "IncomeStatementItems",
    }


def test_update_body_has_no_destructive_defaults() -> None:
    """A PUT replaces everything, so an omitted field must be refused, not defaulted: an omitted
    `status` used to un-approve the integration and an omitted `features` cleared them."""
    from pydantic import ValidationError

    for omitted in ("firm_id", "status", "enabled", "features"):
        fields: dict[str, Any] = {
            "firm_id": None,
            "status": "approved",
            "enabled": True,
            "features": [],
        }
        del fields[omitted]
        with pytest.raises(ValidationError):
            router.UpdatePlatformIntegration(**fields)


@pytest.mark.asyncio
async def test_update_422s_on_an_unknown_firm_or_feature_and_writes_nothing(
    actor_session: AsyncSession, firm_tenant_id: int
) -> None:
    uid = f"plat_{uuid.uuid4().hex[:8]}"
    await _insert_platform(actor_session, uid=uid, firm_id=firm_tenant_id)
    for fields in (
        {"firm_id": 2_000_000_000, "features": []},
        {"firm_id": firm_tenant_id, "features": ["NoSuchFeature"]},
    ):
        body = router.UpdatePlatformIntegration(status="approved", enabled=True, **fields)
        with pytest.raises(HTTPException) as exc_info:
            await router.update_integration(
                uid, body, principal=_principal(WRITE), session=actor_session
            )
        assert exc_info.value.status_code == 422
    status = (
        await actor_session.execute(
            text("SELECT status::text FROM integrations.platforms WHERE uid = :uid"), {"uid": uid}
        )
    ).scalar_one()
    assert status != "approved"


@pytest.mark.asyncio
async def test_update_404_for_unknown_uid(actor_session: AsyncSession) -> None:
    body = router.UpdatePlatformIntegration(
        firm_id=None, status="approved", enabled=True, features=[]
    )
    with pytest.raises(HTTPException) as exc_info:
        await router.update_integration(
            "does-not-exist", body, principal=_principal(WRITE), session=actor_session
        )
    assert exc_info.value.status_code == 404


# ============================================================================
# runs
# ============================================================================


async def _insert_run(
    session: AsyncSession,
    *,
    platform_id: int,
    feature: str,
    status: bool,
    error_message: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    await session.execute(
        text(
            """INSERT INTO integrations.job_runs (platform_id, feature, status, error_message, data)
               VALUES (:p, :feature, :status, :error_message, CAST(:data AS jsonb))"""
        ),
        {
            "p": platform_id,
            "feature": feature,
            "status": status,
            "error_message": error_message,
            "data": json.dumps(data) if data is not None else None,
        },
    )


@pytest.mark.asyncio
async def test_runs_pagination_defaults(service_session: AsyncSession) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    platform_id = await _insert_platform(service_session, uid=uid)
    for _ in range(3):
        await _insert_run(
            service_session, platform_id=platform_id, feature="Valuations", status=True
        )

    page = await router.list_integration_runs(
        uid, principal=_principal(READ), session=service_session
    )
    assert page.page == 1
    assert page.limit == 20
    assert page.total == 3
    assert len(page.items) == 3


@pytest.mark.asyncio
async def test_runs_pagination_math(service_session: AsyncSession) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    platform_id = await _insert_platform(service_session, uid=uid)
    for _ in range(25):
        await _insert_run(
            service_session, platform_id=platform_id, feature="Valuations", status=True
        )

    page1 = await router.list_integration_runs(
        uid, page=1, limit=10, principal=_principal(READ), session=service_session
    )
    page2 = await router.list_integration_runs(
        uid, page=2, limit=10, principal=_principal(READ), session=service_session
    )
    page3 = await router.list_integration_runs(
        uid, page=3, limit=10, principal=_principal(READ), session=service_session
    )
    assert page1.total == 25
    assert len(page1.items) == 10
    assert len(page2.items) == 10
    assert len(page3.items) == 5


@pytest.mark.asyncio
async def test_runs_search_matches_feature_or_error_message(service_session: AsyncSession) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    platform_id = await _insert_platform(service_session, uid=uid)
    await _insert_run(service_session, platform_id=platform_id, feature="Valuations", status=True)
    await _insert_run(
        service_session,
        platform_id=platform_id,
        feature="Pricing",
        status=False,
        error_message="upstream timed out badly",
    )

    by_feature = await router.list_integration_runs(
        uid, search="Valuations", principal=_principal(READ), session=service_session
    )
    by_error = await router.list_integration_runs(
        uid, search="timed out", principal=_principal(READ), session=service_session
    )
    assert [r.feature for r in by_feature.items] == ["Valuations"]
    assert [r.feature for r in by_error.items] == ["Pricing"]


async def test_runs_search_treats_percent_and_underscore_literally(
    service_session: AsyncSession,
) -> None:
    """A search term containing `%` or `_` must not act as a SQL wildcard -- those are literal
    characters a user typed (e.g. an error message that genuinely contains "100%"), not a pattern
    they meant to write.

    Two checks: a search for "100%" matches only the row that actually contains that literal
    substring, not a "1", then anything, then "0" pattern; and a search for a bare "%" (unescaped,
    a wildcard matching any string) returns nothing against rows that contain no literal percent
    sign at all -- unescaped, that same search would match every row in the table.
    """
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    platform_id = await _insert_platform(service_session, uid=uid)
    await _insert_run(
        service_session,
        platform_id=platform_id,
        feature="Valuations",
        status=False,
        error_message="upload failed at 100% completion",
    )
    await _insert_run(
        service_session,
        platform_id=platform_id,
        feature="Transactions",
        status=False,
        error_message="connection reset",
    )

    literal_percent = await router.list_integration_runs(
        uid, search="100%", principal=_principal(READ), session=service_session
    )
    assert [r.feature for r in literal_percent.items] == ["Valuations"]

    bare_wildcard = await router.list_integration_runs(
        uid, search="%", principal=_principal(READ), session=service_session
    )
    assert [r.feature for r in bare_wildcard.items] == ["Valuations"]

    other_uid = f"plat_{uuid.uuid4().hex[:8]}"
    other_platform_id = await _insert_platform(service_session, uid=other_uid)
    await _insert_run(
        service_session,
        platform_id=other_platform_id,
        feature="Transactions",
        status=False,
        error_message="connection reset",
    )
    no_percent_anywhere = await router.list_integration_runs(
        other_uid, search="%", principal=_principal(READ), session=service_session
    )
    assert no_percent_anywhere.items == []


@pytest.mark.asyncio
async def test_runs_stat_extraction_exact_case_and_lowercase_keys(
    service_session: AsyncSession,
) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    platform_id = await _insert_platform(service_session, uid=uid)
    # Exact-case key matching the row's own `feature` column.
    await _insert_run(
        service_session,
        platform_id=platform_id,
        feature="Valuations",
        status=True,
        data={"Valuations": {"record_count": 10, "newly_added": 2, "failed": 1}},
    )
    # Lowercase key -- the legacy writer's inconsistent casing.
    await _insert_run(
        service_session,
        platform_id=platform_id,
        feature="Pricing",
        status=False,
        data={"pricing": {"record_count": 5, "newly_added": 1, "failed": 0}},
    )

    page = await router.list_integration_runs(
        uid, principal=_principal(READ), session=service_session
    )
    by_feature = {r.feature: r for r in page.items}
    assert by_feature["Valuations"].record_count == 10
    assert by_feature["Valuations"].newly_added == 2
    assert by_feature["Valuations"].failed == 1
    assert by_feature["Valuations"].status == "success"
    assert by_feature["Pricing"].record_count == 5
    assert by_feature["Pricing"].status == "failed"


@pytest.mark.asyncio
async def test_runs_stat_extraction_reads_the_engines_flat_counts(
    service_session: AsyncSession,
) -> None:
    """The engine stores a feature's own counts at the top level of `data` (not keyed by feature);
    the Job Runs tab used to read them as null and show 0/0 for every run."""
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    platform_id = await _insert_platform(service_session, uid=uid)
    await _insert_run(
        service_session,
        platform_id=platform_id,
        feature="Transactions",
        status=True,
        data={"record_count": 34, "newly_added": 1, "failed": 0, "skipped_no_link": 24},
    )

    page = await router.list_integration_runs(
        uid, principal=_principal(READ), session=service_session
    )
    (run,) = [r for r in page.items if r.feature == "Transactions"]
    assert (run.record_count, run.newly_added, run.failed) == (34, 1, 0)


@pytest.mark.asyncio
async def test_runs_404_for_unknown_uid(service_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await router.list_integration_runs(
            "does-not-exist", principal=_principal(READ), session=service_session
        )
    assert exc_info.value.status_code == 404


# ============================================================================
# permission gate
# ============================================================================


@pytest.mark.asyncio
async def test_read_endpoint_403s_without_the_read_permission(
    service_session: AsyncSession,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await router.list_integrations(principal=_principal([]), session=service_session)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_write_endpoint_403s_with_only_the_read_permission(
    service_session: AsyncSession,
) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"plat_{s}"
    await _insert_platform(service_session, uid=uid)
    body = router.UpdatePlatformIntegration(
        firm_id=None, status="approved", enabled=True, features=[]
    )
    with pytest.raises(HTTPException) as exc_info:
        await router.update_integration(
            uid, body, principal=_principal(READ), session=service_session
        )
    assert exc_info.value.status_code == 403


# ============================================================================
# /integrations/providers was NOT ported (dead/broken in the legacy source)
# ============================================================================


def test_no_providers_route_was_ported() -> None:
    from app.main import app
    from tests.conftest import api_routes

    paths = {r.path for r in api_routes(app)}
    assert "/integrations/providers" not in paths


# ============================================================================
# The feature list (0231 seeds it, 0255 prunes it)
# ============================================================================

_ENGINE_FEATURES = {"Valuations", "Transactions", "IncomeStatements", "IncomeStatementItems"}
_PRUNE_SQL = (
    Path(__file__).resolve().parent.parent / "migrations" / "sql" / "0275_integrations_features.sql"
)


def _run_prune(engine: Engine) -> None:
    """Re-run 0255's SQL as the migration does: inside one transaction."""
    sql = _PRUNE_SQL.read_text(encoding="utf-8")
    with engine.connect().execution_options(isolation_level="READ COMMITTED") as c:
        with c.begin():
            c.exec_driver_sql(sql.replace("%", "%%"))


def _feature_codes(engine: Engine) -> set[str]:
    with engine.connect() as c:
        return {r[0] for r in c.execute(text("SELECT code FROM integrations.features"))}


def test_the_feature_list_is_only_what_the_engine_runs(super_engine: Engine) -> None:
    """0231 seeds eight legacy features nothing runs; 0255 removes them. A plain DELETE under the
    forced row-level security would remove NOTHING and leave twelve, which is what this catches."""
    assert _feature_codes(super_engine) == _ENGINE_FEATURES


def test_the_prune_puts_row_level_security_back(super_engine: Engine) -> None:
    """The force is lifted for the DELETE; it must be forced again afterwards, or the engine's
    tables are readable by their owner without a policy."""
    _run_prune(super_engine)

    with super_engine.connect() as c:
        forced = dict(
            c.execute(
                text(
                    "SELECT relname, relforcerowsecurity FROM pg_class "
                    "WHERE oid IN ('integrations.features'::regclass, "
                    "              'integrations.platform_features'::regclass)"
                )
            ).all()
        )
    assert forced == {"features": True, "platform_features": True}


def test_the_prune_is_idempotent(super_engine: Engine) -> None:
    _run_prune(super_engine)
    _run_prune(super_engine)

    assert _feature_codes(super_engine) == _ENGINE_FEATURES


def test_the_prune_keeps_a_feature_a_platform_has_enabled(super_engine: Engine) -> None:
    """An enabled feature is RESTRICT-protected: deleting it would fail the whole migration. It is
    kept (an operator disables it first); an unused one beside it still goes."""
    s = uuid.uuid4().hex[:8]
    in_use, unused = f"InUse{s}", f"Unused{s}"
    with super_engine.connect() as c:
        c.execute(
            text("INSERT INTO integrations.features (code) VALUES (:a), (:b)"),
            {"a": in_use, "b": unused},
        )
        platform_id = int(
            c.execute(
                text(
                    "INSERT INTO integrations.platforms "
                    "  (uid, platform_name, tenant_id, tenant_name) "
                    "VALUES (:uid, 'prune-test', :tid, 'Prune test') RETURNING id"
                ),
                {"uid": f"prune_{s}", "tid": f"tenant-prune-{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO integrations.platform_features (platform_id, feature_code) "
                "VALUES (:p, :f)"
            ),
            {"p": platform_id, "f": in_use},
        )
    try:
        _run_prune(super_engine)

        codes = _feature_codes(super_engine)
        assert in_use in codes
        assert unused not in codes
    finally:
        with super_engine.connect() as c:
            c.execute(
                text("DELETE FROM integrations.platform_features WHERE platform_id = :p"),
                {"p": platform_id},
            )
            c.execute(text("DELETE FROM integrations.platforms WHERE id = :p"), {"p": platform_id})
            c.execute(
                text("DELETE FROM integrations.features WHERE code = ANY(:c)"),
                {"c": [in_use, unused]},
            )
