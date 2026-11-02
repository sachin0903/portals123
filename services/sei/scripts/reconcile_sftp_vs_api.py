"""SFTP TransactionHeader vs SWP /transactiondetailsservice reconciliation.

Pulls the live drop (whatever SEI still has on SFTP — typically ~5 days), decrypts the
local p04 TransactionHeader corpus, fans the API out per account, and compares
populations, types and overlapping-row fields.

Output lands in services/sei/.data/txn-recon/ (gitignored). Resume after a completed
API fetch with --skip-fetch.

  uv run python scripts/reconcile_sftp_vs_api.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from sei_sync.api import AccountRef, SyncStats, earliest_start, horizon
from sei_sync.decrypt import unlocked
from sei_sync.pull import fetch_transactions
from sei_sync.reports import split_trailer
from sei_sync.settings import SeiSettings
from sei_sync.sftp import connect, discover, read_file

log = logging.getLogger("sei_recon")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".data" / "txn-recon"
P04_DIR = Path.home() / "dev" / "sei" / "p04-production"
SFTP_TABLES = (
    "transaction_header",
    "transaction_header_upd",
    "transaction_detail",
    "transaction_detail_upd",
    "accounts",
    "reference",
)
P04_REPORTS = (
    "TransactionHeader",
    "TransactionHeaderUpd",
    "TransactionDetail",
    "TransactionDetailUpd",
)
_P04_NAME = re.compile(
    r"^GWP_UK\d+_Out_(?P<report>Transaction(?:Header|Detail)(?:Upd)?)_(?P<date>\d{8})\.out(?:\.pgp)?$"
)

CSV_OPTS = """
    delim = '|',
    header = true,
    all_varchar = true,
    filename = true,
    quote = '',
    escape = '',
    encoding = 'latin-1',
    union_by_name = true,
    null_padding = true,
    max_line_size = 16000000
"""


def _write_csv(path: Path, blob: bytes) -> int:
    csv, rows = split_trailer(blob)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(csv)
    return rows


def pull_sftp(settings: SeiSettings, dest: Path) -> dict[str, int]:
    """Download + decrypt the live reports we reconcile on."""
    counts: dict[str, int] = {}
    with connect(settings) as client, unlocked(settings) as decrypt:
        drops = [
            d
            for d in discover(client, settings)
            if d.table in SFTP_TABLES and not d.filename.startswith("PREVIEW_")
        ]
        log.info("sftp: %d file(s) to stage", len(drops))
        for drop in drops:
            out = (
                dest
                / drop.table
                / f"dt={drop.file_date.isoformat()}"
                / f"{drop.filename.removesuffix('.pgp')}"
            )
            if out.exists() and out.stat().st_size > 0:
                continue
            blob = read_file(client, settings, drop)
            if drop.encrypted:
                blob = decrypt(blob)
            n = _write_csv(out, blob)
            counts[drop.table] = counts.get(drop.table, 0) + n
            log.info("sftp %s %s  %d rows", drop.file_date, drop.table, n)
    return counts


def decrypt_p04(settings: SeiSettings, dest: Path) -> dict[str, int]:
    """Decrypt the local p04 TransactionHeader corpus into the recon dir."""
    counts: dict[str, int] = {}
    files = [
        p
        for p in P04_DIR.iterdir()
        if p.is_file() and not p.name.startswith("PREVIEW_") and _P04_NAME.match(p.name)
    ]
    log.info("p04: %d file(s) to decrypt from %s", len(files), P04_DIR)
    with unlocked(settings) as decrypt:
        for path in sorted(files):
            match = _P04_NAME.match(path.name)
            if match is None:
                continue
            report = match.group("report")
            day = date.fromisoformat(
                f"{match.group('date')[:4]}-{match.group('date')[4:6]}-{match.group('date')[6:]}"
            )
            table = re.sub(r"(?<!^)(?=[A-Z])", "_", report).lower()
            out = dest / table / f"dt={day.isoformat()}" / path.name.removesuffix(".pgp")
            if out.exists() and out.stat().st_size > 0:
                continue
            blob = path.read_bytes()
            if path.suffix == ".pgp":
                blob = decrypt(blob)
            n = _write_csv(out, blob)
            counts[table] = counts.get(table, 0) + n
    return counts


def _read_report(con: duckdb.DuckDBPyConnection, name: str, glob: str, extra: str = "") -> None:
    # name, glob and extra are this script's own table names and paths.
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {name} AS
        SELECT
            regexp_extract(filename, 'dt=([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})', 1)::DATE AS drop_date,
            {extra}
            *
        FROM read_csv('{glob}', {CSV_OPTS})
        """  # noqa: S608
    )


