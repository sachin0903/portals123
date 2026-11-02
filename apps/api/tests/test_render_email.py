"""The email render seam (app/email/document.py) — CSS inlining + document wrapping.

The Jinja merge is tested with the document pipeline; this covers only the email-specific output
step: folding CSS into inline `style` attributes and producing a complete, client-safe document.

It also pins the TWO style shapes the platform's email-template editor has produced in the wild —
see `_RULE_*` / `_INLINE_*` below. Both must survive the send path, because a firm's already-
published template is whichever shape the editor was emitting the day it was saved.
"""

import re

from app.email.document import EMAIL_BASE_CSS, MSO_CTA_INK, to_email_html


def _without_shell_conditionals(html: str) -> str:
    """The document minus the shell's own `[if mso]` block.

    Every `<style` assertion in this module means "no un-inlined author CSS survived, because email
    clients drop stylesheets". The shell now ships exactly one deliberate stylesheet — the CTA ink
    conditional, which is inside an HTML COMMENT and so is not a stylesheet to anything but Word.
    Stripping it by its literal (not by a pattern) keeps those assertions meaning what they said:
    a second `<style>` from anywhere still fails them."""
    return html.replace(MSO_CTA_INK, "")


# ---------------------------------------------------------------------------------------------
# The two style shapes a stored `schema.{html, css}` can carry. They describe the SAME email — a
# padded wrapper, a teal heading and the email-safe button pattern (background + radius on the
# <td>, padding/colour on the inner <a>) — so they can be compared declaration for declaration.

# Rule-shaped (LEGACY): every draft saved before the editor fix looks like this. No style
# attributes at all; the styling is id-keyed CSS rules in `schema.css`.
_RULE_HTML = (
    '<div id="iwrap">'
    '<h1 id="ihead">Welcome</h1>'
    '<table id="ibtnw"><tr><td id="icell">'
    '<a id="ibtn" href="https://portal.example.com">Set your password</a>'
    "</td></tr></table>"
    "</div>"
)
_RULE_CSS = (
    "#iwrap{padding:24px}"
    "#ihead{font-size:20px;color:#0f766e}"
    "#icell{background-color:#0f766e;border-radius:6px}"
    "#ibtn{padding:12px 24px;color:#ffffff;font-weight:600}"
)

# Inline-shaped (CURRENT): with grapesjs `avoidInlineStyle:false` + `forceClass:false` the editor
# writes the styling onto the elements and `schema.css` comes back empty.
_INLINE_HTML = (
    '<div id="iwrap" style="padding:24px">'
    '<h1 id="ihead" style="font-size:20px;color:#0f766e">Welcome</h1>'
    '<table id="ibtnw"><tr>'
    '<td id="icell" style="background-color:#0f766e;border-radius:6px">'
    '<a id="ibtn" href="https://portal.example.com" '
    'style="padding:12px 24px;color:#ffffff;font-weight:600">Set your password</a>'
    "</td></tr></table>"
    "</div>"
)

# Every element the two shapes style, plus the <table> the email base CSS reaches.
_STYLED_IDS = ("iwrap", "ihead", "ibtnw", "icell", "ibtn")


def _declarations(html: str, element_id: str) -> dict[str, str]:
    """The effective inline declarations on `id=element_id`, as property → value.

    Whitespace is normalised because css-inline re-serialises a declaration list it merged into
    (`color:#fff` → `color: #fff`) but leaves an untouched one byte-exact — so comparing the raw
    attribute text would fail on formatting rather than on styling.
    """
    tag = re.search(rf'<[a-zA-Z][^>]*\bid="{element_id}"[^>]*>', html)
    assert tag, f"no element with id={element_id!r} in:\n{html}"
    style = re.search(r'\bstyle="([^"]*)"', tag.group(0))
    assert style, f"element id={element_id!r} carries no style attribute: {tag.group(0)}"
    out: dict[str, str] = {}
    for declaration in style.group(1).split(";"):
        if ":" in declaration:
            prop, _, value = declaration.partition(":")
            out[prop.strip().lower()] = " ".join(value.split())
    return out


