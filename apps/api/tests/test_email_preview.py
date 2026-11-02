"""Email-template preview rendering (app/email/preview.py).

Pure text-in → text-out: merge the editor's current content against representative sample values and
fold CSS inline. No DB, no network and — importantly — no send path. The sample keys mirror the FLAT
context the real send sites pass (first_name / document_name / item_titles …, brand nested).
"""

from app.email import document as render_email
from app.email import preview as email_preview
from app.email.transport import Brand


def test_render_preview_merges_sample_values_and_inlines() -> None:
    brand = Brand(name="Ridgeway Wealth", accent="#0f766e")
    result = email_preview.render_preview(
        subject="Welcome {{ first_name }}",
        preheader="A note from {{ brand.name }}",
        html="<p class='hi'>Hi {{ first_name }}, welcome to {{ brand.name }}.</p>",
        css=".hi{color:#0f766e}",
        brand=brand,
    )
    assert result.subject == "Welcome Sarah"  # sample recipient first name
    assert result.preheader == "A note from Ridgeway Wealth"  # real brand name
    assert "Hi Sarah, welcome to Ridgeway Wealth." in result.html
    assert result.html.startswith("<!DOCTYPE html>")
    assert "#0f766e" in result.html  # template CSS folded inline
    # Nothing un-inlined survived — MSO_CTA_INK, the one deliberate stylesheet, is stripped first.
    assert "<style" not in result.html.replace(render_email.MSO_CTA_INK, "")
    assert result.missing == []  # every token resolved from the sample context


def test_render_preview_keeps_an_inline_shaped_body_with_no_css() -> None:
    """The current editor shape (grapesjs `avoidInlineStyle:false`) puts styling on the elements
    and returns `css` empty, which must not preview unstyled. The rule-shaped legacy path is the
    other shape in the wild; declaration-level assertions for both live in
    tests/test_render_email.py."""
    result = email_preview.render_preview(
        subject="Welcome {{ first_name }}",
        preheader="",
        html=(
            '<table><tr><td style="background-color:#0f766e;border-radius:6px">'
            '<a href="{{ set_password_url }}" style="color:#ffffff;padding:12px 24px">'
            "Set your password</a></td></tr></table>"
        ),
        css="",
        brand=Brand(name="Ridgeway Wealth", accent="#0f766e"),
        code="invite",
    )
    assert "Set your password" in result.html
    assert "background-color:#0f766e" in result.html  # the cell's own styling was not blanked
    assert "border-radius:6px" in result.html
    assert result.missing == []


def test_render_preview_reports_unresolved_tokens() -> None:
    result = email_preview.render_preview(
        subject="Hi {{ first_name }}",
        preheader="",
        html="<p>{{ mystery_token }}</p>",
        css="",
        brand=Brand(),
    )
    assert "mystery_token" in result.missing  # unknown token surfaced
    assert "first_name" not in result.missing  # a provided token isn't flagged
    assert result.html.startswith("<!DOCTYPE html>")  # still renders around the blank


def test_sample_context_is_scoped_per_code() -> None:
    brand = Brand(name="Acme FS", accent="#123456", support_email="help@acme.test")
    invite = email_preview.sample_context(brand, "invite")
    assert invite["brand"]["name"] == "Acme FS"
    assert set(invite) == {
        "brand",
        "first_name",
        "full_name",
        "surname",
        "portal_label",
        "set_password_url",
    }
    assert "document_name" not in invite
    doc = email_preview.sample_context(brand, "document_sent")
    assert {"document_name", "sent_by", "message", "portal_url"} <= set(doc)
    # Both task codes take the same five keys — one shape, two near-identical templates, chosen by
    # the notification rule's `email_code`.
    # `is_reminder` is deliberately gone with the branch the single `task_notice` template had.
    for code in ("task_assigned", "task_reminder"):
        task = email_preview.sample_context(brand, code)
        assert {
            "task_title",
            "task_description",
            "due_date",
            "button_url",
            "button_label",
        } <= set(task)
        assert "is_reminder" not in task, code
        assert "document_name" not in task
    all_keys = email_preview.sample_context(brand)
    assert {"first_name", "document_name", "item_titles", "set_password_url", "due_date"} <= set(
        all_keys
    )


def test_preview_flags_a_token_the_code_does_not_provide() -> None:
    result = email_preview.render_preview(
        subject="",
        preheader="",
        html="<p>{{ document_name }}</p>",
        css="",
        brand=Brand(),
        code="invite",
    )
    assert "document_name" in result.missing


