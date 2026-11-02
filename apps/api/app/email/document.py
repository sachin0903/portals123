"""Email render seam — the OUTPUT half of the email pipeline.

The document pipeline is: Jinja merge (`app/render/jinja_engine.py`) → component substitution →
`renderers.py::to_pdf`. Email reuses the merge half verbatim and swaps only the final step: instead
of a PDF, it folds all CSS into inline `style` attributes (email clients ignore `<style>` blocks and
classes) and wraps the result in a minimal, client-safe HTML document.

So the caller Jinja-merges the template body (+ subject/preheader) first, then hands the merged body
HTML + the template CSS here. This module is pure text-in → text-out and has no DB or network access
(remote stylesheet fetching is disabled — an email render must never make an outbound request).
"""

from __future__ import annotations

import re

import css_inline

from app import storage

# Minimal email-safe base: reset client default margins and normalise tables/images/links. Kept
# deliberately tiny — templates carry their own styling; this only removes cross-client quirks.
#: The `<body>` reset, INLINE, because a stylesheet cannot reach it here.
#:
#: THE BUG THIS CLOSES. `body { margin: 0; padding: 0 }` used to live in
#: :data:`EMAIL_BASE_CSS` and never applied to anything. That stylesheet is handed to
#: ``css_inline.inline_fragment`` along with the BODY FRAGMENT — the page table and its
#: contents — and there is no ``<body>`` element in it, so the rule matched nothing and was
#: dropped. :func:`_document` then added a bare ``<body>`` afterwards.
#:
#: The visible result was every client's default 8px body margin insetting the whole email, so
#: a page background could never reach the edges. Reported as "a bit of space around it in the
#: preview and likely the email" — it was both.
#:
#: Inline on the tag is the only place it can go, and it is where every hand-written email
#: template puts it for the same reason.
BODY_RESET = "margin:0;padding:0;background-color:#ffffff;"

EMAIL_BASE_CSS = """
table { border-collapse: collapse; }
img {
  border: 0;
  line-height: 100%;
  outline: none;
  text-decoration: none;
  -ms-interpolation-mode: bicubic;
}
a { text-decoration: none; }
""".strip()


#: Characters used to pad the preview line out. ZERO-WIDTH ONLY, which narrows what the kits use:
#: `svelte-email`'s set opens with U+00A0, a non-breaking SPACE that renders. Inside a block hidden
#: six ways over that makes no difference, but it is the one character that would show if any of the
#: hiding failed — and it is why a golden test of the VISIBLE copy started reading the padding as
#: words. Everything here is `Cf`, so it cannot be drawn or read.
_FILLER_CHARS = "​‌‍‎‏﻿"

#: How long the padded line runs to. Inbox previews are cut well before this; the point is only that
#: whatever the client shows AFTER the preheader is filler and not the email's first sentence.
_PREVIEW_TARGET = 150


def _preview_filler(preheader: str) -> str:
    """Invisible padding for the inbox line, or "" when the preheader already fills it.

    THE DEFECT THIS CLOSES. A client shows the preheader and then keeps reading the body to fill the
    rest of the preview, so a two-word preheader arrives in the inbox followed by the email's own
    opening words — "It's waiting for you in your portal. Hi Sarah, A new document has bee…". The
    author wrote one line and the inbox shows one and a half. Padding the block to a length no
    preview reaches means the only thing after the preheader is nothing.

    Every kit does this (`svelte-email`'s Preview, react-email's) with the same character set.
    """
    missing = _PREVIEW_TARGET - len(preheader)
    if missing <= 0:
        return ""
    # Cycled rather than one character repeated: a long run of one codepoint is the shape a filter
    # notices, and this costs nothing.
    return "".join(_FILLER_CHARS[i % len(_FILLER_CHARS)] for i in range(missing))


def _preheader_span(preheader: str) -> str:
    """A hidden preview-text block: the inbox line most clients show after the subject. It must be
    visually hidden in the body itself (display:none + zero size + off-screen colour + mso-hide) but
    present in the markup. ``preheader`` is already Jinja-merged + autoescaped by the caller.

    The filler rides INSIDE the same hidden block, so everything the hiding covers covers it too.
    """
    if not preheader.strip():
        return ""
    return (
        '<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;'
        'font-size:1px;line-height:1px;color:#ffffff;opacity:0">'
        f"{preheader}{_preview_filler(preheader)}</div>"
    )


