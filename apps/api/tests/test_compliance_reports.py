"""Graphene's own compliance reports, for Graphene operations and for a firm.

Against real Postgres, on the identity capture tests' own journey, with the PDF engine replaced by
one that keeps the page it was given:

* operations download the client report and any check's report, read everything — the client's
  risk, each match, each decision — and each download is recorded;
* a firm downloads the client report once a check has run, an identity report once its check has
  a result, and an AML report only once the screen is clear. Each status reads in the firm's own
  words, and no match, decision or comment is in any of them. Each download is recorded in the
  firm's own trail.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.complycube import reports
from app.config import settings
from app.routers import aml as aml_router
from app.routers import client_identity
from tests.test_identity_capture import (
    FakeComplyCube,
    _console,
    _instantiate,
    _latest_check,
    _result,
    _rows,
    _seed,
    _try,
)

#: What the replaced PDF engine returns for any page.
PDF = b"%PDF-1.4 a Graphene report"

#: The identity check's analysis, as ComplyCube sends it.
ANALYSIS = {
    "outcome": "attention",
    "breakdown": {"faceAnalysis": {"facialSimilarity": "attention"}},
}

#: What a reviewer wrote. A firm never reads it.
COMMENT = "The photo does not match the document."


@pytest.fixture
def complycube(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "complycube_api_key", "test-key")
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each page the reports asked to be made into a PDF, in order."""
    made: list[str] = []

    async def render(page: str) -> bytes:
        made.append(page)
        return PDF

    monkeypatch.setattr(reports, "render", render)
    return made


async def _identity_held(
    sess: AsyncSession, fx: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """An identity check a reviewer referred back for new photos, with its analysis and the
    reviewer's comment."""
    fake = FakeComplyCube(fx["nonce"])
    await _instantiate(sess, fx, "complycube")
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    await _result(sess, fx, "refer", result=ANALYSIS)
    held = await _latest_check(sess, fx)
    await sess.execute(
        text(
            "UPDATE compliance.checks SET decided_by = 'Rita Reviewer', decided_at = now(), "
            "  decision_comment = :c WHERE id = :i"
        ),
        {"c": COMMENT, "i": held["id"]},
    )
    return held


async def _aml_check(sess: AsyncSession, fx: dict[str, Any], status: str) -> int:
    """An AML screen of the client with one match."""
    uid = f"aml{uuid.uuid4().hex[:8]}"
    check = int(
        (
            await sess.execute(
                text(
                    "INSERT INTO compliance.checks (uid, tenant_id, client_id, kind, status, "
                    "  origin, provider_check_id, provider_type, monitoring) "
                    "SELECT :u, cl.tenant_id, cl.id, 'aml', :st, 'portal', :p, "
                    "  'extensive_screening_check', true FROM compliance.clients cl "
                    "WHERE cl.contact_id = :c RETURNING id"
                ),
                {"u": uid, "st": status, "p": f"chk_{uid}", "c": fx["contact"]},
            )
        ).scalar_one()
    )
    await sess.execute(
        text(
            "INSERT INTO compliance.matches (tenant_id, check_id, provider_match_id, name, "
            "  match_types, score, decision, comment) "
            "VALUES (:t, :k, :p, 'Aleksandr Matchmann', ARRAY['pep'], 92, 'confirmed', :c)"
        ),
        {"t": fx["tenant"], "k": check, "p": f"match_{uid}", "c": COMMENT},
    )
    return check


async def _adviser(sess: AsyncSession, fx: dict[str, Any]) -> Principal:
    user = str(uuid.uuid4())
    await sess.execute(
        text(
            "INSERT INTO auth.users (id, tenant_id, email, first_name, last_name, workos_user_id) "
            "VALUES (:id, :t, :e, 'Ada', 'Adviser', :w)"
        ),
        {"id": user, "t": fx["tenant"], "e": f"ada_{fx['nonce']}@t.test", "w": f"wos_{user}"},
    )
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"sub": f"wos_{user}", "user_uuid": user})},
    )
    return Principal(
        user_id=f"wos_{user}",
        organisation_id="org_1",
        role="adviser",
        permissions=["screening:read"],
    )


