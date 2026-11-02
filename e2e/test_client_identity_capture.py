"""Authenticated client E2E — the in-portal identity photo capture.

Requires the client to have an open ComplyCube identity step (start a journey for their contact
from the platform portal first). Chromium's fake camera stands in for a real one, so the live view,
Take photo and Use photo all run for real.

Taking the photos is in scope; sending them is not by default. Submit sends the photos straight to
ComplyCube and starts an identity check, an external call. Set E2E_COMPLYCUBE_SUBMIT=1 to run that step against
a sandbox key.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from conftest import CLIENT_URL, _dev_token
from playwright.sync_api import Browser, Page, expect, sync_playwright

SUBMIT = os.environ.get("E2E_COMPLYCUBE_SUBMIT") == "1"


@pytest.fixture(scope="module")
def camera_browser() -> Iterator[Browser]:
    """A Chromium with a fake camera that grants access without a prompt."""
    with sync_playwright() as p:
        b = p.chromium.launch(
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ],
        )
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def camera_page(camera_browser: Browser) -> Iterator[Page]:
    ctx = camera_browser.new_context(permissions=["camera"])
    ctx.add_cookies(
        [
            {
                "name": "dev-auth",
                "value": _dev_token(role="client", permissions=[]),
                "url": CLIENT_URL,
            }
        ]
    )
    page = ctx.new_page()
    try:
        yield page
    finally:
        ctx.close()


def _open_identity(page: Page) -> None:
    # Not "networkidle": the page polls while a check runs, so the network is never idle.
    page.goto(f"{CLIENT_URL}/onboarding", wait_until="domcontentloaded")
    if "/login" in page.url:
        pytest.skip("the client portal refused the dev login (DEMO_AUTH_BYPASS is off)")
    expect(page.get_by_role("heading", name="Onboarding")).to_be_visible(timeout=30_000)
    start = page.get_by_role("button", name="Verify identity")
    if start.count() == 0:
        pytest.skip(
            "client has no open identity step — start a journey from the platform first"
        )
    start.first.click()
    if page.get_by_text("Choose your photo ID").count() == 0:
        pytest.skip(
            "the identity step is not a ComplyCube capture (manual or not configured)"
        )


def _take(page: Page, label: str) -> None:
    """Take the photo the group named ``label`` asks for, with the fake camera, and accept it."""
    group = page.get_by_role("group", name=label)
    expect(group).to_be_visible()
    # A document step opens on two choices; the camera starts only once
    # "Take a photo" is chosen.
    choose_camera = group.get_by_role("button", name="Take a photo", exact=True)
    if choose_camera.count():
        choose_camera.click()
    take = group.get_by_role("button", name="Take photo", exact=True)
    expect(take).to_be_enabled(timeout=15_000)
    take.click()
    group.get_by_role("button", name="Use photo").click()


def test_a_passport_is_two_photos_and_waits_for_the_likeness(camera_page: Page) -> None:
    page = camera_page
    _open_identity(page)

    page.get_by_role("radio", name="Passport").click()
    page.get_by_role("button", name="Continue").click()

    _take(page, "The photo page of your passport")
    # A passport has no back: the next photo is the face.
    _take(page, "A photo of your face")

    expect(page.get_by_text("Check your photos")).to_be_visible()
    submit = page.get_by_role("button", name="Submit photos")
    expect(submit).to_be_disabled()
    page.get_by_role("checkbox").click()
    expect(submit).to_be_enabled()

    if not SUBMIT:
        return
    submit.click()
    expect(page.get_by_text("Checking your photos")).to_be_visible(timeout=30_000)


def test_a_driving_licence_asks_for_the_back(camera_page: Page) -> None:
    page = camera_page
    _open_identity(page)

    page.get_by_role("radio", name="Driving licence").click()
    page.get_by_role("button", name="Continue").click()
    _take(page, "The front of your driving licence")
    expect(
        page.get_by_role("group", name="The back of your driving licence")
    ).to_be_visible()
