"""The email house chrome — the masthead, the button, the footer and the card around them.

Every email Graphene sends wears this, whichever path produced it: ``email_defaults`` builds the
shipped editor defaults from these parts as Jinja SOURCE, unescaped (Jinja's autoescape escapes the
merged VALUE later), and :func:`shell` renders the same parts NOW, escaped, for the flow tier.

ONE AUTHORING, FROM PARTS THAT ARE ALREADY HTML-SAFE. The two shapes must never grow separate
builders — that produced six copy divergences. Both callers compose the functions below; neither
writes chrome markup of its own.

This module holds no credential, opens no connection and knows nothing about delivery: the envelope
identity, the non-production recipient guard and the transport all live in ``app.email``, which this
module deliberately does not import beyond :class:`~app.email.Brand`. Pure text in, text out.
"""

from __future__ import annotations

import html as html_lib

from app.email import chrome_generated as chrome
from app.email.transport import GRAPHENE_BRAND, Brand


def _esc(value: object) -> str:
    """Escape anything user/adviser-typed before it lands in an email's HTML.

    Only for content being rendered NOW — the flow action's already-merged body and its subject
    line. Jinja SOURCE must never come through here; that was the whole cause of the two-copy
    split."""
    return html_lib.escape(str(value), quote=True)


def _gjs(name: str, *, names: bool) -> str:
    """The editor's name for one part of the chrome, on the EDITOR shape alone.

    ``names=True`` is passed by ``email_defaults``' ``source`` path and by nothing else: GrapesJS
    consumes ``data-gjs-name`` when it parses the canvas — so it heads the Style rail with the
    palette's own word for the part (``create.ts`` → ``EMAIL_BLOCKS``, and the NAMES rule there)
    and never reaches a recipient — while the send shape stays byte-for-byte what it always was."""
    return f' data-gjs-name="{name}"' if names else ""


# The palette, the SAME set the editor holds (`email-editor/create.ts`) — FONT / INK / BODY_INK /
# MUTED_INK / RULE, neither side with a page background — so a palette block dropped
# into a default cannot land Arial 15/24 beside system-ui 14/22. Hexes because nothing parses oklch.

#: The type face. ARIAL FIRST is an Outlook fix: the Word engine takes the first family and falls
#: back to Times New Roman rather than walking the stack. No web font — a remote fetch is blocked.
FONT_STACK = "Arial,Helvetica,sans-serif"
#: Headings and the wordmark.
INK = "#0f172a"
#: Body copy.
BODY_INK = "#334155"
#: Secondary copy — the supporting line under an action, and every piece of small print.
#:
#: THERE IS NO `FAINT_INK` ANY MORE. It was `#94a3b8`, which is **2.56:1** on white — under half
#: the 4.5:1 WCAG AA asks of body text — and it carried the three things a recipient most needs to
#: be able to read when the button has failed them: the "paste this link into your browser" line,
#: the link itself, and the footer's support address. A fourth ink that nobody can read is not a
#: step in a scale, it is a hole in one. `MUTED_INK` is 4.76:1 and takes all three over.
MUTED_INK = "#64748b"
#: Hairline rules.
RULE = "#e2e8f0"

# THE TYPE SCALE: THREE STEPS, and there are three because a reader counted six different
# treatments in a six-line email. One family (`FONT_STACK`), with hierarchy carried by size,
# weight and ink rather than by another face or another size — which is the current advice for
# transactional mail and the only kind that survives a mail client.
#
#   22px  the heading (`chrome.svelte`)
#   15px  body copy AND the quieter aside beneath it — the aside steps back in INK, not in size.
#         It was 14px, a step so small it read as a mistake rather than as a level.
#   13px  small print: the masthead eyebrow, the paste-this-link fallback, the footer. Was 12px;
#         13 is the floor below which text stops being reliably readable on a phone.
SMALL_SIZE = "13px"
#: The card itself. There is NO `PAGE_BG` any more: the band drew a grey stripe between the
#: editor's white sheet and the card, and the white fill, 10px radius and accent bar are what
#: read as a card anyway. The centring table and cell stay (Outlook ignores `margin:0 auto`).
CARD_BG = "#ffffff"

#: The reading measure: 600px, what the editor's card and starter skeleton are built to (was 480).
CARD_WIDTH = 600


def logo_wordmark(src: str, alt: str, *, arm: str = "") -> str:
    """The masthead's logo arm. ``src``/``alt`` must already be HTML-safe (escaped, or token
    source).

    ``arm`` stamps ``data-brand-arm``, which the EDITOR's canvas reads to preview only the arm this
    firm will actually get (both arms are always in the markup, because the choice is made at send).
    It is inert in a mail client and absent unless asked for, so ``masthead`` — which has already
    resolved the brand and emits ONE arm — passes nothing."""
    marker = f' data-brand-arm="{arm}"' if arm else ""
    return (
        f'<img{marker} src="{src}" alt="{alt}" height="28" '
        'style="height:28px;max-width:220px;margin:0 0 28px;display:block">'
    )


