"""transactions, accounts, links: read policies that evaluate their helpers once per statement.

Revision ID: 0230_transactions_rls_once
Revises: 0229_documents_files_policy
Create Date: 2026-10-05

docs/gotchas.md ("A LISTING THAT FEELS SLOW IS USUALLY RLS EVALUATING ITS HELPERS ONCE PER ROW")
measured this on platform.transactions on 22 Sep and named the fix; the listing work that followed
(#190) changed the query and left the policies alone. A bare `auth.tenant_lineage()` /
`auth.account_scope()` / `auth.contact_scope()` / `auth.can_read_all()` in a USING clause runs for
every row the scan touches, so `GET /crm/transactions` paid ~1.07s to filter 8,141 rows before the
first join. Each is wrapped in a scalar subquery, which Postgres evaluates ONCE per statement as an
InitPlan. The `::integer[]` cast is required: `= ANY ((SELECT ...))` without it parses as the
set-membership form and fails with `operator does not exist: integer = integer[]`.

Nothing else changes: same arms, same columns, same OR/AND structure, so the rows each principal
can read are identical — tests/test_rls_crm.py's `test_wrapped_policies_admit_the_rows_the_old_ones_did`
checks that row for row, per persona, against the old expressions. Measured with the listing's own
per-account custodian change: 2.46s -> 0.06s for a 25-row page of 8,141 transactions.

Only the three tables that listing reads per row. The other ~130 policies with the same shape
(the gotcha has the query that finds them) are a separate, estate-wide change.
"""

from alembic import op

revision: str = "0230_transactions_rls_once"
down_revision: str | None = "0229_documents_files_policy"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        ALTER POLICY transactions_read ON platform.transactions USING (
            ((SELECT auth.can_read_all())
                AND tenant_id = ANY ((SELECT auth.tenant_lineage())::integer[]))
            OR account_id = ANY ((SELECT auth.account_scope())::integer[])
            OR contact_id = ANY ((SELECT auth.contact_scope())::integer[])
        )
        """
    )
    op.execute(
        """
        ALTER POLICY accounts_read ON platform.accounts USING (
            ((SELECT auth.can_read_all())
                AND tenant_id = ANY ((SELECT auth.tenant_lineage())::integer[]))
            OR id = ANY ((SELECT auth.account_scope())::integer[])
            OR contact_id = ANY ((SELECT auth.contact_scope())::integer[])
        )
        """
    )
    op.execute(
        """
        ALTER POLICY links_read ON platform.links USING (
            ((SELECT auth.can_read_all())
                AND tenant_id = ANY ((SELECT auth.tenant_lineage())::integer[]))
            OR (entity_type = 'account'
                AND entity_id = ANY ((SELECT auth.account_scope())::integer[]))
            OR (entity_type = 'contact'
                AND entity_id = ANY ((SELECT auth.contact_scope())::integer[]))
        )
        """
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        ALTER POLICY transactions_read ON platform.transactions USING (
            (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
            OR account_id = ANY (auth.account_scope())
            OR contact_id = ANY (auth.contact_scope())
        )
        """
    )
    op.execute(
        """
        ALTER POLICY accounts_read ON platform.accounts USING (
            (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
            OR id = ANY (auth.account_scope())
            OR contact_id = ANY (auth.contact_scope())
        )
        """
    )
    op.execute(
        """
        ALTER POLICY links_read ON platform.links USING (
            (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
            OR (entity_type = 'account' AND entity_id = ANY (auth.account_scope()))
            OR (entity_type = 'contact' AND entity_id = ANY (auth.contact_scope()))
        )
        """
    )
