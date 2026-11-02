"""documents.files: a read policy the planner can answer by index, and the indexes it needs.

Revision ID: 0229_documents_files_policy
Revises: 0228_compliance_client_notes
Create Date: 2026-10-05

0006 gave `documents_files_read` a second arm — a file is readable when a document the caller can
read points at it — written as ONE EXISTS over a three-way OR:
`files.id IN (d.original_file_id, d.pdf_file_id, d.html_file_id)`. No index answers that, and
none of the three columns had one anyway, so the planner costs the arm as a scan of
`documents.documents` (through ITS policy) for every file row. `GET /documents` joins every file,
so its estimate grew with files x documents — 22M at a thousand documents — and spent ~1.4s in JIT
before `app/db.py::_jit_off`, and still ~1.3s at three thousand without it.

Same rule, three EXISTS, one per column, each over its own partial index. EXISTS(a OR b OR c) is
EXISTS(a) OR EXISTS(b) OR EXISTS(c), and each inner `documents.documents` read still goes through
that table's own policy, so who can read which file does not move. Measured at three thousand
documents with JIT off: ~1.3s -> ~0.5s, and the estimate falls from 184M to 0.8M.
`auth.tenant_lineage()` is wrapped in a scalar subquery (docs/gotchas.md, the RLS-per-row entry)
so it runs once per statement, not per row; the `::integer[]` cast is what lets `= ANY` read it
as an array.

The write policy (FOR ALL, so it is OR-ed into every SELECT too) gets the same wrapping and nothing
else.
"""

from alembic import op

revision: str = "0229_documents_files_policy"
down_revision: str | None = "0228_compliance_client_notes"
_ = (revision, down_revision)  # Alembic reads these names off the module

_FILE_COLUMNS = ("original_file_id", "pdf_file_id", "html_file_id")
_LINEAGE = "tenant_id = ANY ((SELECT auth.tenant_lineage())::integer[])"


def _index(column: str) -> str:
    return f"documents_{column}_idx"


def upgrade() -> None:
    """Build the three indexes online, then swap both policies' expressions in place."""
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        for column in _FILE_COLUMNS:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_index(column)} "
                f"ON documents.documents ({column}) WHERE {column} IS NOT NULL"
            )
        op.execute("SET lock_timeout = DEFAULT")

    # ALTER POLICY keeps each policy's name, command and roles; only the expressions change.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        ALTER POLICY documents_files_read ON documents.files USING (
            tenant_id = ANY ((SELECT auth.tenant_lineage())::integer[])
            OR EXISTS (SELECT 1 FROM documents.documents d
                        WHERE d.original_file_id = documents.files.id)
            OR EXISTS (SELECT 1 FROM documents.documents d
                        WHERE d.pdf_file_id = documents.files.id)
            OR EXISTS (SELECT 1 FROM documents.documents d
                        WHERE d.html_file_id = documents.files.id)
        )
        """
    )
    op.execute(
        f"ALTER POLICY documents_files_write ON documents.files "
        f"USING ({_LINEAGE}) WITH CHECK ({_LINEAGE})"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER POLICY documents_files_read ON documents.files USING ("
        "tenant_id = ANY (auth.tenant_lineage()) OR EXISTS ("
        "SELECT 1 FROM documents.documents d "
        "WHERE documents.files.id IN (d.original_file_id, d.pdf_file_id, d.html_file_id)))"
    )
    op.execute(
        "ALTER POLICY documents_files_write ON documents.files "
        "USING (tenant_id = ANY (auth.tenant_lineage())) "
        "WITH CHECK (tenant_id = ANY (auth.tenant_lineage()))"
    )
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        for column in _FILE_COLUMNS:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS documents.{_index(column)}")
        op.execute("SET lock_timeout = DEFAULT")