#: The wordmark's look, needed IDENTICALLY by three places: `text_wordmark`, the editor's palette
#: block, and the masthead image's FALLBACK attribute (`conditions/attrs.ts` → `FALLBACK_ATTR`),
#: pinned by `brand-logo.test.ts` and `test_email_defaults`. `font-family` is here because an
#: element outside the cells the shell styles inherits nothing: Word would render it serif.
WORDMARK_STYLE = (
    f"margin:0 0 26px;font-family:{FONT_STACK};font-size:{SMALL_SIZE};font-weight:700;"
    f"letter-spacing:0.09em;line-height:1.4;text-transform:uppercase;color:{MUTED_INK}"
)


def text_wordmark(name: str, *, arm: str = "") -> str:
    """The masthead's wordmark arm — what a firm with no https logo wears. ``name`` must already be
    HTML-safe.

    THE PRODUCT'S EYEBROW, transposed: small, tracked, uppercase, muted (docs/design-language.md →
    Type scale, "Page eyebrow"). At 18px bold it carried the same weight as the `<h1>` two lines
    below it, so the eye met two headings and led with the firm's name rather than with what the
    email is about — and a masthead is a label, not a title.

    ``text-transform`` is the one declaration here Word ignores, and it degrades to the firm's name
    in its own case at the same size, colour and tracking: still plainly a masthead. Nothing depends
    on the uppercasing."""
    marker = f' data-brand-arm="{arm}"' if arm else ""
    return f'<div{marker} style="{WORDMARK_STYLE}">{name}</div>'


def masthead(brand: Brand) -> str:
    """The masthead for a RESOLVED brand: the logo when there is one, else the wordmark.

    The shipped defaults pick the arm at MERGE time instead (a ``{% if brand.logo_url %}`` over
    the same two arms — see ``email_defaults._MASTHEAD``), so a firm that publishes the prefill
    unchanged keeps its logo. The token prefill hard-coded the wordmark, and dropped it silently."""
    return (
        logo_wordmark(_esc(brand.logo_url), _esc(brand.name))
        if brand.logo_url
        else text_wordmark(_esc(brand.name))
    )


# ---- THE BULLETPROOF BUTTON: a TABLE, not a padded anchor, label in its own `<span>` ---------
# SIZE: Word implements no `display:inline-block`, so padding on an `<a>` collapses the pill; the
# CELL carries padding, fill and radius (Word honours those), the anchor type and ink only. INK:
# Word's Hyperlink style beats a plain inline `color`, and it INVALIDATES an inline `!important`
# (hteumeuleu/email-bugs#31) — which is how the label came out `#0f172a`, the fill itself. Hence
# three UNFLAGGED layers: the label `<span>` (a child the Hyperlink style cannot reach), the anchor
# for text an RTE retype leaves outside it, and `render/email.py::MSO_CTA_INK` (`a.cta`; Word
# honours `!important` in a STYLESHEET and misses the span, so a firm's own label colour wins).
# `background-color`, never the shorthand: the Style rail edits the longhand. ALL THREE LAYERS
# STAY IN BOTH COPIES (create.ts; nine divergences), pinned by tests/test_email_send_parity.py.

#: The CELL is the pill: padding (the level Word honours), fill, radius. `{accent}` may be Jinja.
_CTA_CELL = "padding:12px 24px;background-color:{accent};border-radius:6px"

#: The ANCHOR: type and ink only, no box property for Word to drop. Six-digit `#ffffff` (Word is
#: unreliable on three-digit hex), UNFLAGGED — an inline `!important` is what Word invalidates.
_CTA_LINK = (
    f"font-family:{FONT_STACK};font-size:15px;font-weight:600;line-height:1.2;"
    "color:#ffffff;text-decoration:none"
)

#: Layer 1, the span Word's Hyperlink style does not reach. Ink only; the typography inherits.
_CTA_LABEL = "color:#ffffff"

#: The hook `render/email.py::MSO_CTA_INK` targets — a CLASS, because Word reads class selectors.
_CTA_CLASS = "cta"


