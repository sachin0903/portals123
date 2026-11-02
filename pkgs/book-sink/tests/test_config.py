"""DSN handling. Ported from `apps/api/tests/test_providers.py` when `app/fanout.py` was deleted.

The cases are the deleted module's, because they were learned the same way its predicate cases
were: a DSN that does not parse fails at CONNECT time, inside a daemon, and the symptom is a
catalogue that reads as empty — indistinguishable from a firm with no data. The logic moved to
`book_sink.config`; its tests move with it rather than going down with the module.
"""

from __future__ import annotations

from book_sink.config import _as_libpq, _libpq_value, sql_str


class TestLibpqValues:
    """libpq quotes a value only when it has to, and a naive quote-everything breaks DuckDB's
    parser — which is why this is a function and not an f-string at the call site."""

    def test_a_plain_value_is_left_alone(self) -> None:
        assert _libpq_value("lake_writer") == "lake_writer"

    def test_a_space_or_quote_forces_quoting_and_escaping(self) -> None:
        assert _libpq_value("p a'ss") == "'p a\\'ss'"

    def test_a_backslash_is_escaped(self) -> None:
        assert _libpq_value("back\\slash") == "'back\\\\slash'"

    def test_an_empty_value_is_explicit(self) -> None:
        """`password=` with nothing after it changes what the next keyword means; `''` does not."""
        assert _libpq_value("") == "''"


class TestUrlsBecomeKeywords:
    """Both spellings are accepted because both exist in the .env files this reads, and they get
    copied between them: `DATALAKE_WRITER_DSN` is normally keywords already, while apps/api's
    `DATALAKE_DATABASE_URL` is a SQLAlchemy URL. Converting is cheaper than diagnosing an "empty"
    catalogue."""

    def test_a_sqlalchemy_url_converts(self) -> None:
        assert _as_libpq("postgresql+psycopg://lake_writer:pw@localhost:5435/datalakes") == (
            "dbname=datalakes host=localhost port=5435 user=lake_writer password=pw"
        )

    def test_keywords_pass_through_untouched(self) -> None:
        keywords = "dbname=portals host=localhost port=5432 user=book_writer password=book_writer"
        assert _as_libpq(keywords) == keywords

    def test_a_percent_encoded_password_is_decoded(self) -> None:
        """A password with a `@` or `/` MUST be percent-encoded in a URL and must NOT stay that way
        in the keyword form, or the daemon authenticates with the literal `%40`."""
        # `@` needs no libpq quoting — only spaces, quotes and backslashes do — so the decoded
        # value is bare. What matters is that `%40` did not survive into it.
        assert "password=p@ss" in _as_libpq("postgresql://u:p%40ss@host:5432/db")

    def test_an_empty_dsn_stays_empty(self) -> None:
        """Blank means unconfigured, which the caller reports as such — converting it into a
        half-formed keyword string would turn a clear refusal into a connection error."""
        assert _as_libpq("") == ""


def test_sql_string_literals_cannot_be_closed_early() -> None:
    """Every value the sink interpolates goes through this, because the predicate and the staging
    projections are TEXT by the time interlace fingerprints them — there is no bind parameter to
    reach for."""
    assert sql_str("a'b") == "'a''b'"
    assert sql_str("plain") == "'plain'"
