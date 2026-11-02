"""What the sink needs from the environment, read once.

Deliberately NOT a pydantic-settings clone of apps/api's Settings: this runs inside a provider
daemon, and the only things it needs are the two DSNs and the object-store credential the daemon
already holds for its own warehouse (docs/storage.md -- the three services read `/.env` before
their own, so these are the canonical names, not per-provider copies).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


class SinkUnavailable(RuntimeError):
    """The sink is not configured, so a run must refuse rather than write nothing quietly."""


def _libpq_value(value: str) -> str:
    """Quote one libpq keyword/value entry: a password containing a space, a quote or a
    backslash must be single-quoted with both escaped, or the DSN silently splits."""
    if value and not re.search(r"[\s'\\]", value):
        return value
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


@dataclass(frozen=True)
class SinkConfig:
    #: The portals database as `book_writer` -- a libpq DSN or URL psycopg accepts.
    book_dsn: str
    #: The `datalakes` database as `lake_writer`, in libpq keyword form: this is handed to
    #: DuckDB's `ATTACH 'ducklake:postgres:…'`, which takes libpq syntax and not a URL.
    datalake_dsn: str
    s3_endpoint: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    def require_book(self) -> str:
        if not self.book_dsn:
            raise SinkUnavailable("BOOK_WRITER_DSN is not set, so the book cannot be written")
        return self.book_dsn

    def require_datalake(self) -> str:
        if not self.datalake_dsn:
            raise SinkUnavailable("DATALAKE_WRITER_DSN is not set, so no tenant lake can be opened")
        return self.datalake_dsn


def from_env(env: dict[str, str] | None = None) -> SinkConfig:
    values = os.environ if env is None else env
    return SinkConfig(
        book_dsn=values.get("BOOK_WRITER_DSN", ""),
        datalake_dsn=_as_libpq(values.get("DATALAKE_WRITER_DSN", "")),
        s3_endpoint=values.get("S3_ENDPOINT_HOST", ""),
        s3_access_key_id=values.get("S3_ACCESS_KEY_ID", ""),
        s3_secret_access_key=values.get("S3_SECRET_ACCESS_KEY", ""),
    )


def _as_libpq(dsn: str) -> str:
    """Accept either form for DATALAKE_WRITER_DSN and hand DuckDB the one it can parse.

    interlace.yaml already interpolates this variable into `ducklake:postgres:${…}`, so it is
    normally libpq keywords already. A URL is accepted because that is what apps/api's
    DATALAKE_DATABASE_URL looks like and the two get copied between .env files; converting is
    cheaper than a support conversation about a catalogue that "is empty".
    """
    if not dsn or "://" not in dsn:
        return dsn
    from urllib.parse import unquote, urlsplit

    parts = urlsplit(dsn)
    fields = [f"dbname={_libpq_value(unquote(parts.path.lstrip('/')))}"]
    if parts.hostname:
        fields.append(f"host={_libpq_value(parts.hostname)}")
    if parts.port:
        fields.append(f"port={parts.port}")
    if parts.username:
        fields.append(f"user={_libpq_value(unquote(parts.username))}")
    if parts.password:
        fields.append(f"password={_libpq_value(unquote(parts.password))}")
    return " ".join(fields)


def sql_str(value: str) -> str:
    """Single-quote a value for SQL that cannot be parameterised (DuckDB ATTACH options,
    CREATE SECRET fields, and the tenant predicates built as SQL text)."""
    return "'" + value.replace("'", "''") + "'"
