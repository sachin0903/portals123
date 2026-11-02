"""Interpolation — the ``{{ key || fallback }}`` grammar automations author their copy in.

One tiny grammar, three sinks: a task's title/description, an email's context, and (Phase D) a
webhook's payload. Phase B's case memory reads the same context, so this module is deliberately
pure and dependency-free — it is a string function, not a template engine, and the difference is
the whole point.

**Why not Jinja.** ``app/render/jinja_engine.py`` already renders documents, and reaching for it
here would be the obvious reuse. It is the wrong reuse: Jinja is a *programming language* (calls,
attribute access, filters, control flow), and the strings it would be evaluating are authored in
a no-code rule builder by a firm's operations staff, then rendered in the worker under
``service_role``. A template language whose input is end-user-authored and whose output goes to a
client is a sandbox-escape surface we would have to defend for ever. This grammar cannot express
anything except "substitute a value, or a fallback" — there is nothing to escape from.

**The three safety properties**, each pinned by tests:

1. **Values are data, never format strings.** Substitution is a SINGLE pass: a value that itself
   contains ``{{ … }}`` is inserted literally and never re-scanned. Without this, a client whose
   surname is ``{{ contact.bank_account }}`` becomes an exfiltration primitive.
2. **Escaping belongs to the sink, not here.** ``render`` returns plain text. An HTML sink passes
   ``escape=html.escape``, which is applied to substituted VALUES only — never to the template
   literal, which is the firm's own authored, publish-gated markup. Getting this backwards either
   double-escapes the author's markup or lets a client's data inject into it.
3. **A missing key is empty, never an error and never the raw token.** Rendering ``{{ x }}``
   literally into a client's email because a fact was absent is the failure mode this replaces;
   ``render_with_missing`` hands the caller the list so a preflight can warn about it instead.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

#: The whole grammar. ``{{ key }}`` or ``{{ key || fallback }}``.
#:
#: The key is a dotted path (``contact.name``, ``event.subject.amount``) — the same flat
#: dotted-path vocabulary ``app/flows/conditions.py`` evaluates against, so an author who has
#: learned the condition field picker already knows how to name a value here.
#:
#: The fallback is literal text up to the closing braces, optionally quoted so leading/trailing
#: spaces survive. It may not itself contain ``}}`` — a fallback needing brace syntax is a sign
#: the author wants a template language, which is exactly what this is not.
_TOKEN = re.compile(
    r"""
    \{\{\s*
    (?P<key>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)
    (?:\s*\|\|\s*(?P<fallback>(?:(?!\}\}).)*?))?
    \s*\}\}
    """,
    re.VERBOSE | re.DOTALL,
)

#: Cap on one rendered string. A rule that interpolates a 10MB dataset cell into a task title
#: would otherwise put it in a page the whole firm loads. Truncation is visible (an ellipsis)
#: rather than silent.
MAX_RENDERED = 20_000

#: Cap on a single substituted value, for the same reason but per-token — so one runaway value
#: cannot consume the entire budget and push every later token out of the string.
MAX_VALUE = 2_000

_ELLIPSIS = "…"


def _lookup(context: Mapping[str, Any], key: str) -> Any:
    """Resolve one dotted key.

    Flat-key first: the condition context (``automation.build_facts``) is a flat mapping whose
    KEYS contain dots (``"contact.type"``), so an exact hit is both the common case and the
    cheap one. Only if that misses do we walk nested mappings, which is what a caller who
    assembles a structured context (Phase B's case memory) will hand us. Anything that is not a
    mapping mid-walk ends the walk — attribute access is deliberately NOT supported, because
    reaching into arbitrary Python objects from author-supplied text is the sandbox escape this
    grammar exists to avoid.
    """
    if key in context:
        return context[key]
    current: Any = context
    for part in key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _stringify(value: Any) -> str:
    """One value as text. ``True``/``False`` render as "Yes"/"No" because these strings are read
    by people in task titles and emails, not parsed; everything else is ``str``."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _is_absent(value: Any) -> bool:
    """Does this value trigger the fallback? ``None`` and blank text only.

    ``0`` and ``False`` are VALUES, deliberately — "0 outstanding items" is a sentence a firm
    means to send, and the fallback firing on it would be a quiet lie. This is the same emptiness
    rule ``conditions.isEmpty`` uses, minus the container cases (a list interpolates as itself).
    """
    return value is None or (isinstance(value, str) and not value.strip())


def to_jinja(template: str) -> str:
    """Rewrite this grammar's fallbacks into the template engine's, leaving everything else alone.

    WHY THIS EXISTS. ``send_email`` used to render an email template with :func:`render`, and the
    email EDITOR writes Jinja — so a Format, a condition or a field fallback authored there was
    passed through unchanged into a client's inbox as literal braces. That path now renders with
    ``app/render/jinja_engine.py``, which fixes it in the direction that matters. But it breaks the
    other direction: ``{{ x || y }}`` is not Jinja, it is a syntax error, and templates already
    authored with it would stop rendering at all. So the two are translated rather than one being
    declared the loser.

    ``{{ key || text }}`` becomes ``{{ key | fallback('text') }}``, and the semantics survive
    exactly: :func:`_is_absent` fires on None and blank text and deliberately not on ``0``/``False``
    ("0 outstanding items" is a sentence a firm means to send), and ``jinja_engine``'s ``fallback``
    filter implements that same rule — the two were written independently and agree.

    A token WITHOUT ``||`` is left untouched: ``{{ contact.name }}`` is already valid Jinja and
    means the same thing in both. A token carrying a Jinja filter (``{{ amount | currency }}``) does
    not match :data:`_TOKEN` at all — one pipe is not two — so it passes through as the Jinja it
    already is, which is the whole point of doing this as a translation of one construct rather than
    a rewrite of the string.
    """

    def convert(match: re.Match[str]) -> str:
        key = match.group("key")
        fallback = match.group("fallback")
        if fallback is None:
            return match.group(0)
        # Quoted in the source to protect leading/trailing spaces; the quotes are the grammar's,
        # not part of the text, so they come off before the text goes into a Jinja literal.
        text = fallback.strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
            text = text[1:-1]
        else:
            text = fallback
        # Backslash FIRST, or the escape added for the quote is itself re-escaped and the literal
        # ends early — the same ordering `jinjaString` in the conditions model documents.
        literal = text.replace("\\", "\\\\").replace("'", "\\'")
        return f"{{{{ {key} | fallback('{literal}') }}}}"

    return _TOKEN.sub(convert, template)


def render_with_missing(
    template: str,
    context: Mapping[str, Any],
    *,
    escape: Callable[[str], str] | None = None,
) -> tuple[str, list[str]]:
    """Render ``template``, returning the text and the keys that resolved to nothing.

    A key is "missing" when it resolved to absent AND the author supplied no fallback — a
    fallback is the author saying "I know this may be blank, here is what to say instead", so
    counting it as missing would make the diagnostic useless noise.

    ``escape`` is applied to each substituted value only. See the module header: the template
    literal is the author's own markup and must pass through untouched.
    """
    missing: list[str] = []

    def substitute(match: re.Match[str]) -> str:
        key = match.group("key")
        fallback = match.group("fallback")
        value = _lookup(context, key)
        if _is_absent(value):
            if fallback is None:
                missing.append(key)
                text = ""
            else:
                # A quoted fallback keeps its inner whitespace; a bare one is trimmed by the
                # pattern's own \s* — so `{{ name || "  " }}` can mean two literal spaces.
                text = fallback.strip()
                if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
                    text = text[1:-1]
        else:
            text = _stringify(value)
        if len(text) > MAX_VALUE:
            text = text[:MAX_VALUE] + _ELLIPSIS
        # Escape LAST, so the cap never slices a multi-byte entity in half.
        return escape(text) if escape else text

    # ONE pass. `re.sub` with a function inserts the returned string literally — no backreference
    # expansion, no re-scan — which is property (1) in the module header.
    rendered = _TOKEN.sub(substitute, template)
    if len(rendered) > MAX_RENDERED:
        rendered = rendered[:MAX_RENDERED] + _ELLIPSIS
    return rendered, missing


def render(
    template: str,
    context: Mapping[str, Any],
    *,
    escape: Callable[[str], str] | None = None,
) -> str:
    """``render_with_missing`` without the diagnostics — the common call."""
    return render_with_missing(template, context, escape=escape)[0]


def keys(template: str) -> list[str]:
    """Every key a template references, in order of first appearance (deduplicated).

    Used by publish preflight to tell an author which facts their copy depends on, before it is
    rendered at 3am against a client who happens not to have one of them.
    """
    seen: list[str] = []
    for match in _TOKEN.finditer(template):
        key = match.group("key")
        if key not in seen:
            seen.append(key)
    return seen
