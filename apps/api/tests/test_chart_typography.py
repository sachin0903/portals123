"""Chart typography — the font, base size and text colour an author sets in the chart's own
Appearance panel.

It lives there and NOT in the style rail because every word in a chart — the title, the axis ticks,
the legend, the data labels — is drawn by this module inside the SVG. A font set on the wrapper div
reaches none of them, so the rail's Typography sector was a set of controls that changed nothing.

The two things worth pinning are that an untouched chart still draws exactly what it always drew
(these settings are retrofitted onto documents already authored), and that a setting the author DOES
make reaches every word rather than half of them.
"""

from app.render.charts import render_chart_svg

_COMP = {
    "type": "line_chart",
    "props": {"title": "Fund value", "x_label": "Month", "y_label": "Value"},
    "binding": {"x_field": "month", "y_field": ["value"]},
}
_ROWS = [{"month": "Jan", "value": 1200}, {"month": "Feb", "value": 1800}]


def _svg(**props: object) -> str:
    comp = {**_COMP, "props": {**_COMP["props"], **props}}
    return render_chart_svg(comp, _ROWS)


class TestDefaults:
    def test_an_untouched_chart_draws_the_canonical_sizes(self) -> None:
        # The exact numbers this module wrote before typography was configurable. A scaled base that
        # merely rounds to them would move every already-authored chart by a fraction of a point.
        svg = _svg()
        assert 'font-size="13"' in svg  # title
        assert 'font-size="10"' in svg  # axis label
        assert 'font-size="9"' in svg  # tick / legend

    def test_an_untouched_chart_keeps_the_default_font_and_colours(self) -> None:
        svg = _svg()
        assert 'font-family="Arial, sans-serif"' in svg
        assert "#1e293b" in svg  # text
        assert "#94a3b8" in svg  # muted ticks

    def test_a_blank_setting_is_not_a_setting(self) -> None:
        # The editor writes these through blank rather than defaulting at its own seam, so blank has
        # to mean "unset" here or every chart would be re-styled the moment it was re-saved.
        assert _svg(font_family="", font_size="", text_color="") == _svg()

    def test_an_unusable_size_falls_back_rather_than_collapsing_the_chart(self) -> None:
        for bad in ("", "0", "-4", "abc", None):
            assert 'font-size="13"' in _svg(font_size=bad), bad


class TestSettings:
    def test_the_font_reaches_the_root_and_the_title(self) -> None:
        svg = _svg(font_family="Georgia, serif")
        # Once on the SVG root (everything inherits) and once on the title, which sets its own.
        assert svg.count('font-family="Georgia, serif"') == 2
        assert "Arial" not in svg

    def test_one_size_scales_every_other_in_proportion(self) -> None:
        # 9 is the base, so 12 scales the title 13 → 17.3 and the axis label 10 → 13.3.
        svg = _svg(font_size="12")
        assert 'font-size="17.3"' in svg
        assert 'font-size="13.3"' in svg
        assert 'font-size="12"' in svg

    def test_the_text_colour_reaches_the_ticks_too(self) -> None:
        # THE POINT OF THE CONTROL. A chart on a dark panel needs light text on its AXIS as much as
        # in its title; a fixed muted grey would leave half the words unreadable.
        svg = _svg(text_color="#ffffff")
        assert "#94a3b8" not in svg
        assert "#1e293b" not in svg
        assert svg.count("#ffffff") >= 4

    def test_a_font_name_cannot_break_out_of_the_attribute(self) -> None:
        svg = _svg(font_family='Arial" onload="alert(1)')
        assert 'onload="alert(1)"' not in svg
        assert "&quot;" in svg


class TestEmptyState:
    def test_the_empty_state_takes_the_typography_too(self) -> None:
        # It is the same figure in the same footprint, and it carries the chart's title.
        comp = {**_COMP, "props": {**_COMP["props"], "font_family": "Georgia, serif"}}
        svg = render_chart_svg(comp, [])
        assert 'font-family="Georgia, serif"' in svg
        assert "Arial" not in svg


class TestBackground:
    """The chart's own background paint, moved off the wrapper div's style rail."""

    def test_transparent_by_default(self) -> None:
        # `none` is a PAINT, not a colour: the card rect is still drawn, for its stroke.
        assert 'fill="none"' in _svg()

    def test_a_blank_background_is_not_a_background(self) -> None:
        assert _svg(background="") == _svg()

    def test_the_chosen_colour_paints_the_card(self) -> None:
        svg = _svg(background="#fff7ed")
        assert 'fill="#fff7ed"' in svg
        # The border is a separate decision and is unchanged by it.
        assert 'stroke="#e2e8f0"' in svg

    def test_the_empty_state_stays_unpainted(self) -> None:
        # An empty chart is a dashed outline on purpose — a filled slab reads as a broken figure
        # rather than as a deliberate empty state — so a background does not fill it in.
        svg = render_chart_svg({**_COMP, "props": {**_COMP["props"], "background": "#fff7ed"}}, [])
        assert "#fff7ed" not in svg


