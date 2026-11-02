"""Regenerate ``sei_sync.schema`` by profiling the staged SEI corpus.

    uv run python scripts/profile_schema.py            # print a summary, write the module
    uv run python scripts/profile_schema.py --dry-run  # summary only

SEI ships every report as untyped pipe-delimited text, so the only way to know a
column's real type is to look at the values. This reads every staged object for
the configured ``SEI_ENV``, classifies each column by the shape of its non-blank
values, and writes the non-VARCHAR findings to ``src/sei_sync/schema.py``.

Run it after SEI adds a report or a column, and review the diff — the generated
module is the reviewed contract, not a runtime lookup. A column that is empty
across the whole corpus is left VARCHAR deliberately: we do not guess a type we
have never seen a value for.
"""

from __future__ import annotations

import argparse
import collections
import logging
import re
from pathlib import Path

import duckdb

from sei_sync.reports import DELIMITER
from sei_sync.settings import SeiSettings

logger = logging.getLogger("sei_sync.profile")

_DATE_YMD = re.compile(r"^\d{8}$")
# Fully anchored, and one pattern per format SEI actually uses. An unanchored
# timestamp pattern is what mis-declared clients.PREF_TAX_FORM_DEL_UPD_DT: it
# matched the leading `2024-10-16 02:00:07` and ignored the ` PM +01:00` that made
# every value uncastable, so 8,303 of 8,321 values converted to NULL.
_TIMESTAMPTZ = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{2}:\d{2}$")
_TIMESTAMPTZ_12H = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (?:AM|PM) [+-]\d{2}:\d{2}$")
_INTEGER = re.compile(r"^-?\d+$")
_DECIMAL = re.compile(r"^-?\d*\.\d+$")

_MODULE = Path(__file__).resolve().parent.parent / "src" / "sei_sync" / "schema.py"

_HEADER = '''"""Declared column types for the SEI raw layer — GENERATED, review the diff.

Regenerate with ``uv run python scripts/profile_schema.py`` (see that script for
the rules). SEI delivers untyped text; this is the reviewed statement of what
each column actually holds, so ``raw.*`` materialises real types instead of
deferring every cast to the consumption layer.

Only non-VARCHAR columns are listed. **Anything absent is VARCHAR**, which is the
deliberate default for three kinds of column:

* **identifiers** (``*_ID``, ``ACCOUNT_NUMBER``, ``SEDOL``…) — leading zeros are
  real in this feed ({leading_zero} columns carry them), and numeric typing would
  silently destroy them. They are join keys, never arithmetic.
* **numeric enumeration codes** (``ADDRESS_TYPE``, ``ACTIVITY_STATUS``,
  ``CAPACITY_TYPE``…) — categorical, and they join to ``ref.codes.code``, which is
  text. Typing them integer would break that join.
* **columns never populated** in the profiled corpus — we do not guess.

Profiled from the ``{env}`` corpus: {tables} reports, {columns} columns
({typed} typed — {dates} DATE, {decimals} DECIMAL, {booleans} BOOLEAN,
{timestamps} TIMESTAMPTZ).
"""

from __future__ import annotations

# SEI writes every date as YYYYMMDD with no separators. DuckDB cannot CAST that
# to DATE — ``try_cast('20260327' AS DATE)`` is NULL — which is why the dates have
# to be parsed with an explicit format rather than cast.
DATE_FORMAT = "%Y%m%d"

# report table -> column -> declared type. Absent column => VARCHAR (see above).
COLUMN_TYPES: dict[str, dict[str, str]] = {{
'''

_FOOTER = '''}


def column_type(table: str, column: str) -> str:
    """The declared type for one column. VARCHAR when we have not typed it."""
    return COLUMN_TYPES.get(table, {}).get(column, "VARCHAR")
'''


