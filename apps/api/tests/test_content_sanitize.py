"""Sanitise-on-publish: scripts and handlers never reach the client portal."""

from app.content import sanitise_html


def test_strips_script_and_handlers() -> None:
    dirty = (
        "<p>Hello</p><script>alert(1)</script>"
        '<img src="https://example.test/a.png" onerror="alert(1)">'
        '<a href="javascript:alert(1)">x</a>'
    )
    cleaned = sanitise_html(dirty)
    assert "<script" not in cleaned.lower()
    assert "onerror" not in cleaned.lower()
    assert "javascript:" not in cleaned.lower()
    assert "Hello" in cleaned


def test_keeps_article_markup() -> None:
    html = (
        "<h1>Title</h1><p>A <strong>bold</strong> <em>note</em>.</p>"
        "<ul><li>One</li></ul>"
        '<table><thead><tr><th scope="col">Year</th></tr></thead>'
        "<tbody><tr><td>2026</td></tr></tbody></table>"
    )
    cleaned = sanitise_html(html)
    assert "<h1>Title</h1>" in cleaned
    assert "<strong>bold</strong>" in cleaned
    assert "<li>One</li>" in cleaned
    assert '<th scope="col">Year</th>' in cleaned
    assert "<td>2026</td>" in cleaned


def test_keeps_same_origin_asset_proxy_sources() -> None:
    cleaned = sanitise_html('<img src="/_assets/content-aB3-0123456789abcdef.png" alt="Chart">')
    assert 'src="/_assets/content-aB3-0123456789abcdef.png"' in cleaned


def test_inlines_css_then_sanitises() -> None:
    html = '<p class="lede">Hi</p>'
    css = "p.lede { color: #0f172a; } script { display:block }"
    cleaned = sanitise_html(html, css)
    assert "Hi" in cleaned
    assert "<script" not in cleaned.lower()