def load_drop(con: duckdb.DuckDBPyConnection, staged: Path) -> None:
    """Load decrypted reports. Header + upd are unioned; latest drop of an id wins."""
    header_glob = str(staged / "transaction_header" / "dt=*" / "*.out")
    upd_glob = str(staged / "transaction_header_upd" / "dt=*" / "*.out")
    detail_glob = str(staged / "transaction_detail" / "dt=*" / "*.out")
    accounts_glob = str(staged / "accounts" / "dt=*" / "*.out")
    reference_glob = str(staged / "reference" / "dt=*" / "*.out")

    _read_report(con, "drop_header_raw", header_glob)
    try:
        _read_report(con, "drop_header_upd_raw", upd_glob)
        con.execute(
            """
            CREATE OR REPLACE TABLE drop_header AS
            WITH unioned AS (
                SELECT * FROM drop_header_raw
                UNION ALL BY NAME
                SELECT * FROM drop_header_upd_raw
            )
            SELECT * FROM unioned
            QUALIFY row_number() OVER (
                PARTITION BY "TRANSACTION_ID" ORDER BY drop_date DESC
            ) = 1
            """
        )
    except duckdb.IOException:
        con.execute("CREATE OR REPLACE TABLE drop_header AS SELECT * FROM drop_header_raw")

    try:
        _read_report(con, "drop_detail", detail_glob)
    except duckdb.IOException:
        con.execute(
            "CREATE OR REPLACE TABLE drop_detail AS "
            'SELECT NULL::VARCHAR AS "TRANSACTION_ID" WHERE 1=0'
        )

    _read_report(con, "drop_accounts_raw", accounts_glob)
    con.execute(
        """
        CREATE OR REPLACE TABLE drop_accounts AS
        SELECT DISTINCT "FIRM_ID" AS firm_id, "ACCOUNT_NUMBER" AS account_number
        FROM drop_accounts_raw
        WHERE "FIRM_ID" IS NOT NULL AND "ACCOUNT_NUMBER" IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY "FIRM_ID", "ACCOUNT_NUMBER" ORDER BY drop_date DESC
        ) = 1
        """
    )
    try:
        _read_report(con, "drop_reference", reference_glob)
    except duckdb.IOException:
        con.execute(
            """CREATE OR REPLACE TABLE drop_reference AS
               SELECT NULL::VARCHAR AS "TYPE", NULL::VARCHAR AS "CODE",
                      NULL::VARCHAR AS "DESCRIPTION" WHERE 1=0"""
        )

    con.execute(
        """
        CREATE OR REPLACE TABLE drop_txn AS
        SELECT
            "TRANSACTION_ID" AS transaction_id,
            "FIRM_ID" AS firm_id,
            "ACCOUNT_NUMBER" AS account_number,
            "TRANSACTION_TYPE" AS type_code,
            "TRANSACTION_DESCRIPTION" AS type_description,
            "DESCRIPTION_CODE_ID" AS description_code_id,
            "TRANSACTION_DATE" AS transaction_date,
            "TRADE_DATE" AS trade_date,
            "SETTLEMENT_DATE" AS settlement_date,
            "ACTUAL_SETTLEMENT_DATE" AS actual_settlement_date,
            "INSTRUMENT_ID" AS instrument_id,
            "TRANSACTION_CURRENCY" AS currency,
            try_cast("QUANTITY" AS DECIMAL(28,8)) AS quantity,
            try_cast("UNIT_PRICE" AS DECIMAL(28,8)) AS unit_price,
            try_cast("NET_TRANSACTION_AMOUNT" AS DECIMAL(28,4))
                * CASE WHEN "DEBIT_CREDIT_FLAG_1" = 'D' THEN -1 ELSE 1 END AS net_amount,
            try_cast("GROSS_TRANSACTION_AMOUNT" AS DECIMAL(28,4))
                * CASE WHEN "DEBIT_CREDIT_FLAG_7" = 'D' THEN -1 ELSE 1 END AS gross_amount,
            try_cast("BASE_TRANSACTION_AMOUNT" AS DECIMAL(28,4))
                * CASE WHEN "DEBIT_CREDIT_FLAG_2" = 'D' THEN -1 ELSE 1 END AS base_amount,
            try_cast("NET_FEE_AMT" AS DECIMAL(28,4))
                * CASE WHEN "DEBIT_CREDIT_FLAG_4" = 'D' THEN -1 ELSE 1 END AS fee_amount,
            try_cast("NET_TAXES_AMT" AS DECIMAL(28,4))
                * CASE WHEN "DEBIT_CREDIT_FLAG_6" = 'D' THEN -1 ELSE 1 END AS tax_amount,
            "REVERSAL_FLAG" AS reversal_flag,
            "SUPPRESSION_INDICATOR" AS suppression_flag,
            drop_date,
            filename AS source_file
        FROM drop_header
        """
    )


