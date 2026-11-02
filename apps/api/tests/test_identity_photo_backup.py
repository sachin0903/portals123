"""Graphene's copy of a client's identity photos, the backup of what ComplyCube holds.

Against real Postgres with a fake ComplyCube, on the identity capture tests' own journey:

* the console shows ComplyCube's images first, and our copy of any image ComplyCube cannot give;
* a copy is kept while its step is open or its check undecided: a check held for review keeps its
  photos when its journey is cancelled, and they go once an operator decides it;
* a check with no image anywhere says why.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube import decisions
from app.complycube.client import ComplyCubeNotConfigured, ScreeningClient
from app.config import settings
from app.idv import capture
from app.idv import held as idv_held
from app.routers import aml as aml_router
from tests.test_identity_capture import (
    PHOTOS,
    FakeComplyCube,
    _console,
    _held_copies,
    _instantiate,
    _latest_check,
    _result,
    _seed,
    _try,
)


@pytest.fixture
def complycube(monkeypatch: pytest.MonkeyPatch) -> None:
    """ComplyCube configured, and a master key to seal the client's photos with."""
    monkeypatch.setattr(settings, "complycube_api_key", "test-key")
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())


async def _held_try(
    sess: AsyncSession, fx: dict[str, Any], fake: FakeComplyCube, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """The client's licence try, which ComplyCube did not clear: held for an operator."""
    await _instantiate(sess, fx, "complycube")
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    await _result(sess, fx, "refer")
    monkeypatch.setattr(aml_router, "require_client", fake.client)
    return await _latest_check(sess, fx)


def _forget(fake: FakeComplyCube, check: dict[str, Any]) -> None:
    """ComplyCube no longer holds the check's document or live photo."""
    fake.gone.add(str(check["provider_document_id"]))
    fake.live_photos.pop(str(check["provider_live_photo_id"]), None)


@pytest.mark.asyncio
async def test_the_console_shows_our_copy_of_an_image_complycube_cannot_give(
    service_session: AsyncSession, complycube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    held = await _held_try(sess, fx, fake, monkeypatch)
    ctx = await _console(sess, fx, monkeypatch)
    listed = (await aml_router.get_check(str(held["uid"]), ctx=ctx))["identity"]
    assert [(i["part"], i["source"]) for i in listed["images"]] == [  # type: ignore[index]
        ("front", "complycube"),
        ("back", "complycube"),
        ("selfie", "complycube"),
    ]

    _forget(fake, held)

    listed = (await aml_router.get_check(str(held["uid"]), ctx=ctx))["identity"]
    assert [(i["part"], i["source"]) for i in listed["images"]] == [  # type: ignore[index]
        ("front", "graphene"),
        ("back", "graphene"),
        ("selfie", "graphene"),
    ]
    assert listed["images_missing"] is None  # type: ignore[index]
    shown = await aml_router.get_check_image(str(held["uid"]), "selfie", ctx=ctx)
    assert shown.body == PHOTOS[capture.SELFIE][0]


@pytest.mark.asyncio
async def test_a_held_check_keeps_its_photos_after_its_journey_is_cancelled(
    service_session: AsyncSession, complycube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    held = await _held_try(sess, fx, fake, monkeypatch)
    kept = await _held_copies(sess, fx)
    assert set(kept) == set(capture.KEYS)

    await sess.execute(
        text("UPDATE flows.case_steps SET status = 'cancelled' WHERE case_id = :c"),
        {"c": fx["case"]},
    )
    await idv_held.release_finished(sess, tenant_id=fx["tenant"])
    # Undecided: the operator still needs them.
    assert await _held_copies(sess, fx) == kept

    await aml_router.decide_check(
        str(held["uid"]),
        aml_router.CheckDecisionRequest(decision="rejected", comment="Not the client"),
        ctx=await _console(sess, fx, monkeypatch),
    )
    assert await _held_copies(sess, fx) == {}


@pytest.mark.asyncio
async def test_a_check_with_no_image_anywhere_says_why(
    service_session: AsyncSession, complycube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    held = await _held_try(sess, fx, fake, monkeypatch)
    _forget(fake, held)
    await sess.execute(
        text("DELETE FROM compliance.identity_photos WHERE check_id = :c"), {"c": held["id"]}
    )
    ctx = await _console(sess, fx, monkeypatch)

    listed = (await aml_router.get_check(str(held["uid"]), ctx=ctx))["identity"]
    assert listed["images"] == []  # type: ignore[index]
    assert listed["images_missing"] == "not_held"  # type: ignore[index]

    def unconfigured() -> ScreeningClient:
        raise ComplyCubeNotConfigured("ComplyCube is not configured.")

    monkeypatch.setattr(decisions, "require_client", unconfigured)
    listed = (await aml_router.get_check(str(held["uid"]), ctx=ctx))["identity"]
    assert listed["images_missing"] == "not_configured"  # type: ignore[index]
