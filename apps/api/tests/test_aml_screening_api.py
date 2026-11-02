"""The firm's read-only window onto Graphene's screening (``crm.get_contact_screening``).

Driven against a real Postgres, as a real ``authenticated`` principal with claims minted by
``auth.resolve_claims`` — the same harness as ``test_crm_api.py``. A container is warranted here
because the controls under test ARE the query and the grants under it: ``compliance.checks``
grants a firm its status columns only (0221), and RLS scopes it to the firm's own lineage.

The boundary this file defends: AML is Graphene's regulatory responsibility as the platform
provider, and telling a client they are under suspicion is tipping off (POCA 2002 s.333A). A firm
sees one status and one date per kind — never the result, the outcome, the matches, how many there
were, a monitoring alert or the reviewer's comment.
"""

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import Principal
from app.routers import crm as crm_router


@asynccontextmanager
async def _authed(app_url: str, workos_user: str, client_id: str) -> AsyncIterator[AsyncSession]:
    """A session acting as a request principal — SET ROLE authenticated + claims minted by
    auth.resolve_claims, exactly like db.py::tenant_session. Row visibility is then the database's
    decision. App-only portal (0107): the application client id (arg 3) is what classifies the
    portal claim."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        resolved = (
            await session.execute(
                text("SELECT auth.resolve_claims(:w, NULL, :cid)"),
                {"w": workos_user, "cid": client_id},
            )
        ).scalar_one()
        claims = {
            **resolved,
            "sub": workos_user,
            "role": "adviser",
            "permissions": ["contacts:read_all", "screening:read"],
        }
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


def _principal(workos_user: str, permissions: list[str]) -> Principal:
    return Principal(
        user_id=workos_user,
        organisation_id="org_test",
        role="member",
        permissions=permissions,
    )


# What Graphene's review holds about a client. None of it may reach the firm.
MATCH_NAME = "Augusta King"
REVIEW_COMMENT = "Same person: passport and date of birth match"


@pytest.fixture
def bed(super_engine: Engine) -> Iterator[dict[str, object]]:
    """One firm with three clients — one screened and reviewed, one whose screen ComplyCube could
    not run, one never screened — plus a second firm with a screened client of its own."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:

        def tenant(tag: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"scrten_{tag}_{s}", "a": f"scr-{tag}-{s}", "n": f"Screening {tag} {s}"},
                ).scalar_one()
            )

        firm = tenant("a")
        other_firm = tenant("b")

        workos = f"scrwos_staff_{s}"
        user_id = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                    "VALUES (:uid, :t, :email, :w, true) RETURNING id"
                ),
                {
                    "uid": f"scrusr_{s}",
                    "t": firm,
                    "email": f"staff-{s}@screening.test",
                    "w": workos,
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, 'platform')"
            ),
            {"u": user_id, "t": firm},
        )
        # App-only portal (0107): declare the firm's platform application so the staff login
        # resolves portal='platform' and lineage is non-empty.
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  platform_client_id) VALUES (:t, 'dev', :env, :app)"
            ),
            {"t": firm, "env": f"env_scr_{s}", "app": f"app_scr_platform_{s}"},
        )

        def contact(tid: int, tag: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, "
                        "  service_type) "
                        "VALUES (:t, :uid, 'individual', 'Ada', 'Lovelace', 'advised') "
                        "RETURNING id"
                    ),
                    {"t": tid, "uid": f"scrcon_{tag}_{s}"},
                ).scalar_one()
            )

        def kyc(tid: int, contact_id: int, kind: str, kyc_status: str, minutes_ago: int) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO crm.kyc_checks (tenant_id, contact_id, kind, status, "
                        "  provider, updated_at) "
                        "VALUES (:t, :c, :k, :st, 'complycube', "
                        "  now() - make_interval(mins => :m)) RETURNING id"
                    ),
                    {"t": tid, "c": contact_id, "k": kind, "st": kyc_status, "m": minutes_ago},
                ).scalar_one()
            )

        def check(
            tid: int,
            contact_id: int,
            kind: str,
            check_status: str,
            minutes_ago: int,
            *,
            decided: bool = True,
            kyc_check_id: int | None = None,
        ) -> None:
            client = c.execute(
                text(
                    "INSERT INTO compliance.clients (tenant_id, contact_id, provider_client_id) "
                    "VALUES (:t, :c, :p) "
                    "ON CONFLICT (tenant_id, contact_id) DO UPDATE SET updated_at = now() "
                    "RETURNING id"
                ),
                {"t": tid, "c": contact_id, "p": f"cli_{contact_id}_{s}"},
            ).scalar_one()
            check_id = c.execute(
                text(
                    "INSERT INTO compliance.checks (tenant_id, client_id, kind, status, "
                    "  provider_check_id, outcome, decided_by, decided_at, decision_comment, "
                    "  monitoring, result, kyc_check_id, updated_at) "
                    "VALUES (:t, :cl, :k, :st, :p, 'attention', 'Olive Operator', "
                    "  CASE WHEN :decided THEN now() END, :comment, true, "
                    "  CAST(:result AS jsonb), :kyc, now() - make_interval(mins => :m)) "
                    "RETURNING id"
                ),
                {
                    "t": tid,
                    "cl": client,
                    "k": kind,
                    "st": check_status,
                    "p": f"chk_{contact_id}_{kind}_{s}",
                    "comment": REVIEW_COMMENT,
                    "result": json.dumps({"outcome": "attention", "note": MATCH_NAME}),
                    "decided": decided,
                    "kyc": kyc_check_id,
                    "m": minutes_ago,
                },
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO compliance.matches (tenant_id, check_id, provider_match_id, "
                    "  name, decision, comment) "
                    "VALUES (:t, :ch, 'm1', :n, 'confirmed', :comment)"
                ),
                {"t": tid, "ch": check_id, "n": MATCH_NAME, "comment": REVIEW_COMMENT},
            )

        screened = contact(firm, "screened")
        # AML: the kyc row moved first, then the check was reviewed — the check is the latest.
        kyc(firm, screened, "aml", "referred", 30)
        check(firm, screened, "aml", "refer", 5)
        # Identity: the check passed, then the kyc row was settled after it.
        check(firm, screened, "identity", "clear", 20)
        kyc(firm, screened, "identity", "passed", 10)

        failed = contact(firm, "failed")
        # ComplyCube could not run it. Not a verdict: it reads as pending, like the kyc row.
        kyc(firm, failed, "aml", "pending", 30)
        check(firm, failed, "aml", "failed", 1)
        kyc(firm, failed, "identity", "expired", 30)

        reviewing = contact(firm, "reviewing")
        # A partner check ComplyCube referred, still under Graphene's review.
        check(firm, reviewing, "aml", "refer", 1, decided=False)
        # A portal check referred by ComplyCube: the kyc row has not moved, so neither does the
        # firm's read, even though the check row is newer.
        portal_kyc = kyc(firm, reviewing, "identity", "pending", 30)
        check(firm, reviewing, "identity", "refer", 1, decided=False, kyc_check_id=portal_kyc)

        never_screened = contact(firm, "clean")
        foreign = contact(other_firm, "foreign")
        check(other_firm, foreign, "aml", "rejected", 1)

    yield {
        "staff": workos,
        "app": f"app_scr_platform_{s}",
        "screened": screened,
        "failed": failed,
        "never_screened": never_screened,
        "reviewing": reviewing,
        "foreign": foreign,
    }

    # Children before the tenant: every one of these tables is audited and audit.log references the
    # tenant, so a single cascading tenant delete trips a foreign-key violation (see test_aml_rls).
    with super_engine.connect() as c:
        ids = [firm, other_firm]
        for stmt in (
            "DELETE FROM compliance.matches WHERE tenant_id = ANY(:ids)",
            "DELETE FROM compliance.checks WHERE tenant_id = ANY(:ids)",
            "DELETE FROM compliance.clients WHERE tenant_id = ANY(:ids)",
            "DELETE FROM crm.kyc_checks WHERE tenant_id = ANY(:ids)",
            "DELETE FROM crm.contacts WHERE tenant_id = ANY(:ids)",
            "DELETE FROM auth.organisation_memberships WHERE tenant_id = ANY(:ids)",
            "DELETE FROM auth.tenant_environments WHERE tenant_id = ANY(:ids)",
            "DELETE FROM auth.users WHERE tenant_id = ANY(:ids)",
            "DELETE FROM auth.tenants WHERE id = ANY(:ids)",
        ):
            c.execute(text(stmt), {"ids": ids})