def test_inlines_template_and_base_css_into_style_attrs() -> None:
    body = '<table class="wrap"><tr><td class="cell">Hi</td></tr></table>'
    out = to_email_html(body, ".cell { color: #0f766e; }")

    assert out.startswith("<!DOCTYPE html>")
    assert "#0f766e" in out  # the template CSS was inlined onto the cell
    assert "border-collapse" in out  # the email base CSS was inlined onto the <table>
    assert 'style="' in out
    assert "<style" not in _without_shell_conditionals(out)  # nothing un-inlined survived


def test_wraps_a_complete_document() -> None:
    out = to_email_html("<p>Hi there</p>")
    assert "<!DOCTYPE html>" in out
    assert "<html" in out and "</html>" in out
    assert "charset" in out
    assert "Hi there" in out


def test_does_not_fetch_remote_stylesheets() -> None:
    # A remote <link> stylesheet must be ignored, never fetched (the render makes no requests).
    body = '<link rel="stylesheet" href="https://example.com/does-not-exist.css"><p>Body</p>'
    out = to_email_html(body)  # must not raise or hang on a network call
    assert "Body" in out


def test_preheader_is_prepended_as_a_hidden_preview_span() -> None:
    # The preheader must ship inside the email as a visually-hidden preview-text block.
    out = to_email_html("<p>Body</p>", preheader="Your March statement is ready")
    assert "Your March statement is ready" in out
    assert "display:none" in out  # hidden in the body itself
    # It sits before the body content.
    assert out.index("Your March statement is ready") < out.index("Body")


def test_no_preheader_span_when_absent() -> None:
    out = to_email_html("<p>Body</p>")
    assert "display:none" not in out


def test_is_not_an_html_sanitiser() -> None:
    # SECURITY BOUNDARY: to_email_html inlines CSS, it does NOT strip active content. An event
    # handler (and, where the inliner preserves it, a <script>) survives. The preview iframe's
    # sandbox="" (pkgs/ui email-preview-frame.svelte — the one path every preview renders through)
    # is what neutralises author script — this test pins that assumption so no one later treats
    # this function as a sanitiser.
    body = '<p>Hi</p><img src=x onerror="alert(1)">'
    out = to_email_html(body)
    assert "onerror" in out  # the handler is not stripped — the frontend sandbox must remain


def test_rule_shaped_body_inlines_its_id_keyed_css() -> None:
    """Prevents a legacy already-published template (styling only in `css`) sending unstyled."""
    # Guard the fixture: the whole point of this shape is that the markup carries NO styling, so if
    # it ever gains a style attribute the test stops proving anything.
    assert 'style="' not in _RULE_HTML

    out = to_email_html(_RULE_HTML, _RULE_CSS)

    assert _declarations(out, "ihead")["font-size"] == "20px"
    assert _declarations(out, "ihead")["color"] == "#0f766e"
    assert _declarations(out, "iwrap")["padding"] == "24px"
    assert _declarations(out, "icell")["background-color"] == "#0f766e"
    assert _declarations(out, "ibtn")["font-weight"] == "600"
    assert "<style" not in _without_shell_conditionals(out)  # nothing un-inlined survived


def test_inline_shaped_body_survives_an_empty_css() -> None:
    """Prevents an empty `schema.css` blanking a template whose styling is already inline."""
    out = to_email_html(_INLINE_HTML, "")

    assert _declarations(out, "ihead")["font-size"] == "20px"
    assert _declarations(out, "ihead")["color"] == "#0f766e"
    assert _declarations(out, "iwrap")["padding"] == "24px"
    assert _declarations(out, "icell")["background-color"] == "#0f766e"
    assert _declarations(out, "icell")["border-radius"] == "6px"
    assert _declarations(out, "ibtn")["padding"] == "12px 24px"
    assert _declarations(out, "ibtn")["color"] == "#ffffff"
    # The email base CSS still lands on top of the author's own inline styling.
    assert _declarations(out, "ibtnw")["border-collapse"] == "collapse"
    assert _declarations(out, "ibtn")["text-decoration"] == "none"


