"""Chart/table render engine (app.render.charts / tables / components / resolve helpers). Pure — no
DB. Covers the fix for charts + tables rendering blank in generated PDFs: markers are substituted
with server-rendered SVG/HTML from the component config + resolved rows."""

from __future__ import annotations

import re

from app.render.charts import _value_text, render_chart_svg
from app.render.components import (
    _size,
    dataset_codes,
    loop_sources,
    substitute_components,
)
from app.render.empty_state import label_for_code
from app.render.resolve import rows_from_static, to_number
from app.render.tables import render_table_html

ROWS = [
    {"product": "Equity", "value": 18500, "units": 1200},
    {"product": "Bonds", "value": 9600, "units": 800},
    {"product": "Cash", "value": 2500, "units": 1},
]


# ── resolve helpers ──────────────────────────────────────────────────────────
def test_to_number_coerces_currency_percent_and_thousands() -> None:
    assert to_number("£20,000") == 20000.0
    assert to_number("5.0%") == 5.0
    assert to_number("1,234.5") == 1234.5
    assert to_number(42) == 42.0
    assert to_number("nope") is None
    assert to_number(None) is None
    assert to_number(True) is None  # booleans are not chart values


def test_rows_from_static_columns_rows_shape() -> None:
    static = {"columns": ["Tax year", "ISA limit"], "rows": [["2024/25", "£20,000"]]}
    assert rows_from_static(static) == [{"Tax year": "2024/25", "ISA limit": "£20,000"}]


def test_rows_from_static_list_of_dicts_shape() -> None:
    assert rows_from_static([{"a": 1}, {"a": 2}]) == [{"a": 1}, {"a": 2}]


def test_rows_from_static_garbage_is_empty() -> None:
    assert rows_from_static(None) == []
    assert rows_from_static("x") == []


# ── charts → SVG ─────────────────────────────────────────────────────────────
def _chart(ctype: str, binding: dict) -> dict:
    return {"id": "c1", "type": ctype, "props": {"title": "Holdings"}, "binding": binding}


def test_bar_chart_renders_bars() -> None:
    svg = render_chart_svg(_chart("bar_chart", {"x_field": "product", "y_field": "value"}), ROWS)
    assert svg.startswith("<svg") and "<rect" in svg and "Holdings" in svg


def test_line_chart_renders_polyline() -> None:
    svg = render_chart_svg(_chart("line_chart", {"x_field": "product", "y_field": "value"}), ROWS)
    assert "<polyline" in svg


def test_stacked_area_renders_polygon() -> None:
    svg = render_chart_svg(
        _chart("stacked_area_chart", {"x_field": "product", "series_fields": ["value"]}), ROWS
    )
    assert "<polygon" in svg


def test_pie_chart_renders_slices() -> None:
    svg = render_chart_svg(
        _chart("pie_chart", {"label_field": "product", "value_field": "value"}), ROWS
    )
    assert "<path" in svg


def test_donut_chart_has_inner_hole() -> None:
    # The hole is a real hole: the ring is STROKED, so the frame's background shows through it. It
    # used to be a white disc painted over the wedges, which is only invisible on a white page.
    svg = render_chart_svg(
        _chart("donut_chart", {"label_field": "product", "value_field": "value"}), ROWS
    )
    assert 'fill="#fff"' not in svg
    assert "stroke-width=" in svg and 'fill="none"' in svg
    # A pie is still filled wedges — the two types must not collapse into one shape.
    pie = render_chart_svg(
        _chart("pie_chart", {"label_field": "product", "value_field": "value"}), ROWS
    )
    assert ' Z" fill=' in pie


def test_chart_without_data_is_a_placeholder_not_empty() -> None:
    svg = render_chart_svg(_chart("bar_chart", {"x_field": "product", "y_field": "value"}), [])
    # Configured but unfed: the dataset's answer, named after the dataset (unnamed here → "data").
    assert svg.startswith("<svg") and "No data to show" in svg


def test_multi_series_bar_uses_distinct_colours() -> None:
    svg = render_chart_svg(
        _chart("bar_chart", {"x_field": "product", "y_field": ["value", "units"]}), ROWS
    )
    assert svg.count("<rect") >= 6  # 3 categories × 2 series


