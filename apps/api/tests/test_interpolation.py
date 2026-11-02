"""The ``{{ key || fallback }}`` grammar — pure, so tested exhaustively and hostilely.

This module renders strings that end up in a client's inbox, so the tests are weighted towards
the ways that could go wrong rather than the ways it is meant to be used: values that look like
templates, values that look like HTML, values that look like regex replacement syntax, and keys
that do not exist.
"""

from __future__ import annotations

import html

import pytest

from app.flows import interpolation

# ---- The grammar ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("template", "context", "expected"),
    [
        ("Hello {{ name }}", {"name": "Ada"}, "Hello Ada"),
        ("{{name}}", {"name": "Ada"}, "Ada"),  # no inner spaces
        ("{{   name   }}", {"name": "Ada"}, "Ada"),  # generous inner spaces
        ("{{ a }} and {{ b }}", {"a": "1", "b": "2"}, "1 and 2"),
        ("no tokens at all", {}, "no tokens at all"),
        ("", {}, ""),
        ("{{ contact.name }}", {"contact.name": "Ada"}, "Ada"),
        ("{{ event.subject.amount }}", {"event.subject.amount": 500}, "500"),
        ("{{ contact.name }}", {"contact": {"name": "Ada"}}, "Ada"),
        ("{{ a.b.c }}", {"a": {"b": {"c": "deep"}}}, "deep"),
        # -- a flat key WINS over a nested one, because the consumer builds flat -----------------
        ("{{ a.b }}", {"a.b": "flat", "a": {"b": "nested"}}, "flat"),
        ("Hi {{ name || there }}", {}, "Hi there"),
        ("Hi {{ name || there }}", {"name": None}, "Hi there"),
        ("Hi {{ name || there }}", {"name": ""}, "Hi there"),
        ("Hi {{ name || there }}", {"name": "   "}, "Hi there"),
        ("Hi {{ name || there }}", {"name": "Ada"}, "Hi Ada"),
        ("{{ a||b }}", {}, "b"),  # no spaces around the bars
        ("{{ a ||  b  }}", {}, "b"),  # a bare fallback is trimmed
        ('{{ a || "  " }}', {}, "  "),  # a quoted one keeps its spaces
        ("{{ a || 'x' }}", {}, "x"),
        ("{{ a || }}", {}, ""),  # an empty fallback is a deliberate blank
        ("{{ a || some longer phrase }}", {}, "some longer phrase"),
        ("Hi {{ nope }}!", {}, "Hi !"),
        ("{{ n || fallback }}", {"n": 0}, "0"),
        ("{{ n || fallback }}", {"n": False}, "No"),
        ("{{ n || fallback }}", {"n": True}, "Yes"),
        ("{{ n }}", {"n": 42}, "42"),
        ("{{ n }}", {"n": 3.5}, "3.5"),
        ("{{ n }}", {"n": ["a", "b"]}, "['a', 'b']"),
        ("{ name }", {"name": "Ada"}, "{ name }"),
        ("{{ }}", {}, "{{ }}"),  # no key at all is not a token
        ("{{ 9lives }}", {"9lives": "x"}, "{{ 9lives }}"),  # keys cannot start with a digit
        ("{{ a-b }}", {"a-b": "x"}, "{{ a-b }}"),  # hyphens are not part of the grammar
    ],
)
def test_grammar(template: str, context: dict[str, object], expected: str) -> None:
    assert interpolation.render(template, context) == expected


def test_a_walk_into_a_non_mapping_yields_nothing_rather_than_raising() -> None:
    """Attribute access is deliberately unsupported — reaching into arbitrary objects from
    author-supplied text is the sandbox escape this grammar exists not to have."""
    assert interpolation.render("{{ a.b }}", {"a": "a string, not a mapping"}) == ""
    assert interpolation.render("{{ a.b.c }}", {"a": {"b": 5}}) == ""


# ---- Hostile values — the three safety properties -------------------------------------------


