"""GOLDEN COPY — the exact words an un-overridden send delivers, pinned as literals.

Most firms have published no override, so ``app/email/defaults.py``'s shipped default IS the email
their clients receive. Every other email test compares one derivation against another: preview
against send, the prefill against the builders, the wrapped document against the fragment. That
family of tests is worth having, but it shares one blind spot — it cannot notice both sides moving
together. When the two hand-written copies of the shipped default were collapsed into one source,
``test_the_prefilled_default_and_the_shipped_default_read_identically`` became trivially true and
stopped protecting anything, and there was nothing left that would fail if the collapse quietly
reworded an email.

So this module states the copy OUTRIGHT. The expectations below are literal strings captured from
the shipped defaults as they stood BEFORE the collapse — no helper builds them, so nothing in
``app/`` can move them. A diff here means one of two things and the failure message says so: either
the copy really changed (update the literal deliberately, in the same change as the copy) or the
refactor changed it by accident, which is the case this file exists for.

WHAT IS PINNED, and why in this shape:

  * **The subject, verbatim** — including its HTML *escaping*, because a subject is plain text on
    the wire and must not arrive as ``Bonds &amp; Gilts``. The frozen brand and document names both
    carry an ampersand on purpose: rendering the subject through the body's autoescaping Jinja pass
    is the specific regression the collapse could have introduced, and this is what catches it.
  * **The delivered document's visible text**, HTML entities decoded and whitespace collapsed —
    i.e. what a recipient reads, in order, including the hidden preheader line (which leads, as an
    inbox shows it). Markup is deliberately NOT pinned: the collapse re-spells attribute quoting and
    entity forms without changing a single word, and a golden that failed on that would be noise.
    ``tests/test_email_send_parity.py`` owns the markup-level claims (the wrapping is additive, the
    preheader span is hidden and first, nothing survives in a ``<style>`` block).
  * **The footer**, which is copy too: every email closes with the firm's name and, when it has
    one, its support address. The frozen ``BRAND`` always has one, so the two states an absent
    address produces — no dangling separator, and no empty bordered strip when there is no name
    either — are pinned separately below rather than by a third variant.
  * **Both contexts.** ``full`` has every value; ``sparse`` nulls every legitimately-nullable one —
    no name on the record, no covering note, no deadline, no portal host. Sparse is where copy
    defects live (four of the six divergences found in Aug 2026 only appeared there), so half these
    literals are the sparse reading.

    ``sparse`` used to do a second job: it flipped ``is_reminder``, the one branch-shaped value that
    was not nullable, so that ``task_notice``'s "Reminder" wording got pinned without a third
    variant. That flag is gone. ``task_notice`` was one template saying two things and is now two
    templates — ``task_assigned`` and ``task_reminder`` — so both wordings are pinned by both being
    CODES, in all four cells, which is what the variant axis was standing in for. Nothing here
    branches on a context value any more.

The context is frozen HERE rather than taken from ``email_preview.sample_context``: a golden whose
inputs can move is not a golden. That is the point of the duplication — do not "fix" it by importing
the sample.
"""

from __future__ import annotations

import html as html_lib
import re
import unicodedata
from typing import Any

import pytest

from app.email import render as email_render
from app.email.catalog import KNOWN_CODES
from app.email.transport import Brand

# The frozen inputs. The brand name carries an ampersand, so subject-escaping is decidable here.

BRAND = Brand(
    name="Ridley & Co", logo_url=None, accent="#0f766e", support_email="hello@ridley.test"
)

#: MULTI-PARAGRAPH, soft newline inside block two: a single-line note hid the paragraph divergence.
NOTE = "First paragraph.\n\nSecond paragraph, line one\nline two."

NULLABLE = frozenset(
    {
        "first_name",
        "sent_by",
        "message",
        "due_date",
        "portal_url",
        "button_url",
        "button_label",
        "document_name",
    }
)

