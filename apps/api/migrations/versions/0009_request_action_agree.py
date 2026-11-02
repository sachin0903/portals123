"""rename request-item action 'sign' -> 'agree'

Revision ID: 0009_request_action_agree
Revises: 0008_document_requests_v2
Create Date: 2026-07-10

Interim 'agree' is a confirm-only action (the client views the target document and ticks "I agree");
real signing comes later. A data + constraint migration, so the statements run individually (drop the
CHECK, remap the rows, re-add the CHECK) rather than as one SQL batch.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_request_action_agree"
down_revision: str | None = "0008_document_requests_v2"
_ = (revision, down_revision)  # Alembic reads these names off the module

_ACTIONS_NEW = "'upload', 'agree', 'acknowledge', 'complete_form'"
_ACTIONS_OLD = "'upload', 'sign', 'acknowledge', 'complete_form'"


def _remap(from_action: str, to_action: str, check_actions: str) -> None:
    # request_items has FORCE ROW LEVEL SECURITY with policies only TO authenticated, so the migrator
    # role's UPDATE would be filtered to zero rows. Toggle RLS off for the remap, then restore it.
    op.execute("ALTER TABLE documents.request_items DROP CONSTRAINT request_items_action_check")
    op.execute("ALTER TABLE documents.request_items DISABLE ROW LEVEL SECURITY")
    # from/to are fixed literals from this module, not user input.
    op.execute(
        f"UPDATE documents.request_items SET action = '{to_action}' WHERE action = '{from_action}'"  # noqa: S608
    )
    op.execute("ALTER TABLE documents.request_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents.request_items FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE documents.request_items ADD CONSTRAINT request_items_action_check "
        f"CHECK (action IN ({check_actions}))"
    )


def upgrade() -> None:
    _remap("sign", "agree", _ACTIONS_NEW)


def downgrade() -> None:
    _remap("agree", "sign", _ACTIONS_OLD)