def test_chart_card_is_transparent_by_default() -> None:
    # The background belongs to the FRAME div, which `chart-frame` serialises around this marker
    # with the author's own padding/background/border. A white slab here painted over it, so a chart
    # on a tinted panel came out white and no control anywhere would change it. The hairline and the
    # rounded corners stay: a chart still reads as a framed figure.
    svg = render_chart_svg(_chart("bar_chart", {"x_field": "product", "y_field": "value"}), ROWS)
    assert 'fill="#ffffff"' not in svg
    assert 'rx="8"' in svg and 'stroke="#e2e8f0"' in svg


def test_chart_placeholder_keeps_its_own_panel() -> None:
    # An empty chart draws its own unfilled DASHED frame, so it reads as a deliberate empty state
    # and never as a populated figure that failed to draw. With the populated card transparent too,
    # the dash is the whole of that distinction — and a grey slab is still not it.
    svg = render_chart_svg(_chart("bar_chart", {"x_field": "product", "y_field": "value"}), [])
    assert 'fill="#ffffff"' not in svg
    assert 'stroke-dasharray="4 4"' in svg and 'fill="none"' in svg
    assert 'fill="#fafafa"' not in svg  # no grey slab


def test_chart_empty_state_keeps_the_populated_footprint() -> None:
    # The document's layout must not move when a client happens to have no rows.
    binding = {"x_field": "product", "y_field": "value"}
    full = render_chart_svg(_chart("bar_chart", binding), ROWS, 480.0, 260.0)
    empty = render_chart_svg(_chart("bar_chart", binding), [], 480.0, 260.0)
    no_fields = render_chart_svg(_chart("bar_chart", {}), ROWS, 480.0, 260.0)
    box = 'viewBox="0 0 480 260"'
    assert box in full and box in empty and box in no_fields
    for svg in (full, empty, no_fields):
        assert 'width="100%"' in svg and 'height="100%"' in svg


# ── the three causes of an empty visual ──────────────────────────────────────
def _bound(ctype: str, code: str, binding: dict) -> dict:
    """A chart bound to a dataset the way the editor binds one (``_resolved_data``)."""
    return _chart(ctype, {**binding, "_resolved_data": f"{{{{ {code} }}}}"})


def test_label_drops_the_reserved_prefix_and_underscores() -> None:
    assert label_for_code("book_valuations") == "valuations"
    assert label_for_code("book_contributions") == "contributions"
    assert label_for_code("client_holdings") == "client holdings"
    assert label_for_code("") == "data"  # nothing nameable still reads as a sentence


def test_empty_dataset_is_named_in_the_message() -> None:
    svg = render_chart_svg(
        _bound("bar_chart", "book_valuations", {"x_field": "d", "y_field": "v"}), []
    )
    assert "No valuations to show" in svg


def test_chart_with_no_fields_selected_says_so() -> None:
    # Cause 2 is an authoring omission and is reported even with rows in hand — there is nothing
    # to plot whatever the dataset holds, and the editor is where it's fixed.
    comp = _bound("bar_chart", "book_valuations", {})
    assert "This chart has no fields selected" in render_chart_svg(comp, ROWS)
    assert "This chart has no fields selected" in render_chart_svg(comp, [])
    # ...and an x_field alone is still not a series.
    half = _bound("line_chart", "book_valuations", {"x_field": "product"})
    assert "This chart has no fields selected" in render_chart_svg(half, ROWS)


def test_the_three_causes_produce_three_messages() -> None:
    binding = {"x_field": "product", "y_field": "value"}
    no_rows = render_chart_svg(_bound("bar_chart", "book_contributions", binding), [])
    no_fields = render_chart_svg(_bound("bar_chart", "book_contributions", {}), ROWS)
    # Cause 3 (an unresolvable dataset) is a blocking problem reported upstream, so a chart that
    # simply received no rows must not claim anything about resolution.
    assert no_rows != no_fields
    assert "No contributions to show" in no_rows
    assert "This chart has no fields selected" in no_fields
    for svg in (no_rows, no_fields):
        assert "unknown" not in svg.lower() and "could not" not in svg.lower()