def test_both_style_shapes_resolve_to_the_same_declarations() -> None:
    """Prevents the editor's shape change altering how an equivalently-styled email renders."""
    rule = to_email_html(_RULE_HTML, _RULE_CSS)
    inline = to_email_html(_INLINE_HTML, "")

    # Not string equality of the two documents: css-inline re-serialises a merged declaration list,
    # so the attribute TEXT differs even where the resolved styling is identical.
    for element_id in _STYLED_IDS:
        assert _declarations(rule, element_id) == _declarations(inline, element_id), element_id


def test_a_styled_td_keeps_its_background_in_both_shapes() -> None:
    """Prevents the email-safe button regression: the cell losing its background/radius."""
    for out in (to_email_html(_RULE_HTML, _RULE_CSS), to_email_html(_INLINE_HTML, "")):
        cell = _declarations(out, "icell")
        assert cell["background-color"] == "#0f766e"  # the button's colour lives on the CELL
        assert cell["border-radius"] == "6px"
        link = _declarations(out, "ibtn")
        assert link["padding"] == "12px 24px"  # and its size/colour on the inner <a>
        assert link["color"] == "#ffffff"


def test_shorthand_declarations_are_not_expanded() -> None:
    """Pins css-inline 0.21 passing shorthands through, so an upgrade that expands them is seen."""
    # Verified behaviour, not an assumption: `background` and `border-radius` are carried across
    # verbatim rather than being resolved into their longhand components. If a css-inline upgrade
    # starts expanding them, the two shapes could stop matching declaration for declaration.
    from_rule = _declarations(
        to_email_html('<div id="d">x</div>', "#d{background:#0f766e;border-radius:6px}"), "d"
    )
    assert from_rule == {"background": "#0f766e", "border-radius": "6px"}
    from_inline = _declarations(
        to_email_html('<div id="d" style="background:#0f766e;border-radius:6px">x</div>', ""), "d"
    )
    assert from_inline == from_rule


def test_the_shell_ships_the_cta_ink_conditional_and_nothing_else_in_the_head() -> None:
    """LAYER 3 of the CTA ink fix (app/email/transport.py → THE BULLETPROOF BUTTON).

    Word's Hyperlink character style beats a plain inline `color`, and flagging that inline
    declaration is worse than useless — Word INVALIDATES a declaration carrying `!important`, which
    is how the label came out inheriting the shell's `#0f172a`, i.e. the button's own fill. In a
    STYLESHEET the flag works, so it lives here, behind `[if mso]` where no other client sees it.

    Four claims, because this ships in EVERY email including the vast majority with no button:
    it is present, it is a comment (so it is inert everywhere but Word), it is in the HEAD, and it
    is small. The size bound is deliberately generous — it is there to fail a head that has grown a
    stylesheet, not to police a byte.
    """
    out = to_email_html("<p>Body</p>")
    head = out[out.index("<head>") : out.index("</head>")]
    assert MSO_CTA_INK in head, "the CTA ink conditional is not in the document head"
    assert MSO_CTA_INK.startswith("<!--[if mso]>") and MSO_CTA_INK.endswith("<![endif]-->"), (
        "the conditional is not wrapped in an mso comment, so every client would apply it"
    )
    assert "a.cta{color:#ffffff !important}" in MSO_CTA_INK
    # A SPACE before the flag: Outlook 2019 needs one inside a <style> block (email-bugs#31).
    assert " !important" in MSO_CTA_INK
    # NOT `a.cta span`. `color` is inherited and an inherited value never competes in the cascade,
    # so the label span's own declaration wins — which is what keeps this from overruling a firm
    # that recolours its button. Adding the span here would silently take that away.
    assert "span" not in MSO_CTA_INK
    assert len(MSO_CTA_INK) < 120, f"the shell conditional has grown to {len(MSO_CTA_INK)} bytes"


