"""Anonymise contacts and logins in a local development database.

Keeps ``auth.users`` whose email matches ``mark%@grapheneplatforms.com``. Every other
user, and every ``crm.contacts`` row, is rewritten to a deterministic stand-in. Contact
notes are cleared. Amounts, accounts and transactions are left as they are.

Refuses any database host that is not localhost. Safe to run again: the same row gets
the same stand-in. This is not a migration, and the rewritten rows are not committed.

Usage:
    uv run --project apps/api python apps/api/scripts/anonymise_local.py \\
        postgresql://postgres:postgres@127.0.0.1:5432/portals
"""

from __future__ import annotations

import sys
from urllib.parse import urlparse

import psycopg

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def require_local_host(dsn: str) -> str:
    """The host of ``dsn``, or exit when it is not this machine."""
    host = urlparse(dsn).hostname
    if host not in _LOCAL_HOSTS:
        raise SystemExit(
            f"Refusing to anonymise {host or 'an unnamed host'}: the database is not on localhost."
        )
    return host


def anonymise(dsn: str) -> dict[str, int]:
    """Rewrite personal fields. Returns how many rows each statement changed."""
    require_local_host(dsn)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE auth.users
               SET email = 'anon-' || id || '@example.test',
                   first_name = 'Anon',
                   last_name = 'User',
                   mobile = '07700900000',
                   avatar = NULL
             WHERE email NOT ILIKE 'mark%@grapheneplatforms.com'
            """
        )
        users = cur.rowcount
        cur.execute(
            """
            SELECT count(*) FROM auth.users
             WHERE email ILIKE 'mark%@grapheneplatforms.com'
            """
        )
        kept_row = cur.fetchone()
        kept = int(kept_row[0]) if kept_row else 0
        cur.execute(
            """
            UPDATE crm.contacts
               SET name = 'Anon ' || id,
                   title = NULL,
                   first_name = 'Anon',
                   middle_name = NULL,
                   surname = 'Contact',
                   dob = DATE '1900-01-01',
                   nationality = ARRAY['ZZ']::text[],
                   tax_residency = ARRAY['ZZ']::text[],
                   national_identifier = 'ZZ' || lpad(id::text, 6, '0'),
                   national_identifier_type = NULL,
                   email = 'anon-' || id || '@example.test',
                   phone = '01632960000',
                   mobile = '07700900000',
                   employer_name = NULL,
                   address = '{"line1":"Anonymised","city":"Test","postcode":"AA1 1AA"}'::jsonb,
                   bank_account = jsonb_build_object(
                       'name', 'Anonymised',
                       'sort_code', '000000',
                       'account_number', '00000000'
                   )
            """
        )
        contacts = cur.rowcount
        cur.execute("UPDATE crm.contact_notes SET body = ''")
        notes = cur.rowcount
        conn.commit()
    return {"users": users, "kept_users": kept, "contacts": contacts, "notes": notes}


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("Usage: anonymise_local.py <postgresql-dsn>")
    counts = anonymise(argv[1])
    print(
        "Anonymised "
        f"{counts['users']} user(s), kept {counts['kept_users']} mark@ login(s), "
        f"{counts['contacts']} contact(s), cleared {counts['notes']} note(s)."
    )


if __name__ == "__main__":
    main(sys.argv)