class TestShape:
    """Per-type shape: how a line is stroked, how wide a bar is, how thick a donut's ring is.

    The rule every one of them follows is that ABSENT MEANS TODAY'S DRAWING — a named step falls
    back to the first entry and a switch falls back to off. These land on documents already in
    clients' hands, and a chart must not gain ink because a feature shipped.
    """

    def test_a_line_is_solid_with_no_points_and_no_fill_by_default(self) -> None:
        svg = _svg()
        assert "stroke-dasharray" not in svg
        assert "<circle" not in svg
        assert "<polygon" not in svg

    def test_line_style_strokes_the_dash(self) -> None:
        assert 'stroke-dasharray="7 5"' in _svg(line_style="dashed")
        assert 'stroke-dasharray="1 4"' in _svg(line_style="dotted")
        assert "stroke-dasharray" not in _svg(line_style="solid")

    def test_an_unknown_step_falls_back_rather_than_failing(self) -> None:
        assert _svg(line_style="squiggly") == _svg()
        assert _svg(bar_width="enormous", line_style="") == _svg()

    def test_points_are_drawn_on_the_line(self) -> None:
        svg = _svg(show_points="true")
        assert svg.count("<circle") == 2  # one per row
        assert "<circle" not in _svg(show_points="false")

    def test_the_area_closes_on_the_axis(self) -> None:
        # A fill floating above the baseline reads as a band rather than as "how much", and it must
        # sit UNDER the stroke or a second series disappears behind the first one's wash.
        svg = _svg(area_fill="true")
        assert 'fill-opacity="0.14"' in svg
        assert svg.index("<polygon") < svg.index("<polyline")

    def test_bar_width_and_corners(self) -> None:
        bars = {"type": "bar_chart", "props": _COMP["props"], "binding": _COMP["binding"]}
        # ENOUGH CATEGORIES THAT THE CAP DOES NOT BIND. A bar is `min(slot * fill, 48)`, so over two
        # rows every setting draws the same 48px bar and the control genuinely has no effect — which
        # is correct (a chart of two bars should not paint half the plot) and would make a two-row
        # fixture assert that the setting does nothing.
        many = [{"month": f"M{i}", "value": 100 + i * 10} for i in range(12)]

        def bar(**props: object) -> str:
            return render_chart_svg({**bars, "props": {**bars["props"], **props}}, many)

        assert 'rx="0"' in bar(bar_corners="square")
        assert 'rx="2"' in bar(bar_corners="rounded")
        assert bar(bar_width="narrow") != bar(bar_width="wide")
        assert bar(bar_width="medium") == bar()

    def test_ring_width_changes_the_hole_and_not_the_radius(self) -> None:
        pie = {
            "type": "donut_chart",
            "props": {},
            "binding": {"label_field": "month", "value_field": "value"},
        }

        def donut(**props: object) -> str:
            return render_chart_svg({**pie, "props": props}, _ROWS)

        assert donut(ring_width="thin") != donut(ring_width="thick")
        assert donut(ring_width="medium") == donut()


class TestGridInk:
    """The gridlines and the axis lines, as one decision."""

    def test_the_default_pair_is_unchanged(self) -> None:
        svg = _svg()
        assert "#eef2f6" in svg  # grid
        assert "#cbd5e1" in svg  # axis

    def test_a_chosen_grid_colour_takes_the_axis_with_it(self) -> None:
        # THE POINT. They are the chart's structural ink: an author who could darken the grid but
        # not the axis would get a pale L drawn around a dark grid.
        svg = _svg(grid_color="#334155")
        assert "#eef2f6" not in svg
        assert "#cbd5e1" not in svg
        assert svg.count("#334155") >= 3

    def test_a_blank_grid_colour_is_not_a_colour(self) -> None:
        assert _svg(grid_color="") == _svg()

    def test_grid_colour_is_independent_of_the_grid_switch(self) -> None:
        # Gridlines off still leaves the axis, and it still takes the colour.
        svg = _svg(grid_color="#334155", show_grid=False)
        assert "#334155" in svg
        assert "#cbd5e1" not in svg
