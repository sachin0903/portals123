"""Pure-Python chart → inline SVG for the PDF render path.

WeasyPrint has no JavaScript engine, so the editor's canvas/JS chart preview can never appear in a
generated PDF (docs/document-generation.md §5.1). Instead each ``<g-chart>`` marker is substituted
at render time with a static SVG built here from the component's config + its resolved dataset rows.
Supports the five editor chart types (line / bar / pie / donut / stacked area). Inline SVG renders
natively in WeasyPrint (and Chromium), needs no assets, and is deterministic.

Everything here is a pure function of (component, rows, size) — no I/O — so it unit-tests without
the render stack. An empty chart renders a captioned empty state in the same footprint rather than
nothing, so a misconfigured component is visible (not a silent blank), and the caption names the
cause: an empty dataset and an unfinished component are different problems with different fixes
(``app/render/empty_state.py``).
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple

from app.render.empty_state import CHART_NO_FIELDS, dataset_label, no_rows_message
from app.render.resolve import to_number

# Default series palette — brand teal + ink-navy lead (the platform's brand accent + primary),
# then a categorical spread. Kept in sync with chart-preview.ts DEFAULT_PALETTE so the editor and
# PDF match. A component may override per-chart via props (data-comp-colors).
DEFAULT_PALETTE = [
    "#0f766e",
    "#1e3a5f",
    "#3b82f6",
    "#f59e0b",
    "#8b5cf6",
    "#10b981",
    "#ef4444",
    "#ec4899",
]
_GRID = "#eef2f6"
_AXIS = "#cbd5e1"
_TEXT = "#1e293b"
_MUTED = "#94a3b8"
# Default card chrome drawn behind every chart — TRANSPARENT, with a hairline border and rounded
# corners so a chart still reads as a framed figure. The background belongs to the frame DIV, which
# `chart-frame` serialises with its own padding/background/border around this marker, so a white
# slab here painted straight over whatever the author had set: a chart on a tinted panel came out
# white and no control anywhere would change it. `none` is a paint, not a colour — the rect is still
# drawn for its stroke.
_CARD_BG = "none"
_CARD_BORDER = "#e2e8f0"
# Chart text. The family is a stack, not a face: WeasyPrint resolves against the fonts installed in
# the render container, so a name it cannot find must fall back to something rather than to nothing.
_FONT_FAMILY = "Arial, sans-serif"
# Every font-size in this module is expressed as a multiple of this base, so ONE control resizes
# all of a chart's text in proportion. 9 is the axis-tick size, the smallest thing a reader must
# still be able to read, which makes it the honest thing to anchor on.
_BASE_FONT = 9.0


class _Type(NamedTuple):
    """Resolved chart typography — the font stack, the base size, and the two text colours."""

    family: str
    base: float
    text: str
    muted: str


def _typography(props: dict[str, Any]) -> _Type:
    """Read the author's typography choices, each falling back to the module default.

    MUTED FOLLOWS TEXT ONLY WHEN TEXT WAS CHOSEN. Tick labels are a quieter grey than the title,
    and that grey is only quieter against a light card — set white text for a chart on a dark panel
    and a fixed `_MUTED` leaves the axis unreadable, which is the "control changed half the words"
    defect. Deriving it unconditionally would instead re-tone the ticks of every chart already
    authored, so the default is preserved exactly and a chosen colour reaches every word.
    """
    family = str(props.get("font_family") or "").strip() or _FONT_FAMILY
    text = str(props.get("text_color") or "").strip() or _TEXT
    return _Type(
        family=family,
        base=_px(props.get("font_size"), _BASE_FONT),
        text=text,
        muted=_MUTED if text == _TEXT else text,
    )


def _fs(t: _Type, canonical: float) -> str:
    """A font size, scaled from the author's base. `canonical` is the size at the default base, so
    an untouched chart renders the exact numbers this module has always written."""
    return _fmt(t.base * canonical / _BASE_FONT)


def _esc(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt(v: float) -> str:
    """Compact coordinate string (1dp, trailing-zero-stripped)."""
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _axis_label(v: float) -> str:
    """Human y-axis tick label — thousands-separated, no noise decimals."""
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


#: What a figure on a chart may be. Closed on purpose: an author picks from a list, and an
#: unrecognised value falls back to ``number`` rather than reaching a format string.
VALUE_FORMATS = ("number", "currency", "percent")


def _value_format(props: dict[str, Any]) -> tuple[str, int | None]:
    """The figure format an author chose, and how many decimals, both validated.

    ``decimals`` of ``None`` means the automatic behaviour ``_axis_label`` has always had: an
    integer prints bare, anything else to at most 2dp with trailing zeros trimmed. A number chosen
    explicitly is honoured exactly -- ``0`` is a real answer for a chart of whole pounds, so it
    cannot be conflated with "unset".
    """
    fmt = str(props.get("value_format") or "number")
    if fmt not in VALUE_FORMATS:
        fmt = "number"
    raw = props.get("value_decimals")
    dec: int | None = None
    if raw is not None and str(raw) != "":
        num = to_number(raw)
        if num is not None:
            dec = max(0, min(4, int(num)))
    return fmt, dec


def _value_text(v: float, fmt: str = "number", decimals: int | None = None) -> str:
    """A tick or a data label. £ is the platform's currency (British English throughout); a chart
    that needs another one needs a `currency_symbol` prop, not a guess from the data."""
    body = _axis_label(v) if decimals is None else f"{v:,.{decimals}f}"
    if fmt == "currency":
        return f"£{body}"
    if fmt == "percent":
        return f"{body}%"
    return body


def _px(value: Any, default: float) -> float:
    n = to_number(value)
    return n if n and n > 0 else default


def _palette(props: dict[str, Any]) -> list[str]:
    colors = props.get("colors")
    if isinstance(colors, list) and colors:
        return [str(c) for c in colors]
    if isinstance(colors, str) and colors.strip():
        parts = [c.strip() for c in colors.split(",") if c.strip()]
        if parts:
            return parts
    return DEFAULT_PALETTE


# Per-type shape controls. Each is a NAMED STEP rather than a free number: a chart is read at a
# glance in a printed document, and "bar width 0.83" is a question nobody can answer about their own
# report. The first entry of each is the value these charts have always drawn, so a component that
# names none of them is untouched.
_LINE_DASH = {"solid": "", "dashed": "7 5", "dotted": "1 4"}
_BAR_FILL = {"medium": 0.8, "narrow": 0.5, "wide": 0.95}
_RING_HOLE = {"medium": 0.58, "thin": 0.72, "thick": 0.4}


def _step(props: dict[str, Any], key: str, table: dict[str, Any]) -> Any:
    """One of a named set, falling back to the first entry — which is always today's drawing."""
    name = str(props.get(key) or "").strip().lower()
    return table.get(name, next(iter(table.values())))


