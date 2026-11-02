"""Unit tests for the template variable lint (jinja_engine.unknown_variables)."""

from __future__ import annotations

from app.render.jinja_engine import unknown_variables


def test_flags_hand_typed_token_not_in_catalogue() -> None:
    html = "<p>{{ client.name }} — {{ investor_naem }}</p>"
    # client is a known catalogue root; investor_naem is a typo of the form field investor_name.
    assert unknown_variables(html, {"client", "investor_name"}) == ["investor_naem"]


def test_loop_bound_vars_are_not_flagged() -> None:
    # `row` is bound by the {% for %} loop (excluded); only the loop SOURCE is checked.
    html = "{% for row in holdings %}{{ row.value }}{% endfor %}"
    assert unknown_variables(html, {"holdings"}) == []
    assert unknown_variables(html, set()) == ["holdings"]


def test_known_variables_pass() -> None:
    html = "{{ client.name }} {{ adviser.name }} {{ today }}"
    assert unknown_variables(html, {"client", "adviser", "today"}) == []