def cta_block(label: str, url: str, accent: str, *, names: bool = False) -> str:
    """The accent call-to-action button AND its paste-the-link fallback paragraph — ONE unit,
    because the fallback is the half that still works for a recipient whose client strips the link,
    and a hand-rolled copy of this button had already lost it once.

    The button is a one-cell presentation table, not a padded inline-block anchor — see THE
    BULLETPROOF BUTTON above for what Outlook did to the earlier shape.

    SPACING lives on the wrapping table, because that is how every other part of this chrome spaces
    itself: each block carries its own margin on its OUTERMOST element (``logo_wordmark``/
    ``text_wordmark`` a ``margin-bottom``, the heading a ``margin``, the fallback paragraph below a
    ``margin-top``), and ``shell_html`` adds only the CELL padding around the lot. It cannot stay on
    the anchor (Word drops margins as readily as it drops the padding) and it cannot go on the cell,
    whose padding IS the pill's size.

    All three arguments must already be HTML-safe. The block is emitted unconditionally; a caller
    whose URL can be absent guards the whole thing (``email_defaults._cta`` wraps it in an
    ``{% if %}``), because an unguarded button renders ``href=""`` above a "paste this link into
    your browser:" line with nothing after it.

    ``names`` is the editor shape: every element the kit can carry an attribute on, so the
    select-parent chain reads as one Button rather than Table / Table row / Table cell / Link. The
    `<tr>` is the exception — the kit has no bare-row primitive, so `Button row` is unnamed and
    falls back to the editor's own component vocabulary (`create.ts`).

    THE BUTTON IS GENERATED (``chrome_generated.CTA``, from ``chrome-cta.svelte``); the fallback
    paragraph is still built here, because it is a sibling of the button, not part of it."""
    button = (chrome.CTA_NAMED if names else chrome.CTA) % {
        "url": url,
        "label": label,
        "cta_class": _CTA_CLASS,
        "cell_style": _CTA_CELL.format(accent=accent),
        "link_style": _CTA_LINK,
        "label_style": _CTA_LABEL,
    }
    return (
        button + f'<p style="margin:20px 0 0;font-size:{SMALL_SIZE};line-height:1.6;'
        f'color:{MUTED_INK}">'
        "If the button doesn't work, paste this link into your browser:"
        # AN EXPLICIT ANCHOR, not a bare `<span>` of URL text. Gmail and Apple Mail auto-link a
        # bare URL and then paint it in THEIR link colour, which is how this line came out green
        # in a firm whose email has no green in it — the one spot of unexplained colour in the
        # whole message, on the fallback a recipient only reaches when something has gone wrong.
        # Given an anchor of our own they leave the colour alone.
        f'<br><a href="{url}" style="color:{MUTED_INK};text-decoration:underline;'
        f'word-break:break-all">{url}</a></p>'
    )


# The card and its footer: tables all the way down, because Word implements neither half of
# `max-width;margin:0 auto` and a centred `<div>` went full-bleed in Outlook. `align="center"` and
# `width="600"` are ATTRIBUTES because those are what Word reads; `<tbody>` is written out at every
# level so the merged fragment and the delivered document are the same element sequence.

#: The card's accent edge. `{accent}` may be Jinja, as in `_CTA_CELL`. The `&nbsp;` keeps Gmail
#: from collapsing an empty cell; `font-size:0` keeps it from adding height.
_ACCENT_BAR = (
    "height:4px;padding:0;font-size:0;line-height:4px;background-color:{accent};"
    "border-radius:10px 10px 0 0"
)

#: The footer's cell. Its own TOP border, so the rule belongs to the block that can disappear.
_FOOTER_CELL = (
    f"padding:20px 32px 28px;border-top:1px solid {RULE};font-family:{FONT_STACK};"
    f"font-size:{SMALL_SIZE};line-height:1.6;color:{MUTED_INK}"
)


def support_link(address: str, *, names: bool = False) -> str:
    """The footer's support arm — the separator AND the address, as one unit, because they disappear
    together. ``address`` must already be HTML-safe.

    Underlined as well as coloured: several clients strip a link's colour and keep its decoration,
    and Word re-inks an undecorated link unpredictably. It is faint on purpose — a support address
    is the quietest thing in the email, and the CTA is the only link meant to be pressed.

    ``names`` names the anchor on the editor shape (``_gjs``). The flag is here rather than at the
    call site because the outermost thing this returns is the separator TEXT, so ``_named`` cannot
    reach the anchor and an outside caller would have to string-match this module's own markup."""
    return (
        f' · <a{_gjs("Support link", names=names)} href="mailto:{address}" '
        f'style="color:{MUTED_INK};text-decoration:underline">'
        f"{address}</a>"
    )