def _on(props: dict[str, Any], key: str) -> bool:
    """An explicitly-on switch. Absent is OFF for every one of these: each adds ink to the figure,
    and a chart already in a client's report must not gain marks because a feature shipped."""
    return str(props.get(key) or "").strip().lower() in ("true", "1", "yes", "on")


def _grid_ink(props: dict[str, Any]) -> tuple[str, str]:
    """The gridlines and the axis lines, as one decision.

    THE AXIS FOLLOWS THE GRID ONLY WHEN THE GRID WAS CHOSEN — the same rule `_typography` applies to
    its muted text, and for the same reason. These are the chart's structural ink: the grid is a
    near-white hairline and the axis a mid grey, a hierarchy that only reads on a pale card. Put the
    chart on a dark background and both need moving, and an author who could darken one but not the
    other would get a pale L drawn around a dark grid. Deriving it unconditionally would instead
    re-tone the axis of every chart already authored, so the default pair is preserved exactly.
    """
    grid = str(props.get("grid_color") or "").strip()
    return (grid or _GRID, grid or _AXIS)


def _card_bg(props: dict[str, Any]) -> str:
    """The chart's own background paint.

    IT IS THE CHART'S NOW, NOT THE FRAME'S. It used to be set on the wrapper div through the style
    rail, which put the one visual decision an author makes most often two panels away from the
    chart it applies to — and left the SVG painting `none` over it on the promise that the div
    behind would show through. Painted here it is a property of the figure, travels with it, and is
    configured beside the colours of the series it sits behind. Default stays transparent, so a
    chart already carrying a background on its frame is unchanged.
    """
    return str(props.get("background") or "").strip() or _CARD_BG


