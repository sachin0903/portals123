"""Safe merge-field substitution for notification copy.

A rule's title/body may carry ``{dotted.key}`` placeholders (``{client.name}``, ``{account.number}``
…) that render against :func:`app.notifications.context.build_context`. This is DELIBERATELY not a
template engine: only ``{a.b.c}`` tokens are recognised, resolved by a plain dotted lookup into the
context — no expressions, filters, conditionals or attribute access, so authored copy can never
execute anything or read outside the provided context.

An UNRESOLVED token (a placeholder the context does not provide) is dropped to empty, then runs of
spaces left behind are collapsed and the ends trimmed — so ``"A document {document.name} arrived"``
with no ``document.name`` reads ``"A document arrived"``, never a raw ``{document.name}``. Newlines
are preserved (a body may be multi-line). Pinned by ``tests/test_notifications_render.py``.
"""

from __future__ import annotations

import re
from typing import Any

#: A merge token: ``{`` + a dotted lowercase path + ``}``. Anything else (e.g. a stray ``{`` or a
#: brace with spaces) is left untouched — it was not a placeholder.
_TOKEN = re.compile(r"\{([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\}", re.IGNORECASE)


def _lookup(context: dict[str, Any], path: str) -> str | None:
    """Resolve a dotted path against the (possibly nested) context, or None if any segment is
    missing / not a mapping. Returns the string value only for a scalar leaf."""
    node: Any = context
    for segment in path.split("."):
        if not isinstance(node, dict) or segment not in node:
            return None
        node = node[segment]
    if isinstance(node, dict):
        return None
    return str(node)


def render(template: str, context: dict[str, Any]) -> str:
    """Substitute ``{dotted.key}`` tokens in ``template`` from ``context``; drop the unresolved ones
    and tidy the whitespace they leave behind."""
    if not template or "{" not in template:
        return template

    def _sub(match: re.Match[str]) -> str:
        value = _lookup(context, match.group(1))
        return value if value is not None else ""

    out = _TOKEN.sub(_sub, template)
    # Collapse runs of spaces/tabs a dropped token left, and trim each line's ends — but keep
    # newlines (the body can be multi-line).
    out = re.sub(r"[ \t]{2,}", " ", out)
    return "\n".join(line.strip() for line in out.split("\n")).strip("\n")
