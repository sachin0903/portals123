"""Read-only check of the database objects the engine needs.

Never creates or alters anything; the migrations (scripts/) create what is missing.
"""

import logging

logger = logging.getLogger(__name__)

# {"schema.table": ["column", ...]}
Requirements = dict[str, list[str]]


def missing_columns(cur, required: Requirements) -> dict[str, list[str]]:
    """Return {table: [missing columns]} for what is absent; a missing table is reported as ["*"].

    Uses pg_catalog, so the answer does not depend on which privileges the connected role has.
    """
    missing: dict[str, list[str]] = {}
    for table, columns in required.items():
        cur.execute(
            """
            SELECT a.attname FROM pg_attribute a
            WHERE a.attrelid = to_regclass(%s) AND a.attnum > 0 AND NOT a.attisdropped
            """,
            (table,),
        )
        present = {row[0] for row in cur.fetchall()}
        if not present:
            missing[table] = ["*"]
        else:
            gaps = [c for c in columns if c not in present]
            if gaps:
                missing[table] = gaps
    return missing


def merge(*requirement_sets: Requirements) -> Requirements:
    merged: Requirements = {}
    for requirements in requirement_sets:
        for table, columns in requirements.items():
            seen = merged.setdefault(table, [])
            seen.extend(c for c in columns if c not in seen)
    return merged


def describe(missing: dict[str, list[str]]) -> list[str]:
    """Flatten to ["schema.table", "schema.table.column", ...]."""
    names = []
    for table, columns in missing.items():
        names.extend([table] if columns == ["*"] else [f"{table}.{c}" for c in columns])
    return names