def _y_fields(binding: dict[str, Any]) -> list[str]:
    y = binding.get("y_field")
    if isinstance(y, list):
        return [str(f) for f in y if f]
    return [str(y)] if y else []


def _frame(
    width: float,
    height: float,
    body: str,
    t: _Type,
    title: str = "",
    bg: bool = True,
    card_bg: str = _CARD_BG,
) -> str:
    """Wrap chart body in an SVG root sized to its container.

    NOTHING IS EVER STRETCHED. The chart is LAID OUT at the box it was drawn at -- ``width`` and
    ``height`` here are the author's own box, exported as ``data-width``/``data-height`` -- so a
    taller box is a LONGER Y AXIS and a wider one is a wider plot, not the same picture scaled
    unevenly. Text keeps its size, a stroke keeps its weight, and a pie stays round.

    That is why the missing ``data-width`` was the whole defect and not a detail. Without it this
    was always handed the canonical 600, so the drawing's shape never matched the box and
    ``xMidYMid meet`` -- fit inside and centre -- had something to letterbox on every single chart.
    ``preserveAspectRatio="none"`` was tried as the answer and it is the wrong one: it removes the
    bands by distorting the drawing, treating the symptom. With the real box through, the viewBox
    EQUALS the container and ``meet`` has nothing to fit; it stays as the safe degradation for a
    residual mismatch, where empty space is a far better failure than smeared text.

    A size that cannot be resolved falls back to the canonical figure rather than to a number read
    out of a percentage (``components.py::_size``).

    By default paints the card background/border behind the chart (`bg=False` for callers that draw
    their own frame, e.g. the empty state's dashed outline).
    """
    card = ""
    if bg:
        card = (
            f'<rect x="0.5" y="0.5" width="{_fmt(width - 1)}" height="{_fmt(height - 1)}" '
            f'rx="8" fill="{card_bg}" stroke="{_CARD_BORDER}"/>'
        )
    head = ""
    if title:
        head = (
            f'<text x="{_fmt(width / 2)}" y="18" text-anchor="middle" '
            f'font-size="{_fs(t, 13)}" font-weight="600" fill="{t.text}" '
            f'font-family="{_esc(t.family)}">{_esc(title)}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_fmt(width)} {_fmt(height)}" '
        f'width="100%" height="100%" preserveAspectRatio="xMidYMid meet" '
        f'font-family="{_esc(t.family)}">{card}{head}{body}</svg>'
    )


def _placeholder(width: float, height: float, title: str, message: str, t: _Type) -> str:
    """An empty chart: the card's own footprint drawn as a DASHED, unfilled outline with a small
    quiet caption. It keeps the outer dimensions of a populated chart so the document's layout
    doesn't move, and takes neither the white card nor a filled slab — an unfilled dashed frame
    reads as a deliberate empty state, where a grey slab reads as a broken figure."""
    body = (
        f'<rect x="0.5" y="0.5" width="{_fmt(width - 1)}" height="{_fmt(height - 1)}" rx="8" '
        f'fill="none" stroke="{_CARD_BORDER}" stroke-width="1" stroke-dasharray="4 4"/>'
        f'<text x="{_fmt(width / 2)}" y="{_fmt(height / 2)}" text-anchor="middle" '
        f'font-size="{_fs(t, 10)}" fill="{t.muted}" '
        f'font-family="{_esc(t.family)}">{_esc(message)}</text>'
    )
    return _frame(width, height, body, t, title, bg=False)