def footer_row(inner: str, *, names: bool = False) -> str:
    """The footer as its own TABLE, ready to sit under the content. ``inner`` must already be
    HTML-safe.

    A BLOCK rather than a string of copy, so a caller with nothing to put in it emits nothing at all
    (``shell_html`` writes what it is given). An empty bordered strip under an email is the same
    class of defect as the dead ``href=""`` button and the empty note box: furniture that outlived
    its content. The shipped defaults wrap this in ``{% if brand.name %}`` for the merge-time half
    of the same question.

    A TABLE, not the bare ``<tr>`` this used to be. The row spliced into the card's own open
    ``<tbody>``, and the generated chrome has no such tbody to splice into — the kit has no
    bare-row primitive, so the card is one table per row and a stray ``<tr>`` lands inside a
    ``<td>``. That renders only because an HTML parser hoists it, and Word's is the one least
    likely to hoist it the same way, so the footer carries its own table instead. The hairline
    (``border-top``) is what still joins it to the content above."""
    return (chrome.FOOTER_NAMED if names else chrome.FOOTER) % {
        "cell_style": _FOOTER_CELL,
        "inner": inner,
    }


def footer_for(brand: Brand) -> str:
    """The footer row for a RESOLVED brand: the firm's name, and the support address if it has one.

    The support arm is GUARDED for the common case, not the exotic one — ``brand_for`` passes
    ``support.email`` through exactly as stored and most firms have never set it. Unguarded this
    reads "Ridgeway Wealth ·", a separator pointing at nothing, above a live `mailto:` with no
    address in it."""
    address = (brand.support_email or "").strip()
    return footer_row(_esc(brand.name) + (support_link(_esc(address)) if address else ""))


def shell_html(
    heading: str,
    body_html: str,
    *,
    wordmark: str,
    cta: str = "",
    footer: str = "",
    accent: str = GRAPHENE_BRAND.accent,
    names: bool = False,
) -> str:
    """The email shell: a card with an accent edge, a branded masthead, the heading, the body, an
    optional CTA block and an optional footer row.

    NOT on a muted page any more — see ``CARD_BG`` for why the band went. The outer table and its
    centring cell remain (Outlook ignores ``margin:0 auto``); they just paint nothing.

    Every argument is already-safe HTML — or Jinja source, for the shipped defaults; see the section
    header. ``shell`` is the escaping wrapper for a caller holding a real ``Brand``.

    THE VERTICAL RHYTHM lives here and in the parts, not in the copy: the card's own gutter (32px),
    28px under the masthead, 14px under the heading, 28px above the button, and the footer's
    hairline at the end. Each part still carries its own outer margin (the convention ``cta_block``
    documents) — this adds only the cell padding that Word honours where it drops a margin.

    ``names`` is the editor shape: the chrome elements and the heading, under the palette's own
    words for the Email card block, so the tree an author clicks through reads the same in a shipped
    default as in one they built themselves. The `<tr>`s are unnamed — the kit has no bare-row
    primitive — and fall back to the editor's own component vocabulary (`create.ts`).

    THE MARKUP IS GENERATED (``chrome_generated.SHELL``, from ``chrome.svelte``). It is one table
    per ROW rather than the two-table card this used to emit, for the same reason: with no
    bare-`<tr>` primitive the accent bar, the content and the footer cannot be three rows of one
    tbody."""
    return (chrome.SHELL_NAMED if names else chrome.SHELL) % {
        "accent": accent,
        "card": CARD_BG,
        "font_stack": FONT_STACK,
        "ink": INK,
        "body_ink": BODY_INK,
        "card_width": str(CARD_WIDTH),
        "accent_bar": _ACCENT_BAR.format(accent=accent),
        "wordmark": wordmark,
        "heading": heading,
        "body_html": body_html,
        "cta": cta,
        "footer": footer,
    }


def shell(
    heading: str,
    body_html: str,
    button_label: str | None = None,
    button_url: str | None = None,
    brand: Brand = GRAPHENE_BRAND,
) -> str:
    """The house chrome around an ALREADY-MERGED body — for the one send path that has no shipped
    default behind it: the flow tier's email action, which renders a firm's own published template
    itself and then needs it dressed.

    ``heading`` and the CTA parts are escaped here; ``body_html`` passes through, because the caller
    has already escaped every interpolated value in it (getting that pair the other way round is the
    injection — see ``flows/effects.py::send_email``). Anything WITH a catalogue ``code`` goes
    through ``email_render.render_or_default`` instead, which renders the shipped default."""
    accent = _esc(brand.accent)
    cta = (
        cta_block(_esc(button_label), _esc(button_url), accent)
        if button_label and button_url
        else ""
    )
    return shell_html(
        _esc(heading),
        body_html,
        wordmark=masthead(brand),
        cta=cta,
        footer=footer_for(brand),
        accent=accent,
    )


# NOT here: the per-code default copy (`app/email/defaults.py`, in token form) and `verify_email`,
# which had no caller. `request_due_email`/`share_expiring_email` are deliberately unwritten: with
# no worker scan and no per-row idempotency stamps, they would re-mail on every pass.