async def fetch_api(
    settings: SeiSettings, accounts: list[AccountRef], start: date, end: date, dest: Path
) -> SyncStats:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tables, stats = await fetch_transactions(settings, accounts, start, end)
    nonempty = [t for t in tables if t is not None and t.num_rows]
    if nonempty:
        pq.write_table(pa.concat_tables(nonempty, promote_options="default"), dest)
    else:
        from sei_sync.api_rows import flatten

        pq.write_table(flatten([], fetched_at=datetime.now(tz=UTC)), dest)
    log.info("api parquet %s  %s", dest, stats.summary)
    failures = dest.with_name("api_failures.json")
    failures.write_text(json.dumps(stats.failures, indent=2, default=str))
    return stats


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    return None if row is None else row[0]


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    result = con.execute(sql)
    cols = [c[0] for c in result.description]
    return [dict(zip(cols, row, strict=True)) for row in result.fetchall()]


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "as_tuple"):  # Decimal
        return str(value)
    return value


def reconcile(con: duckdb.DuckDBPyConnection, api_parquet: Path, out: Path) -> dict[str, Any]:
    # api_parquet is the file this script just wrote.
    con.execute(
        f"CREATE OR REPLACE TABLE api_txn AS SELECT * FROM read_parquet('{api_parquet}')"  # noqa: S608
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE api_txn_u AS
        SELECT * FROM api_txn
        QUALIFY row_number() OVER (PARTITION BY transaction_id ORDER BY fetched_at DESC) = 1
        """
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW matched AS
        SELECT
            d.transaction_id,
            d.firm_id,
            d.account_number AS drop_account,
            a.account_number AS api_account,
            d.type_code,
            d.type_description AS drop_type_desc,
            a.transaction_type AS api_type,
            try_strptime(d.transaction_date, '%Y%m%d')::DATE AS drop_txn_date,
            a.transaction_date AS api_txn_date,
            try_strptime(d.trade_date, '%Y%m%d')::DATE AS drop_trade_date,
            a.trade_date AS api_trade_date,
            try_strptime(d.settlement_date, '%Y%m%d')::DATE AS drop_settle_date,
            a.settlement_date AS api_settle_date,
            d.quantity AS drop_qty,
            a.transaction_quantity AS api_qty,
            d.net_amount AS drop_net,
            a.local_net_transaction_amount AS api_net,
            d.gross_amount AS drop_gross,
            a.local_transaction_gross_amount AS api_gross,
            d.base_amount AS drop_base,
            a.base_net_transaction_amount AS api_base,
            d.instrument_id AS drop_instrument,
            a.instrument_id AS api_instrument,
            d.currency AS drop_currency,
            a.local_currency_code AS api_currency,
            d.drop_date
        FROM drop_txn d
        JOIN api_txn_u a ON a.transaction_id = try_cast(d.transaction_id AS BIGINT)
        """
    )

    drop_dates = _rows(
        con,
        "SELECT min(drop_date) AS min_d, max(drop_date) AS max_d, "
        "count(DISTINCT drop_date) AS days FROM drop_txn",
    )[0]
    api_dates = _rows(
        con, "SELECT min(transaction_date) AS min_d, max(transaction_date) AS max_d FROM api_txn_u"
    )[0]

    con.execute(
        """
        CREATE OR REPLACE TABLE drop_on_covered AS
        SELECT d.* FROM drop_txn d
        WHERE try_strptime(d.transaction_date, '%Y%m%d')::DATE IN (
            SELECT DISTINCT try_strptime(transaction_date, '%Y%m%d')::DATE FROM drop_txn
        )
        """
    )
    # Covered days = transaction_date values the drop actually published.
    con.execute(
        """
        CREATE OR REPLACE TABLE covered_days AS
        SELECT DISTINCT try_strptime(transaction_date, '%Y%m%d')::DATE AS d FROM drop_txn
        WHERE try_strptime(transaction_date, '%Y%m%d') IS NOT NULL
        """
    )

    population = {
        "drop_rows": _one(con, "SELECT count(*) FROM drop_txn"),
        "drop_distinct_ids": _one(con, "SELECT count(DISTINCT transaction_id) FROM drop_txn"),
        "api_rows": _one(con, "SELECT count(*) FROM api_txn_u"),
        "api_distinct_ids": _one(con, "SELECT count(DISTINCT transaction_id) FROM api_txn_u"),
        "in_both": _one(
            con,
            """
            SELECT count(*) FROM (
                SELECT try_cast(transaction_id AS BIGINT) AS id FROM drop_txn
                INTERSECT
                SELECT transaction_id FROM api_txn_u
            )
            """,
        ),
        "drop_only": _one(
            con,
            """
            SELECT count(*) FROM (
                SELECT try_cast(transaction_id AS BIGINT) AS id FROM drop_txn
                EXCEPT
                SELECT transaction_id FROM api_txn_u
            )
            """,
        ),
        "api_only": _one(
            con,
            """
            SELECT count(*) FROM (
                SELECT transaction_id FROM api_txn_u
                EXCEPT
                SELECT try_cast(transaction_id AS BIGINT) FROM drop_txn
            )
            """,
        ),
    }

    # Same comparison restricted to days the drop covers — the fair test.
    con.execute(
        """
        CREATE OR REPLACE TABLE api_on_covered AS
        SELECT * FROM api_txn_u
        WHERE transaction_date IN (SELECT d FROM covered_days)
        """
    )
    covered = {
        "drop_distinct_ids": _one(con, "SELECT count(DISTINCT transaction_id) FROM drop_txn"),
        "api_on_covered_days": _one(con, "SELECT count(*) FROM api_on_covered"),
        "in_both": _one(
            con,
            """
            SELECT count(*) FROM (
                SELECT try_cast(transaction_id AS BIGINT) FROM drop_txn
                INTERSECT
                SELECT transaction_id FROM api_on_covered
            )
            """,
        ),
        "drop_only": _one(
            con,
            """
            SELECT count(*) FROM (
                SELECT try_cast(transaction_id AS BIGINT) FROM drop_txn
                EXCEPT
                SELECT transaction_id FROM api_on_covered
            )
            """,
        ),
        "api_only_on_covered_days": _one(
            con,
            """
            SELECT count(*) FROM (
                SELECT transaction_id FROM api_on_covered
                EXCEPT
                SELECT try_cast(transaction_id AS BIGINT) FROM drop_txn
            )
            """,
        ),
        "covered_days": _one(con, "SELECT count(*) FROM covered_days"),
    }

    drop_only_types = _rows(
        con,
        """
        SELECT coalesce(type_code, '?') AS type_code,
               coalesce(type_description, '?') AS type_name,
               count(*) AS n
        FROM drop_txn
        WHERE try_cast(transaction_id AS BIGINT) NOT IN (SELECT transaction_id FROM api_txn_u)
        GROUP BY 1, 2
        ORDER BY n DESC
        """,
    )
    api_only_types = _rows(
        con,
        """
        SELECT coalesce(transaction_type, '?') AS api_type, count(*) AS n
        FROM api_on_covered
        WHERE transaction_id NOT IN (SELECT try_cast(transaction_id AS BIGINT) FROM drop_txn)
        GROUP BY 1
        ORDER BY n DESC
        """,
    )
    both_types = _rows(
        con,
        """
        SELECT coalesce(d.type_code, '?') AS type_code,
               coalesce(d.type_description, '?') AS drop_name,
               coalesce(a.transaction_type, '?') AS api_type,
               count(*) AS n
        FROM drop_txn d
        JOIN api_txn_u a ON a.transaction_id = try_cast(d.transaction_id AS BIGINT)
        GROUP BY 1, 2, 3
        ORDER BY n DESC
        """,
    )

    field_checks = _rows(
        con,
        """
        SELECT
            count(*) AS overlapped,
            count(*) FILTER (WHERE drop_account IS DISTINCT FROM api_account)
                AS account_mismatch,
            count(*) FILTER (WHERE drop_txn_date IS DISTINCT FROM api_txn_date)
                AS txn_date_mismatch,
            count(*) FILTER (WHERE drop_trade_date IS DISTINCT FROM api_trade_date)
                AS trade_date_mismatch,
            count(*) FILTER (WHERE drop_settle_date IS DISTINCT FROM api_settle_date)
                AS settle_date_mismatch,
            count(*) FILTER (WHERE drop_qty IS DISTINCT FROM api_qty) AS qty_mismatch,
            count(*) FILTER (
                WHERE abs(coalesce(drop_qty,0)) IS DISTINCT FROM abs(coalesce(api_qty,0))
                  AND abs(abs(coalesce(drop_qty,0)) - abs(coalesce(api_qty,0))) > 0.01
            ) AS qty_abs_material_mismatch,
            count(*) FILTER (
                WHERE drop_net IS DISTINCT FROM api_net
                  AND abs(coalesce(drop_net,0) - coalesce(api_net,0)) > 0.01
            ) AS net_mismatch,
            count(*) FILTER (
                WHERE drop_gross IS DISTINCT FROM api_gross
                  AND abs(coalesce(drop_gross,0) - coalesce(api_gross,0)) > 0.01
            ) AS gross_mismatch,
            count(*) FILTER (
                WHERE drop_base IS DISTINCT FROM api_base
                  AND abs(coalesce(drop_base,0) - coalesce(api_base,0)) > 0.01
            ) AS base_mismatch,
            count(*) FILTER (WHERE drop_instrument IS DISTINCT FROM api_instrument)
                AS instrument_mismatch,
            count(*) FILTER (WHERE drop_currency IS DISTINCT FROM api_currency)
                AS currency_mismatch,
            count(*) FILTER (WHERE drop_type_desc IS DISTINCT FROM api_type) AS type_text_mismatch
        FROM matched
        """,
    )[0]

    # Drop-only ids that nevertheless appear in TransactionDetail.
    detail_hits = _one(
        con,
        """
        SELECT count(DISTINCT d.transaction_id)
        FROM drop_txn d
        JOIN drop_detail t ON t."TRANSACTION_ID" = d.transaction_id
        WHERE try_cast(d.transaction_id AS BIGINT) NOT IN (SELECT transaction_id FROM api_txn_u)
        """,
    )

    triple_join = _one(
        con,
        """
        SELECT count(*) FROM (
            SELECT d.account_number,
                   try_strptime(d.transaction_date,'%Y%m%d')::DATE AS dt,
                   d.net_amount
            FROM drop_txn d
            INTERSECT
            SELECT a.account_number, a.transaction_date, a.local_net_transaction_amount
            FROM api_txn_u a
        )
        """,
    )

    mismatches = _rows(
        con,
        """
        SELECT transaction_id, drop_account, api_account, type_code, api_type,
               drop_txn_date, api_txn_date, drop_net, api_net, drop_qty, api_qty
        FROM matched
        WHERE drop_account IS DISTINCT FROM api_account
           OR drop_txn_date IS DISTINCT FROM api_txn_date
           OR (abs(coalesce(drop_net,0) - coalesce(api_net,0)) > 0.01)
        LIMIT 50
        """,
    )

    live_days = _rows(
        con,
        """
        SELECT drop_date, count(*) AS n
        FROM drop_txn
        GROUP BY 1
        ORDER BY 1
        """,
    )

    summary = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "drop_date_span": {k: _jsonable(v) for k, v in drop_dates.items()},
        "api_date_span": {k: _jsonable(v) for k, v in api_dates.items()},
        "accounts_in_drop_file": _one(con, "SELECT count(*) FROM drop_accounts"),
        "accounts_with_drop_txns": _one(con, "SELECT count(DISTINCT account_number) FROM drop_txn"),
        "accounts_with_api_txns": _one(con, "SELECT count(DISTINCT account_number) FROM api_txn_u"),
        "population": {k: _jsonable(v) for k, v in population.items()},
        "on_days_the_drop_covers": {k: _jsonable(v) for k, v in covered.items()},
        "field_checks_on_overlapping_ids": {k: _jsonable(v) for k, v in field_checks.items()},
        "drop_only_also_in_transaction_detail": _jsonable(detail_hits),
        "account_date_amount_triples_in_common": _jsonable(triple_join),
        "drop_only_types": drop_only_types,
        "api_only_types_on_covered_days": api_only_types,
        "overlapping_type_map": both_types[:40],
        "drop_rows_by_file_date": [{k: _jsonable(v) for k, v in r.items()} for r in live_days],
        "sample_field_mismatches": [{k: _jsonable(v) for k, v in r.items()} for r in mismatches],
    }

    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    schema_csv = out / "matched_schema.csv"
    con.execute(
        f"COPY (SELECT * FROM matched LIMIT 0) "  # noqa: S608
        f"TO '{schema_csv}' (HEADER, DELIMITER ',')"
    )
    # Persist the break lists for inspection. Paths are this script's output dir.
    drop_only = out / "drop_only.csv"
    con.execute(
        f"""
        COPY (
            SELECT d.*
            FROM drop_txn d
            WHERE try_cast(d.transaction_id AS BIGINT)
                NOT IN (SELECT transaction_id FROM api_txn_u)
        ) TO '{drop_only}' (HEADER, DELIMITER ',')
        """  # noqa: S608
    )
    api_only = out / "api_only_on_covered_days.csv"
    con.execute(
        f"""
        COPY (
            SELECT * FROM api_on_covered
            WHERE transaction_id NOT IN (
                SELECT try_cast(transaction_id AS BIGINT) FROM drop_txn
            )
        ) TO '{api_only}' (HEADER, DELIMITER ',')
        """  # noqa: S608
    )
    log.info("wrote %s", out / "summary.json")
    return summary


