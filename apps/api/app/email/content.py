"""Would a recipient open something worth reading — and is any of it still the editor's own copy?

Two questions the publish gate for an email template (``routers/email_templates.py`` →
``email_templates_service.publish_version``) asks of a body before freezing it. They are the same
class of defect: markup that passes for finished and is not.

THE FIRST QUESTION IS ALSO ASKED ON THE SEND PATH, so nothing here is publish-gate trivia.
``email_render.render_published_email`` — the resolver behind ``render_or_default``, which every
coded send site goes through — runs :func:`has_renderable_content` over the body AFTER the Jinja
merge and returns ``None`` when nothing renders, which is what hands the send to the shipped
default. So the vocabulary this module defines (``_INVISIBLE``, ``_SUBSTANTIVE``,
``_BACKGROUND_IMAGE``) decides live deliveries in both directions: widen it and a client receives a
body they can see nothing in, narrow it and a firm's own published template is discarded in favour
of ours on a send that was perfectly good. Which branch a send takes is pinned behaviourally by
``tests/test_email_render_fallback.py``.

**1. Is there anything visible at all?** (:func:`has_renderable_content`) Non-empty markup is not
the same as visible content — the visual editor emits a wrapper for every section the author drops
on the canvas, so an author who adds a layout block and types nothing produces markup like
``<div style="padding:32px"></div>`` or a nest of empty ``<table>``s. That is a string, but it
renders as a blank page.

It matters more here than in the document pipeline because publishing an empty email version does
not merely ship an empty email: ``email_render.render_published_email`` resolves the tenant's
published version for that ``code`` on EVERY send, so a blank publish silently switches OFF the
fail-safe fallback to the shipped default (``app/email/defaults.py``) for that code, permanently.
Hence a gate on substance rather than on bytes.

**2. Is any of it still the palette's placeholder copy?** (:func:`placeholder_copy`) The inverse
failure, and the one that actually reached a real inbox: an author dropped a Note Box, never typed
into it, saved and published, and a client-facing email went out reading "Add note content here." —
the editor's own scaffold text, delivered as body copy. Blank at least looks broken; this looks
authored, which is why nobody caught it before a test send. Same gate, same reasoning: a firm that
genuinely wants those words in a client email is not a real case.

Text-in → answer-out: no DB, no network, no rendering.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Elements whose text never reaches the recipient's eye, so their contents don't count as content.
_INVISIBLE = frozenset({"script", "style", "title", "head", "template", "noscript"})

# Elements that put something on the page by themselves, with no text of their own. An image-only
# email (a poster, a rendered statement) is a real thing a firm sends, so it must publish — and must
# then SEND as itself rather than being taken for blank and replaced by the shipped default.
_SUBSTANTIVE = frozenset(
    {"img", "picture", "svg", "video", "audio", "iframe", "object", "embed", "canvas"}
)

# `background-image: url(…)` / `background: … url(…)` — a hero panel is visible substance. A
# background COLOUR is deliberately NOT: a coloured band with no words is still a blank message.
_BACKGROUND_IMAGE = re.compile(r"background(?:-image)?\s*:[^;]*url\s*\(", re.IGNORECASE)

# Characters that occupy no space, so a body made only of them is blank. `str.strip()` already
# handles the whitespace ones (``&nbsp;`` decodes to U+00A0, which counts as whitespace); these are
# the zero-width ones it does not.
_ZERO_WIDTH: dict[int, None] = {
    ord(c): None
    for c in "\u200b\u200c\u200d\u2060\ufeff"  # ZWSP ZWNJ ZWJ WJ BOM
}


def _blank(text: str) -> bool:
    return not text.translate(_ZERO_WIDTH).strip()


class _ContentScanner(HTMLParser):
    """Walks the markup once and latches ``found`` as soon as it meets visible substance."""

    def __init__(self) -> None:
        # convert_charrefs (the default) decodes entities into the text nodes, so `&nbsp;` arrives
        # as U+00A0 and is recognised as whitespace rather than as three visible characters.
        super().__init__(convert_charrefs=True)
        self.found = False
        self._invisible = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _INVISIBLE:
            self._invisible += 1
            return
        # A substantive tag or background attribute INSIDE an invisible container renders nothing
        # (e.g. <noscript><img></noscript>). html.parser treats only <script>/<style> as CDATA, so
        # such nesting is still walked; mirror handle_data's `_invisible` check so the two agree —
        # otherwise the blank-body rule passes markup a recipient never sees: at publish, and again
        # at send, where the same rule is what keeps the shipped-default fallback armed.
        if self._invisible:
            return
        if tag in _SUBSTANTIVE:
            self.found = True
            return
        for name, value in attrs:
            # `background="…"` is the legacy table-background attribute email HTML still uses.
            if name == "background" and (value or "").strip():
                self.found = True
                return
            if name == "style" and value and _BACKGROUND_IMAGE.search(value):
                self.found = True
                return

    def handle_endtag(self, tag: str) -> None:
        if tag in _INVISIBLE and self._invisible:
            self._invisible -= 1

    def handle_data(self, data: str) -> None:
        if not self._invisible and not _blank(data):
            self.found = True

    # Comments are dropped: HTMLParser's handle_comment is a no-op and we don't override it.


def has_renderable_content(html: str) -> bool:
    """Would this body show a recipient anything?

    True when the markup carries a non-whitespace TEXT node outside ``<script>``/``<style>``/
    comments (a Jinja token such as ``{{ first_name }}`` counts — it is text the merge fills), an
    element that renders on its own (an image, a video, an SVG), or a background IMAGE. Everything
    else — an empty wrapper at any depth, a nest of empty table cells, ``&nbsp;`` and other
    zero-width padding, a comment, a lone rule or spacer, styling with nothing inside it — is a
    blank message and returns False.
    """
    if not html or not html.strip():
        return False
    scanner = _ContentScanner()
    try:
        scanner.feed(html)
        scanner.close()
    except Exception:  # noqa: BLE001 - a non-validating parser shouldn't raise, but neither a
        # publish nor a send may 500 on odd markup. A parse failure is not evidence of blankness, so
        # fall back to the pre-scanner rule (any markup beyond the editor's `<body>` wrapper) rather
        # than refusing content the author can see — which on the send path means delivering the
        # firm's own template, not silently substituting ours.
        return bool(re.sub(r"</?body[^>]*>", "", html).strip())
    return scanner.found


# ── the editor's own placeholder copy ─────────────────────────────────────────
#
# WHERE THESE STRINGS COME FROM. Each one is the exact default text a block in the visual email
# editor's palette carries the moment an author drops it, read off
# ``pkgs/ui/src/lib/components/email-editor/create.ts`` — ``EMAIL_BLOCKS[].content`` for the palette
# blocks and ``STARTER_HTML`` for the skeleton offered on a blank canvas.
#
# THAT FILE IS THE OTHER COPY OF THIS LIST. The API container ships without pkgs/ui, so Python
# cannot read the block definitions at runtime and nothing but a test can hold the two together:
# ``tests/test_email_publish_guards.py`` parses create.ts and fails if a string we refuse here no
# longer exists there. That direction is the one that matters — a placeholder RENAMED in the editor
# must break loudly rather than quietly stop being caught, which is exactly how this defect shipped
# (the gate simply did not exist). Same idiom as ``block-contract.json`` / ``flow-contract.json``:
# one truth, both sides pinned.
#
# WHAT IS DELIBERATELY NOT HERE. The Button block's "Open portal" and the starter's identical CTA
# label are real copy — "Open portal" is what a firm would genuinely put on that button, so refusing
# it would block a finished email. The judgement throughout is whether the words could ever be
# addressed to a RECIPIENT: "Add note content here." is addressed to the author, "Open portal" to
# the client.
EDITOR_PLACEHOLDERS: tuple[str, ...] = (
    # Note Box (`container-note-box`). THE observed defect — this went out to a real inbox. An
    # instruction to the author, in the imperative, naming the block itself.
    "Add note content here.",
    # Text block (`text`). Same shape, and "your message" can only be the author's.
    "Write your message here.",
    # STARTER_HTML's body paragraph, whose tail names the editor's own UI ("Insert field",
    # "Preview"): copy that describes the tool it was written in cannot be copy for a client. Listed
    # in full because the rule below matches a whole element, and this paragraph is longer than the
    # Text block's placeholder above rather than a superstring the same entry could catch.
    "Write your message here. Use the Insert field menu to add personalised details, and Preview "
    "to see it with sample values.",
    # Heading block (`heading`). A bare generic noun, safe to refuse only because the match is on an
    # element's WHOLE text: an email whose h2 says nothing but "Heading" is unfinished, while one
    # that mentions a heading mid-sentence is untouched by this.
    "Heading",
    # Bullet List / Numbered List (`typo-bullet-list`, `typo-numbered-list`) — three `<li>`s of
    # enumerated scaffold. Whole-element matching is what makes these safe too: "the first item on
    # the agenda" is a real sentence and publishes; an `<li>` reading only "First item" does not.
    "First item",
    "Second item",
    "Third item",
    # Link block (`link`). Names the concept rather than a destination — a firm labels a link with
    # where it goes.
    "Link text",
)

# Formatting elements that do NOT break a run of copy. Everything else does, which is what makes the
# match below "this element's whole text is still the placeholder" rather than a substring hunt.
# `<a>` is absent on purpose: the Link block's placeholder IS an anchor's entire text, so an anchor
# has to be a boundary. `<br>` is absent for the same kind of reason — it ends a line.
_INLINE = frozenset(
    {
        "strong", "b", "em", "i", "u", "s", "strike", "small", "big", "span", "font", "mark",
        "code", "sub", "sup", "abbr", "cite", "q", "time", "ins", "del", "var", "samp", "kbd",
        "wbr", "bdi", "bdo", "ruby", "rt", "rp",
    }
)  # fmt: skip

_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Comparison form: zero-width padding dropped, whitespace runs collapsed to one space, trimmed,
    case-folded. ``\\s`` covers U+00A0, so a ``&nbsp;``-padded placeholder still matches."""
    return _WHITESPACE.sub(" ", text.translate(_ZERO_WIDTH)).strip().casefold()