async def _screening(db: dict[str, str], bed: dict[str, object], contact_id: int) -> dict:
    async with _authed(db["app"], str(bed["staff"]), str(bed["app"])) as session:
        return await crm_router.get_contact_screening(
            contact_id,
            principal=_principal(str(bed["staff"]), ["contacts:read_all", "screening:read"]),
            session=session,
        )


def _statuses(payload: dict) -> dict[str, str]:
    return {item["kind"]: item["status"] for item in payload["checks"]}


@pytest.mark.asyncio
async def test_a_firm_sees_a_status_and_a_date_and_nothing_else(
    db: dict[str, str], bed: dict[str, object]
) -> None:
    """What a firm learns is where each check stands, and whether ongoing monitoring covers the AML
    screen (on or off: not an alert). The review behind it — the match, the outcome, the reviewer's
    comment — stays with Graphene."""
    payload = await _screening(db, bed, int(bed["screened"]))

    assert set(payload) == {"checks"}
    assert [item["kind"] for item in payload["checks"]] == ["identity", "aml"]
    for item in payload["checks"]:
        assert set(item) == {"kind", "status", "updated_at", "monitored"}
        assert item["updated_at"] is not None
    assert {item["kind"]: item["monitored"] for item in payload["checks"]} == {
        "identity": None,
        "aml": True,
    }
    serialised = json.dumps(payload, default=str)
    for fragment in (MATCH_NAME, REVIEW_COMMENT, "attention", "Olive", "monitoring", "cs_"):
        assert fragment not in serialised, f"{fragment!r} leaked into the firm's screening read"


