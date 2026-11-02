"""Build an anonymised, downsampled DuckDB fixture from the real Seccl lake file.

The real ``docs/seccl.duckdb`` holds live client data and must never be committed.
This script produces a small, safe stand-in that keeps the same schema and enough
shape to drive the app (the lake read-path in ``app/lake.py``) and demos.

What it does:
  * keeps a deterministic subset of clients (``--clients N``) and everything that
    joins to them; reference tables (assets, models, nodes, ...) are kept whole.
  * rewrites every personal field with synthetic, deterministic values keyed on the
    original id, so a client/account looks consistent across all tables.
  * pseudonymises ``client_id`` / ``account_id`` so nothing joins back to real Seccl
    records; fund/model/asset ids and ISINs (reference data) are kept as-is.
  * uses guaranteed-unreal values: @example.com emails (RFC 2606), Ofcom reserved
    phone ranges (07700 900xxx / 01632 960xxx), 'ZZ' NINO prefix (never issued).

Re-runnable and stable: same source + same N => identical output.

Usage:
    uv run --project apps/api python apps/api/scripts/anonymise_seccl.py \
        --source docs/seccl.duckdb --out docs/seccl.duckdb.anon --clients 10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, timedelta

import duckdb

# --- deterministic fake pools -------------------------------------------------

FIRST_NAMES = [
    "Oliver",
    "Amelia",
    "Harry",
    "Isla",
    "George",
    "Ava",
    "Noah",
    "Emily",
    "Jack",
    "Grace",
    "Leo",
    "Sophie",
    "Arthur",
    "Mia",
    "Oscar",
    "Poppy",
    "Charlie",
    "Freya",
    "Henry",
    "Ivy",
    "Thomas",
    "Florence",
    "Freddie",
    "Willow",
    "Alfie",
    "Rosie",
    "Theo",
    "Daisy",
    "Max",
    "Evie",
    "Lucas",
    "Phoebe",
    "Ethan",
    "Millie",
    "James",
    "Lily",
    "William",
    "Sienna",
    "Benjamin",
    "Maya",
]
SURNAMES = [
    "Ashford",
    "Barlow",
    "Caldwell",
    "Denton",
    "Ellwood",
    "Fairbourne",
    "Garrick",
    "Halloway",
    "Ingram",
    "Jarrold",
    "Kingsley",
    "Lonsdale",
    "Merrick",
    "Norbury",
    "Oakden",
    "Pennington",
    "Quennell",
    "Rushforth",
    "Salcombe",
    "Thornbury",
    "Underhill",
    "Vellacott",
    "Westbrook",
    "Yardley",
    "Ambleside",
    "Braithwaite",
    "Corfield",
    "Dunmore",
    "Elphick",
    "Fenwick",
]
TITLES = ["Mr", "Mrs", "Ms", "Miss", "Dr"]
STREETS = [
    "Maple Avenue",
    "Cedar Close",
    "Birchwood Lane",
    "Elm Grove",
    "Willow Way",
    "Rowan Court",
    "Hawthorn Rise",
    "Bramble Drive",
    "Juniper Street",
    "Sycamore Road",
    "Chestnut Walk",
    "Larch Meadow",
]
TOWNS = [
    ("Fairhaven", "FH1"),
    ("Netherby", "NB2"),
    ("Oakmere", "OK3"),
    ("Westcliff", "WC4"),
    ("Ashgrove", "AG5"),
    ("Thornbury", "TB6"),
    ("Millbrook", "MB7"),
    ("Kingsmere", "KM8"),
]
COMPANY_WORDS_A = [
    "Northgate",
    "Silverbourne",
    "Crestwood",
    "Havering",
    "Marlowe",
    "Pinehurst",
    "Aldbourne",
    "Fenchurch",
]
COMPANY_WORDS_B = [
    "Trustees",
    "Nominees",
    "Holdings",
    "Investments",
    "Partners",
    "Custody",
    "Capital",
    "Financial",
]
COMPANY_SUFFIX = ["Ltd", "LLP", "plc", "Ltd"]


def _seed(prefix: str, value: str) -> int:
    """Stable integer seed from a string (order-independent, re-runnable)."""
    h = hashlib.sha256(f"{prefix}:{value}".encode()).hexdigest()
    return int(h[:12], 16)


def _pick(pool: list, seed: int, salt: int = 0) -> object:
    return pool[(seed + salt) % len(pool)]


def fake_person(client_id: str) -> dict:
    s = _seed("client", client_id)
    # Independent seeds per component, NOT salts off one seed. Deriving both from `s` meant the
    # pair (first, surname) was determined by (s mod 40, s mod 30), which takes only
    # lcm(40, 30) = 120 values because the pool sizes share a factor of 10 -- a 10x collapse of the
    # 40 x 30 = 1200 space. At ~10 clients that is a ~31% chance of two identical identities, and it
    # duly happened (two distinct Seccl clients both became "Max Oakden"). Separate seeds restore
    # the full space; the pools themselves are already collision-free (40 and 30 distinct values).
    first = _pick(FIRST_NAMES, _seed("first", client_id))
    surname = _pick(SURNAMES, _seed("surname", client_id))
    title = _pick(TITLES, s, 3)
    bnum = (s % 120) + 1
    street = _pick(STREETS, s, 11)
    town, area = _pick(TOWNS, s, 13)
    postcode = f"{area} {s % 9}{chr(65 + s % 26)}{chr(65 + (s >> 3) % 26)}"
    # DOB: plausible adult, deterministic, 1945-01-01 + N days (up to ~55y)
    dob = date(1945, 1, 1) + timedelta(days=s % 20000)
    return {
        "title": title,
        "first_name": first,
        "surname": surname,
        "name": f"{first} {surname}",
        # Keyed on the client id, NOT just the name. Two different Seccl clients can land on
        # the same (first, surname) pair -- the pair is determined by `s mod lcm(41,31)`, so
        # with a real book it is a birthday problem, and it has already happened. A shared
        # NAME is realistic; a shared EMAIL makes the two records indistinguishable in the UI
        # and trips the duplicate-email guard on client create.
        "email": f"{first.lower()}.{surname.lower()}.{s % 100000000:08d}@example.com",
        "dob": dob,
        "nino": f"ZZ{s % 1000000:06d}{chr(65 + s % 4)}",  # ZZ prefix never issued
        "mobile_num": f"07700 9009{s % 100:02d}",  # Ofcom reserved range
        "phone_num": f"01632 9609{(s >> 4) % 100:02d}",  # Ofcom reserved range
        "sort_code": f"04{(s % 90) + 10:02d}{(s >> 6) % 100:02d}",
        "account_number": f"{s % 100000000:08d}",
        "address": {
            "buildingNumber": str(bnum),
            "address1": street,
            "address2": town,
            "address3": area,
            "country": "GB",
            "postCode": postcode,
        },
    }


def fake_company(party_id: str) -> str:
    s = _seed("party", party_id)
    return (
        f"{_pick(COMPANY_WORDS_A, s, 1)} {_pick(COMPANY_WORDS_B, s, 5)} "
        f"{_pick(COMPANY_SUFFIX, s, 9)}"
    )


def fake_account_id(old: str) -> str:
    return "AX" + hashlib.sha256(f"acct:{old}".encode()).hexdigest()[:7].upper()


def fake_client_id(old: str) -> str:
    return "CX" + hashlib.sha256(f"clnt:{old}".encode()).hexdigest()[:7].upper()


# --- build --------------------------------------------------------------------

_DAILY_TABLES = ("asset_performance_daily", "asset_balances_daily", "cash_balances_daily")


def build(source: str, out: str, n_clients: int, history_months: int | None = None) -> None:
    if os.path.exists(out):
        os.remove(out)

    src = duckdb.connect(source, read_only=True)
    # NB: the source's catalog AND its schema are both named "seccl", so every
    # reference must be the three-part catalog.schema.table to disambiguate.
    chosen = [
        r[0]
        for r in src.execute(
            """SELECT id FROM seccl.seccl.clients
           WHERE id IN (SELECT DISTINCT client_id FROM seccl.seccl.asset_performance_daily)
           ORDER BY id LIMIT ?""",
            [n_clients],
        ).fetchall()
    ]
    if not chosen:
        raise SystemExit("no clients found in source")

    # every account_id reachable from the chosen clients, across all fact tables
    acc_rows = src.execute(
        """WITH ids AS (
             SELECT account_id FROM seccl.seccl.asset_performance_daily WHERE client_id IN ?
             UNION SELECT account_id FROM seccl.seccl.asset_balances_daily WHERE client_id IN ?
             UNION SELECT account_id FROM seccl.seccl.cash_balances_daily WHERE client_id IN ?
             UNION SELECT account_id FROM seccl.seccl.transactions WHERE client_id IN ?
             UNION SELECT id AS account_id FROM seccl.seccl.accounts WHERE client_id IN ?
           )
           SELECT DISTINCT account_id FROM ids WHERE account_id IS NOT NULL""",
        [chosen, chosen, chosen, chosen, chosen],
    ).fetchall()
    account_ids = [r[0] for r in acc_rows]
    # map account -> owning client (for building a sensible fake account name)
    acc_owner = dict(
        src.execute(
            "SELECT id, client_id FROM seccl.seccl.accounts WHERE client_id IN ?", [chosen]
        ).fetchall()
    )
    acc_product = dict(
        src.execute(
            "SELECT id, coalesce(product_type, account_type, 'Account') FROM seccl.seccl.accounts "
            "WHERE client_id IN ?",
            [chosen],
        ).fetchall()
    )

    # schema catalogue from the source
    tabs = src.execute(
        """SELECT table_schema, table_name FROM information_schema.tables
           WHERE table_schema IN ('seccl','platform') ORDER BY 1,2"""
    ).fetchall()
    colmap = {
        (s, n): [
            r[0]
            for r in src.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=? AND table_name=? ORDER BY ordinal_position",
                [s, n],
            ).fetchall()
        ]
        for s, n in tabs
    }
    src.close()

    # --- build the fake maps in python -------------------------------------
    client_map = {cid: fake_person(cid) for cid in chosen}
    client_id_map = {cid: fake_client_id(cid) for cid in chosen}

    def account_name(aid: str) -> str:
        owner = acc_owner.get(aid)
        product = acc_product.get(aid, "Account")
        if owner and owner in client_map:
            return f"{client_map[owner]['name']} {product}"
        return f"Redacted {product}"

    account_id_map = {aid: fake_account_id(aid) for aid in account_ids}
    account_name_map = {aid: account_name(aid) for aid in account_ids}

    # --- write output -------------------------------------------------------
    con = duckdb.connect(out)
    db = con.execute("SELECT current_database()").fetchone()[0]  # out catalog name
    con.execute(f"ATTACH '{source}' AS s (READ_ONLY)")
    con.execute("CREATE SCHEMA IF NOT EXISTS seccl; CREATE SCHEMA IF NOT EXISTS platform")

    # map tables (temp) so the big fact tables can be rewritten in pure SQL
    con.execute("CREATE TEMP TABLE m_client(old_id VARCHAR, new_id VARCHAR, name VARCHAR)")
    con.executemany(
        "INSERT INTO m_client VALUES (?,?,?)",
        [(c, client_id_map[c], client_map[c]["name"]) for c in chosen],
    )
    con.execute("CREATE TEMP TABLE m_account(old_id VARCHAR, new_id VARCHAR, name VARCHAR)")
    con.executemany(
        "INSERT INTO m_account VALUES (?,?,?)",
        [(a, account_id_map[a], account_name_map[a]) for a in account_ids],
    )
    node_rows = con.execute("SELECT id, name FROM s.seccl.nodes").fetchall()
    con.execute("CREATE TEMP TABLE m_node(node_id INTEGER, name VARCHAR)")
    con.executemany(
        "INSERT INTO m_node VALUES (?,?)",
        [(nid, f"Advisory Office {i + 1}") for i, (nid, _) in enumerate(node_rows)],
    )

    # The base identity tables key on `id`/`name` (not client_id/account_name),
    # so they need bespoke handling; every other table carries the identity as
    # the denormalised client_id/account_id/*_name columns handled generically.
    def col_expr(table: str, col: str) -> str:
        # base-table identity columns (the id/name of the entity itself)
        if table == "clients" and col == "id":
            return "mc.new_id AS id"
        if table == "accounts" and col == "id":
            return "ma.new_id AS id"
        if table == "accounts" and col == "name":
            return "coalesce(ma.name, '[redacted account]') AS name"
        if table == "client_history" and col == "id":
            return "mc.new_id AS id"
        if table == "client_history" and col == "name":
            return "coalesce(mc.name, '[redacted]') AS name"
        if table == "nodes" and col == "name":
            return "coalesce(mn.name, 'Advisory Office') AS name"
        # generic denormalised identity columns, applied to every table
        if col == "client_id":
            return "coalesce(mc.new_id, t.client_id) AS client_id"
        if col == "account_id":
            return "coalesce(ma.new_id, 'AX' || substr(md5(t.account_id),1,7)) AS account_id"
        if col == "client_name":
            return "coalesce(mc.name, '[redacted]') AS client_name"
        if col == "account_name":
            return "coalesce(ma.name, '[redacted account]') AS account_name"
        if col == "node_name":
            return "coalesce(mn.name, 'Advisory Office') AS node_name"
        if col == "firm_id":
            return "'FIRMX' AS firm_id"
        return f"t.{quote(col)}"

    for (sch, name), cols in colmap.items():
        select = ", ".join(col_expr(name, c) for c in cols)
        # id-column joins for the base identity tables, else the denormalised keys
        mc_on = (
            "t.id"
            if name in ("clients", "client_history")
            else ("t.client_id" if "client_id" in cols else None)
        )
        ma_on = "t.id" if name == "accounts" else ("t.account_id" if "account_id" in cols else None)
        mn_on = "t.id" if name == "nodes" else ("t.node_id" if "node_id" in cols else None)
        joins = ""
        if mc_on:
            joins += f" LEFT JOIN m_client mc ON mc.old_id = {mc_on}"
        if ma_on:
            joins += f" LEFT JOIN m_account ma ON ma.old_id = {ma_on}"
        if mn_on:
            joins += f" LEFT JOIN m_node mn ON mn.node_id = {mn_on}"
        # row filter to the chosen subset (+ optional daily-history window)
        where = subset_where(name, cols, history_months)
        con.execute(
            f'CREATE TABLE "{db}"."{sch}"."{name}" AS '
            f'SELECT {select} FROM s."{sch}"."{name}" t{joins}{where}'
        )

    con.execute("CHECKPOINT")
    con.close()

    # --- python second pass: scrub the detailed identity fields ------------
    scrub_identity_fields(out, db, client_map, client_id_map)

    size = os.path.getsize(out)
    print(f"built {out}: {len(chosen)} clients, {len(account_ids)} accounts, {size / 1e6:.1f} MB")


def quote(col: str) -> str:
    return '"' + col.replace('"', '""') + '"'


def subset_where(name: str, cols: list[str], history_months: int | None = None) -> str:
    """Row filter restricting each table to the chosen client subset, plus an
    optional recent-history window on the big daily tables (the size lever)."""
    conds: list[str] = []
    if "client_id" in cols:
        conds.append("t.client_id IN (SELECT old_id FROM m_client)")
    elif name in ("clients", "client_history"):
        conds.append("t.id IN (SELECT old_id FROM m_client)")
    elif name == "client_products":
        conds.append("list_has_any(t.client_ids, (SELECT list(old_id) FROM m_client))")
    elif "account_id" in cols:
        conds.append("t.account_id IN (SELECT old_id FROM m_account)")
    elif name == "asset_prices":
        conds.append(
            "t.asset_id IN (SELECT DISTINCT asset_id "
            "FROM s.seccl.asset_performance_daily "
            "WHERE client_id IN (SELECT old_id FROM m_client))"
        )
    if history_months:
        if name in _DAILY_TABLES:
            conds.append(
                f"t.date >= ((SELECT max(date) FROM s.seccl.{name}) "
                f"- INTERVAL '{history_months} months')"
            )
        elif name == "asset_prices":
            conds.append(
                f"t.price_date >= ((SELECT max(price_date) FROM s.seccl.asset_prices) "
                f"- INTERVAL '{history_months} months')"
            )
    return (" WHERE " + " AND ".join(conds)) if conds else ""


def scrub_identity_fields(out: str, db: str, client_map: dict, client_id_map: dict) -> None:
    """Second pass over the small identity tables: replace the remaining
    detailed personal fields (JSON blobs, scalars, free text) with synthetics."""
    con = duckdb.connect(out)

    def tbl(name: str) -> str:  # three-part name (catalog==schema=='seccl' collides)
        return f'"{db}"."seccl"."{name}"'

    # clients: rebuild every personal column from the fake person
    rows = con.execute(f"SELECT id FROM {tbl('clients')}").fetchall()
    # id here is already the NEW id; recover the original via reverse map
    rev = {new: old for old, new in client_id_map.items()}
    for (new_id,) in rows:
        old = rev.get(new_id)
        if old is None:
            continue
        p = client_map[old]
        addr = json.dumps(p["address"])
        bank = json.dumps({"sortCode": p["sort_code"], "accountNumber": p["account_number"]})
        phone = json.dumps({"isMobile": "false", "locale": "en-GB", "number": p["phone_num"]})
        mobile = json.dumps({"isMobile": "true", "locale": "en-GB", "number": p["mobile_num"]})
        nats = json.dumps(
            [
                {
                    "code": "GB",
                    "identifiers": [
                        {"name": "National Insurance Number", "available": True, "value": p["nino"]}
                    ],
                }
            ]
        )
        con.execute(
            f"""UPDATE {tbl("clients")} SET
                 title=?, first_name=?, surname=?, name=?, email=?, date_of_birth=?,
                 national_insurance_no=?, address=?::json, bank_details=?::json,
                 phone=?::json, mobile=?::json, nationalities=?::json,
                 employers='[]'::json, update_history='[]'::json
               WHERE id=?""",
            [
                p["title"],
                p["first_name"],
                p["surname"],
                p["name"],
                p["email"],
                p["dob"],
                p["nino"],
                addr,
                bank,
                phone,
                mobile,
                nats,
                new_id,
            ],
        )

    # accounts: drop the residual account-level identity fields.
    # `positions` is a JSON holdings breakdown that embeds the real account name.
    _exec_if_cols(
        con,
        db,
        "accounts",
        {
            "national_insurance_no": "NULL",
            "bank_details": "NULL",
            "recurring_payments": "NULL",
            "positions": "NULL",
        },
    )

    # investment_decision JSON blobs (on users/clients/models/transactions/...) can
    # embed NINOs and names; none are on the app read-path, so null them wherever
    # they appear.
    for sch, tab in con.execute(
        "SELECT table_schema, table_name FROM information_schema.columns "
        "WHERE column_name='investment_decision' AND table_schema IN ('seccl','platform') "
        "GROUP BY 1,2"
    ).fetchall():
        con.execute(f'UPDATE "{db}"."{sch}"."{tab}" SET investment_decision=NULL')

    # users (staff): fake name + email, keyed on their own id for stability
    for (uid,) in con.execute(f"SELECT id FROM {tbl('users')}").fetchall():
        s = _seed("user", uid or "")
        first, surname = _pick(FIRST_NAMES, s, 2), _pick(SURNAMES, s, 5)
        con.execute(
            f"UPDATE {tbl('users')} SET name=?, email=? WHERE id=?",
            [f"{first} {surname}", f"{first.lower()}.{surname.lower()}@example.com", uid],
        )

    # master_parties: fake company name, drop email
    for (pid,) in con.execute(f"SELECT id FROM {tbl('master_parties')}").fetchall():
        con.execute(
            f"UPDATE {tbl('master_parties')} SET name=?, email=NULL WHERE id=?",
            [fake_company(pid or ""), pid],
        )

    # client_products: replace beneficiary (expression of wish) names
    _scrub_eow(con, db)

    # third-party products: strip bank / tax references / contact names
    _exec_if_cols(
        con,
        db,
        "third_party_products",
        {
            "scheme_bank_details": "NULL",
            "unique_tax_payer_reference": "NULL",
            "pension_scheme_tax_reference": "NULL",
            "relief_at_source_reference_number": "NULL",
            "scheme_fca_reference_number": "NULL",
            "contact_name": "'[redacted]'",
        },
    )

    # history free-text (holds previous names/addresses/bank/NINO): redact values
    _exec_if_cols(con, db, "client_history", {"from_data": "NULL", "to_data": "NULL"})
    _exec_if_cols(con, db, "account_history", {"from_data": "NULL", "to_data": "NULL"})

    # messages / notifications free text (not on the app read-path): blank payloads
    _exec_if_cols(con, db, "messages", {"payload": """'{"body":"[redacted]"}'::json"""})
    _exec_if_cols(con, db, "notifications", {"queue_message": "NULL", "notes": "NULL"})

    # transactions: null the personal transfer/bank fields (keep counterparty
    # provider names, which are institutions not individuals)
    _exec_if_cols(
        con,
        db,
        "transactions",
        {
            "bank_ref": "NULL",
            "transfer_account_address": "NULL",
        },
    )

    con.execute("CHECKPOINT")
    con.close()


def _exec_if_cols(con, db: str, name: str, assignments: dict[str, str]) -> None:
    have = {
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='seccl' AND table_name=?",
            [name],
        ).fetchall()
    }
    sets = [f"{quote(c)}={expr}" for c, expr in assignments.items() if c in have]
    if sets:
        con.execute(f'UPDATE "{db}"."seccl"."{name}" SET {", ".join(sets)}')


def _scrub_eow(con, db: str) -> None:
    tp = f'"{db}"."seccl"."client_products"'
    have = {
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='seccl' AND table_name='client_products'"
        ).fetchall()
    }
    if "expression_of_wish_nominations" not in have:
        return
    rows = con.execute(
        f"SELECT id, cast(expression_of_wish_nominations as varchar) FROM {tp} "
        "WHERE expression_of_wish_nominations IS NOT NULL"
    ).fetchall()
    for pid, raw in rows:
        try:
            noms = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            noms = []
        for i, nom in enumerate(noms or []):
            s = _seed("eow", f"{pid}:{i}")
            first, surname = _pick(FIRST_NAMES, s, 4), _pick(SURNAMES, s, 8)
            if "firstName" in nom:
                nom["firstName"] = first
            if "surname" in nom:
                nom["surname"] = surname
            if "title" in nom:
                nom["title"] = _pick(TITLES, s, 1)
            if "name" in nom:
                nom["name"] = f"{first} {surname}"
        con.execute(
            f"UPDATE {tp} SET expression_of_wish_nominations=?::json WHERE id=?",
            [json.dumps(noms), pid],
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="docs/seccl.duckdb")
    ap.add_argument("--out", default="docs/seccl.duckdb.anon")
    ap.add_argument("--clients", type=int, default=10)
    ap.add_argument(
        "--history-months",
        type=int,
        default=None,
        help="trim the big daily tables to the most recent N months "
        "(default: keep full ~3-year history)",
    )
    args = ap.parse_args()
    build(args.source, args.out, args.clients, args.history_months)


if __name__ == "__main__":
    main()