def test_a_value_that_looks_like_a_template_is_not_re_expanded() -> None:
    """Property 1. A client whose surname is `{{ secret }}` must not become a way to read
    `secret` — substitution is one pass and inserted values are never re-scanned."""
    context = {"name": "{{ secret }}", "secret": "the crown jewels"}
    assert interpolation.render("Hi {{ name }}", context) == "Hi {{ secret }}"


def test_a_value_containing_regex_replacement_syntax_is_literal() -> None:
    """`re.sub` with a string replacement would expand `\\1` and `\\g<0>`; with a function it does
    not. This pins that we use the function form."""
    for hostile in ("\\1", "\\g<0>", "\\\\", "$0", "\\n"):
        assert interpolation.render("{{ v }}", {"v": hostile}) == hostile


def test_a_fallback_is_not_re_expanded_either() -> None:
    assert interpolation.render("{{ a || {{ b }} }}", {"b": "nested"}) == "{{ b }}"


def test_values_are_not_escaped_unless_the_sink_asks() -> None:
    """Property 2. Plain-text sinks (a task title) get the raw value..."""
    assert interpolation.render("{{ v }}", {"v": "<b>&</b>"}) == "<b>&</b>"


def test_an_html_sink_escapes_values_but_never_the_template() -> None:
    """...and an HTML sink escapes the VALUES only. The template literal is the firm's own
    published markup; escaping it would render the author's tags as visible text."""
    out = interpolation.render(
        "<p>Dear {{ name }}</p>", {"name": "<script>alert(1)</script>"}, escape=html.escape
    )
    assert out == "<p>Dear &lt;script&gt;alert(1)&lt;/script&gt;</p>"
    assert "<script>" not in out
    assert out.startswith("<p>")  # the author's own markup survived


def test_an_html_sink_escapes_a_fallback_too() -> None:
    out = interpolation.render("<p>{{ x || <i>none</i> }}</p>", {}, escape=html.escape)
    assert out == "<p>&lt;i&gt;none&lt;/i&gt;</p>"


def test_quotes_in_a_value_cannot_break_out_of_an_attribute() -> None:
    out = interpolation.render(
        '<a title="{{ v }}">x</a>', {"v": '" onmouseover="evil()'}, escape=html.escape
    )
    assert 'onmouseover="evil()"' not in out
    assert "&quot;" in out


# ---- Caps -----------------------------------------------------------------------------------


def test_one_runaway_value_is_capped_and_the_rest_of_the_string_survives() -> None:
    out = interpolation.render("{{ big }}|{{ small }}", {"big": "x" * 5000, "small": "end"})
    assert out.endswith("|end")  # the later token was not pushed out
    assert len(out) < 5000


def test_the_whole_rendered_string_is_capped() -> None:
    template = "{{ v }}" * 20
    out = interpolation.render(template, {"v": "y" * interpolation.MAX_VALUE})
    assert len(out) <= interpolation.MAX_RENDERED + 1  # +1 for the ellipsis


def test_the_cap_never_produces_a_half_escaped_entity() -> None:
    """Truncation happens BEFORE escaping, so an escaped entity can never be sliced in half."""
    out = interpolation.render(
        "{{ v }}", {"v": "&" * (interpolation.MAX_VALUE + 50)}, escape=html.escape
    )
    assert "&am" not in out.replace("&amp;", "")


# ---- Diagnostics ----------------------------------------------------------------------------


def test_missing_reports_only_keys_with_no_fallback() -> None:
    """A fallback is the author saying "I know this may be blank" — counting it as missing would
    make the diagnostic noise nobody reads."""
    out, missing = interpolation.render_with_missing("{{ a }} {{ b || ok }} {{ c }}", {"a": "A"})
    assert out == "A ok "
    assert missing == ["c"]


def test_missing_reports_a_present_but_blank_value() -> None:
    _, missing = interpolation.render_with_missing("{{ a }}", {"a": "   "})
    assert missing == ["a"]


def test_missing_does_not_report_zero() -> None:
    _, missing = interpolation.render_with_missing("{{ a }}", {"a": 0})
    assert missing == []


def test_keys_lists_references_in_order_without_duplicates() -> None:
    assert interpolation.keys("{{ b }} {{ a || x }} {{ b }}") == ["b", "a"]
    assert interpolation.keys("nothing here") == []