def test_pie_distinguishes_no_value_field_from_no_rows() -> None:
    with_field = {"label_field": "product", "value_field": "value"}
    assert "No valuations to show" in render_chart_svg(
        _bound("pie_chart", "book_valuations", with_field), []
    )
    assert "This chart has no fields selected" in render_chart_svg(
        _bound("donut_chart", "book_valuations", {"label_field": "product"}), ROWS
    )
    # All-zero rows are the dataset's answer, not a config gap.
    zeroed = [{"product": "Equity", "value": 0}]
    assert "No valuations to show" in render_chart_svg(
        _bound("pie_chart", "book_valuations", with_field), zeroed
    )


# ── tables → HTML ────────────────────────────────────────────────────────────
def _table(props: dict) -> dict:
    return {"id": "t1", "type": "data_table", "props": props, "binding": {}}


def test_table_renders_headers_and_rows() -> None:
    html = render_table_html(_table({"columns": "product,value", "headers": "Product,Value"}), ROWS)
    assert "<table" in html and "<th" in html
    assert "Product" in html and "Value" in html and "Equity" in html and "18500" in html


def test_table_infers_columns_when_unspecified() -> None:
    html = render_table_html(_table({}), ROWS)
    assert "Product" in html  # titleised from the "product" key
    assert "Equity" in html


def test_table_without_rows_shows_no_data() -> None:
    html = render_table_html(_table({"columns": "product,value"}), [])
    assert "No data to show" in html
    # The header still reads as a table, and the message is one spanning row beneath it.
    assert "<th" in html and 'colspan="2"' in html


def test_table_names_its_dataset_when_empty() -> None:
    comp = {
        "id": "t9",
        "type": "data_table",
        "props": {"columns": "date,amount"},
        "binding": {"_resolved_data": "{{ book_contributions }}"},
    }
    html = render_table_html(comp, [])
    assert "No contributions to show" in html and 'colspan="2"' in html


def test_table_with_no_columns_says_so() -> None:
    # Nothing to infer columns from and none authored: the reader sees a bare box, so the
    # authoring omission is both what they're looking at and the thing to fix.
    html = render_table_html(_table({}), [])
    assert "This table has no columns" in html and 'colspan="1"' in html
    assert "to show" not in html  # not the empty-dataset words


def test_table_escapes_cell_values() -> None:
    html = render_table_html(_table({"columns": "product"}), [{"product": "<script>"}])
    assert "<script>" not in html and "&lt;script&gt;" in html


# ── marker substitution ──────────────────────────────────────────────────────
CHART_COMP = {
    "id": "c1",
    "type": "bar_chart",
    "props": {"title": "Holdings"},
    "binding": {
        "_resolved_data": "{{ client_holdings }}",
        "x_field": "product",
        "y_field": "value",
    },
}
TABLE_COMP = {
    "id": "t1",
    "type": "data_table",
    "props": {"columns": "product,value", "headers": "Product,Value"},
    "binding": {"_resolved_data": "{{ client_holdings }}"},
}
ROWS_BY_CODE = {"client_holdings": ROWS}


def test_dataset_codes_parses_bindings() -> None:
    assert dataset_codes([CHART_COMP, TABLE_COMP]) == ["client_holdings"]


def test_loop_sources_extracts_bareword_for_loops() -> None:
    # The doc-jtable shape: a {% for row in <code> %} loop over a dataset.
    html = (
        "<table>{% for row in client_holdings %}<tr><td>{{ row.product }}</td></tr>"
        "{% endfor %}</table>"
        "<ul>{% for r in projections %}<li>{{ r.year }}</li>{% endfor %}</ul>"
        # A nested path is NOT a dataset code — must be excluded.
        "<div>{% for a in answers.items %}{{ a }}{% endfor %}</div>"
        # A duplicate collapses.
        "<p>{% for row in client_holdings %}dup{% endfor %}</p>"
    )
    assert loop_sources(html) == ["client_holdings", "projections"]


def test_loop_sources_handles_whitespace_control_and_empty() -> None:
    assert loop_sources("{%- for x in holdings -%}{{ x }}{%- endfor -%}") == ["holdings"]
    assert loop_sources(None) == []
    assert loop_sources("<p>no loops here</p>") == []


