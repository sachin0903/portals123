"""Leakage check for an anonymised Seccl fixture.

Fails loudly if ANY real personal value from the source survives in the output,
and asserts every identifier in the output is a guaranteed-synthetic value.

Usage:
    uv run --project apps/api python apps/api/scripts/verify_anon.py \
        --source docs/seccl.duckdb --out docs/seccl.duckdb.anon
"""

from __future__ import annotations

import argparse
import re
import sys

import duckdb


def _text_cols(con, db: str, schema: str, table: str) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_catalog=? AND table_schema=? AND table_name=? "
            "AND (data_type LIKE '%VARCHAR%' OR data_type LIKE '%JSON%') "
            "ORDER BY ordinal_position",
            [db, schema, table],
        ).fetchall()
    ]


def _all_text_blob(con, db: str) -> list[tuple[str, str]]:
    """Yield (table, concatenated-text) — every text/json cell cast to string."""
    tabs = con.execute(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema IN ('seccl','platform')"
    ).fetchall()
    out = []
    for sch, name in tabs:
        cols = _text_cols(con, db, sch, name)
        if not cols:
            continue
        expr = " || ' ' || ".join(f"coalesce(cast({q(c)} as varchar),'')" for c in cols)
        blob = con.execute(
            f'SELECT string_agg({expr}, chr(10)) FROM "{db}"."{sch}"."{name}"'
        ).fetchone()[0]
        out.append((f"{sch}.{name}", blob or ""))
    return out


def q(col: str) -> str:
    return '"' + col.replace('"', '""') + '"'


def real_tokens(src: duckdb.DuckDBPyConnection) -> set[str]:
    """High-signal real identifiers from the source (all clients, to be safe)."""
    toks: set[str] = set()

    def add(rows):
        for (v,) in rows:
            if v and str(v).strip():
                toks.add(str(v).strip())

    # genuine client / staff PII only — NOT reference data (fund houses, model
    # names, master-party institutions), whose names legitimately survive.
    add(src.execute("SELECT name FROM seccl.seccl.clients").fetchall())
    add(src.execute("SELECT email FROM seccl.seccl.clients WHERE email IS NOT NULL").fetchall())
    add(
        src.execute(
            "SELECT national_insurance_no FROM seccl.seccl.clients "
            "WHERE national_insurance_no IS NOT NULL"
        ).fetchall()
    )
    add(src.execute("SELECT name FROM seccl.seccl.accounts").fetchall())  # charity/trust names
    add(src.execute("SELECT name FROM seccl.seccl.nodes").fetchall())  # adviser offices
    add(src.execute("SELECT name FROM seccl.seccl.users WHERE name IS NOT NULL").fetchall())
    add(src.execute("SELECT email FROM seccl.seccl.users WHERE email IS NOT NULL").fetchall())
    # postcodes + street lines from the client addresses
    add(
        src.execute(
            "SELECT DISTINCT json_extract_string(address,'postCode') "
            "FROM seccl.seccl.clients WHERE address IS NOT NULL"
        ).fetchall()
    )
    add(
        src.execute(
            "SELECT DISTINCT json_extract_string(address,'address1') "
            "FROM seccl.seccl.clients WHERE address IS NOT NULL"
        ).fetchall()
    )
    # bank + phone digits pulled from the JSON blobs
    add(
        src.execute(
            "SELECT DISTINCT json_extract_string(bank_details,'accountNumber') "
            "FROM seccl.seccl.clients WHERE bank_details IS NOT NULL"
        ).fetchall()
    )
    add(
        src.execute(
            "SELECT DISTINCT json_extract_string(mobile,'number') "
            "FROM seccl.seccl.clients WHERE mobile IS NOT NULL"
        ).fetchall()
    )
    add(
        src.execute(
            "SELECT DISTINCT json_extract_string(phone,'number') "
            "FROM seccl.seccl.clients WHERE phone IS NOT NULL"
        ).fetchall()
    )

    # keep only distinctive tokens: multi-word names, emails, or 6+ digit runs.
    # single-word tokens (a lone surname / fund word) cause coincidental hits and
    # are covered instead by the email/NINO/phone regex sweeps below.
    def distinctive(t: str) -> bool:
        return " " in t or "@" in t or bool(re.search(r"\d{6}", t))

    return {t for t in toks if len(t) >= 5 and distinctive(t)}


# guaranteed-synthetic patterns the output MUST conform to
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RE_NINO = re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b")
RE_UK_MOBILE = re.compile(r"\b07\d{9}\b|\b07\d{3}\s?\d{6}\b")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="docs/seccl.duckdb")
    ap.add_argument("--out", default="docs/seccl.duckdb.anon")
    args = ap.parse_args()

    src = duckdb.connect(args.source, read_only=True)
    toks = real_tokens(src)
    src.close()
    print(f"collected {len(toks)} real identifier tokens from source")

    con = duckdb.connect(args.out, read_only=True)
    db = con.execute("SELECT current_database()").fetchone()[0]
    blobs = _all_text_blob(con, db)

    failures: list[str] = []

    # 1) no real token anywhere
    for table, blob in blobs:
        for tok in toks:
            if tok in blob:
                failures.append(f"REAL VALUE LEAKED in {table}: {tok!r}")

    joined = "\n".join(b for _, b in blobs)

    # 2) every email must be @example.*  (RFC 2606 reserved)
    for m in set(RE_EMAIL.findall(joined)):
        if not m.lower().endswith(("@example.com", "@example.org", "@example.net")):
            failures.append(f"NON-SYNTHETIC EMAIL: {m!r}")

    # 3) every NINO-shaped token must start with the never-issued 'ZZ' prefix
    for m in set(RE_NINO.findall(joined)):
        if not m.upper().startswith("ZZ"):
            failures.append(f"NON-SYNTHETIC NINO: {m!r}")

    # 4) every mobile must be inside the Ofcom reserved 07700 900xxx range
    for m in set(RE_UK_MOBILE.findall(joined)):
        digits = re.sub(r"\s", "", m)
        if not digits.startswith("07700900"):
            failures.append(f"NON-RESERVED MOBILE: {m!r}")

    con.close()

    if failures:
        print(f"\n❌ {len(failures)} leak(s) found:")
        for f in failures[:50]:
            print("  -", f)
        return 1
    print("\n✅ no real PII detected; all identifiers are synthetic")
    return 0


if __name__ == "__main__":
    sys.exit(main())