@pytest.mark.asyncio
async def test_each_kind_reads_from_whichever_moved_last(
    db: dict[str, str], bed: dict[str, object]
) -> None:
    payload = await _screening(db, bed, int(bed["screened"]))
    assert _statuses(payload) == {"identity": "passed", "aml": "referred"}


@pytest.mark.asyncio
async def test_a_check_complycube_could_not_run_reads_as_pending(
    db: dict[str, str], bed: dict[str, object]
) -> None:
    """A technical failure is not a verdict, so the firm is never told its client failed. An
    expired pass needs a new check, which reads as not started."""
    payload = await _screening(db, bed, int(bed["failed"]))
    assert _statuses(payload) == {"identity": "not_started", "aml": "pending"}
    # The screen was started with monitoring asked for, then ComplyCube could not run it. The
    # poller does not ask about a failed check, so the firm is not told monitoring is on.
    assert {item["kind"]: item["monitored"] for item in payload["checks"]} == {
        "identity": None,
        "aml": False,
    }


@pytest.mark.asyncio
async def test_a_refer_still_under_review_reads_as_pending(
    db: dict[str, str], bed: dict[str, object]
) -> None:
    """Referred is the outcome of Graphene's review. A possible match still under review reaches
    the firm as in progress, for a partner check and a portal check alike."""
    payload = await _screening(db, bed, int(bed["reviewing"]))
    assert _statuses(payload) == {"identity": "pending", "aml": "pending"}


@pytest.mark.asyncio
async def test_a_client_who_has_never_been_screened_is_a_normal_state(
    db: dict[str, str], bed: dict[str, object]
) -> None:
    """Not-yet-screened is the majority of any firm's book, so it cannot be an error."""
    payload = await _screening(db, bed, int(bed["never_screened"]))
    assert payload == {
        "checks": [
            {"kind": "identity", "status": "not_started", "updated_at": None, "monitored": None},
            {"kind": "aml", "status": "not_started", "updated_at": None, "monitored": None},
        ]
    }


@pytest.mark.asyncio
async def test_another_firms_client_resolves_nothing(
    db: dict[str, str], bed: dict[str, object]
) -> None:
    """The endpoint takes a raw contact id, so guessing one is trivial. Scoping is RLS, and this is
    what proves the endpoint sits inside it: another firm's rejected client reads as unscreened."""
    payload = await _screening(db, bed, int(bed["foreign"]))
    assert _statuses(payload) == {"identity": "not_started", "aml": "not_started"}


@pytest.mark.asyncio
async def test_screening_read_is_required(db: dict[str, str], bed: dict[str, object]) -> None:
    """Most of /crm is deliberately ungated — RLS is the gate — so this endpoint being different is
    easy to "tidy away". It must not be: a firm decides which of its own roles may see a client's
    screening position."""
    async with _authed(db["app"], str(bed["staff"]), str(bed["app"])) as session:
        with pytest.raises(HTTPException) as raised:
            await crm_router.get_contact_screening(
                int(bed["screened"]),
                principal=_principal(str(bed["staff"]), ["contacts:read", "contacts:read_all"]),
                session=session,
            )
    assert raised.value.status_code == 403
    assert "screening:read" in str(raised.value.detail)