def to_email_html(
    body_html: str, css: str = "", preheader: str = "", *, asset_origin: str | None = None
) -> str:
    """Fold ``css`` (plus the email base) into inline ``style`` attributes on ``body_html`` and wrap
    the result in a complete HTML document ready to send. When ``preheader`` is given, a hidden
    preview-text span is prepended so the inbox preview line ships with the email.

    ``body_html`` is a body fragment — the shape the editor produces. The merge (Jinja tokens →
    values) must already have run; this only inlines styling and wraps the document.

    ``asset_origin`` makes an author's uploaded picture reachable: it is stored as a
    `/_assets/{name}` path (the same form as every logo and every CMS image), and a path resolves
    against nothing in an inbox — a fact no preview reveals, because the portal serves the path
    perfectly. Given an origin, every such path is made absolute against it.

    Optional HERE and mandatory on the seams ABOVE — ``sendable``, ``sendable_default`` and
    ``render_default``. This function only wraps a fragment and holds no view about who is going to
    read it; the send seams are where a new path gets written, so that is where the compiler should
    demand an answer. ``None`` means "leave the paths alone", which is right for the editor's own
    preview (served from the origin they already resolve against) and for a firm with no registered
    host to point them at.
    """
    inlined = css_inline.inline_fragment(
        body_html,
        f"{EMAIL_BASE_CSS}\n{css}",
        load_remote_stylesheets=False,  # an email render must never fetch a remote stylesheet
    )
    # The preheader span carries its own inline styles, so it goes in after inlining.
    document = _document(_preheader_span(preheader) + inlined)
    return storage.absolute_asset_urls(document, asset_origin) if asset_origin else document


# ---- the ONE conditional in the shell -----------------------------------------------------------
#
# THE THIRD LAYER OF THE CTA INK FIX (app/email/transport.py → THE BULLETPROOF BUTTON, and the
# editor's copy
# in pkgs/ui/src/lib/components/email-editor/create.ts). It lives HERE, in the shell of every email
# we send, because Word's stylesheet is the only place `!important` reaches it at all:
#
#   * Word-based Outlook (2007–2019, and the desktop client most advisers use) applies its own
#     Hyperlink character style to any `<a href>`, which beats a plain inline `color`;
#   * and it INVALIDATES an inline declaration that carries `!important` — the declaration, not the
#     whole attribute (hteumeuleu/email-bugs#31). So flagging the anchor's own `color` deleted it,
#     the label inherited `#0f172a` from `shell_html`'s wrapper, and that is EXACTLY the button's
#     fill: black on navy, which is the "black, not blue" a real inbox showed;
#   * but `!important` in a `<style>` block works. Same engine, opposite answer.
#
# So the flag moved off the element and into here, behind `[if mso]` so no other client sees it.
#
# SCOPED TO ONE CLASS, and deliberately NOT to `a.cta span`. `color` is inherited, and an inherited
# value never competes in the cascade — the label span's own declaration wins over this rule however
# it is flagged, because this rule does not match the span. That is what keeps the head from
# overruling a firm that recolours its button label in the editor: the rule reaches only anchor
# text the span does not cover (what an RTE retype can leave behind) — precisely the gap it is for.
#
# A FEW BYTES, because most emails have no button. No `xmlns:v`/`xmlns:o` and no
# `<o:OfficeDocumentSettings>`: those are VML's prerequisites, not a conditional comment's — a
# conditional `<style>` is honoured without them — and `<o:PixelsPerInch>` would change how Outlook
# scales EVERY email we have already sent. Cargo cult, so not carried.
MSO_CTA_INK = "<!--[if mso]><style>a.cta{color:#ffffff !important}</style><![endif]-->"


#: Outlook IGNORES `line-height` and applies its own leading unless `mso-line-height-rule: exactly`
#: sits beside it, so every paragraph arrives more loosely spaced than authored — the one difference
#: an author cannot catch in the canvas, the preview, or a test send to anything but Outlook.
#:
#: DONE HERE, ON THE FINISHED DOCUMENT, and not as a stylesheet rule. Adding `p, td, … {
#: mso-line-height-rule: exactly }` to :data:`EMAIL_BASE_CSS` made the inliner rewrite every matched
#: element and DROP the CTA cell's own `background-color` — the button lost its fill. A textual pass
#: touches one declaration and cannot disturb its neighbours.
#:
#: IDEMPOTENT, and it takes two lookbehinds to be. The property name it inserts contains
#: `line-height` but never `line-height:` (the next character is `-`), so the first lookbehind is
#: not what saves it — the `line-height:` it leaves BEHIND the rule is an ordinary match, and a pass
#: over its own output would prefix it twice. The second lookbehind is what stops that.
_LINE_HEIGHT = re.compile(r"(?<!mso-)(?<!mso-line-height-rule:exactly;)line-height\s*:")


def _outlook_line_height(html: str) -> str:
    """Pair every authored `line-height` with the rule Outlook needs to honour it."""
    return _LINE_HEIGHT.sub("mso-line-height-rule:exactly;line-height:", html)


def _document(body: str) -> str:
    """Wrap an inlined body fragment in a minimal, client-safe email document shell."""
    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        # Apple Mail and iOS otherwise RESIZE the message to fit, which rescales the type and can
        # shrink a 600px card to fit a narrower viewport — the design arrives smaller than authored.
        # Every email kit ships this in its head for that reason.
        '<meta name="x-apple-disable-message-reformatting">'
        f"{MSO_CTA_INK}"
        "</head>"
        f'<body style="{BODY_RESET}">{_outlook_line_height(body)}</body></html>'
    )