def test_loop_sources_handles_filters_and_tuple_targets() -> None:
    # A trailing `if` guard: the source is still captured (Jinja reports it as an undeclared root,
    # so the lint must know it) — the guard doesn't make it a nested path.
    assert loop_sources("{% for h in holdings if h.active %}{{ h }}{% endfor %}") == ["holdings"]
    # A pipe filter on the iterable.
    assert loop_sources("{% for r in rows | sort %}{{ r }}{% endfor %}") == ["rows"]
    # Tuple unpacking target.
    assert loop_sources("{% for k, v in pairs %}{{ k }}{% endfor %}") == ["pairs"]
    # Still excludes dotted / subscripted sources (not dataset codes).
    assert loop_sources("{% for a in answers.items if a %}{{ a }}{% endfor %}") == []
    assert loop_sources("{% for x in data['k'] %}{{ x }}{% endfor %}") == []


def test_substitutes_chart_marker() -> None:
    html = (
        '<div style="height:300px;overflow:hidden;">'
        '<g-chart data-id="c1" data-height="300px" /></div>'
    )
    out = substitute_components(html, [CHART_COMP], ROWS_BY_CODE)
    assert "<g-chart" not in out and "<svg" in out and "<rect" in out
    assert "<div" in out  # the wrapping div is preserved


def test_substitutes_table_marker() -> None:
    html = '<g-table data-id="t1" data-source="client_holdings" />'
    out = substitute_components(html, [TABLE_COMP], ROWS_BY_CODE)
    assert "<g-table" not in out and "<table" in out and "Equity" in out


def test_table_marker_preserves_following_content_when_dom_nested() -> None:
    # A save-time DOM round-trip can reserialise <g-table/> as an open tag that swallows following
    # siblings; substitution must keep them, in order, not delete them.
    html = '<g-table data-id="t1"><p>after the table</p></g-table>'
    out = substitute_components(html, [TABLE_COMP], ROWS_BY_CODE)
    assert "<table" in out and "<p>after the table</p>" in out
    assert "<g-table" not in out and "</g-table>" not in out
    assert out.index("<table") < out.index("after the table")  # table before the trailing text


def test_unknown_marker_is_dropped() -> None:
    html = '<g-chart data-id="missing" />after'
    out = substitute_components(html, [CHART_COMP], ROWS_BY_CODE)
    assert "<g-chart" not in out and out.endswith("after")


def test_marker_with_no_rows_still_replaces_with_placeholder() -> None:
    html = '<div><g-chart data-id="c1" data-height="300px" /></div>'
    out = substitute_components(html, [CHART_COMP], {})  # no resolved data
    assert "<g-chart" not in out and "<svg" in out


def test_static_text_table_passes_through_untouched() -> None:
    # A Text table (doc-texttable) serialises to a plain <table class="doc-jtable-inner"> with no
    # <g-*> marker and no {% for %} loop, so the render pipeline leaves it exactly as authored —
    # WeasyPrint styles it from the shipped DOC_JTABLE_CSS.
    html = (
        '<div data-gjs-type="doc-texttable">'
        '<table class="doc-jtable-inner"><thead><tr><th>Fee</th><th>Amount</th></tr></thead>'
        "<tbody><tr><td>Initial</td><td>1.5%</td></tr></tbody></table></div>"
    )
    out = substitute_components(html, [], {})
    assert out == html  # untouched: no markers to substitute


# ── manual (inline) component data (#7) ──────────────────────────────────────
INLINE_CHART = {
    "id": "m1",
    "type": "bar_chart",
    "props": {"title": "Manual"},
    "binding": {
        "_inline_rows": {
            "columns": ["product", "value"],
            "rows": [["Alpha", 12000], ["Beta", 8000]],
        },
        "x_field": "product",
        "y_field": "value",
    },
}
INLINE_TABLE = {
    "id": "m2",
    "type": "data_table",
    "props": {"columns": "product,value", "headers": "Product,Value"},
    "binding": {"_inline_rows": [{"product": "Alpha", "value": 12000}]},
}


def test_manual_chart_renders_from_inline_rows_without_a_dataset() -> None:
    # source-mode = manual: rows live on the component (no dataset resolved at all).
    out = substitute_components('<g-chart data-id="m1" data-height="300px" />', [INLINE_CHART], {})
    assert "<g-chart" not in out and "<rect" in out  # bars drawn from the inline rows