def test_sample_context_passes_a_null_support_email_through_unchanged() -> None:
    """This used to substitute "support@example.com" for an unset support email — a preview that
    manufactured confidence, since the real send has no such address to produce."""
    ctx = email_preview.sample_context(Brand())
    assert ctx["brand"]["support_email"] is None
    assert (
        email_preview.sample_context(Brand(support_email="h@a.test"))["brand"]["support_email"]
        == "h@a.test"
    )


def test_the_viewer_is_named_in_their_own_preview_and_test_send() -> None:
    """The first real test email addressed its recipient "Hi Sarah," — mail for somebody else,
    landing in the author's own inbox. `build_preview` and `send_test` call the SAME
    `render_preview`, so the name has to arrive here rather than at the send, or the preview an
    author just looked at would disagree with the email they received."""
    assert email_preview.sample_context(Brand())["first_name"] == "Sarah"
    assert email_preview.sample_context(Brand(), viewer_first_name="Craig")["first_name"] == "Craig"

    rendered = email_preview.render_preview(
        subject="Hi {{ first_name }}",
        preheader="",
        html="<p>Hi {{ first_name }},</p>",
        css="",
        brand=Brand(name="Ridley & Co"),
        code="invite",
        viewer_first_name="Craig",
    )
    assert rendered.subject == "Hi Craig"
    assert "Hi Craig," in rendered.html
    assert "Sarah" not in rendered.html


def test_a_blank_viewer_name_leaves_the_fixture_alone() -> None:
    """An operator on the admin door holds no row in the firm's tenant, and a user row may carry
    no first name — so this degrades to the sample rather than rendering "Hi ,". Whitespace counts
    as absent, hence stripped rather than truth-tested."""
    for blank in (None, "", "   "):
        assert (
            email_preview.sample_context(Brand(), viewer_first_name=blank)["first_name"] == "Sarah"
        )


def test_password_reset_stays_nameless_even_for_the_viewer() -> None:
    """`_CODE_SAMPLE_OVERRIDES` forces first_name=None because that send site hard-codes it: a
    personalised reset email is an account-existence oracle. Substituting the viewer's name would
    manufacture confidence in copy that always renders without one."""
    ctx = email_preview.sample_context(Brand(), "password_reset", viewer_first_name="Craig")
    assert ctx["first_name"] is None


def test_a_template_the_engine_cannot_parse_is_a_refusal_not_a_500() -> None:
    """An author who types `{% if %}` used to get a 500 from Preview and Send test.

    `render_preview` RAISES on a parse failure — the router called it unguarded, so a half-finished
    condition read as the platform being broken rather than the author's own mistake. The engine's
    own message names the construct and is client-safe (it never quotes the context).

    An UNDEFINED value is deliberately NOT this: `render_with_diagnostics` reports it in `missing`,
    so a legitimate draft with an unfilled token still previews.
    """
    import pytest
    from fastapi import HTTPException

    from app.email.transport import GRAPHENE_BRAND
    from app.routers.email_templates import _render_or_422

    args = {
        "subject": "x",
        "preheader": "",
        "css": "",
        "brand": GRAPHENE_BRAND,
        "code": "invite",
    }
    with pytest.raises(HTTPException) as caught:
        _render_or_422(html="{% if %}", **args)
    assert caught.value.status_code == 422
    assert "cannot be previewed" in str(caught.value.detail)

    # An UNKNOWN filter is refused at compile time — a `TemplateError` like the syntax case above.
    with pytest.raises(HTTPException) as caught:
        _render_or_422(html="{{ first_name | no_such_filter }}", **args)
    assert caught.value.status_code == 422

    # A MISUSED one is not, and this is the case a narrow `except TemplateError` let through as a
    # 500: Jinja does not wrap what a filter throws, so rounding a string surfaces a plain
    # `TypeError` from inside the filter. Same author mistake, same refusal — `first_name` is a real
    # field with a real sample value here, so this does not depend on the token being unsourced.
    for html in ("{{ first_name | round(2) }}", "{{ first_name | truncate_text('five') }}"):
        with pytest.raises(HTTPException) as caught:
            _render_or_422(html=html, **args)
        assert caught.value.status_code == 422, html
        assert "cannot be previewed" in str(caught.value.detail), html

    # And a token nothing fills still renders — reported, never refused.
    ok = _render_or_422(html="<p>Hi {{ nobody_supplies_this }}</p>", **args)
    assert "nobody_supplies_this" in ok.missing