_BY_NORMALISED: dict[str, str] = {_normalise(p): p for p in EDITOR_PLACEHOLDERS}


class _SegmentScanner(HTMLParser):
    """Splits the markup's visible text into one segment per element that carries copy.

    Inline formatting is joined (so a paragraph broken up by ``<strong>`` is still one segment);
    every other tag flushes. Invisible elements contribute nothing, as in ``_ContentScanner``.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.segments: list[str] = []
        self._buffer: list[str] = []
        self._invisible = 0

    def _flush(self) -> None:
        if self._buffer:
            self.segments.append("".join(self._buffer))
            self._buffer = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _INLINE:
            self._flush()
        if tag in _INVISIBLE:
            self._invisible += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _INVISIBLE and self._invisible:
            self._invisible -= 1
        if tag not in _INLINE:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._invisible:
            self._buffer.append(data)

    def close(self) -> None:
        # The base close() drains the buffered markup, which can emit more data — flush after it.
        super().close()
        self._flush()


def placeholder_copy(html: str) -> str | None:
    """The first :data:`EDITOR_PLACEHOLDERS` string this body would still deliver, else ``None``.

    The test is that some element's ENTIRE visible text is a placeholder — not that the placeholder
    appears somewhere in the markup. That is the question actually being asked ("did the author drop
    this block and type nothing into it?") and it is what lets the short, generic entries be refused
    at all: a paragraph reading "The first item on the agenda is your annual review" publishes, an
    ``<li>`` reading "First item" does not. The corollary is deliberate: copy an author has PARTLY
    rewritten publishes, because they have plainly read it.

    Returns the canonical string from the tuple (not the author's casing), so a refusal quotes the
    author the words they need to find.
    """
    if not html:
        return None
    scanner = _SegmentScanner()
    try:
        scanner.feed(html)
        scanner.close()
    except Exception:  # noqa: BLE001 - as above, a publish must not 500 on odd markup. A parse
        # failure is not evidence of a placeholder, and the blank gate already refuses markup this
        # broken if it renders nothing, so say "none found" rather than block a real email.
        return None
    for segment in scanner.segments:
        found = _BY_NORMALISED.get(_normalise(segment))
        if found is not None:
            return found
    return None