def test_manual_table_renders_from_inline_rows() -> None:
    out = substitute_components('<g-table data-id="m2" />', [INLINE_TABLE], {})
    assert "<table" in out and "Alpha" in out and "12000" in out


def test_inline_rows_win_over_a_dataset_binding() -> None:
    # A component carrying BOTH a dataset code and inline rows uses the inline rows (manual wins).
    comp = {
        "id": "m3",
        "type": "data_table",
        "props": {"columns": "product,value"},
        "binding": {
            "_resolved_data": "{{ client_holdings }}",
            "_inline_rows": [{"product": "OnlyManual", "value": 1}],
        },
    }
    out = substitute_components('<g-table data-id="m3" />', [comp], ROWS_BY_CODE)
    assert "OnlyManual" in out and "Equity" not in out


def test_chart_is_laid_out_at_its_box_and_never_stretched() -> None:
    # The box is the LAYOUT, not a scale factor: a taller box is a longer y axis, so text keeps its
    # size and a pie stays round. `preserveAspectRatio="none"` was tried and is the wrong answer --
    # it removes the letterbox bands by distorting the drawing. Those bands were caused by the
    # marker sending no width, so the drawing was always the canonical 600 and never fit the box.
    svg = render_chart_svg(_chart("bar_chart", {"x_field": "product", "y_field": "value"}), ROWS)
    assert 'preserveAspectRatio="xMidYMid meet"' in svg
    assert 'preserveAspectRatio="none"' not in svg


def test_a_taller_box_lengthens_the_axis_rather_than_scaling_the_picture() -> None:
    # The proof that the box IS the layout: the same rows in a taller box put the baseline lower and
    # the gridlines further apart. A scaled picture would be the same drawing in a bigger frame.
    binding = {"x_field": "product", "y_field": "value"}
    short = render_chart_svg(_chart("bar_chart", binding), ROWS, 600.0, 200.0)
    tall = render_chart_svg(_chart("bar_chart", binding), ROWS, 600.0, 600.0)
    assert 'viewBox="0 0 600 200"' in short
    assert 'viewBox="0 0 600 600"' in tall
    # Same width, so the bars keep their x positions -- only the vertical scale changed. If the
    # drawing were being scaled instead, every coordinate would move.
    bar_x = re.compile(r'<rect x="([0-9.]+)" y="[0-9.]+" width="[0-9.]+" height="[0-9.]+" fill="#')
    xs_short, xs_tall = bar_x.findall(short), bar_x.findall(tall)
    assert xs_short and xs_short == xs_tall, (
        "a taller box moved the bars sideways -- the drawing is being scaled, not laid out"
    )

    # And the baseline really did move: the tallest bar is taller in the taller box.
    def heights(svg: str) -> list[float]:
        pat = r'<rect x="[0-9.]+" y="[0-9.]+" width="[0-9.]+" height="([0-9.]+)"'
        return [float(m) for m in re.findall(pat, svg)]

    assert max(heights(tall)) > max(heights(short))


def test_chart_draws_at_the_exported_box() -> None:
    # With a stretching viewBox the box IS the drawing space: equal numbers mean a stretch of 1 and
    # therefore no distortion, which is what makes exporting `data-width` the fix rather than a
    # tidy-up. Both dimensions must come through.
    svg = render_chart_svg(
        _chart("bar_chart", {"x_field": "product", "y_field": "value"}), ROWS, 900.0, 200.0
    )
    assert 'viewBox="0 0 900 200"' in svg


