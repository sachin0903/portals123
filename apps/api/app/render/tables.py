"""Pure-Python data-table → styled HTML for the PDF render path.

A ``<g-table>`` marker is substituted at render time with the HTML table built here from the
component's config (columns/headers/colours) + its resolved dataset rows. Inline styles keep it
self-contained for WeasyPrint. Pure function of (component, rows) — unit-testable, no I/O.

An empty table keeps its header and states the cause in one spanning row, because an empty dataset
and an unfinished component are different problems with different fixes
(``app/render/empty_state.py``).
"""

from __future__ import annotations

from typing import Any

from app.render.empty_state import TABLE_NO_COLUMNS, dataset_label, no_rows_message


def _esc(value: Any) -> str:
    if value is None:
        return ""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _split(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _titleise(field: str) -> str:
    return field.replace("_", " ").replace("-", " ").strip().title()


#: What a column may be aligned to. Closed, so an unrecognised value leaves the column alone rather
#: than reaching a style string.
ALIGNMENTS = ("left", "right", "center")


def _widths(raw: Any, count: int) -> list[str]:
    """Per-column widths as CSS percentages, one entry per column, "" for automatic.

    PERCENTAGES, not pixels: the table is ``width:100%`` of whatever box it sits in, and a page's
    content width is a physical measurement an author cannot see from the editor. A percentage says
    the same thing at any page size and cannot push the table off the sheet.

    Clamped to 1-100 and never rescaled to sum to 100 -- an author's numbers are their numbers, and
    silently normalising them makes the control unpredictable. Junk falls back to automatic, so a
    half-filled list still lays out.
    """
    given = _split(raw)
    out: list[str] = []
    for i in range(count):
        value = given[i] if i < len(given) else ""
        try:
            num = float(value)
        except (TypeError, ValueError):
            out.append("")
            continue
        out.append(f"{max(1.0, min(100.0, num)):g}%" if num > 0 else "")
    return out


def _aligns(raw: Any, count: int) -> list[str]:
    """Per-column horizontal alignment, one entry per column, defaulting to left.

    Right alignment earns its place: a column of figures is comparable at a glance only when its
    digits line up, which is the rule the book's own tables already follow.
    """
    given = _split(raw)
    return [given[i] if i < len(given) and given[i] in ALIGNMENTS else "left" for i in range(count)]


def render_table_html(comp: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """Render a data_table component + its resolved rows to a standalone styled HTML table."""
    props = comp.get("props") or {}
    columns = _split(props.get("columns"))
    if not columns:  # no explicit column list → take every key from the first row
        columns = list(rows[0].keys()) if rows else []
    headers = _split(props.get("headers"))
    labels = [
        headers[i] if i < len(headers) and headers[i] else _titleise(col)
        for i, col in enumerate(columns)
    ]
    header_bg = str(props.get("header_bg") or "#1e3a5f")
    header_fg = str(props.get("header_fg") or "#ffffff")
    striped = str(props.get("striped", "true")).lower() != "false"
    title = str(props.get("title") or "")

    widths = _widths(props.get("widths"), len(columns))
    aligns = _aligns(props.get("aligns"), len(columns))

    table_style = (
        "width:100%;border-collapse:collapse;font-size:11px;"
        "font-family:Arial,sans-serif;color:#374151;"
    )
    # `fixed` ONLY WHEN A WIDTH IS SET, and that is the whole of why a width used to do nothing:
    # under `auto` the engine treats a declared width as a suggestion and widens the column anyway
    # to fit its longest cell. With no widths set, `auto` is the better layout -- it sizes columns
    # to their content -- so the switch follows the author rather than being imposed.
    if any(widths):
        table_style += "table-layout:fixed;"
    th_base = (
        f"background:{_esc(header_bg)};color:{_esc(header_fg)};"
        "padding:6px 8px;font-weight:600;border:1px solid rgba(0,0,0,0.08);"
    )
    # `word-wrap` because a fixed layout will not widen a column for a long value: without it an
    # unbroken string (an ISIN, a reference) overflows its cell instead of wrapping inside it.
    td_base = (
        "padding:6px 8px;border:1px solid #e5e7eb;vertical-align:top;"
        "word-wrap:break-word;overflow-wrap:break-word;"
    )

    parts: list[str] = []
    if title:
        parts.append(
            f'<div style="font-size:12px;font-weight:600;color:#374151;margin:0 0 4px;'
            f'font-family:Arial,sans-serif;">{_esc(title)}</div>'
        )
    parts.append(f'<table style="{table_style}">')
    if columns and any(widths):
        # A `<colgroup>` rather than a width on every `<th>`: one declaration per column, it applies
        # to the body cells too, and it survives a table whose header is not rendered.
        cols = "".join(f'<col style="width:{w}"/>' if w else "<col/>" for w in widths)
        parts.append(f"<colgroup>{cols}</colgroup>")
    if columns:
        head = "".join(
            f'<th style="{th_base}text-align:{aligns[i]};">{_esc(lbl)}</th>'
            for i, lbl in enumerate(labels)
        )
        parts.append(f"<thead><tr>{head}</tr></thead>")
    parts.append("<tbody>")
    if rows:
        for ri, row in enumerate(rows):
            zebra = ";background:#f9fafb" if striped and ri % 2 else ""
            cells = "".join(
                f'<td style="{td_base}text-align:{aligns[i]};{zebra}">{_esc(row.get(col))}</td>'
                for i, col in enumerate(columns)
            )
            parts.append(f"<tr>{cells}</tr>")
    else:
        # With no columns there is no header either, so the reader is looking at a bare box: the
        # authoring omission is both what they see and the thing to fix, and it is reported ahead
        # of the empty dataset. Choosing columns then gets an empty dataset its own words.
        message = TABLE_NO_COLUMNS if not columns else no_rows_message(dataset_label(comp))
        span = max(len(columns), 1)
        parts.append(
            f'<tr><td colspan="{span}" style="{td_base}text-align:center;color:#9ca3af;">'
            f"{_esc(message)}</td></tr>"
        )
    parts.append("</tbody></table>")
    return "".join(parts)
