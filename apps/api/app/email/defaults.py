"""THE shipped email defaults — one authoring, in token form, for every code we send.

This module IS the default email. There is no second copy: ``email_render.render_default`` merges
what is here through the very same Jinja pass a firm's own published template goes through, so the
mail an un-overridden send delivers and the canvas the editor prefills on "Load default" are one
string rendered twice rather than two texts that agree by vigilance. TOKENS ARE THE SOURCE, not a
Python builder per code, because a token template can be rendered into a sent email where a sent
email cannot be turned back into a template. Nothing here passes Jinja SOURCE through an escaper
(the chrome takes already-safe parts — ``app/email/transport.py``); autoescape escapes the merged
VALUE.

The tokens are the FLAT keys the send sites actually pass (``first_name``, ``document_name``,
``sent_by``, ``item_titles``, ``due_date`` …, with only ``brand`` nested) — see
``app/email/preview.py::sample_context`` and the ``render_or_default`` call sites. A guard here has
to hold at SEND time as well as in the editor, which is why every optional value is ``{% if %}``-ed
or ``or``-defaulted rather than merely looking tidy in a preview.
"""

from __future__ import annotations

import html as html_lib
import json
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple
from urllib.parse import quote

from app.email import shell as email_shell

# Two shapes, differing only here: ``html`` guards with Jinja (what ``render_default`` sends),
# ``source`` with a ``data-if`` on the guarded element, because Jinja on the canvas lands as
# uneditable code chips. ``_visibility_attrs`` MIRRORS the editor's ``encodeVisibility`` (pkgs/ui
# ``email-editor/conditions/attrs.ts``) to the byte, pinned by ``tests/test_email/defaults.py``.

#: How ``describeVisibility`` reads each operator. Author-facing, and part of the mirror.
_GUARD_READING = {"set": "has a value", "unset": "is empty"}


def _visibility_attrs(field: str, op: str, filter_: str = "") -> str:
    """The editor's ``data-if`` pair for "show this when ``field`` is set / unset". ``filter_``
    applies a Jinja filter BEFORE the test — see ``_note`` for the one use and why it is
    load-bearing. The LABEL deliberately names the field alone, because "If message | paragraphs has
    a value" is send-time detail an author does not need to read."""
    rule: dict[str, str] = {"field": field, "op": op}
    if filter_:
        rule["filter"] = filter_
    payload = {
        "action": "show",
        "match": "all",
        "rules": [rule],
    }
    encoded = quote(json.dumps(payload, separators=(",", ":")), safe="-_.!~*'()")
    return f' data-if="{encoded}" data-if-label="If {field} {_GUARD_READING[op]}"'


def _tag_end(markup: str) -> int:
    """Where an attribute goes on ``markup``'s outermost element: after the tag NAME, ahead of the
    element's own attributes. ``markup`` is always one of this module's own fragments, so the first
    ``<tag`` is the element meant."""
    return markup.index(" ") if " " in markup.split(">", 1)[0] else markup.index(">")


def _guard(markup: str, field: str, op: str = "set", filter_: str = "") -> str:
    """Put the condition ON ``markup``'s outermost element, where the panel can find it."""
    at = _tag_end(markup)
    return markup[:at] + _visibility_attrs(field, op, filter_) + markup[at:]


#: Elements that never open a level. Enough for this module's own fragments; a tag outside this set
#: is assumed to have a closing partner, which is true of everything here.
_VOID_TAGS: frozenset[str] = frozenset({"br", "img", "hr", "input", "meta", "link"})