class TestChartSizeParsing:
    """`_size` reads CSS px and refuses anything else.

    The old reader stripped every non-digit, so `100%` arrived as 100. Harmless while the SVG
    letterboxed; with a stretching viewBox a 100-unit drawing across a 600px box magnifies every
    glyph six times, so an unresolvable unit must fall back rather than be salvaged.
    """

    def test_reads_px_with_or_without_the_unit(self) -> None:
        assert _size("620px", 600.0) == 620.0
        assert _size("620", 600.0) == 620.0
        assert _size(" 300 px ", 600.0) == 300.0

    def test_refuses_a_unit_it_cannot_resolve(self) -> None:
        # There is no CSS engine here, so a percentage has nothing to be a percentage OF.
        for value in ("100%", "12.5rem", "50vw", "auto", "", "inherit"):
            assert _size(value, 600.0) == 600.0, value

    def test_refuses_a_box_that_is_not_a_box(self) -> None:
        # Zero would divide; a negative is not a dimension.
        assert _size("0px", 600.0) == 600.0
        assert _size("-5px", 600.0) == 600.0


def test_the_exported_box_reaches_the_viewbox() -> None:
    # End to end through the substitution: the marker's px box is what the drawing is laid out at,
    # which is what makes the stretch a no-op in the ordinary case.
    html = substitute_components(
        '<g-chart data-id="c1" data-width="900px" data-height="200px" />',
        [CHART_COMP],
        ROWS_BY_CODE,
    )
    assert 'viewBox="0 0 900 200"' in html


def test_a_percentage_width_does_not_shrink_the_drawing() -> None:
    # The failure `_size` exists to stop: `%` read as digits would emit `viewBox="0 0 100 …"` and,
    # stretched across the page, magnify every glyph six times.
    html = substitute_components(
        '<g-chart data-id="c1" data-width="100%" data-height="240px" />',
        [CHART_COMP],
        ROWS_BY_CODE,
    )
    assert 'viewBox="0 0 600 240"' in html
    assert 'viewBox="0 0 100 240"' not in html


def test_axis_labels_are_drawn() -> None:
    # The drawer has offered X LABEL and Y LABEL all along, `serialize.ts` has stored them all
    # along, and nothing drew them: an author typed a label, applied, and it vanished. Worse than
    # an absent control, because a control is a promise.
    svg = render_chart_svg(
        {
            "id": "c1",
            "type": "bar_chart",
            "props": {"x_label": "Year", "y_label": "Fund Value"},
            "binding": {"x_field": "product", "y_field": "value"},
        },
        ROWS,
    )
    assert "Year" in svg and "Fund Value" in svg
    # The y title is rotated about its own anchor, so it stays centred at any height.
    assert 'transform="rotate(-90' in svg


def test_axis_labels_take_their_own_room() -> None:
    # Each label must claim space from the plot rather than overprint what is already there: the y
    # ticks end at `left - 6` and the x categories sit at `bottom + 12`.
    binding = {"x_field": "product", "y_field": "value"}
    bare = render_chart_svg(
        {"id": "c", "type": "bar_chart", "props": {}, "binding": binding}, ROWS, 480.0, 260.0
    )
    labelled = render_chart_svg(
        {
            "id": "c",
            "type": "bar_chart",
            "props": {"x_label": "Year", "y_label": "Value"},
            "binding": binding,
        },
        ROWS,
        480.0,
        260.0,
    )
    # Same footprint — a label must not change the figure's size in the document.
    box = 'viewBox="0 0 480 260"'
    assert box in bare and box in labelled
    # But a different plot, because the axes moved in to make room.
    assert bare != labelled


class TestValueFormat:
    """How a figure reads. A financial document's y axis is money far more often than a bare count,
    and there was no way to say so — every chart printed `40,000` where the firm meant `£40,000`."""

    def test_the_three_formats(self) -> None:
        assert _value_text(1234.5) == "1,234.5"
        assert _value_text(1234.5, "currency", 0) == "£1,234"
        assert _value_text(12.345, "percent", 1) == "12.3%"

    def test_automatic_decimals_are_not_zero_decimals(self) -> None:
        # Blank means AUTOMATIC: an integer prints bare, anything else to at most 2dp. Conflating it
        # with 0 would silently round every fractional figure on every existing chart.
        assert _value_text(1234.5, "number", None) == "1,234.5"
        assert _value_text(1234.5, "number", 0) == "1,234"
        # And 0 is a real answer, so it must be honoured rather than read as unset.
        assert _value_text(1200.0, "currency", 0) == "£1,200"

    def test_an_unknown_format_falls_back_rather_than_reaching_a_format_string(self) -> None:
        svg = render_chart_svg(
            {
                "id": "c",
                "type": "bar_chart",
                "props": {"value_format": "klingon", "value_decimals": "nonsense"},
                "binding": {"x_field": "product", "y_field": "value"},
            },
            ROWS,
        )
        assert "klingon" not in svg and "nonsense" not in svg

    def test_the_ticks_carry_the_format(self) -> None:
        svg = render_chart_svg(
            {
                "id": "c",
                "type": "bar_chart",
                "props": {"value_format": "currency", "value_decimals": 0},
                "binding": {"x_field": "product", "y_field": "value"},
            },
            ROWS,
        )
        assert "£" in svg


