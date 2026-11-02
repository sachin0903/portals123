"""A client ComplyCube no longer holds — their record was made under another key (live and test are
separate accounts), or deleted there — is bound again rather than asked for photos in a loop.

Against real Postgres with a fake ComplyCube that forgets the client, on the identity capture
tests' own journey: an operator asks for a new selfie, ComplyCube loses the client, and the client
sends the selfie.

* the send binds the contact to a client ComplyCube holds and sends the whole try again from our
  copies: the check starts, and the client is asked for nothing more;
* a photo we hold no copy of is the only one asked for again, at the same attempt.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.idv import capture
from app.routers import aml as aml_router
from tests.test_identity_capture import (
    FakeComplyCube,
    _capture,
    _console,
    _instantiate,
    _items,
    _latest_check,
    _result,
    _rows,
    _seed,
    _try,
)


@pytest.fixture
def complycube(monkeypatch: pytest.MonkeyPatch) -> None:
    """ComplyCube configured, and a master key to seal the client's photos with."""
    monkeypatch.setattr(settings, "complycube_api_key", "test-key")
    monkeypatch.setattr(settings, "secrets_master_key", Fernet.generate_key().decode())


async def _provider_client(sess: AsyncSession, fx: dict[str, Any]) -> str:
    (row,) = await _rows(
        sess,
        "SELECT provider_client_id FROM compliance.clients WHERE contact_id = :c",
        c=fx["contact"],
    )
    return str(row["provider_client_id"])


async def _selfie_asked_then_client_lost(
    sess: AsyncSession, fx: dict[str, Any], fake: FakeComplyCube, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, dict[str, Any]]:
    """A licence try held for review, a new selfie asked for, and then ComplyCube forgets the
    client and everything it held of them."""
    await _instantiate(sess, fx, "complycube")
    await _try(sess, fx, fake, monkeypatch, "driving_licence")
    await _result(sess, fx, "refer")
    held = await _latest_check(sess, fx)
    await aml_router.request_photos(
        str(held["uid"]),
        aml_router.PhotoRequest(photos=["selfie"], reason="face", comment="Selfie is dark"),
        ctx=await _console(sess, fx, monkeypatch),
    )
    old_client = await _provider_client(sess, fx)
    fake.unknown_clients.add(old_client)
    fake.gone.add(str(held["provider_document_id"]))
    fake.live_photos.pop(str(held["provider_live_photo_id"]), None)
    return old_client, held


@pytest.mark.asyncio
async def test_a_lost_client_is_bound_again_and_the_try_sent_from_our_copies(
    service_session: AsyncSession, complycube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    old_client, held = await _selfie_asked_then_client_lost(sess, fx, fake, monkeypatch)

    body = await _try(sess, fx, fake, monkeypatch, "driving_licence", keys=(capture.SELFIE,))

    assert body["status"] == "submitted"
    new_client = await _provider_client(sess, fx)
    assert new_client != old_client
    made = await _latest_check(sess, fx)
    assert made["uid"] != held["uid"]
    assert made["attempt"] == 2
    # A new document made from our copy of both sides, under the client ComplyCube holds.
    assert made["provider_document_id"] != held["provider_document_id"]
    assert set(fake.documents[str(made["provider_document_id"])]) == {"front", "back"}


@pytest.mark.asyncio
async def test_only_a_photo_we_hold_no_copy_of_is_asked_for_again(
    service_session: AsyncSession, complycube: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = service_session
    fx = await _seed(sess)
    fake = FakeComplyCube(fx["nonce"])
    await _selfie_asked_then_client_lost(sess, fx, fake, monkeypatch)
    await sess.execute(
        text("DELETE FROM compliance.identity_photos WHERE case_id = :c AND item_key = :k"),
        {"c": fx["case"], "k": capture.BACK},
    )

    body = await _try(sess, fx, fake, monkeypatch, "driving_licence", keys=(capture.SELFIE,))

    assert body["status"] == "retake"
    assert body["attempt"] == 2
    items = _items(body)
    assert items[capture.BACK][1] == "open"
    assert items[capture.FRONT][1] == "fulfilled"
    assert items[capture.SELFIE][1] == "fulfilled"
    # The client sends the back, and the try goes whole to the client ComplyCube holds.
    body = await _try(sess, fx, fake, monkeypatch, "driving_licence", keys=(capture.BACK,))
    assert body["status"] == "submitted"
    assert (await _capture(sess, fx))["attempt"] == 2