def test_the_shell_conditional_is_never_inlined_onto_the_body() -> None:
    """It must reach the wire as source, not be folded into a `style` attribute — and it must not
    reach anything else either. `_document` runs AFTER `css_inline`, which is what guarantees both;
    a refactor that put the block in before inlining would have css-inline consume it (it is inside
    a comment, so most likely silently) and the flag would vanish with no test to say so."""
    out = to_email_html('<a class="cta" id="ibtn" href="https://p.test">Open portal</a>')
    assert MSO_CTA_INK in out
    # The anchor keeps the class (the hook the conditional needs) and gains no colour from it.
    assert 'class="cta"' in out
    assert "color" not in _declarations(out, "ibtn")


def test_an_important_flag_survives_inlining_on_an_element_a_rule_also_matched() -> None:
    """Not the CTA's mechanism any more — its flag lives in `MSO_CTA_INK`, not on the element — but
    still a property a firm's already-published template can depend on, and one an upgrade could
    take away silently: css-inline re-serialises the declaration list of any element a stylesheet
    matched, and `EMAIL_BASE_CSS` has `a { text-decoration: none }`, so every anchor we send IS
    re-serialised. Kept pointed at an anchor because that is the re-serialised case.
    """
    out = to_email_html(
        '<a id="ibtn" href="https://p.test" style="color:#ffffff !important;text-decoration:none">'
        "Open portal</a>",
        "",
    )
    assert _declarations(out, "ibtn")["color"] == "#ffffff !important"
    # And the flag genuinely wins where something competes: a stylesheet rule for the same property
    # must not overwrite the flagged inline declaration.
    out = to_email_html(
        '<a id="ibtn" href="https://p.test" style="color:#ffffff !important">Open portal</a>',
        "a { color: #1d4ed8; }",
    )
    assert _declarations(out, "ibtn")["color"] == "#ffffff !important"


def test_an_embedded_style_block_is_consumed_not_passed_through() -> None:
    """Prevents a pasted <style> block shipping as dead markup instead of inline styling."""
    out = to_email_html('<style>#ihead{color:#0f766e}</style><h1 id="ihead">Welcome</h1>')
    assert _declarations(out, "ihead")["color"] == "#0f766e"  # folded onto the element
    assert "<style" not in _without_shell_conditionals(out)  # and the block itself is gone
    assert "#ihead" not in out  # the rule's own text, gone with it


def test_the_body_carries_its_reset_inline_not_in_a_stylesheet() -> None:
    """THE BUG THIS PINS. `body { margin: 0; padding: 0 }` lived in `EMAIL_BASE_CSS` and applied to
    nothing: that stylesheet is inlined against the body FRAGMENT, which has no `<body>` element, so
    the rule matched nothing and was dropped — and `_document` then wrapped the result in a bare
    `<body>`. Every client's default 8px body margin therefore inset the whole email, and a page
    background could never reach the edges.

    Asserted on the rendered document rather than on the constant: the constant was right all
    along, it was where it lived that was wrong."""
    out = to_email_html("<table><tr><td>hi</td></tr></table>", "", preheader="")
    body_tag = re.search(r"<body[^>]*>", out)
    assert body_tag is not None
    assert "margin:0" in body_tag.group(0)
    assert "padding:0" in body_tag.group(0)
    # And the dead rule is gone from the stylesheet, so nobody re-adds it there believing it works.
    assert "body {" not in EMAIL_BASE_CSS


