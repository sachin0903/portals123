"""A firm's window onto its client's identity checks (``app.routers.client_identity``).

Against real Postgres with a fake ComplyCube, on the identity capture tests' own journey, as an
adviser of the client's firm. ``book_contact_id`` (RLS) resolving the client is the existing gate
and is not re-tested here. What is:

* a firm reads each check's status in its own vocabulary — anything with Graphene is "in progress",
  never "held" — with the document type, who uploaded the photos, the photos of the current check or
  any earlier one, and that check's analysis; viewing the photos is recorded in the firm's own
  trail; never Graphene's decision comment;
* reading needs ``screening:read``;
* a firm never uploads photos: there is no such route.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.config import settings
from app.idv import capture
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


@pytest.fixture
def complycube(monkeypatch: pytest.MonkeyPatch) -> None:
    """ComplyCube configured, and a master key to seal the client's own photos with."""
    monkeypatch.setattr(settings, "complycube_api_key", "test-key")
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())


async def _adviser(sess: AsyncSession, fx: dict[str, Any]) -> str:
    """An adviser of the client's firm, named on the session's claims."""
    if "adviser" not in fx:
        user = str(uuid.uuid4())
        await sess.execute(
            text(
                "INSERT INTO auth.users (id, tenant_id, email, first_name, last_name, "
                "  workos_user_id) VALUES (:id, :t, :e, 'Ada', 'Adviser', :w)"
            ),
            {
                "id": user,
                "t": fx["tenant"],
                "e": f"ada_{fx['nonce']}@t.test",
                "w": f"wos_ada_{fx['nonce']}",
            },
        )
        fx["adviser"] = user
    await sess.execute(
        text("SELECT set_config('request.jwt.claims', :c, true)"),
        {"c": json.dumps({"sub": f"wos_ada_{fx['nonce']}", "user_uuid": fx["adviser"]})},
    )
    return str(fx["adviser"])


def _principal(fx: dict[str, Any], *permissions: str) -> Principal:
    return Principal(
        user_id=f"wos_ada_{fx['nonce']}",
        organisation_id="org_1",
        role="adviser",
        permissions=list(permissions),
    )


async def _read(
    sess: AsyncSession, fx: dict[str, Any], *permissions: str, check: str | None = None
) -> dict[str, Any]:
    await _adviser(sess, fx)
    return await client_identity.get_contact_identity(
        fx["contact_uid"],
        fx["contact"],
        check=check,
        principal=_principal(fx, *(permissions or ("screening:read",))),
        session=sess,
    )


async def _settle(sess: AsyncSession, check_uid: object) -> None:
    """Leave no check pending: the poller's own tests count every pending check in the database."""
    await sess.execute(
        text("UPDATE compliance.checks SET status = 'clear' WHERE uid = :u"), {"u": str(check_uid)}
    )


def test_the_firm_reads_anything_with_graphene_as_in_progress() -> None:
    assert client_identity.firm_status("pending", False) == "in_progress"
    assert client_identity.firm_status("refer", False) == "in_progress"
    assert client_identity.firm_status("failed", False) == "in_progress"
    assert client_identity.firm_status("clear", True) == "passed"
    assert client_identity.firm_status("rejected", True) == "failed"
    # A try closed for newer photos.
    assert client_identity.firm_status("refer", True) == "superseded"


