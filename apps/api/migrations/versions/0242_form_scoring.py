"""Score a form, and keep the result beside the answers.

Revision ID: 0242_form_scoring
Revises: 0241_seeded_form_lists
Create Date: 2026-10-07

``alembic_version.version_num`` is ``varchar(32)``; this id is 17 characters.

A risk questionnaire is still a form. The points and the band are not answers the
browser may post: ``documents.forms.scoring`` is the author's weights and bands,
and ``documents.form_responses.outputs`` is what the server computed on submit.

``ADD COLUMN IF NOT EXISTS`` because a database stamped ``0236_account_role_party``
already has both columns: that work was applied locally, then numbered 0242.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0242_form_scoring"
down_revision: str | None = "0241_seeded_form_lists"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE documents.forms ADD COLUMN IF NOT EXISTS scoring jsonb")
    op.execute(
        "COMMENT ON COLUMN documents.forms.scoring IS "
        "'Null when the form is not scored. Otherwise "
        '{"fields": [{"field_key", "weights": {option: points}}], '
        '"bands": [{"key", "label", "min", "max", "definition"}]}.\''
    )
    op.execute(
        "ALTER TABLE documents.form_responses "
        "ADD COLUMN IF NOT EXISTS outputs jsonb NOT NULL DEFAULT '{}'::jsonb"
    )
    op.execute(
        "COMMENT ON COLUMN documents.form_responses.outputs IS "
        "'Computed on submit from forms.scoring. "
        'Shape: {"band", "score", "label", "definition"}. '
        "Empty when the form is not scored. A posted answer whose key matches "
        "an output is overwritten with this value.'"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("ALTER TABLE documents.form_responses DROP COLUMN IF EXISTS outputs")
    op.execute("ALTER TABLE documents.forms DROP COLUMN IF EXISTS scoring")
