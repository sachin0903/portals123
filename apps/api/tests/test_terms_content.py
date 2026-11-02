"""Inline declaration copy becomes the HTML the client reads — never a script."""

from app.flows.terms_content import declaration_html


def test_markdown_becomes_readable_html() -> None:
    html = declaration_html(
        "# Title\n\nI **agree** to the *terms*.\n\n- First\n- Second\n\n"
        "See [the policy](https://example.test/policy)."
    )
    assert "<h1>Title</h1>" in html
    assert "<strong>agree</strong>" in html
    assert "<em>terms</em>" in html
    assert "<li>First</li>" in html
    assert '<a href="https://example.test/policy"' in html


def test_a_script_in_markdown_does_not_execute() -> None:
    html = declaration_html("Hello <script>alert(1)</script>")
    assert "<script" not in html
    assert "alert(1)" in html


def test_pasted_html_is_sanitised_not_escaped() -> None:
    html = declaration_html("<ol><li>I agree.</li></ol><script>alert(1)</script>")
    assert "<ol>" in html
    assert "<li>I agree.</li>" in html
    assert "<script" not in html


def test_blank_copy_is_empty() -> None:
    assert declaration_html("  \n") == ""
