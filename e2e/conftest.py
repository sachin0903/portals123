"""E2E fixtures — real Chromium (Playwright, already a repo dep) driving the running dev servers.

Auth: the portals are behind WorkOS, so the authenticated suites load a **storageState** JSON that
you capture once by logging in yourself (see e2e/README.md) and point at via env:
  E2E_PLATFORM_STATE=/abs/path/platform-state.json
  E2E_CLIENT_STATE=/abs/path/client-state.json
When a state file is absent the matching fixtures ``pytest.skip`` — so the public/nav suite still
runs, and the authenticated suites light up the moment you provide the state.

Base URLs (override via env): API_URL, PLATFORM_URL, CLIENT_URL.
Run:  cd <repo> && uv run --project apps/api pytest e2e/ -q
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000")
PLATFORM_URL = os.environ.get("PLATFORM_URL", "http://localhost:5175")
CLIENT_URL = os.environ.get("CLIENT_URL", "http://localhost:5173")


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


# The dev principal E2E authenticates as: a real graphene login (so tenant + contact scope resolve).
# gio is a platform director + linked to contacts, so it works for both the staff and client views.
GIO_WORKOS_ID = os.environ.get("E2E_WORKOS_SUB", "user_01KWF1SDX8WDJV4ZXWX2H1N3M1")


def _dev_token(*, role: str, permissions: list[str] | None = None) -> str:
    """A `dev:<base64url-json>` token the DEMO_AUTH_BYPASS path accepts (dev only). org_id just needs
    to be non-null (project-per-tenant resolves the tenant from the login), so a placeholder is fine."""
    import base64
    import json

    payload = {
        "sub": GIO_WORKOS_ID,
        "email": "gio@grapheneplatforms.com",
        "first_name": "Gio",
        "last_name": "A",
        "org_id": "org_dev",
        "role": role,
        "permissions": permissions or [],
    }
    b = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"dev:{b}"


def _authed_context(
    browser: Browser, base_url: str, *, role: str, perms: list[str]
) -> BrowserContext:
    """A context carrying the `dev-auth` cookie the portals' hooks trust under DEMO_AUTH_BYPASS."""
    ctx = browser.new_context()
    ctx.add_cookies(
        [
            {
                "name": "dev-auth",
                "value": _dev_token(role=role, permissions=perms),
                "url": base_url,
            }
        ]
    )
    return ctx


@pytest.fixture
def public_page(browser: Browser) -> Iterator[Page]:
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        yield page
    finally:
        ctx.close()


@pytest.fixture
def platform_page(browser: Browser) -> Iterator[Page]:
    # role 'admin' → the portal's can() and the API's is_system both pass every gate.
    ctx = _authed_context(browser, PLATFORM_URL, role="admin", perms=["system"])
    page = ctx.new_page()
    try:
        yield page
    finally:
        ctx.close()


@pytest.fixture
def client_page(browser: Browser) -> Iterator[Page]:
    ctx = _authed_context(browser, CLIENT_URL, role="client", perms=[])
    page = ctx.new_page()
    try:
        yield page
    finally:
        ctx.close()