#: The flat merge values each send site provides; ``email_preview._CODE_KEYS`` owns WHICH keys.
FULL: dict[str, dict[str, Any]] = {
    "invite": {
        "first_name": "Bo",
        "portal_label": "Client Portal",
        "set_password_url": "https://portal.ridley.test/set-password?token=golden",
    },
    "password_reset": {
        # Always nameless: `routers/internal.py` hard-codes it (no account-existence oracle).
        "first_name": None,
        "set_password_url": "https://portal.ridley.test/set-password?token=golden",
    },
    "document_sent": {
        "first_name": "Bo",
        "document_name": "Bonds & Gilts review",
        "sent_by": "Ada Adviser",
        "message": NOTE,
        "portal_url": "https://portal.ridley.test",
    },
    "request_raised": {
        "first_name": "Bo",
        # TWO items pins the pluralised subject; `item_titles` is not nullable, so sparse keeps it.
        "item_titles": ["Proof of identity", "Proof of address"],
        "due_date": "2026-01-22",
        "message": NOTE,
        "portal_url": "https://portal.ridley.test",
    },
    # The same context twice, spelled out: a golden's inputs are frozen per code, never shared.
    "task_assigned": {
        "task_title": "Review Q2 suitability report",
        "task_description": "Check the attitude-to-risk answers.",
        "due_date": "2026-01-22",
        "button_label": "Open your portal",
        "button_url": "https://portal.ridley.test",
    },
    "task_reminder": {
        "task_title": "Review Q2 suitability report",
        "task_description": "Check the attitude-to-risk answers.",
        "due_date": "2026-01-22",
        "button_label": "Open your portal",
        "button_url": "https://portal.ridley.test",
    },
    # The general wrapper: its title and body always arrive, so sparse is its no-CTA reading.
    "notification": {
        "title": "A document was shared with you",
        "body": "Your adviser has shared a document. Open your portal to view it.",
        "button_label": "Open your portal",
        "button_url": "https://portal.ridley.test",
    },
}

VARIANTS = ("full", "sparse")


def context(code: str, variant: str) -> dict[str, Any]:
    """The frozen merge context for one code and variant."""
    sparse = variant == "sparse"
    ctx: dict[str, Any] = {
        "brand": {
            "name": BRAND.name,
            "logo_url": BRAND.logo_url,
            "accent": BRAND.accent,
            "support_email": BRAND.support_email,
        }
    }
    for key, value in FULL[code].items():
        ctx[key] = None if (sparse and key in NULLABLE) else value
    return ctx


def delivered(code: str, variant: str) -> tuple[str, str]:
    """``(subject, html)`` exactly as an un-overridden send delivers it — the COMPLETE document,
    preheader span included, not the body fragment.

    ``email_render.render_default`` IS the send path's fallback branch — ``render_or_default`` calls
    exactly this once it finds no published override — so these literals are pinned against the
    thing that really sends, not a mirror of it.

    This one line is all that moved when the two authorings collapsed; the literals below did not,
    which is the whole point — they are the net the refactor was carried out over.
    """
    return email_render.render_default(code, context(code, variant), asset_origin=None)


#: Dropped WHOLE, not tag-stripped: the shell's `[if mso]` CSS (`render/email.py::MSO_CTA_INK`).
_UNREAD = re.compile(r"<(style|script)\b[^>]*>.*?</\1>", re.S | re.I)


def visible(html: str) -> str:
    """What a recipient reads: unread elements dropped whole, tags stripped, entities decoded,
    whitespace collapsed.

    Entities are decoded so that ``&#x27;`` vs ``&#39;`` — ``html.escape`` and markupsafe spell the
    apostrophe differently, and the collapse swapped one for the other — reads as the non-change it
    is. Escaping is asserted as escaping in ``test_a_hostile_value_is_still_escaped_in_the_body``,
    so decoding here does not weaken that.

    DECODED BEFORE THE WHITESPACE COLLAPSE, not after, so that whitespace SPELT as an entity
    collapses like the whitespace it is. The shell's accent bar is a zero-height cell holding
    ``&nbsp;`` (the idiom that stops Gmail dropping an empty cell — the editor's Divider block uses
    it too), and decoding last put a literal U+00A0 into these literals: a character no recipient
    reads, in a golden whose whole subject is what a recipient reads.
    """
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", _UNREAD.sub(" ", html)))
    # ZERO-WIDTH CHARACTERS DROPPED, for the same reason the accent bar's `&nbsp;` is: this golden's
    # whole subject is what a recipient reads, and a `Cf` codepoint is not read. The preview-line
    # filler (`render/email.py::_preview_filler`) is 100+ of them, deliberately, so without this the
    # golden for every code would carry a wall of invisible padding as though it were copy.
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return " ".join(text.split())