def _reference_firms(con: duckdb.DuckDBPyConnection) -> set[str]:
    """``TYPE = 'FIRM_ID'`` on the staged Reference report. Empty when the file is absent."""
    columns = {row[0].upper() for row in con.execute("DESCRIBE drop_reference").fetchall()}
    if "TYPE" not in columns or "CODE" not in columns:
        return set()
    rows = con.execute(
        """
        SELECT DISTINCT "CODE"
        FROM drop_reference
        WHERE "TYPE" = 'FIRM_ID' AND "CODE" IS NOT NULL AND "CODE" <> ''
        """
    ).fetchall()
    return {str(code).strip() for (code,) in rows if str(code).strip()}


def accounts_from_drop(con: duckdb.DuckDBPyConnection) -> list[AccountRef]:
    """Accounts whose firm is on the Reference report (``TYPE = 'FIRM_ID'``)."""
    wanted = _reference_firms(con)
    rows = con.execute(
        """
        SELECT firm_id, account_number FROM drop_accounts
        UNION
        SELECT firm_id, account_number FROM drop_txn
        """
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    out: list[AccountRef] = []
    for firm, account in rows:
        if not firm or not account:
            continue
        firm, account = str(firm).strip(), str(account).strip()
        if firm not in wanted:
            continue
        if (firm, account) in seen:
            continue
        seen.add((firm, account))
        out.append(AccountRef(firm_id=firm, account_number=account))
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-fetch", action="store_true", help="reuse api_transactions.parquet")
    parser.add_argument("--skip-sftp", action="store_true")
    parser.add_argument("--skip-p04", action="store_true")
    parser.add_argument("--api-from", type=date.fromisoformat, default=None)
    parser.add_argument("--api-to", type=date.fromisoformat, default=None)
    return parser.parse_args()


async def async_main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = parse_args()
    settings = SeiSettings()
    OUT.mkdir(parents=True, exist_ok=True)
    staged = OUT / "staged"

    if not args.skip_sftp:
        pull_sftp(settings, staged)
    if not args.skip_p04 and P04_DIR.is_dir():
        decrypt_p04(settings, staged)

    con = duckdb.connect(str(OUT / "recon.duckdb"))
    load_drop(con, staged)
    accounts = accounts_from_drop(con)
    log.info("accounts to query: %d", len(accounts))

    drop_min = _one(con, "SELECT min(try_strptime(transaction_date,'%Y%m%d')::DATE) FROM drop_txn")
    today = date.today()
    start = args.api_from or drop_min or (horizon(today) - timedelta(days=30))
    end = args.api_to or horizon(today)
    floor = earliest_start(today)
    if start < floor:
        log.warning("clamping api start %s to SEI floor %s", start, floor)
        start = floor
    log.info("api window %s .. %s", start, end)

    parquet = OUT / "api_transactions.parquet"
    stats: SyncStats | None = None
    if args.skip_fetch and parquet.exists():
        log.info("reusing %s", parquet)
    else:
        stats = await fetch_api(settings, accounts, start, end, parquet)

    summary = reconcile(con, parquet, OUT)
    if stats is not None:
        summary["api_fetch"] = {
            "requests": stats.requests,
            "rows": stats.rows,
            "accounts": stats.accounts,
            "retries": stats.retries,
            "failures": len(stats.failures),
        }
        (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(json.dumps(summary["population"], indent=2))
    print(json.dumps(summary["on_days_the_drop_covers"], indent=2))
    print(json.dumps(summary["field_checks_on_overlapping_ids"], indent=2))
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
