"""Public / unauthenticated E2E — no login, no side effects (no form submits).

Proves the servers are up and the unauthenticated surface renders: API health, the public
onboarding /start page, the login pages, and that a protected route bounces to /login.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from conftest import API_URL, CLIENT_URL, PLATFORM_URL


def test_api_health(public_page: Page) -> None:
    res = public_page.request.get(f"{API_URL}/health")
    assert res.ok
    assert res.json()["status"] == "ok"


def test_client_start_page_renders(public_page: Page) -> None:
    """The public hybrid-entry page renders its capture form (we do NOT submit — side-effecting)."""
    public_page.goto(f"{CLIENT_URL}/start", wait_until="networkidle")
    # An email field is the heart of the start form.
    expect(
        public_page.locator("input[type='email'], input[name='email']").first
    ).to_be_visible()


def test_client_login_renders(public_page: Page) -> None:
    public_page.goto(f"{CLIENT_URL}/login", wait_until="networkidle")
    expect(public_page.locator("input[type='password']").first).to_be_visible()


def test_platform_protected_route_redirects_to_login(public_page: Page) -> None:
    """Unauthenticated, /flows must bounce to /login (per-route guard)."""
    public_page.goto(f"{PLATFORM_URL}/flows", wait_until="networkidle")
    expect(public_page).to_have_url(re.compile(r"/login"))