def _nice_ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    """A small set of round-ish gridline values spanning [lo, hi]."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for mult in (1, 2, 2.5, 5, 10):
        step = mult * mag
        if raw <= step:
            break
    start = math.floor(lo / step) * step
    ticks: list[float] = []
    v = start
    while v <= hi + step * 0.5:
        ticks.append(round(v, 6))
        v += step
    return ticks


def _cartesian(
    ctype: str,
    rows: list[dict[str, Any]],
    binding: dict[str, Any],
    props: dict[str, Any],
    width: float,
    height: float,
    label: str,
) -> str:
    """Line / bar / stacked-area on a shared axis frame."""
    x_field = str(binding.get("x_field") or "")
    if ctype == "stacked_area_chart":
        series_fields = [str(f) for f in (binding.get("series_fields") or []) if f]
    else:
        series_fields = _y_fields(binding)
    title = str(props.get("title") or "")
    typo = _typography(props)
    # Unfinished authoring is reported ahead of an empty dataset: with no fields chosen there is
    # nothing to plot whatever the rows hold, and the editor is where that is fixed.
    if not x_field or not series_fields:
        return _placeholder(width, height, title, CHART_NO_FIELDS, typo)
    if not rows:
        return _placeholder(width, height, title, no_rows_message(label), typo)

    palette = _palette(props)
    show_grid = props.get("show_grid", True)
    show_legend = props.get("show_legend", True) and len(series_fields) > 1
    categories = [str(r.get(x_field, "")) for r in rows]
    # series[j] = floats aligned with categories (missing → 0 for area/bar, skipped for line)
    series: list[list[float | None]] = [[to_number(r.get(f)) for r in rows] for f in series_fields]

    # AXIS TITLES. The drawer has offered these two fields all along and nothing read them, so an
    # author typed a label, applied, and it vanished -- worse than an absent control, because the
    # control is a promise. Each takes its own room from the plot rather than overlapping what is
    # there already: the y ticks end at `left - 6` and the x categories sit at `bottom + 12`.
    x_label = str(props.get("x_label") or "")
    y_label = str(props.get("y_label") or "")
    val_fmt, val_dec = _value_format(props)
    # DATA LABELS. Offered for bars and line points only, and that is a judgement rather than an
    # omission: a figure inside a stacked band is unreadable against the fill beneath it, and a pie
    # already states each share in its legend. Printing them anyway would be clutter an author then
    # has no control to remove.
    show_values = bool(props.get("show_values")) and ctype in ("bar_chart", "line_chart")
    top = 30.0 if title else 12.0
    bottom = height - (48.0 if show_legend else 30.0) - (14.0 if x_label else 0.0)
    left = 50.0 + (14.0 if y_label else 0.0)
    right = width - 14.0
    plot_h = max(bottom - top, 10.0)
    plot_w = max(right - left, 10.0)

    # y-range: include 0; for stacked, sum per category.
    if ctype == "stacked_area_chart":
        totals = [sum((s[i] or 0.0) for s in series) for i in range(len(categories))]
        hi = max(totals) if totals else 1.0
    else:
        vals = [v for s in series for v in s if v is not None]
        hi = max(vals) if vals else 1.0
    lo = 0.0
    ticks = _nice_ticks(lo, hi)
    hi = max(ticks) if ticks else hi or 1.0

    def y_of(v: float) -> float:
        return bottom - (v - lo) / (hi - lo) * plot_h if hi > lo else bottom

    grid_ink, axis_ink = _grid_ink(props)
    parts: list[str] = []
    # gridlines + y labels
    for t in ticks:
        y = y_of(t)
        if show_grid:
            parts.append(
                f'<line x1="{_fmt(left)}" y1="{_fmt(y)}" x2="{_fmt(right)}" y2="{_fmt(y)}" '
                f'stroke="{grid_ink}" stroke-width="1"/>'
            )
        parts.append(
            f'<text x="{_fmt(left - 6)}" y="{_fmt(y + 3)}" text-anchor="end" '
            f'font-size="{_fs(typo, 9)}" '
            f'fill="{typo.muted}">{_esc(_value_text(t, val_fmt, val_dec))}</text>'
        )
    # axes
    parts.append(
        f'<line x1="{_fmt(left)}" y1="{_fmt(top)}" x2="{_fmt(left)}" y2="{_fmt(bottom)}" '
        f'stroke="{axis_ink}" stroke-width="1"/>'
        f'<line x1="{_fmt(left)}" y1="{_fmt(bottom)}" x2="{_fmt(right)}" y2="{_fmt(bottom)}" '
        f'stroke="{axis_ink}" stroke-width="1"/>'
    )
    n = len(categories)
    slot = plot_w / max(n, 1)
    # x category labels (thinned if crowded)
    step_lbl = max(1, math.ceil(n / 12))
    for i, cat in enumerate(categories):
        if i % step_lbl:
            continue
        cx = left + slot * (i + 0.5)
        parts.append(
            f'<text x="{_fmt(cx)}" y="{_fmt(bottom + 12)}" text-anchor="middle" '
            f'font-size="{_fs(typo, 9)}" '
            f'fill="{typo.muted}">{_esc(cat[:14])}</text>'
        )

    if x_label:
        parts.append(
            f'<text x="{_fmt((left + right) / 2)}" y="{_fmt(bottom + 28)}" text-anchor="middle" '
            f'font-size="{_fs(typo, 10)}" fill="{typo.text}">{_esc(x_label)}</text>'
        )
    if y_label:
        # Rotated about its own anchor, which is the plot's vertical middle -- so it stays centred
        # whatever the height, where a fixed translate would drift.
        cy = (top + bottom) / 2
        parts.append(
            f'<text x="14" y="{_fmt(cy)}" text-anchor="middle" '
            f'font-size="{_fs(typo, 10)}" fill="{typo.text}" '
            f'transform="rotate(-90 14 {_fmt(cy)})">{_esc(y_label)}</text>'
        )

    if ctype == "bar_chart":
        g = len(series)
        bar_w = min(slot * float(_step(props, "bar_width", _BAR_FILL)) / g, 48.0)
        # Square corners are a real answer: a 2px radius on a 6px-wide bar reads as a rounded blob.
        bar_rx = "0" if str(props.get("bar_corners") or "").strip().lower() == "square" else "2"
        group_w = bar_w * g
        for i in range(n):
            gx = left + slot * (i + 0.5) - group_w / 2
            for j, s in enumerate(series):
                v = s[i] or 0.0
                bh = (v - lo) / (hi - lo) * plot_h if hi > lo else 0.0
                x = gx + bar_w * j
                parts.append(
                    f'<rect x="{_fmt(x)}" y="{_fmt(bottom - bh)}" width="{_fmt(bar_w)}" '
                    f'height="{_fmt(max(bh, 0))}" fill="{palette[j % len(palette)]}" '
                    f'rx="{bar_rx}"/>'
                )
                if show_values and s[i] is not None:
                    # Above the bar, and clamped INSIDE the plot: a tall bar reaching the top tick
                    # would otherwise print its figure over the title.
                    ly = max(bottom - bh - 4.0, top + 8.0)
                    parts.append(
                        f'<text x="{_fmt(x + bar_w / 2)}" y="{_fmt(ly)}" text-anchor="middle" '
                        f'font-size="{_fs(typo, 8)}" fill="{typo.text}">'
                        f"{_esc(_value_text(v, val_fmt, val_dec))}</text>"
                    )
    elif ctype == "stacked_area_chart":
        cum = [0.0] * n
        for j, s in enumerate(series):
            top_pts = []
            bot_pts = []
            for i in range(n):
                cx = left + slot * (i + 0.5)
                base = cum[i]
                cum[i] = base + (s[i] or 0.0)
                top_pts.append((cx, y_of(cum[i])))
                bot_pts.append((cx, y_of(base)))
            pts = top_pts + list(reversed(bot_pts))
            poly = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts)
            parts.append(
                f'<polygon points="{poly}" fill="{palette[j % len(palette)]}" fill-opacity="0.85"/>'
            )
    else:  # line_chart
        lw = to_number(props.get("line_width")) or 1.5
        dash = _LINE_DASH.get(str(props.get("line_style") or "").strip().lower(), "")
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        show_points = _on(props, "show_points")
        # A marker has to clear the line it sits on, or it reads as a thickening of the stroke
        # rather than as a point.
        point_r = max(2.0, lw * 1.5)
        for j, s in enumerate(series):
            pts = [(left + slot * (i + 0.5), y_of(v)) for i, v in enumerate(s) if v is not None]
            if len(pts) < 2:
                # single point → a dot so it isn't invisible
                for x, y in pts:
                    parts.append(
                        f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="2.5" '
                        f'fill="{palette[j % len(palette)]}"/>'
                    )
                continue
            poly = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts)
            colour = palette[j % len(palette)]
            # THE AREA GOES DOWN FIRST, under every line, so a second series is not hidden
            # behind the first one's wash. It closes on the AXIS rather than on the lowest point:
            # a fill floating above the baseline reads as a band, not as "how much".
            if _on(props, "area_fill"):
                floor = " ".join(f"{_fmt(x)},{_fmt(bottom)}" for x, _ in (pts[-1], pts[0]))
                parts.append(
                    f'<polygon points="{poly} {floor}" fill="{colour}" fill-opacity="0.14"/>'
                )
            parts.append(
                f'<polyline points="{poly}" fill="none" stroke="{colour}" '
                f'stroke-width="{_fmt(lw)}" stroke-linejoin="round" '
                f'stroke-linecap="round"{dash_attr}/>'
            )
            if show_points:
                for px, py in pts:
                    parts.append(
                        f'<circle cx="{_fmt(px)}" cy="{_fmt(py)}" r="{_fmt(point_r)}" '
                        f'fill="{colour}"/>'
                    )
            if show_values:
                # ABOVE the point and clamped inside the plot, matching the bars. Thinned by the
                # same rule as the x categories: a figure per point is unreadable at 40 points, and
                # thinning by a different rule would put a label where there is no category.
                for i, pv in enumerate(s):
                    if pv is None or i % step_lbl:
                        continue
                    px = left + slot * (i + 0.5)
                    py = max(y_of(pv) - 6.0, top + 8.0)
                    parts.append(
                        f'<text x="{_fmt(px)}" y="{_fmt(py)}" text-anchor="middle" '
                        f'font-size="{_fs(typo, 8)}" '
                        f'fill="{typo.text}">{_esc(_value_text(pv, val_fmt, val_dec))}</text>'
                    )

    if show_legend:
        parts.append(_legend(series_fields, palette, left, bottom + 22, width - left - 14, typo))

    return _frame(width, height, "".join(parts), typo, title, card_bg=_card_bg(props))


def _legend(
    labels: list[str], palette: list[str], x: float, y: float, max_w: float, t: _Type
) -> str:
    parts: list[str] = []
    cx = x
    for i, label in enumerate(labels):
        if cx > x + max_w - 60 and i:  # wrap-guard: stop before overflow
            break
        parts.append(
            f'<rect x="{_fmt(cx)}" y="{_fmt(y - 8)}" width="10" height="10" rx="2" '
            f'fill="{palette[i % len(palette)]}"/>'
            f'<text x="{_fmt(cx + 14)}" y="{_fmt(y + 1)}" '
            f'font-size="{_fs(t, 9)}" fill="{t.text}">'
            f"{_esc(str(label)[:16])}</text>"
        )
        cx += 24 + len(str(label)[:16]) * 5.4
    return "".join(parts)


def _pie(
    rows: list[dict[str, Any]],
    binding: dict[str, Any],
    props: dict[str, Any],
    width: float,
    height: float,
    donut: bool,
    label: str,
) -> str:
    title = str(props.get("title") or "")
    typo = _typography(props)
    label_field = str(binding.get("label_field") or "")
    value_field = str(binding.get("value_field") or "")
    if not value_field:
        return _placeholder(width, height, title, CHART_NO_FIELDS, typo)
    if not rows:
        return _placeholder(width, height, title, no_rows_message(label), typo)
    slices = [(str(r.get(label_field, "")), to_number(r.get(value_field)) or 0.0) for r in rows]
    slices = [(lbl, v) for lbl, v in slices if v > 0]
    total = sum(v for _, v in slices)
    # Rows that are all zero/blank are nothing to show — the dataset's answer, not a config gap.
    if total <= 0:
        return _placeholder(width, height, title, no_rows_message(label), typo)

    palette = _palette(props)
    show_legend = props.get("show_legend", True)
    top = 24.0 if title else 8.0
    legend_w = 130.0 if show_legend else 0.0
    avail_w = width - legend_w
    r = max(min(avail_w, height - top - 12) / 2 - 6, 10.0)
    cx = avail_w / 2
    cy = top + (height - top) / 2

    parts: list[str] = []
    # A DONUT IS A STROKED RING, not wedges under a cover disc. That disc had to be PAINTED, and
    # `#fff` painted it straight over whatever background the frame carries — `_CARD_BG`'s defect in
    # miniature, and unreachable by any control. Stroking an arc leaves the hole genuinely
    # transparent, and on the default white page the two are indistinguishable.
    hole = r * float(_step(props, "ring_width", _RING_HOLE))
    ring_r = (r + hole) / 2
    ring_w = r - hole
    angle = -math.pi / 2  # start at 12 o'clock
    for i, (_lbl, v) in enumerate(slices):
        frac = v / total
        end = angle + frac * 2 * math.pi
        color = palette[i % len(palette)]
        if frac >= 0.999:  # single full slice → a plain circle (arc path degenerates)
            if donut:
                parts.append(
                    f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(ring_r)}" fill="none" '
                    f'stroke="{color}" stroke-width="{_fmt(ring_w)}"/>'
                )
            else:
                parts.append(
                    f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(r)}" fill="{color}"/>'
                )
        elif donut:
            x0, y0 = cx + ring_r * math.cos(angle), cy + ring_r * math.sin(angle)
            x1, y1 = cx + ring_r * math.cos(end), cy + ring_r * math.sin(end)
            large = 1 if (end - angle) > math.pi else 0
            parts.append(
                f'<path d="M {_fmt(x0)} {_fmt(y0)} '
                f'A {_fmt(ring_r)} {_fmt(ring_r)} 0 {large} 1 {_fmt(x1)} {_fmt(y1)}" '
                f'fill="none" stroke="{color}" stroke-width="{_fmt(ring_w)}"/>'
            )
        else:
            x0, y0 = cx + r * math.cos(angle), cy + r * math.sin(angle)
            x1, y1 = cx + r * math.cos(end), cy + r * math.sin(end)
            large = 1 if (end - angle) > math.pi else 0
            parts.append(
                f'<path d="M {_fmt(cx)} {_fmt(cy)} L {_fmt(x0)} {_fmt(y0)} '
                f'A {_fmt(r)} {_fmt(r)} 0 {large} 1 {_fmt(x1)} {_fmt(y1)} Z" fill="{color}"/>'
            )
        angle = end

    if show_legend:
        ly = top + 6
        for i, (lbl, v) in enumerate(slices):
            pct = v / total * 100
            parts.append(
                f'<rect x="{_fmt(avail_w + 6)}" y="{_fmt(ly - 8)}" width="10" height="10" rx="2" '
                f'fill="{palette[i % len(palette)]}"/>'
                f'<text x="{_fmt(avail_w + 20)}" y="{_fmt(ly + 1)}" '
                f'font-size="{_fs(typo, 9)}" fill="{typo.text}">'
                f"{_esc(str(lbl)[:14])} ({pct:.0f}%)</text>"
            )
            ly += 16
    return _frame(width, height, "".join(parts), typo, title, card_bg=_card_bg(props))


def render_chart_svg(
    comp: dict[str, Any],
    rows: list[dict[str, Any]],
    width: float = 600.0,
    height: float = 340.0,
) -> str:
    """Render a chart component + its resolved rows to a standalone inline SVG string."""
    ctype = str(comp.get("type") or "line_chart")
    props = comp.get("props") or {}
    binding = comp.get("binding") or {}
    # Resolved once here: an empty state names the dataset the reader was expecting, and only the
    # component knows which that is.
    label = dataset_label(comp)
    if ctype in ("pie_chart", "donut_chart"):
        return _pie(rows, binding, props, width, height, ctype == "donut_chart", label)
    return _cartesian(ctype, rows, binding, props, width, height, label)