@pytest.mark.asyncio
async def test_a_firm_reads_its_clients_checks_and_never_a_result(
    service_session: AsyncSession, complycube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    await _instantiate(sess, fx, "complycube")
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    await _result(
        sess,
        fx,
        "refer",
        result={
            "outcome": "attention",
            "breakdown": {
                "faceAnalysis": {
                    "facialSimilarity": "attention",
                    "breakdown": {"facialSimilarityScore": 0},
                }
            },
        },
    )
    await sess.execute(
        text(
            "UPDATE compliance.checks SET decision_comment = 'Looks forged' WHERE kyc_check_id = :k"
        ),
        {"k": fx["kyc"]},
    )

    page = await _read(sess, fx)

    assert set(page) == {"checks", "shown", "analysis", "images", "images_missing"}
    (check,) = page["checks"]
    assert check["status"] == "in_progress"
    assert check["document_type"] == "driving_licence"
    assert check["uploaded_by"] is None
    assert set(check) == {
        "uid",
        "status",
        "document_type",
        "uploaded_by",
        "created_at",
        "updated_at",
    }
    assert page["shown"] == check["uid"]
    assert [image["part"] for image in page["images"]] == ["front", "back", "selfie"]
    # ComplyCube's analysis of the check, as the AML console lays it out; never Graphene's comment.
    assert page["analysis"]["groups"] == [
        {
            "key": "faceAnalysis",
            "label": "Face analysis",
            "items": [
                {
                    "key": "faceAnalysis.facialSimilarity",
                    "label": "Facial similarity",
                    "outcome": "attention",
                }
            ],
        }
    ]
    assert [score["value"] for score in page["analysis"]["scores"]] == [0]
    assert "Looks forged" not in json.dumps(page, default=str)
    viewed = await _rows(
        sess,
        "SELECT event_type, actor_id, path_params FROM audit.actions "
        "WHERE event_type = 'client.identity_images_viewed' AND tenant_id = :t",
        t=fx["tenant"],
    )
    assert len(viewed) == 1
    assert str(viewed[0]["actor_id"]) == fx["adviser"]

    with pytest.raises(HTTPException) as raised:
        await _read(sess, fx, "contacts:read")
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_any_earlier_check_opens_with_its_photos(
    service_session: AsyncSession, complycube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two tries: an operator asked for a new selfie after the first. The firm reads both, and the
    first's photos on asking for it; a check that is not this client's is not found."""
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    await _instantiate(sess, fx, "complycube")
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    await _result(sess, fx, "refer")
    first = await _latest_check(sess, fx)
    await aml_router.request_photos(
        str(first["uid"]),
        aml_router.PhotoRequest(photos=["selfie"], reason="face", comment="Selfie is dark"),
        ctx=await _console(sess, fx, monkeypatch),
    )
    await _try(sess, fx, fake, monkeypatch, "driving_licence", keys=(capture.SELFIE,))
    second = await _latest_check(sess, fx)

    page = await _read(sess, fx)
    assert [c["uid"] for c in page["checks"]] == [second["uid"], first["uid"]]
    assert [c["status"] for c in page["checks"]] == ["in_progress", "superseded"]
    assert page["shown"] == second["uid"]

    earlier = await _read(sess, fx, check=str(first["uid"]))
    assert earlier["shown"] == first["uid"]
    assert [image["part"] for image in earlier["images"]] == ["front", "back", "selfie"]
    with pytest.raises(HTTPException) as raised:
        await _read(sess, fx, check="not-this-clients")
    assert raised.value.status_code == 404
    await _settle(sess, second["uid"])


@pytest.mark.asyncio
async def test_an_operators_upload_reads_as_graphenes(
    service_session: AsyncSession, complycube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    await _instantiate(sess, fx, "complycube")
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    await _result(sess, fx, "refer")
    held = await _latest_check(sess, fx)
    await sess.execute(
        text(
            "UPDATE compliance.checks SET uploaded_by = 'Olive Operator', "
            "  uploaded_via = 'operations' WHERE id = :id"
        ),
        {"id": held["id"]},
    )
    page = await _read(sess, fx)
    assert page["checks"][0]["uploaded_by"] == client_identity.GRAPHENE


def test_a_firm_cannot_upload_identity_photos() -> None:
    """Only Graphene operations upload a client's identity photos (the AML console); the firm's
    side has no such route, whatever its permissions."""
    from app.main import app
    from tests.conftest import api_routes

    firm_routes = {r.path for r in api_routes(app) if r.path.startswith("/crm/contacts/")}
    assert "/crm/contacts/{contact_uid}/identity-photos" not in firm_routes
    assert "/crm/contacts/{contact_uid}/identity" in firm_routes