def test_render_never_raises_on_any_context_shape() -> None:
    """The worker's hot path. A template that 500s the executor fails a firm's automation with a
    stack trace instead of a reason, so the total-function property is worth pinning."""
    hostile: list[dict[str, object]] = [
        {},
        {"a": None},
        {"a": object()},
        {"a": {"b": object()}},
        {"a": [1, 2, 3]},
        {"a": {"b": None}},
    ]
    for context in hostile:
        for template in ("{{ a }}", "{{ a.b }}", "{{ a.b.c || z }}", "{{ }}", "{{"):
            interpolation.render(template, context)


# ---- to_jinja — the bridge to the template engine -------------------------------------------


def test_to_jinja_translates_a_fallback_into_the_engines_own() -> None:
    """``send_email`` renders email templates with the template engine now, because the email
    EDITOR writes Jinja and this grammar passed it through as literal braces into a client's inbox.
    ``{{ x || y }}`` is a Jinja syntax error, so the two are translated rather than one being
    declared the loser."""
    assert (
        interpolation.to_jinja("<p>Hi {{ contact.first_name || there }}</p>")
        == "<p>Hi {{ contact.first_name | fallback('there') }}</p>"
    )


def test_to_jinja_leaves_everything_that_is_already_jinja_alone() -> None:
    assert interpolation.to_jinja("{{ contact.name }}") == "{{ contact.name }}"
    # A filter is one pipe, not two, so `_TOKEN` cannot match it: a translation, not a rewrite.
    assert interpolation.to_jinja("{{ amount | currency }}") == "{{ amount | currency }}"
    assert interpolation.to_jinja("{{ x | fallback('a') }}") == "{{ x | fallback('a') }}"
    assert interpolation.to_jinja("{% if x %}a{% else %}b{% endif %}") == (
        "{% if x %}a{% else %}b{% endif %}"
    )
    assert interpolation.to_jinja("plain copy") == "plain copy"


def test_to_jinja_escapes_a_fallback_that_would_break_the_literal() -> None:
    """The failure this prevents reaches a client: an unescaped apostrophe closes the Jinja string
    early and the whole template stops rendering. "your adviser's team" is the obvious case."""
    assert interpolation.to_jinja("{{ x || it's fine }}") == r"{{ x | fallback('it\'s fine') }}"
    # Backslash first, or the escape added for the quote is itself re-escaped.
    assert interpolation.to_jinja(r"{{ x || a\b }}") == r"{{ x | fallback('a\\b') }}"


def test_to_jinja_keeps_a_quoted_fallbacks_spaces_and_drops_its_quotes() -> None:
    """The quotes are the grammar's own way of protecting leading/trailing spaces, so they are not
    part of the text and must not survive into the Jinja literal."""
    assert interpolation.to_jinja('{{ x || "  padded  " }}') == "{{ x | fallback('  padded  ') }}"


def test_to_jinja_output_renders_through_the_engine_with_the_same_emptiness_rule() -> None:
    """The two fallbacks were written independently and have to agree, or moving this path to the
    engine would change what a firm's existing templates say. The case that matters is ``0``:
    "0 outstanding items" is a sentence a firm means to send, so it is a VALUE, not an absence."""
    from app.render.jinja_engine import render

    assert render(interpolation.to_jinja("{{ n || nothing }}"), {"n": 0}) == "0"
    assert render(interpolation.to_jinja("{{ n || nothing }}"), {"n": None}) == "nothing"
    assert render(interpolation.to_jinja("{{ n || nothing }}"), {"n": "  "}) == "nothing"
    assert render(interpolation.to_jinja("{{ n || nothing }}"), {}) == "nothing"
    assert render(interpolation.to_jinja("{{ n || nothing }}"), {"n": "real"}) == "real"


def test_to_jinja_never_raises_on_the_shapes_render_survives() -> None:
    for template in ("{{ a }}", "{{ a.b || x }}", "{{ }}", "{{", "{{ a ||  }}"):
        interpolation.to_jinja(template)
