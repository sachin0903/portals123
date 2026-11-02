"""documents -- backfill the 'subject' link for every document already filed under a client

Revision ID: 0105_document_subject_links
Revises: 0104_service_type_discretionary
Create Date: 2026-08-17

A client should see every document filed against them, without a staffer having to "send" it.
`intended_contact_id` (0021) has recorded who a document is FOR since it replaced the old
over-eager link, but no RLS policy reads it -- so a filed document stayed invisible to its own
subject. Auto-linking on filing fixes that going forward (`documents/service.py::link_subject`);
this backfills the documents already filed. Retroactive and deliberate: it grants clients sight of
their own documents. No policy is touched -- the client branch of `documents_read` matches on
`contact_id` with no role predicate, so a row is enough.

The reasoning and the role choice ('subject', never 'recipient' -- that value is read as evidence
of a send) live in the sibling SQL file; that file is the reviewable artifact and this script just
applies it. Runs as the elevated migrator role (migrations/env.py), which is NOT BYPASSRLS, so the
SQL elevates to service_role itself.
"""

from pathlib import Path

from alembic import op

revision: str = "0105_document_subject_links"
down_revision: str | None = "0104_service_type_discretionary"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0105_document_subject_links.sql")


def downgrade() -> None:
    # Remove only the links this backfill can account for: a 'subject' row whose contact IS the
    # document's own `intended_contact_id`. That predicate deliberately spares the pre-existing
    # illustration links (`illustrations._store_pdf` files a 'subject' row and sets no
    # `intended_contact_id`, so they cannot match) -- a downgrade must not revoke visibility this
    # migration never granted.
    #
    # It CANNOT distinguish a backfilled row from one the new filing paths have written since, and
    # does not try: on the way down the code that writes them is going too, so both are equally
    # "the auto-link", and leaving them would keep the very disclosure being reverted. Stated
    # rather than hidden.
    op.execute(
        """
        SET LOCAL ROLE service_role;
        DELETE FROM documents.document_contacts dc
         USING documents.documents d
         WHERE d.id = dc.document_id
           AND dc.role = 'subject'
           AND dc.contact_id = d.intended_contact_id;
        RESET ROLE;
        """
    )
