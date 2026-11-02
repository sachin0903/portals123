"""The send path falls back to the shipped default when a published override renders empty.

Publish already refuses a blank body, but that cannot cover a version published before the guard
existed, nor a body that is legitimately publishable yet merges to nothing for a particular send.
Without the post-merge check the fail-safe silently inverts: publishing an override becomes the one
way to make a client receive a subject with no body.

`FakeRenderSession` answers the render path's one SELECT, keeping these hermetic (no Docker). What
they establish is that the fallback BRANCH fires on the resolution rules that decide it; the copy it
produces is pinned as literals in test_email_shipped_copy_golden.py and its wrapping in
test_email_send_parity.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.email import render as email_render
from tests import email_support

_FakeSession = email_support.FakeRenderSession
_row = email_support.published_row


#: The merge context these tests render with — this module is about WHICH branch runs.
CONTEXT: dict[str, Any] = {
    "brand": {"name": "Ridley & Co", "logo_url": None, "accent": "#0f766e", "support_email": None},
    "first_name": "Bo",
    "portal_label": "Client Portal",
    "set_password_url": "https://portal.ridley.test/set-password?token=t",
    "document_name": "Suitability Report",
    "sent_by": "Ada Adviser",
    "message": "",
    "portal_url": "https://portal.ridley.test",
}


def fallback(code: str, context: dict[str, Any] | None = None) -> tuple[str, str]:
    """The shipped default for `code`, merged and put in the SAME envelope the override path uses,
    so a recipient cannot tell which branch a send took from the shape of what arrives."""
    return email_render.render_default(
        code, context if context is not None else CONTEXT, asset_origin=None
    )


@pytest.mark.asyncio
async def test_a_published_override_with_real_content_is_used() -> None:
    session = _FakeSession(_row("<p>Our own wording</p>"))

    subject, html = await email_render.render_or_default(
        session,  # type: ignore[arg-type]
        tenant_id=1,
        code="invite",
        context=CONTEXT,
    )

    assert subject == "Subject"
    assert "Our own wording" in html


@pytest.mark.asyncio
async def test_an_override_published_blank_falls_back_to_the_shipped_default() -> None:
    """The retroactive case: published before the publish guard existed."""
    session = _FakeSession(_row('<div style="padding:32px"></div>'))

    assert await email_render.render_or_default(
        session,  # type: ignore[arg-type]
        tenant_id=1,
        code="invite",
        context=CONTEXT,
    ) == fallback("invite")


@pytest.mark.asyncio
async def test_an_override_that_merges_to_nothing_falls_back_for_that_send_only() -> None:
    """A body whose only content is a token is publishable, but empty when the token is."""
    body = "<p>{{ message }}</p>"

    empty = _FakeSession(_row(body))
    assert await email_render.render_or_default(
        empty,  # type: ignore[arg-type]
        tenant_id=1,
        code="document_sent",
        context=CONTEXT | {"message": ""},
    ) == fallback("document_sent", CONTEXT | {"message": ""})

    filled = _FakeSession(_row(body))
    _subject, html = await email_render.render_or_default(
        filled,  # type: ignore[arg-type]
        tenant_id=1,
        code="document_sent",
        context=CONTEXT | {"message": "A note from your adviser"},
    )
    assert "A note from your adviser" in html


@pytest.mark.asyncio
async def test_an_image_only_override_is_content_and_is_not_discarded() -> None:
    """Guards the guard: `has_renderable_content` counts an image, so an image-only email -- a
    legitimate design -- must still be sent rather than silently replaced by the default."""
    session = _FakeSession(_row('<img src="https://cdn.example.com/hero.png" alt="Welcome">'))

    _subject, html = await email_render.render_or_default(
        session,  # type: ignore[arg-type]
        tenant_id=1,
        code="invite",
        context=CONTEXT,
    )

    assert "hero.png" in html


@pytest.mark.asyncio
async def test_no_published_override_still_uses_the_default() -> None:
    session = _FakeSession(None)

    assert await email_render.render_or_default(
        session,  # type: ignore[arg-type]
        tenant_id=1,
        code="invite",
        context=CONTEXT,
    ) == fallback("invite")


@pytest.mark.asyncio
async def test_render_published_email_reports_the_empty_case_as_absent() -> None:
    """The seam itself: callers distinguish "no override" from "an override" by None, so an override
    that renders empty must present as absent rather than as a RenderedEmail with a blank body."""
    session = _FakeSession(_row("<table><tr><td>&nbsp;</td></tr></table>"))

    assert (
        await email_render.render_published_email(
            session,  # type: ignore[arg-type]
            tenant_id=1,
            code="invite",
            context=CONTEXT,
        )
        is None
    )
