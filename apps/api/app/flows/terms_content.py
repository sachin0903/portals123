"""Authored declaration copy → the HTML the client reads and ticks.

An ``accept_terms`` step can point at a library PDF **or** carry the words inline (markdown, or
already-sanitised HTML). Either way the client portal renders HTML from
``documents.documents.metadata.content`` for ``document_type='terms'``; this module is the only
door that authored prose may pass through, so a firm cannot inject a script into the portal.
"""

from __future__ import annotations

import re
from html import escape

from app.content import sanitise_html

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_HEADING = re.compile(r"^(#{1,3})\s+(.+)$")
_UL = re.compile(r"^[-*]\s+(.+)$")
_OL = re.compile(r"^\d+[.)]\s+(.+)$")


def declaration_html(src: str) -> str:
    """Markdown (or a pasted HTML snippet) → sanitised HTML for the client to read."""
    text = (src or "").strip()
    if not text:
        return ""
    if text.lstrip().startswith("<"):
        return sanitise_html(text)
    return sanitise_html(_markdown_to_html(text))


def _inline(text: str) -> str:
    escaped = escape(text)
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC.sub(r"<em>\1</em>", escaped)
    return _LINK.sub(r'<a href="\2">\1</a>', escaped)


def _markdown_to_html(src: str) -> str:
    lines = src.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue
        if _UL.match(line):
            items: list[str] = []
            while i < len(lines) and (m := _UL.match(lines[i])):
                items.append(f"<li>{_inline(m.group(1))}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        if _OL.match(line):
            items = []
            while i < len(lines) and (m := _OL.match(lines[i])):
                items.append(f"<li>{_inline(m.group(1))}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue
        para = [line]
        i += 1
        while (
            i < len(lines)
            and lines[i].strip()
            and not _HEADING.match(lines[i])
            and not _UL.match(lines[i])
            and not _OL.match(lines[i])
        ):
            para.append(lines[i])
            i += 1
        out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
    return "".join(out)
