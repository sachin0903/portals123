"""Authenticated platform E2E — flow authoring + case lifecycle.

Requires E2E_PLATFORM_STATE (a captured platform-portal login). Skips side-effecting actions:
NO case invite (that mints a WorkOS login + emails). Creating flows/cases + setting up an account
are DB-only and safe. Each created entity is uniquely named so re-runs don't collide.
"""

from __future__ import annotations

import re
import time

from playwright.sync_api import Page, expect

from conftest import PLATFORM_URL


def test_flows_list_shows_seeded_onboarding(platform_page: Page) -> None:
    platform_page.goto(f"{PLATFORM_URL}/flows", wait_until="networkidle")
    expect(platform_page.get_by_text("Onboarding", exact=False).first).to_be_visible()


def test_author_and_publish_a_flow(platform_page: Page) -> None:
    """Create a flow, add a step, save the draft, publish — the authoring happy path."""
    suffix = str(int(time.time()))
    page = platform_page
    page.goto(f"{PLATFORM_URL}/flows", wait_until="networkidle")
    page.get_by_role("button", name="New flow").click()
    page.get_by_label("Name").fill(f"E2E Flow {suffix}")
    # Code auto-fills from the name; ensure it's unique + valid.
    page.get_by_label("Reference code").fill(f"e2e_flow_{suffix}")
    page.get_by_role("button", name="Create flow").click()
    # Lands in the builder.
    expect(page).to_have_url(re.compile(r"/flows/[^/]+"))
    # Add a step, title it (the Title label is bare, so target the input by its placeholder),
    # save, publish.
    page.get_by_role("button", name="Add step").click()
    page.get_by_placeholder("e.g. Complete your fact find").first.fill("Your details")
    page.get_by_role("button", name="Save draft").click()
    expect(page.get_by_text("Draft saved.")).to_be_visible(timeout=10_000)
    page.get_by_role("button", name="Publish").click()
    # The header status badge flips to Published after the publish + reload.
    expect(page.get_by_text("Published", exact=True).first).to_be_visible(
        timeout=10_000
    )


def test_cases_page_loads(platform_page: Page) -> None:
    platform_page.goto(f"{PLATFORM_URL}/cases", wait_until="networkidle")
    expect(platform_page.get_by_role("heading", name="Cases")).to_be_visible()


def test_start_a_case_from_seeded_onboarding(platform_page: Page) -> None:
    """Start a case from the seeded Onboarding flow for the first available client (DB-only)."""
    page = platform_page
    page.goto(f"{PLATFORM_URL}/cases", wait_until="networkidle")
    page.get_by_role("button", name="Start onboarding").click()
    dialog = page.get_by_role("dialog")
    # Pick the flow (Onboarding). The seeded Onboarding has a client step, so a subject is
    # REQUIRED — also pick the first client, else the API (correctly) refuses.
    dialog.get_by_role("button", name="Flow").click()
    page.get_by_role("option", name="Onboarding", exact=False).first.click()
    dialog.get_by_role("button", name="Client").click()
    page.get_by_role("option").first.click()
    dialog.get_by_role("button", name="Start", exact=True).click()
    # The new case appears in the list; open it and confirm the detail page renders its steps.
    case_link = page.locator("a[href^='/cases/']").first
    expect(case_link).to_be_visible(timeout=10_000)
    case_link.click()
    expect(page).to_have_url(re.compile(r"/cases/[^/]+"))
    expect(page.get_by_text("Open your account")).to_be_visible(timeout=10_000)
