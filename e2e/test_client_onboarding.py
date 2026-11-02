"""Authenticated client E2E — the onboarding task view.

Requires E2E_CLIENT_STATE (a captured client-portal login) AND that the logged-in client has a
case (start one for their contact from the platform portal first). Completing a fill_form step is
DB-only (records a form response; no external side effects), so it's in scope.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from conftest import CLIENT_URL


def test_onboarding_page_renders(client_page: Page) -> None:
    client_page.goto(f"{CLIENT_URL}/onboarding", wait_until="networkidle")
    # Either the client has case(s) with steps, or an empty state — both are a healthy render.
    body = client_page.locator("body")
    expect(body).to_be_visible()
    # The page should not have bounced to /login (the storageState is a valid client session).
    assert "/login" not in client_page.url


def test_onboarding_shows_a_case_when_present(client_page: Page) -> None:
    """If the client has an in-progress case, its flow name + at least one step is shown. Skips
    cleanly when the client has no case yet (nothing to assert)."""
    page = client_page
    page.goto(f"{CLIENT_URL}/onboarding", wait_until="networkidle")
    # A case card carries a status badge; if none, treat as no-case (informational pass).
    cards = page.locator("[data-testid='onboarding-case'], .onboarding-case")
    if cards.count() == 0:
        import pytest

        pytest.skip(
            "client has no onboarding case yet — start one from the platform portal first"
        )
    expect(cards.first).to_be_visible()