class TestClientQuirksTheKitsExistFor:
    """Two rules every email kit (react-email, svelte-email) ships in its head or its text
    components, checked here because a client-specific quirk is invisible in the canvas, in the
    preview and in a test send to anything but the client it affects."""

    def test_apple_is_told_not_to_reformat_the_message(self) -> None:
        """Without it Apple Mail and iOS resize the message to fit the viewport, rescaling the type
        and shrinking a 600px card — the design arrives smaller than it was authored."""
        from app.email.document import _document

        assert '<meta name="x-apple-disable-message-reformatting">' in _document("<p>x</p>")

    def test_every_line_height_is_paired_for_outlook(self) -> None:
        """Outlook ignores `line-height` and applies its own leading unless
        `mso-line-height-rule: exactly` sits beside it, so paragraphs arrive looser than
        authored."""
        from app.email.document import _document

        html = _document('<p style="line-height:1.6;color:red">x</p>')
        assert "mso-line-height-rule:exactly;line-height:1.6" in html
        # The neighbouring declaration is untouched — the first attempt at this was a stylesheet
        # rule, and the inliner rewrote every element it matched and DROPPED the CTA cell's own
        # background-color, so the button lost its fill.
        assert "color:red" in html

    def test_pairing_a_document_twice_changes_nothing(self) -> None:
        """The `line-height:` left behind the inserted rule is an ordinary match; without the second
        lookbehind a re-render would prefix it again, and again."""
        from app.email.document import _outlook_line_height

        once = _outlook_line_height('<p style="line-height: 1.6">x</p>')
        assert _outlook_line_height(once) == once
        assert once.count("mso-line-height-rule") == 1

    def test_the_property_name_is_not_matched_by_its_own_pattern(self) -> None:
        """`mso-line-height-rule` CONTAINS `line-height`. It is only safe because the next character
        is `-` rather than `:`, which is the kind of thing that stops being true when someone
        "tidies" the regex."""
        from app.email.document import _LINE_HEIGHT

        assert not _LINE_HEIGHT.search("mso-line-height-rule:exactly")


class TestThePreviewLineStopsWhereTheAuthorStopped:
    """A client shows the preheader and then keeps reading the BODY to fill the rest of the inbox
    preview, so a short preheader arrives followed by the email's own opening words — the author
    wrote one line and the inbox shows one and a half. Padding the hidden block with invisible
    characters means the only thing after the preheader is nothing."""

    def test_a_short_preheader_is_padded_out(self) -> None:
        from app.email.document import _PREVIEW_TARGET, _preheader_span

        block = _preheader_span("It's waiting for you in your portal.")
        content = block.split('">', 1)[1].rsplit("</div>", 1)[0]
        assert len(content) == _PREVIEW_TARGET

    def test_nothing_in_the_padding_can_be_drawn(self) -> None:
        """The whole block is hidden six ways over, but the padding must be invisible on its own
        merits too: it is the one part with no semantic reason to exist, so a client that ignores
        the hiding must still show nothing rather than a run of dots or boxes."""
        import unicodedata

        from app.email.document import _FILLER_CHARS, _preview_filler

        filler = _preview_filler("x")
        assert filler
        assert set(filler) <= set(_FILLER_CHARS)
        # CATEGORY, not membership. Asserting `c in _FILLER_CHARS` passed vacuously — the set itself
        # opened with U+00A0, a non-breaking SPACE that renders, copied from the kit. `Cf` is the
        # claim that means something: a format character no client can draw and no reader reads.
        assert all(unicodedata.category(c) == "Cf" for c in filler)

    def test_a_preheader_that_already_fills_the_line_gets_none(self) -> None:
        from app.email.document import _preview_filler

        assert _preview_filler("y" * 200) == ""

    def test_no_preheader_still_means_no_block(self) -> None:
        """The filler must not resurrect a block the caller asked for nothing in — an email with no
        preview line would otherwise ship 150 invisible characters and an empty div."""
        from app.email.document import _preheader_span

        assert _preheader_span("") == ""
        assert _preheader_span("   ") == ""

    def test_the_filler_rides_inside_the_hidden_block(self) -> None:
        """Not as a sibling: everything that hides the preheader has to hide the padding too, and a
        second element is a second thing to keep in step."""
        from app.email.document import _preheader_span

        block = _preheader_span("Short line.")
        assert block.count("<div") == 1
        assert "mso-hide:all" in block