def classify(values: dict[str, int]) -> str:
    """The narrowest type every non-blank value in this column satisfies.

    Deliberately conservative: a single value that does not fit widens the column
    back to VARCHAR, because a half-converted column is worse than an untyped one.

    Takes the per-column tallies produced by :func:`tally_sql` rather than the values
    themselves. The tallies are computed by DuckDB over the whole corpus: the p04 drop
    is 37M rows and reading it into Python was an out-of-memory kill, not a slow run.
    """
    nonblank = values["nonblank"]
    if not nonblank:
        return "VARCHAR"  # never populated — we do not guess a type we have not seen
    if values["ymd"] == nonblank:
        return "DATE"
    if values["tz12"] == nonblank:
        return "TIMESTAMPTZ_12H"
    if values["tz"] == nonblank:
        return "TIMESTAMPTZ"
    if values["yn"] == nonblank:
        return "BOOLEAN"
    if values["numeric"] == nonblank and values["dec"] > 0:
        return "DECIMAL"
    if values["int"] == nonblank:
        # Integer-shaped, but see the module docstring: identifiers and code
        # enumerations both look like this, and both must stay text.
        return "LEADING_ZERO" if values["lz"] else "VARCHAR"
    return "VARCHAR"


def tally_sql(column: str) -> str:
    """Aggregates that decide one column's type, evaluated by DuckDB over every row.

    ``ymd`` requires a real calendar date as well as eight digits, so an 8-digit
    identifier is not mistaken for one. The timestamp patterns are fully anchored —
    an unanchored one is what mis-declared `clients.PREF_TAX_FORM_DEL_UPD_DT`.
    """
    q = '"' + column.replace('"', '""') + '"'
    v = f"nullif({q}, '')"
    return ", ".join(
        (
            f"count({v})",
            f"count(*) FILTER (WHERE regexp_matches({v}, '^\\d{{8}}$') "
            f"AND try_strptime({v}, '%Y%m%d') IS NOT NULL)",
            f"count(*) FILTER (WHERE regexp_matches({v}, '^\\d{{4}}-\\d{{2}}-\\d{{2}} "
            f"\\d{{2}}:\\d{{2}}:\\d{{2}} (AM|PM) [+-]\\d{{2}}:\\d{{2}}$'))",
            f"count(*) FILTER (WHERE regexp_matches({v}, "
            f"'^\\d{{4}}-\\d{{2}}-\\d{{2}} \\d{{2}}:\\d{{2}}:\\d{{2}} [+-]\\d{{2}}:\\d{{2}}$'))",
            f"count(*) FILTER (WHERE {v} IN ('Y', 'N'))",
            f"count(*) FILTER (WHERE regexp_matches({v}, '^-?\\d*\\.\\d+$|^-?\\d+$'))",
            f"count(*) FILTER (WHERE regexp_matches({v}, '^-?\\d*\\.\\d+$'))",
            f"count(*) FILTER (WHERE regexp_matches({v}, '^-?\\d+$'))",
            f"count(*) FILTER (WHERE regexp_matches({v}, '^0\\d+$'))",
        )
    )


_TALLY_KEYS = ("nonblank", "ymd", "tz12", "tz", "yn", "numeric", "dec", "int", "lz")

# One report has 152 columns; nine aggregates each would be 1,368 in a single SELECT.
_COLUMN_CHUNK = 24