# The golden copy: change a literal here only when you mean to change what a client reads.

GOLDEN: dict[tuple[str, str], tuple[str, str]] = {
    ("invite", "full"): (
        "You've been invited to Client Portal",
        "Set a password to activate your account. Ridley & Co Set your password Hi Bo, You've "
        "been invited to Client Portal . Set a password to activate your account and sign in. Set "
        "password If the button doesn't work, paste this link into your browser: "
        "https://portal.ridley.test/set-password?token=golden Ridley & Co · hello@ridley.test",
    ),
    ("invite", "sparse"): (
        "You've been invited to Client Portal",
        "Set a password to activate your account. Ridley & Co Set your password Hi there, You've "
        "been invited to Client Portal . Set a password to activate your account and sign in. Set "
        "password If the button doesn't work, paste this link into your browser: "
        "https://portal.ridley.test/set-password?token=golden Ridley & Co · hello@ridley.test",
    ),
    ("password_reset", "full"): (
        "Reset your password",
        "Choose a new password to get back into your account. Ridley & Co Reset your password Hi "
        "there, We received a request to reset your password. Choose a new one below. If you "
        "didn't ask for this, you can safely ignore this email. Choose a new password If the "
        "button doesn't work, paste this link into your browser: "
        "https://portal.ridley.test/set-password?token=golden Ridley & Co · hello@ridley.test",
    ),
    ("password_reset", "sparse"): (
        "Reset your password",
        "Choose a new password to get back into your account. Ridley & Co Reset your password Hi "
        "there, We received a request to reset your password. Choose a new one below. If you "
        "didn't ask for this, you can safely ignore this email. Choose a new password If the "
        "button doesn't work, paste this link into your browser: "
        "https://portal.ridley.test/set-password?token=golden Ridley & Co · hello@ridley.test",
    ),
    ("document_sent", "full"): (
        "Ridley & Co: Bonds & Gilts review is ready for you",
        "It's waiting for you in your portal. Ridley & Co A new document for you Hi Bo, Bonds & "
        "Gilts review has been shared with you by Ada Adviser and is now available in your "
        "portal. First paragraph. Second paragraph, line one line two. Open your portal If the "
        "button doesn't work, paste this link into your browser: https://portal.ridley.test "
        "Ridley & Co · hello@ridley.test",
    ),
    ("document_sent", "sparse"): (
        "Ridley & Co: A document is ready for you",
        "It's waiting for you in your portal. Ridley & Co A new document for you Hi there, A "
        "document has been shared with you by your adviser and is now available in your portal. "
        "Ridley & Co · hello@ridley.test",
    ),
    ("request_raised", "full"): (
        "Ridley & Co: 2 things need your attention",
        "Your adviser needs some information from you. Ridley & Co Action needed Hi Bo, Your "
        "adviser has asked you for the following: Proof of identity Proof of address Please "
        "complete this by 22 January 2026 . First paragraph. Second paragraph, line one line "
        "two. Open "
        "your portal If the button doesn't work, paste this link into your browser: "
        "https://portal.ridley.test Ridley & Co · hello@ridley.test",
    ),
    ("request_raised", "sparse"): (
        "Ridley & Co: 2 things need your attention",
        "Your adviser needs some information from you. Ridley & Co Action needed Hi there, Your "
        "adviser has asked you for the following: Proof of identity Proof of address Ridley & Co "
        "· hello@ridley.test",
    ),
    # The four task cells recombine the old two: not one word of shipped copy moved in the split.
    ("task_assigned", "full"): (
        "New task: Review Q2 suitability report",
        "Here's what needs doing. Ridley & Co New task: Review Q2 suitability report You have "
        "been assigned a task. Check the attitude-to-risk answers. Due 22 January 2026 . Open your "
        "portal If the button doesn't work, paste this link into your browser: "
        "https://portal.ridley.test Ridley & Co · hello@ridley.test",
    ),
    ("task_assigned", "sparse"): (
        "New task: Review Q2 suitability report",
        "Here's what needs doing. Ridley & Co New task: Review Q2 suitability report You have "
        "been assigned a task. Check the attitude-to-risk answers. Ridley & Co · hello@ridley.test",
    ),
    ("task_reminder", "full"): (
        "Reminder: Review Q2 suitability report",
        "Here's what needs doing. Ridley & Co Reminder: Review Q2 suitability report This task is "
        "due soon. Check the attitude-to-risk answers. Due 22 January 2026 . Open your portal If "
        "the button doesn't work, paste this link into your browser: https://portal.ridley.test "
        "Ridley & Co · hello@ridley.test",
    ),
    ("task_reminder", "sparse"): (
        "Reminder: Review Q2 suitability report",
        "Here's what needs doing. Ridley & Co Reminder: Review Q2 suitability report This task is "
        "due soon. Check the attitude-to-risk answers. Ridley & Co · hello@ridley.test",
    ),
    ("notification", "full"): (
        "A document was shared with you",
        "There's an update waiting in your portal. Ridley & Co A document was shared with you "
        "Your adviser has shared a document. Open your portal to view it. Open your portal If the "
        "button doesn't work, paste this link into your browser: https://portal.ridley.test "
        "Ridley & Co · hello@ridley.test",
    ),
    ("notification", "sparse"): (
        "A document was shared with you",
        "There's an update waiting in your portal. Ridley & Co A document was shared with you "
        "Your adviser has shared a document. Open your portal to view it. Ridley & Co · "
        "hello@ridley.test",
    ),
}