def _guard_all(markup: str, field: str, op: str = "set", filter_: str = "") -> str:
    """Put the condition on EVERY top-level element of ``markup``, not just the first.

    WHY THIS EXISTS. ``_guard`` marks the outermost element, which is right for a fragment that has
    one. ``email_shell.cta_block`` returns TWO siblings — the button table and the "paste this
    link into your browser" paragraph — so guarding the first left the paragraph unconditional, and
    the two
    shapes ``_cta`` builds stopped agreeing: the ``html`` shape wraps both in one ``{% if %}``,
    while
    the editor shape compiled to a guarded table above a permanent sentence with an empty span under
    it. That is the exact defect the guard exists to prevent, re-created by the guard itself.

    Each element gets its own condition rather than a wrapper, because the compile step reads the
    attribute per element (`pkgs/ui` ``conditions/compile.ts``) and one ``{% if %}`` per sibling is
    the same emission as one around both.
    """
    attrs = _visibility_attrs(field, op, filter_)
    out: list[str] = []
    depth = 0
    i = 0
    while i < len(markup):
        if markup[i] != "<":
            nxt = markup.find("<", i)
            nxt = len(markup) if nxt < 0 else nxt
            out.append(markup[i:nxt])
            i = nxt
            continue
        close = markup.index(">", i)
        tag = markup[i : close + 1]
        name = tag[1:].split(">")[0].split(" ")[0].lstrip("/").lower()
        if tag.startswith("</"):
            depth -= 1
        else:
            if depth == 0:
                at = _tag_end(tag)
                tag = tag[:at] + attrs + tag[at:]
            if name not in _VOID_TAGS and not tag.endswith("/>"):
                depth += 1
        out.append(tag)
        i = close + 1
    return "".join(out)


# ---- editor names — SOURCE SHAPE ONLY ---------------------------------------------------------
# A part of a shipped default heads the Style rail with the SAME word as the palette block an author
# can drag in (pkgs/ui ``email-editor/create.ts`` → ``EMAIL_BLOCKS`` and the NAMES rule there): a
# capitalised human word and never a bare GrapesJS key, since ``i18n[name]`` is consulted first and
# "cell" resolves straight back to "Table cell". GrapesJS consumes ``data-gjs-name`` on parse, so
# only the editor needs one and the ``html`` shape carries not a byte of it — pinned by the tests.


def _field(field: str, fallback: str, *, source: bool) -> str:
    """A merge field as the EDITOR's own chip, or the bare Jinja for the send shape.

    A MIRROR of `fieldSpanHtml` (pkgs/ui ``email-editor/create.ts``) on the same terms as
    ``_visibility_attrs``: the attribute names and the token spelling have to match to the byte or
    the panel reads the chip back as ordinary text. `contenteditable="false"` is what actually makes
    the token atomic — without it the caret walks in and a deleted brace ships `{{ first_nam }}`,
    which renders as nothing.

    The FILTER, not `or`: `{{ x or 'y' }}` fires on anything falsy, so a real £0.00 would report the
    fallback, and `fallback` fires only on None/Undefined/whitespace. It is also the only spelling
    the field's "If empty" control can read, so this is what makes a default's fallback editable.
    """
    token = f"{{{{ {field} | fallback('{fallback}') }}}}" if fallback else f"{{{{ {field} }}}}"
    if not source:
        return token
    spare = f' data-field-fallback="{html_lib.escape(fallback, quote=True)}"' if fallback else ""
    # Named in human words, never the bare key: `i18n[name]` is consulted first, so a chip named
    # `first_name` would resolve to something else entirely (the NAMES rule in create.ts).
    label = field.replace("_", " ").capitalize()
    return (
        f'<span data-gjs-type="email-field" data-gjs-name="{html_lib.escape(label, quote=True)}"'
        f' contenteditable="false" data-field="{field}"{spare}>{token}</span>'
    )


def _named(markup: str, name: str, *, source: bool) -> str:
    """``markup`` under the palette's word for it, on the editor shape alone."""
    if not source:
        return markup
    at = _tag_end(markup)
    return markup[:at] + f' data-gjs-name="{name}"' + markup[at:]


# The masthead: the firm's image, or its name set as type — baking either loses a logo or ships a
# broken image. ``data-brand-arm`` previews the arm (``gjs-email-canvas.css``), inert at send.
def _image_fallback(field: str, text: str, *, tag: str = "div", style: str = "") -> str:
    """The editor's ``data-fallback`` attribute: what an image shows when its source is empty. A
    MIRROR of ``encodeFallback`` (pkgs/ui ``email-editor/conditions/attrs.ts``) on the same terms as
    ``_visibility_attrs`` — the encoding has to match to the byte. The fallback's own tag and
    styling travel INSIDE it, so the compile step stays mechanical and this module remains the only
    place that decides what a wordmark looks like."""
    payload = {"field": field, "text": text, "tag": tag, "style": style}
    encoded = quote(json.dumps(payload, separators=(",", ":")), safe="-_.!~*'()")
    return f' data-fallback="{encoded}"'


