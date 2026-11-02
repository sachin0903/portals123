"""Substitute ``<g-chart>`` / ``<g-table>`` markers in rendered template HTML with server-rendered
SVG / HTML (docs/document-generation.md §5). Runs AFTER the Jinja pass (the markers carry no Jinja)
and BEFORE the PDF renderer, so WeasyPrint never sees the custom elements.

Each marker's ``data-id`` keys into ``schema.components[]`` for the component's config; its bound
dataset code (``binding._resolved_data`` = ``"{{ code }}"``) keys into the resolved rows map.

Robustness: the editor emits self-closing markers, but save-time DOM round-tripping can reserialise
``<g-table/>`` into an open tag whose following siblings get nested inside it. So we replace only
the OPENING tag (in place) and strip any close tags — preserving the document order of surrounding
real content instead of deleting it. A missing component or a render error yields an empty string
(the marker was blank anyway) — one bad component never fails the whole document.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.render.charts import render_chart_svg
from app.render.resolve import rows_from_static
from app.render.tables import render_table_html

logger = logging.getLogger("document_worker")

_OPEN_TAG = re.compile(r"<g-(chart|table)\b([^>]*)>", re.IGNORECASE)
_CLOSE_TAG = re.compile(r"</g-(?:chart|table)\s*>", re.IGNORECASE)
_RESOLVED = re.compile(r"\{\{\s*(.+?)\s*\}\}")
# A `{% for <target(s)> in <source> %}` whose source is a bare identifier — the Jinja-table /
# repeat loop shape. Handles a tuple target (`a, b`) and a trailing filter / `if` guard
# (`... if x.active %}`, `... | sort %}`). A subscripted or dotted source (`answers.items`,
# `data['k']`) is a nested path, not a dataset code, and is excluded by the negative lookahead.
_FOR_LOOP = re.compile(
    r"\{%-?\s*for\s+\w+(?:\s*,\s*\w+)*\s+in\s+([a-zA-Z_]\w*)(?![\w.\[])", re.IGNORECASE
)


_PX = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:px)?\s*$")


def _size(value: str, fallback: float) -> float:
    """A chart drawing dimension in CSS px, or the canonical fallback.

    Deliberately NOT ``to_number``, which strips every non-digit and so reads ``100%`` as 100,
    ``12.5rem`` as 12.5 and ``auto`` as nothing. That was survivable while the SVG letterboxed --
    a wrong number merely shifted the fit -- and is not now the viewBox STRETCHES to the container
    (``charts.py::_frame``): a 100-unit viewBox across a 600px box magnifies every glyph six times.

    A unit we cannot resolve here is not a number to salvage. There is no CSS engine in this
    process, so a percentage has nothing to be a percentage OF, and the canonical size is the only
    honest answer. Zero and negatives fall back too: they are not a box, and they would divide.
    """
    m = _PX.match(value or "")
    if m is None:
        return fallback
    px = float(m.group(1))
    return px if px > 0 else fallback


def _attr(name: str, attrs: str) -> str:
    m = re.search(rf'{name}\s*=\s*"([^"]*)"', attrs, re.IGNORECASE)
    return m.group(1) if m else ""


def _dataset_code(comp: dict[str, Any]) -> str:
    """The dataset code a component binds — from ``binding._resolved_data`` (``{{ code }}``)."""
    resolved = str((comp.get("binding") or {}).get("_resolved_data") or "")
    m = _RESOLVED.search(resolved)
    return m.group(1).strip() if m else ""


def _inline_rows(comp: dict[str, Any]) -> list[dict[str, Any]]:
    """Manually-entered rows on a component (chart/table source-mode = manual), normalised to
    ``list[dict]``. Preferred over a dataset binding when present, so a chart/table with hand-typed
    or CSV-imported data renders without any dataset (see the editor's manual data-source mode)."""
    data = (comp.get("binding") or {}).get("_inline_rows")
    return rows_from_static(data) if data else []


def dataset_codes(components: list[dict[str, Any]] | None) -> list[str]:
    """Every dataset code the components bind (de-duped) — so the worker resolves each even when a
    component's source wasn't also recorded in the template's ``linked_datasets``."""
    codes = [_dataset_code(c) for c in (components or [])]
    return list(dict.fromkeys(c for c in codes if c))


def loop_sources(html: str | None) -> list[str]:
    """Bare-identifier ``{% for ... in <source> %}`` loop sources in the HTML (de-duped, in order),
    including filtered and tuple-target loops; dotted / subscripted sources are excluded.

    A Jinja table (``doc-jtable``) or a repeat block iterates a dataset directly by code and carries
    no component binding, so — unlike a chart — its dataset is invisible to ``dataset_codes``. These
    sources are candidate dataset codes; the worker resolves them next to the component bindings so
    the loop's rows reach the render context. Non-dataset names resolve to nothing (harmless)."""
    if not html:
        return []
    return list(dict.fromkeys(m.group(1) for m in _FOR_LOOP.finditer(html)))


def substitute_components(
    html: str,
    components: list[dict[str, Any]] | None,
    rows_by_code: dict[str, list[dict[str, Any]]],
) -> str:
    """Replace every chart/table marker with its rendered visual; strip leftover close tags."""
    by_id = {c.get("id"): c for c in (components or []) if c.get("id")}

    def render_marker(match: re.Match[str]) -> str:
        kind = match.group(1).lower()
        attrs = match.group(2)
        comp = by_id.get(_attr("data-id", attrs))
        if comp is None:
            return ""
        # Manual (hand-typed / CSV-imported) rows win over a dataset binding when present.
        rows = _inline_rows(comp) or rows_by_code.get(_dataset_code(comp), [])
        try:
            if kind == "table" or comp.get("type") == "data_table":
                return render_table_html(comp, rows)
            width = _size(_attr("data-width", attrs), 600.0)
            height = _size(_attr("data-height", attrs), 340.0)
            return render_chart_svg(comp, rows, width, height)
        except Exception as exc:  # noqa: BLE001 — one bad component must not fail the document
            logger.warning("component %r failed to render: %s", comp.get("id"), exc)
            return ""

    return _CLOSE_TAG.sub("", _OPEN_TAG.sub(render_marker, html))