# ---- the pins --------------------------------------------------------------------------------


def test_the_golden_table_covers_every_code_and_variant() -> None:
    """A code missing from the table would silently stop being pinned — and the codes come from the
    catalogue, so a NEW email type has to arrive with its golden copy."""
    expected = {(c.code, variant) for c in KNOWN_CODES for variant in VARIANTS}
    assert set(GOLDEN) == expected, (
        f"golden table out of step with the catalogue: missing {sorted(expected - set(GOLDEN))}, "
        f"stale {sorted(set(GOLDEN) - expected)}"
    )
    assert set(FULL) == {c.code for c in KNOWN_CODES}


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("code", [c.code for c in KNOWN_CODES])
def test_the_shipped_default_still_says_exactly_this(code: str, variant: str) -> None:
    expected_subject, expected_text = GOLDEN[(code, variant)]
    subject, html = delivered(code, variant)

    assert subject == expected_subject, (
        f"{code} ({variant}): the shipped SUBJECT changed.\n"
        f"  was: {expected_subject!r}\n  now: {subject!r}\n"
        "If that was intended, update the literal in the same change as the copy. If it was not, "
        "a refactor has reworded — or re-escaped — an email nobody edited. A subject is plain text "
        "on the wire: `&amp;` in it is a defect, not a difference."
    )
    assert visible(html) == expected_text, (
        f"{code} ({variant}): the shipped COPY changed.\n"
        f"  was: {expected_text}\n  now: {visible(html)}\n"
        "This is the words a client with no firm override actually reads."
    )


def test_a_multi_paragraph_covering_note_is_set_in_paragraphs() -> None:
    """The adviser's note keeps its shape, and this is the assertion the visible-text goldens above
    structurally cannot make.

    ``_text``-style comparison collapses whitespace, so "one paragraph" and "three paragraphs"
    read identically to it — which is precisely why the divergence between the two authorings (the
    send path made ``<p>`` blocks and ``<br>``, the editor prefill made neither) survived every
    parity test the suite had. A note typed as three paragraphs must not arrive as one run-on
    sentence, so the markup is pinned here explicitly.
    """
    for code in ("document_sent", "request_raised"):
        _subject, html = delivered(code, "full")
        assert html.count('<p style="margin:0 0 12px">') == 2, (
            f"{code}: the two-paragraph covering note did not become two <p> blocks — the "
            "recipient reads the adviser's note as one run-on paragraph"
        )
        assert "line one<br>line two" in html, (
            f"{code}: a soft newline inside a paragraph of the note was dropped rather than "
            "becoming a <br>"
        )