def _repeat_attr(field: str, alias: str = "item") -> str:
    """The editor's ``data-repeat`` attribute: this element is written once per item of ``field``. A
    MIRROR of ``encodeRepeat`` (pkgs/ui ``email-editor/conditions/attrs.ts``) on the same terms as
    ``_visibility_attrs`` and ``_image_fallback``. It is what makes the request list authorable at
    all: as Jinja the loop is two tags around an example row, which renders on the canvas as visible
    lines of code no author could rebuild."""
    payload = {"field": field, "alias": alias}
    encoded = quote(json.dumps(payload, separators=(",", ":")), safe="-_.!~*'()")
    return f' data-repeat="{encoded}"'


def _masthead(*, source: bool) -> str:
    # One element, so it is named from out here like the footer's parts; `email.py` needs no flag.
    logo = _named(
        email_shell.logo_wordmark("{{ brand.logo_url }}", "{{ brand.name }}", arm="logo"),
        "Brand logo",
        source=source,
    )
    if source:
        # ONE image with a fallback property, not a conditional; the editor compiles it on save.
        return logo.replace(
            "<img",
            "<img"
            + _image_fallback(
                "brand.logo_url", "{{ brand.name }}", style=email_shell.WORDMARK_STYLE
            ),
            1,
        )
    return (
        "{% if brand.logo_url %}"
        + logo
        + "{% else %}"
        + email_shell.text_wordmark("{{ brand.name }}", arm="wordmark")
        + "{% endif %}"
    )


# The outer guard hides the whole footer ROW rather than an empty bordered strip, and is kept
# although ``brand_for`` makes it look never-false — the golden pins it. The inner wraps separator
# AND address in a SPAN, as pkgs/ui's Footer does, or a " · " dangles.
def _footer(*, source: bool) -> str:
    if source:
        inner = "{{ brand.name }}" + _guard(
            f'<span data-gjs-name="Support address">'
            f"{email_shell.support_link('{{ brand.support_email }}', names=True)}</span>",
            "brand.support_email",
        )
        # Editor shape only — the send keeps it, so a firm's own publish ships without it. The cell
        # is the generated chrome's markup, so it is named here rather than at source. The ROW
        # cannot be: `better-svelte-email` has no bare-row primitive, so the `<tr>` is the kit's own
        # and carries no attribute (pkgs/ui/src/lib/email-chrome/README.md §2).
        return email_shell.footer_row(inner).replace("<td ", '<td data-gjs-name="Footer text" ', 1)
    return (
        "{% if brand.name %}"
        + email_shell.footer_row(
            "{{ brand.name }}"
            + "{% if brand.support_email %}"
            + email_shell.support_link("{{ brand.support_email }}")
            + "{% endif %}"
        )
        + "{% endif %}"
    )


# Four paragraph registers. ``margin:0 0 12px`` is the ``paragraphs`` filter's; a golden counts it.

_P = "margin:0 0 14px"
#: The last paragraph in a run: no bottom margin, so the gap below is the shell's cell padding.
_P_LAST = "margin:0"
#: A paragraph FOLLOWING something optional — the margin is on top, so the block above can vanish.
_P_FOLLOWS = "margin:14px 0 0"
#: There is no ASIDE register any more. It set a 14px muted paragraph for the one line under the
#: main copy ("If you didn't ask for this…"), and both halves of that were wrong: a one-pixel step
#: down from the body is not a level, it reads as a mistake; and greying it out made the single
#: sentence a recipient MOST needs to read — the one telling them they can ignore an email they
#: did not ask for — the quietest thing on the card. It is body copy, set as body copy, and only
#: its top margin now says it follows something. `_P_FOLLOWS` already said exactly that.


def _p(inner: str, style: str = _P_LAST, *, source: bool = False, name: str = "Text") -> str:
    """One body paragraph of token source. Every paragraph is the palette's "Text" unless it is a
    named piece of furniture."""
    return _named(f'<p style="{style}">{inner}</p>', name, source=source)


# ``first_name`` is absent on some sends (a personalised reset is an account-existence oracle), so
# it defaults: "Hi there,", never "Hi None,". A PARAGRAPH — "<br><br>" opens a ~50px hole at 1.65.
def _greeting(*, source: bool) -> str:
    return _p(
        f"Hi {_field('first_name', 'there', source=source)},", _P, source=source, name="Greeting"
    )