class TestValueLabels:
    """Figures printed on the chart itself."""

    def _svg(self, ctype: str, **props: object) -> str:
        return render_chart_svg(
            {
                "id": "c",
                "type": ctype,
                "props": props,
                "binding": {
                    "x_field": "product",
                    "y_field": "value",
                    "label_field": "product",
                    "value_field": "value",
                },
            },
            ROWS,
        )

    def test_off_by_default(self) -> None:
        # A chart that has never been configured must not suddenly grow figures on it.
        assert self._svg("bar_chart") == self._svg("bar_chart", show_values=False)

    def test_bars_and_lines_print_them(self) -> None:
        for ctype in ("bar_chart", "line_chart"):
            with_labels = self._svg(ctype, show_values=True)
            assert with_labels != self._svg(ctype), ctype
            # One per row, on top of the ticks that were already there.
            assert with_labels.count("18,500") >= 1, ctype

    def test_a_stacked_area_and_a_pie_do_not(self) -> None:
        # Judgement, not omission: a figure inside a stacked band is unreadable against the fill
        # beneath it, and a pie already states each share in its legend. Printing them anyway would
        # be clutter with no control to remove it.
        for ctype in ("stacked_area_chart", "pie_chart", "donut_chart"):
            assert self._svg(ctype, show_values=True) == self._svg(ctype), ctype


class TestColumnLayout:
    """Per-column width and alignment. A table could only be as wide as its box and shared that
    width by content, so a reference column took as much room as a description and a column of
    figures could not be lined up on its digits."""

    def _table(self, **props: object) -> str:
        base = {"columns": "a,b,c", "headers": "A,B,C"}
        return render_table_html(
            {"id": "t", "type": "data_table", "props": {**base, **props}, "binding": {}},
            [{"a": "x", "b": "1", "c": "2"}],
        )

    def test_widths_become_a_colgroup(self) -> None:
        # One declaration per column, which applies to the body cells too and survives a table
        # whose header is not rendered.
        html = self._table(widths="50,,25")
        assert "<colgroup>" in html
        assert "width:50%" in html and "width:25%" in html
        assert "<col/>" in html  # the blank one stays automatic

    def test_a_width_only_takes_effect_with_a_fixed_layout(self) -> None:
        # THE REASON A WIDTH USED TO DO NOTHING: under `auto` the engine treats a declared width as
        # a suggestion and widens the column to its longest cell anyway.
        assert "table-layout:fixed" in self._table(widths="50")
        # But with none set, `auto` sizes columns to content and must not be overridden.
        bare = self._table()
        assert "table-layout:fixed" not in bare and "<colgroup>" not in bare

    def test_widths_are_clamped_and_junk_falls_back(self) -> None:
        html = self._table(widths="500,-3,abc")
        assert "width:100%" in html  # clamped down
        # A negative and a non-number are automatic, not a broken declaration.
        assert "width:-3" not in html and "abc" not in html

    def test_widths_are_never_rescaled_to_total_a_hundred(self) -> None:
        # An author's numbers are their numbers; silently normalising makes the control
        # unpredictable and there is no honest way to show what it did.
        html = self._table(widths="10,10,10")
        assert html.count("width:10%") == 3

    def test_alignment_reaches_the_header_and_the_body(self) -> None:
        html = self._table(aligns="left,right,center")
        assert html.count("text-align:right") >= 2  # the <th> and its <td>
        assert "text-align:center" in html

    def test_an_unknown_alignment_leaves_the_column_alone(self) -> None:
        html = self._table(aligns="sideways,right")
        assert "sideways" not in html
        assert "text-align:left" in html  # the default, not the junk
