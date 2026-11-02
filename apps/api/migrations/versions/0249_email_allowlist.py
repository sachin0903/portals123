"""A saved outbound-email allowlist for dev and staging.

Revision ID: 0249_email_allowlist
Revises: 0248_illustration_sharing
Create Date: 2026-10-12

``EMAIL_ALLOWLIST`` is the default (``@grapheneplatforms.com`` when unset). The admin tenant
page can replace it for the whole environment; no row means keep using that default. An empty
array is a saved "mail nobody", distinct from the absent row.
"""

from alembic import op

revision: str = "0249_email_allowlist"
down_revision: str | None = "0248_illustration_sharing"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "CREATE TABLE auth.outbound_email_allowlist ("
        "  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton), "
        "  entries text[] NOT NULL, "
        "  updated_at timestamptz NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "COMMENT ON TABLE auth.outbound_email_allowlist IS "
        "'Who dev and staging may email. No row: EMAIL_ALLOWLIST. A row replaces it.'"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON auth.outbound_email_allowlist TO service_role"
    )
    # Estate-wide, no tenant_id, so a tenant policy has nothing to test. The operations endpoint
    # reads and writes it on service_role (which bypasses RLS). No policy and no grant to
    # authenticated is the deny-all posture the other singleton tables use.
    op.execute("ALTER TABLE auth.outbound_email_allowlist ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE auth.outbound_email_allowlist FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TABLE IF EXISTS auth.outbound_email_allowlist")