# The adviser's covering note, via the ``paragraphs`` filter and guarded on ``message | paragraphs``
# — bare ``message`` is truthy for whitespace and ships an empty box. ``background-color``, never
# the ``background`` shorthand the Style rail cannot clear. The 4px foot: ``paragraphs`` adds 12px.
_NOTE_BOX = (
    '<div style="margin:24px 0 0;padding:14px 18px 4px;border-left:3px solid #cbd5e1;'
    f'background-color:#f8fafc;line-height:1.6;color:{email_shell.BODY_INK}">'
    "{{ message | paragraphs }}</div>"
)


def _note(*, source: bool) -> str:
    """The covering-note box, guarded on the RENDERED note (``message | paragraphs``) rather than
    the raw value — see ``_NOTE_BOX``. Flattening that to a plain ``set`` rule is a silent
    regression, which is why the visibility grammar carries an optional filter at all."""
    if source:
        panel = _named(_NOTE_BOX, "Note Box panel", source=True)
        return _guard(panel, "message", "set", "paragraphs")
    return "{% if message | paragraphs %}" + _NOTE_BOX + "{% endif %}"


#: A part that may differ between the two shapes: a plain string, or a callable given ``source``.
_Part = str | Callable[..., str]


def _pick(part: _Part, *, source: bool) -> str:
    """One part of a default, in the shape asked for. A part is the same string in both unless it
    carries a CONDITION — the whole distinction between the two shapes — in which case it passes a
    callable and this is the single place that resolves it."""
    return part(source=source) if callable(part) else part


# NO Python writer for the editor's conditional text node: two wordings is two defaults, pinned.


def _both(heading: _Part, body: _Part, cta: _Cta | str = "") -> tuple[str, str]:
    """A code's two shapes from ONE authoring: ``(html, source)``. Every branch of ``default_for``
    goes through here rather than calling ``_shell`` twice, so a branch physically cannot produce a
    render-ready shape without the editor shape that matches it — the one way this pair could
    silently diverge. A ``cta`` is a plain string (the same in both) or a ``_Cta`` with its own
    two."""
    return (
        _shell(
            _pick(heading, source=False), _pick(body, source=False), _cta_shape(cta, source=False)
        ),
        _shell(
            _pick(heading, source=True),
            _pick(body, source=True),
            _cta_shape(cta, source=True),
            source=True,
        ),
    )


def _cta_shape(cta: _Cta | str, *, source: bool) -> str:
    """The same markup in both shapes (unguarded), or a ``_Cta`` carrying its own two."""
    return (cta.source if source else cta.html) if isinstance(cta, _Cta) else cta


class _Cta(NamedTuple):
    """A guarded CTA's two shapes — see ``_cta``."""

    html: str
    source: str


def _shell(heading: str, body: str, cta: str = "", *, source: bool = False) -> str:
    """The house chrome around a token body — ``email_shell.shell_html`` with the token masthead,
    footer and accent. ``source`` picks the EDITOR's shape — the two conditional parts, and the
    chrome's own editor names — and nothing else differs, so both shapes come from one call site."""
    return email_shell.shell_html(
        heading,
        body,
        wordmark=_masthead(source=source),
        cta=cta,
        footer=_footer(source=source),
        accent="{{ brand.accent }}",
        names=source,
    )


def _cta(label: str, url_token: str, *, guard: bool = True) -> _Cta:
    """A call-to-action, guarded only where its URL can actually be absent.

    ``email_shell.cta_block`` emits button + fallback paragraph unconditionally, which is wrong for
    ``portal_url`` and the task CTA: a deployment with no client-portal host passes None, and an
    unguarded block then publishes a dead ``href=""`` button plus a "paste this link into your
    browser:" line with nothing after it. ``guard=False`` where the link IS the email's purpose
    (``set_password_url``): without it there is nothing to send, so a send never happens, and a
    never-false guard leaves an author unable to tell a real decision from a defensive one.

    An unguarded CTA still carries its two shapes: the button's five parts are named for the editor
    and nowhere else, so even a CTA with no condition on it differs between them."""

    def block(*, source: bool) -> str:
        return email_shell.cta_block(
            label, "{{ " + url_token + " }}", "{{ brand.accent }}", names=source
        )

    if not guard:
        return _Cta(html=block(source=False), source=block(source=True))
    return _Cta(
        html="{% if " + url_token + " %}" + block(source=False) + "{% endif %}",
        # EVERY top-level element: `cta_block` returns the button table AND the paste-the-link
        # paragraph, and guarding only the first is what left the sentence unconditional.
        source=_guard_all(block(source=True), url_token),
    )