def _connect(settings: SeiSettings) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    parts = [
        "TYPE s3",
        f"KEY_ID '{settings.s3_access_key_id}'",
        f"SECRET '{settings.s3_secret_access_key}'",
    ]
    if settings.s3_endpoint:
        host = settings.s3_endpoint.removeprefix("https://").removeprefix("http://")
        parts += [
            f"ENDPOINT '{host}'",
            f"USE_SSL {'true' if settings.endpoint_url.startswith('https') else 'false'}",
            "URL_STYLE 'path'",
        ]
    conn.execute(f"CREATE SECRET profile_s3 ({', '.join(parts)})")
    return conn


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="summarise without writing")
    parser.add_argument(
        "--dates",
        type=int,
        default=12,
        help=(
            "profile the N most recent drop dates per report (default 12; 0 = every date). "
            "A full p04 corpus is ~37M rows and cannot be read into memory, and the "
            "classification only needs enough values to be confident — but see the note in "
            "`classify`: a widening value in an unprofiled date will not be seen, which is "
            "what the conversion audit at ingest exists to catch."
        ),
    )
    args = parser.parse_args(argv)

    settings = SeiSettings()
    conn = _connect(settings)
    root = f"s3://{settings.core_bucket}/staged/sei/{settings.env}"
    # The glob is built from our own settings, not caller input.
    listing = f"SELECT DISTINCT file FROM glob('{root}/*/dt=*/*.out')"  # noqa: S608
    found = conn.execute(listing).fetchall()
    if not found:
        logger.error("no staged objects under %s — run `sei-sync pull` first", root)
        return 1
    by_table: dict[str, set[str]] = {}
    for (path,) in found:
        table, _, rest = path.removeprefix(f"{root}/").partition("/")
        by_table.setdefault(table, set()).add(rest.split("/")[0])  # dt=YYYY-MM-DD
    tables = sorted(by_table)

    profile: dict[str, dict[str, str]] = {}
    kinds: collections.Counter[str] = collections.Counter()
    leading_zero: list[str] = []
    total_columns = 0

    for table in tables:
        dates = sorted(by_table[table])
        sampled = dates if args.dates <= 0 else dates[-args.dates :]
        paths = ", ".join(f"'{root}/{table}/{d}/*.out'" for d in sampled)
        src = (
            f"read_csv([{paths}], delim='{DELIMITER}', header=true, "
            "all_varchar=true, union_by_name=true, quote='', escape='', "
            "null_padding=true, max_line_size=16000000)"
        )
        described = conn.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()  # noqa: S608
        columns = [c for c, *_ in described if c != "dt"]  # dt is the hive partition
        typed: dict[str, str] = {}
        rows_seen = 0
        # Chunked so a 152-column report does not build one query with 1,368 aggregates.
        for start in range(0, len(columns), _COLUMN_CHUNK):
            chunk = columns[start : start + _COLUMN_CHUNK]
            tallies = ", ".join(tally_sql(c) for c in chunk)
            row = conn.execute(f"SELECT count(*), {tallies} FROM {src}").fetchone()  # noqa: S608
            if row is None:  # a SELECT with aggregates always returns one row
                continue
            rows_seen = row[0]
            for i, column in enumerate(chunk):
                counts = dict(zip(_TALLY_KEYS, row[1 + i * len(_TALLY_KEYS) :], strict=False))
                total_columns += 1
                kind = classify(counts)
                kinds[kind] += 1
                if kind == "LEADING_ZERO":
                    leading_zero.append(f"{table}.{column}")
                elif kind != "VARCHAR":
                    typed[column] = kind
        if typed:
            profile[table] = typed
        logger.info(
            "%-34s %4d cols %9d rows %3d typed  (%d/%d dates)",
            table,
            len(columns),
            rows_seen,
            len(typed),
            len(sampled),
            len(dates),
        )

    logger.info(
        "typed %d of %d columns: %s",
        sum(len(v) for v in profile.values()),
        total_columns,
        dict(kinds),
    )
    if leading_zero:
        logger.info("kept VARCHAR for leading zeros: %s", ", ".join(leading_zero))

    if args.dry_run:
        return 0

    body = "".join(
        f"    {table!r}: {{\n"
        + "".join(f"        {col!r}: {kind!r},\n" for col, kind in sorted(cols.items()))
        + "    },\n"
        for table, cols in sorted(profile.items())
    )
    header = _HEADER.format(
        env=settings.env,
        tables=len(tables),
        columns=total_columns,
        typed=sum(len(v) for v in profile.values()),
        dates=kinds["DATE"],
        decimals=kinds["DECIMAL"],
        booleans=kinds["BOOLEAN"],
        timestamps=kinds["TIMESTAMPTZ"] + kinds["TIMESTAMPTZ_12H"],
        leading_zero=len(leading_zero),
    )
    _MODULE.write_text(header + body + _FOOTER, encoding="utf-8")
    logger.info("wrote %s", _MODULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