@pytest.mark.asyncio
async def test_operations_download_any_report_read_everything_and_each_is_recorded(
    service_session: AsyncSession,
    complycube: None,
    pages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    held = await _identity_held(sess, fx, monkeypatch)
    aml = await _aml_check(sess, fx, "refer")
    aml_uid = (
        await sess.execute(text("SELECT uid FROM compliance.checks WHERE id = :i"), {"i": aml})
    ).scalar_one()
    ctx = await _console(sess, fx, monkeypatch)

    identity_report = await aml_router.get_check_report(str(held["uid"]), ctx=ctx)
    aml_report = await aml_router.get_check_report(str(aml_uid), ctx=ctx)
    client_report = await aml_router.get_client_report(fx["contact_uid"], ctx=ctx)

    for response in (identity_report, aml_report, client_report):
        assert response.body == PDF
        assert response.media_type == "application/pdf"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["content-disposition"].startswith("attachment")
    identity_page, aml_page, client_page = pages
    assert "Identity check report" in identity_page
    assert "Facial similarity" in identity_page
    assert "Referred" in identity_page
    assert "Rita Reviewer" in identity_page and COMMENT in identity_page
    assert "Lists screened" in aml_page
    assert "Aleksandr Matchmann" in aml_page
    assert "Client report" in client_page
    assert "AML risk" in client_page
    assert "Identity check" in client_page and "AML screen" in client_page
    recorded = await _rows(
        sess,
        "SELECT metadata -> 'subject' ->> 'report' AS report FROM audit.actions "
        "WHERE event_type = 'aml.report_downloaded' AND target_tenant_id = :t ORDER BY id",
        t=fx["tenant"],
    )
    assert [row["report"] for row in recorded] == ["identity", "aml", "client"]


@pytest.mark.asyncio
async def test_a_firm_gets_reports_in_its_own_words_and_never_what_is_withheld(
    service_session: AsyncSession,
    complycube: None,
    pages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    held = await _identity_held(sess, fx, monkeypatch)
    adviser = await _adviser(sess, fx)

    async def offered() -> dict[str, Any]:
        return await client_identity.get_contact_compliance_reports(
            fx["contact"], principal=adviser, session=sess
        )

    async def download(kind: str, check: str | None = None) -> Any:
        return await client_identity.get_contact_compliance_report(
            fx["contact_uid"],
            kind,  # type: ignore[arg-type]
            fx["contact"],
            check=check,
            principal=adviser,
            session=sess,
        )

    state = await offered()
    assert state["identity"] == {"check_uid": held["uid"], "available": True, "reason": None}
    assert (state["aml"]["available"], state["aml"]["reason"]) == (
        False,
        client_identity.NO_AML_SCREEN,
    )
    assert state["client"] == {"available": True, "reason": None}
    assert (await download("identity", str(held["uid"]))).body == PDF
    identity_page = pages[-1]
    assert "Facial similarity" in identity_page
    assert "Superseded by newer photos" in identity_page
    for withheld in ("Referred", "Rita Reviewer", COMMENT):
        assert withheld not in identity_page

    # A screen with a match under review: its report is not offered, and the client report gives
    # it as in progress, with no match and no risk.
    await _aml_check(sess, fx, "refer")
    state = await offered()
    assert (state["aml"]["available"], state["aml"]["reason"]) == (
        False,
        client_identity.AML_NOT_CLEAR,
    )
    with pytest.raises(HTTPException) as raised:
        await download("aml")
    assert raised.value.status_code == 409
    assert (await download("client")).body == PDF
    client_page = pages[-1]
    assert "In progress" in client_page
    for withheld in ("Referred", "Aleksandr Matchmann", COMMENT, "AML risk"):
        assert withheld not in client_page

    # Once the screen is clear, its report is offered, still without its match.
    await sess.execute(
        text(
            "UPDATE compliance.checks SET status = 'clear' WHERE kind = 'aml' AND client_id = "
            "(SELECT id FROM compliance.clients WHERE contact_id = :c)"
        ),
        {"c": fx["contact"]},
    )
    assert (await download("aml")).body == PDF
    aml_page = pages[-1]
    assert "Lists screened" in aml_page and "Passed" in aml_page
    assert "Aleksandr Matchmann" not in aml_page
    recorded = await _rows(
        sess,
        "SELECT metadata -> 'subject' ->> 'report' AS report FROM audit.actions "
        "WHERE event_type = 'client.compliance_report_downloaded' AND tenant_id = :t ORDER BY id",
        t=fx["tenant"],
    )
    assert [row["report"] for row in recorded] == ["identity", "client", "aml"]


@pytest.mark.asyncio
async def test_a_firm_reads_the_confirmed_matches_once_graphene_lets_the_journey_continue(
    service_session: AsyncSession,
    complycube: None,
    pages: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sess = service_session
    fx = await _seed(sess)
    await _identity_held(sess, fx, monkeypatch)
    adviser = await _adviser(sess, fx)
    aml = await _aml_check(sess, fx, "refer")
    await sess.execute(
        text(
            "INSERT INTO compliance.matches (tenant_id, check_id, provider_match_id, name, "
            "  match_types, decision, comment) "
            "VALUES (:t, :k, :p, 'Dismissed Person', ARRAY['watchlist'], 'dismissed', :c)"
        ),
        {"t": fx["tenant"], "k": aml, "p": f"dismissed_{aml}", "c": COMMENT},
    )

    async def matches() -> dict[str, Any]:
        return await client_identity.get_contact_aml_matches(
            fx["contact"], principal=adviser, session=sess
        )

    # Held on its confirmed match: Graphene's to review, so the firm reads nothing of it.
    assert (await matches())["matches"] == []

    # Graphene lets the journey continue past it (``aml.allow_journey``).
    await sess.execute(
        text(
            "UPDATE compliance.checks SET decided_by = 'Rita Reviewer', decided_at = now(), "
            "  decision_comment = :c WHERE id = :k"
        ),
        {"c": COMMENT, "k": aml},
    )
    shown = await matches()
    assert [match["name"] for match in shown["matches"]] == ["Aleksandr Matchmann"]
    assert shown["matches"][0]["match_types"] == ["pep"]
    assert shown["allowed_at"] is not None
    first = shown["matches"][0]
    # The console's profile of the match, without Graphene's decision or its comment.
    for key in ("images", "names", "roles", "notes", "references", "source_links"):
        assert key in first
    for withheld in ("decision", "decided_by", "decided_at", "comment"):
        assert withheld not in first

    # Its report is now offered, passed, with the confirmed match and nothing Graphene wrote.
    state = await client_identity.get_contact_compliance_reports(
        fx["contact"], principal=adviser, session=sess
    )
    assert state["aml"]["available"] is True
    response = await client_identity.get_contact_compliance_report(
        fx["contact_uid"],
        "aml",
        fx["contact"],
        check=None,
        principal=adviser,
        session=sess,
    )
    assert response.body == PDF
    aml_page = pages[-1]
    assert "Confirmed matches" in aml_page and "Aleksandr Matchmann" in aml_page
    assert "Passed" in aml_page
    for withheld in ("Dismissed Person", "Rita Reviewer", COMMENT, "Referred"):
        assert withheld not in aml_page


def test_an_allowed_screen_reads_as_the_console_reads_it_for_operations_and_passed_for_a_firm() -> (
    None
):
    allowed = {
        "kind": "aml",
        "status": "refer",
        "origin": "portal",
        "decided_at": datetime.now(UTC),
    }
    assert reports._status(allowed, "operations") == ("Allowed to continue", "ok")
    assert reports._status(allowed, "firm") == ("Passed", "ok")
    # An identity check decided while referred (new photos asked for) is not an allowed screen.
    assert reports._status({**allowed, "kind": "identity"}, "operations")[0] == "Referred"
