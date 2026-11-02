"""The read-only SQL console's statement guard (app/platforms._scan_sql / _query_sync):
comments strip OUTSIDE string literals only; multi-statement and non-SELECT rejection
must not be fooled by (or trip over) literal contents."""

from app.platforms import _SELECT_ONLY, _scan_sql


def _clean(sql: str) -> str:
    return _scan_sql(sql)[0]


def test_strips_line_comments() -> None:
    assert _clean("SELECT 1 -- trailing\nFROM t").split() == ["SELECT", "1", "FROM", "t"]


def test_literal_containing_comment_marker_survives() -> None:
    sql = "SELECT * FROM contacts WHERE ref LIKE '%--%'"
    assert _clean(sql) == sql  # the -- inside the literal is data, not a comment


def test_escaped_quote_keeps_masking_aligned() -> None:
    cleaned, masked = _scan_sql("SELECT 'it''s -- fine' -- comment")
    assert cleaned.rstrip() == "SELECT 'it''s -- fine'"
    assert len(cleaned) == len(masked)


def test_semicolon_inside_literal_is_one_statement() -> None:
    _, masked = _scan_sql("SELECT ';' AS c")
    assert ";" not in masked


def test_semicolon_between_statements_is_caught() -> None:
    _, masked = _scan_sql("SELECT 1; DROP TABLE t")
    assert ";" in masked


def test_describe_is_a_read() -> None:
    assert _SELECT_ONLY.match("DESCRIBE platform.instruments")
    assert _SELECT_ONLY.match("describe select 1")


def test_summarize_is_a_read() -> None:
    assert _SELECT_ONLY.match("SUMMARIZE platform.instruments")
    assert _SELECT_ONLY.match("summarize select * from platform.instruments")


def test_keyword_check_uses_masked_form() -> None:
    # A literal can't smuggle a leading SELECT past the keyword check's masked view.
    _, masked = _scan_sql("ATTACH 'select.db'")
    assert not _SELECT_ONLY.match(masked.strip())