def test_a_blank_covering_note_ships_no_quote_block_at_all() -> None:
    """Whitespace is not a note. A message of spaces must take the whole attributed block with it,
    not leave an empty bordered box in the middle of the email — which is what a bare
    ``{% if message %}`` truthiness check produces."""
    for code in ("document_sent", "request_raised"):
        ctx = context(code, "full") | {"message": "   \n  "}
        _subject, html = email_render.render_default(code, ctx, asset_origin=None)
        assert "border-left" not in html, f"{code}: a blank note still shipped its quote block"


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("code", [c.code for c in KNOWN_CODES])
def test_a_firm_with_no_support_address_ships_no_dangling_separator(
    code: str, variant: str
) -> None:
    """MOST firms have never set a support address, so this is the common reading of the footer,
    not the exotic one.

    The separator and the address are one unit (``email_shell.support_link``) because they disappear
    together: split, they ship "Ridgeway Wealth ·" pointing at nothing, above a live ``mailto:``
    with no address in it — observed in a real inbox from the editor's own footer block, which is
    where this shape was copied from. The firm's NAME still has to be there; a footer that vanishes
    with the address would leave a bordered strip of nothing."""
    ctx = context(code, variant)
    ctx["brand"] = dict(ctx["brand"], support_email=None)
    _subject, html = email_render.render_default(code, ctx, asset_origin=None)

    assert "mailto:" not in html, f"{code} ({variant}): a mailto: link with no address behind it"
    assert " · " not in visible(html), (
        f"{code} ({variant}): the footer separator survived its address — {visible(html)[-60:]!r}"
    )
    assert visible(html).endswith(BRAND.name), (
        f"{code} ({variant}): the footer lost the firm's name with the support address — "
        f"{visible(html)[-60:]!r}"
    )


@pytest.mark.parametrize("code", [c.code for c in KNOWN_CODES])
def test_a_nameless_brand_ships_no_empty_footer_strip(code: str) -> None:
    """The other end of the same guard. A brand with nothing to say takes the whole footer ROW with
    it, rather than ruling a hairline across the card and leaving the space under it blank — the
    defect shape of the dead ``href=""`` button and the empty note box, in the one block that is
    otherwise on every email we send."""
    ctx = context(code, "full")
    ctx["brand"] = dict(ctx["brand"], name="", support_email=None)
    _subject, html = email_render.render_default(code, ctx, asset_origin=None)
    assert "border-top" not in html, f"{code}: an empty bordered footer strip was still shipped"


def test_a_hostile_value_is_still_escaped_in_the_body() -> None:
    """``visible()`` decodes entities, so escaping needs saying once, directly. The adviser's note
    is the reachable case: it is free text we mail on the firm's behalf."""
    ctx = context("document_sent", "full") | {"message": "<script>alert(1)</script>"}
    _subject, html = email_render.render_default("document_sent", ctx, asset_origin=None)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize(
    ("code", "lead"),
    [
        ("task_assigned", "You have been assigned a task."),
        ("task_reminder", "This task is due soon."),
    ],
)
def test_a_task_notice_omits_the_description_clause_when_there_is_none(
    code: str, lead: str
) -> None:
    """A description-less task renders just the lead — no filler sentence, no empty clause.

    The full/sparse goldens always supply a description, so this is the case they don't cover: a
    task with none leaves ``task_description`` blank, and the template's
    ``{% if task_description %}`` must then drop the clause so the lead runs straight into the due
    line. The stand-in it replaced ("No further detail — see the task itself.") read as a clumsy
    second sentence.

    PARAMETRISED OVER BOTH TASK CODES since the split, and the lead sentence is the parameter: the
    two templates are near-identical on purpose, so a guard fixed in one and lost in the other is
    exactly the drift two near-identical defaults invite."""
    ctx = context(code, "full") | {"task_description": ""}
    _subject, html = email_render.render_default(code, ctx, asset_origin=None)
    body = visible(html)
    assert f"{lead} Due 22 January 2026" in body
    assert "No further detail" not in body
