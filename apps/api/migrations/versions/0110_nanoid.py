"""nanoid() in the database -- one shape for a uid, whoever mints it

Revision ID: 0110_nanoid
Revises: 0109_account_role_attorney_other
Create Date: 2026-08-25

`app/ids.py` mints a uid as nanoid(12) over the 62 alphanumerics (nanoid's default set minus
`_` and `-`, neither of which is safe in the SQL identifier a tenant uid becomes). Nothing but the
application could, so every other writer invented its own expression and three shapes were in use.
This installs the function so `nanoid(12)` is the answer everywhere; `0113_uid_defaults` then makes
it every uid column's DEFAULT. Vendored from viascom/nanoid-postgres (Apache 2.0).
"""

from pathlib import Path

from alembic import op

revision: str = "0110_nanoid"
# RE-PARENTED onto main's `0110_account_fee_packages`, not `0109`. Both were written against 0109
# while this branch was in flight, so the chain forked and `alembic upgrade head` failed with
# "Multiple head revisions are present" -- which surfaces as every DB-backed test erroring, not as
# a migration problem. The duplicate `0110_` FILE prefix is left alone: alembic keys on the
# revision string, not the filename, and the branch stacked on this one replaces all of these with
# the three-file baseline, so renumbering six files would be churn with a delete already queued.
down_revision: str | None = "0110_account_fee_packages"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0110_nanoid.sql")


def downgrade() -> None:
    # Only the functions go. pgcrypto stays: it predates this migration (the merge procedures have
    # used gen_random_bytes since 0065), so dropping it here would take a dependency with it.
    # RESTRICT rather than CASCADE, deliberately: if a column DEFAULT still calls nanoid, the drop
    # must fail rather than silently strip the defaults off every uid column in the database.
    op.get_bind().exec_driver_sql("DROP FUNCTION IF EXISTS public.nanoid(int, text, float, text)")
    op.get_bind().exec_driver_sql(
        "DROP FUNCTION IF EXISTS public.nanoid_optimized(int, text, int, int)"
    )