#: The shipped inbox preview line per code — one line that earns its place beside the subject, and
#: deliberately TOKEN-FREE: it renders first, so a token blanks on sparse sends. No entry, no span.
PREHEADERS: dict[str, str] = {
    "invite": "Set a password to activate your account.",
    "password_reset": "Choose a new password to get back into your account.",
    "document_sent": "It's waiting for you in your portal.",
    "request_raised": "Your adviser needs some information from you.",
    # The same line on BOTH task codes: it is true of an assignment and of a reminder alike.
    "task_assigned": "Here's what needs doing.",
    "task_reminder": "Here's what needs doing.",
    # Token-free like the rest: a notification's own title and body vary, and render after this.
    "notification": "There's an update waiting in your portal.",
}


def preheader_for(code: str) -> str:
    """The shipped preview-text line for ``code``, or "" for a code with no shipped default (a
    firm's own custom code authors its own; an empty preheader ships no span at all)."""
    return PREHEADERS.get(code, "")


class ShippedDefault(NamedTuple):
    """A code's built-in default: the token form the editor prefills from AND the send path renders.

    ``css`` is always empty — the shipped emails are entirely inline-styled — and is carried so the
    tuple matches the editor's four compose fields exactly."""

    subject: str
    preheader: str
    html: str
    css: str
    #: The EDITOR's copy; feeding the canvas `html` is round-trip damage (round-trip.test.ts).
    source: str = ""


#: Tokens the send DERIVES for a subject — a subject box takes no control — once, for all renderers.
_SUBJECT_TOKENS: dict[str, tuple[str, ...]] = {
    "request_raised": ("request_summary",),
}


def derived_tokens(code: str, context: Mapping[str, Any]) -> dict[str, str]:
    """The tokens a template needs that are DECIDED by the send, not supplied by it.

    Total and never raising: a renderer calls this for every code and merges the answer, so a code
    with no derived tokens gets ``{}`` and a context missing the input gets the safe wording rather
    than an exception on a send path.

    ALWAYS DERIVED, never deferring to a value already in the context. Letting a caller override
    what we infer reads as the more generous rule and is a staleness trap: derive once, change
    ``item_titles``, and the token silently keeps the old count. Nothing overrides these (the flow
    ``send_email`` action supplies its ``variables`` on a different path entirely).
    """
    declared = _SUBJECT_TOKENS.get(code, ())
    if not declared:
        return {}
    out: dict[str, str] = {}
    if code == "request_raised":
        # Pluralised off the count; 0 and 1 both read "action needed" rather than "1 things".
        titles = context.get("item_titles")
        count = len(titles) if isinstance(titles, (list, tuple)) else 0
        out["request_summary"] = (
            f"{count} things need your attention" if count > 1 else "action needed"
        )
    # Declared or it does not ship: the editor's catalogue mirrors this (`merge-fields.test.ts`).
    return {k: v for k, v in out.items() if k in declared}


def default_for(code: str) -> ShippedDefault | None:
    """A code's built-in default with brand / recipient / document values as merge tokens, or
    ``None`` when the code has no shipped default. Rendered against a send site's real context this
    IS the email that goes out (``email_render.render_default``); handed to the editor unrendered it
    is the "Load default" canvas. One function, both answers."""
    if code == "invite":
        subject = "You've been invited to {{ portal_label }}"

        def _invite_body(*, source: bool) -> str:
            return _greeting(source=source) + _p(
                "You've been invited to <strong>{{ portal_label }}</strong>. "
                "Set a password to activate your account and sign in.",
                source=source,
            )

        html, source = _both(
            "Set your password", _invite_body, _cta("Set password", "set_password_url", guard=False)
        )
    elif code == "password_reset":
        subject = "Reset your password"

        def _pr_body(*, source: bool) -> str:
            # An ASIDE, not the paragraph's tail: the one line a recipient who did not ask needs.
            return (
                _greeting(source=source)
                + _p(
                    "We received a request to reset your password. Choose a new one below.",
                    source=source,
                )
                + _p(
                    "If you didn't ask for this, you can safely ignore this email.",
                    _P_FOLLOWS,
                    source=source,
                )
            )

        html, source = _both(
            "Reset your password",
            _pr_body,
            _cta("Choose a new password", "set_password_url", guard=False),
        )
    elif code == "document_sent":
        # Without the fallback the subject reads "Acme Wealth:  is ready for you" — a double space.
        subject = "{{ brand.name }}: {{ document_name | fallback('A document') }} is ready for you"

        def _ds_body(*, source: bool) -> str:
            return (
                _greeting(source=source)
                + _p(
                    f"<strong>{_field('document_name', 'A document', source=source)}</strong> "
                    f"has been shared with you by "
                    f"{_field('sent_by', 'your adviser', source=source)} and is now available "
                    "in your portal.",
                    source=source,
                )
                + _note(source=source)
            )

        html, source = _both(
            "A new document for you", _ds_body, _cta("Open your portal", "portal_url")
        )
    elif code == "request_raised":
        # A plain merge field: the pluralisation is decided by `derived_tokens` on the send side.
        subject = "{{ brand.name }}: {{ request_summary }}"

        def _rr_body(*, source: bool) -> str:
            # `| date`: the send passes `due_date::text`; unfiltered, the inbox reads "2026-01-22".
            deadline = _p(
                "Please complete this by <strong>{{ due_date | date }}</strong>.",
                _P_FOLLOWS,
                source=source,
            )
            return (
                _greeting(source=source)
                + _p("Your adviser has asked you for the following:", source=source)
                # The list is this email's substance, so 8px between items, not the client's zero.
                + _named(
                    '<ul style="margin:14px 0 0;padding-left:22px">', "Bullet List", source=source
                )
                # ONE row marked as repeating in the editor's shape; the send shape keeps the loop.
                + (
                    f'<li{_repeat_attr("item_titles")} data-gjs-name="List item" '
                    'style="margin:0 0 8px">{{ item }}</li>'
                    if source
                    else (
                        "{% for item in item_titles %}"
                        '<li style="margin:0 0 8px">{{ item }}</li>'
                        "{% endfor %}"
                    )
                )
                + "</ul>"
                # Guarded: unguarded it renders "Please complete this by ." with no deadline set.
                + (
                    _guard(deadline, "due_date")
                    if source
                    else "{% if due_date %}" + deadline + "{% endif %}"
                )
                + _note(source=source)
            )

        html, source = _both("Action needed", _rr_body, _cta("Open your portal", "portal_url"))
    elif code in ("task_assigned", "task_reminder"):
        # One shape, two templates, near-identical by design: neither is built out of conditions.
        reminder = code == "task_reminder"
        kind = "Reminder" if reminder else "New task"
        lead = "This task is due soon." if reminder else "You have been assigned a task."
        # A plain string in both shapes, and the heading is the same string — one authoring.
        subject = kind + ": {{ task_title }}"

        def _task_body(*, source: bool) -> str:
            due = _p("Due <strong>{{ due_date | date }}</strong>.", _P_FOLLOWS, source=source)
            # Guarded on the `<span>`, not the token: the leading space must go with the clause.
            clause = (
                _guard(
                    '<span data-gjs-name="Description"> {{ task_description }}</span>',
                    "task_description",
                )
                if source
                else "{% if task_description %} {{ task_description }}{% endif %}"
            )
            return _p(lead + clause, source=source) + (
                # Guarded on both codes: a notification RULE can render either with no `due_date`.
                _guard(due, "due_date") if source else "{% if due_date %}" + due + "{% endif %}"
            )

        # Conditional because a client notice carries a portal link and a staff one carries none.
        html, source = _both(subject, _task_body, _cta("{{ button_label }}", "button_url"))
    elif code == "notification":
        # The ONE wrapper for every notification email — title, body and CTA are plain slots.
        subject = "{{ title }}"

        def _notif_body(*, source: bool) -> str:
            # Guarded because a notification can carry a title and no message at all.
            message = _p("{{ body }}", source=source)
            return _guard(message, "body") if source else "{% if body %}" + message + "{% endif %}"

        html, source = _both("{{ title }}", _notif_body, _cta("{{ button_label }}", "button_url"))
    else:
        return None
    return ShippedDefault(
        subject=subject,
        preheader=preheader_for(code),
        html=html,
        css="",
        source=source,
    )
