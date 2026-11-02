"""Keep the original seeded-form-lists revision id locatable

Revision ID: 0237_seeded_form_lists
Revises: 0234_txn_public_id
Create Date: 2026-10-08

NO DDL. The fact-find UPDATE lives in `0241_seeded_form_lists`; this file exists only so
Alembic can still resolve the id this revision was first applied under.

The work was written as 0235–0237, applied to at least one real database, then renamed to
0239–0241 when 0235–0238 (identity photos, phone links, shared-app portal, named portal)
had already taken those numbers on main. Alembic records the id, not the filename, so
`upgrade head` on those databases fails with `Can't locate revision identified by
'0237_seeded_form_lists'` until this id is back in the graph.

Do not stamp that database to 0241: 0235_identity_photos / 0236_identity_phone_links /
0237_shared_app_portal / 0238_named_portal have not run on it. `0244_merge_seeded_form_lists`
joins this stub to the linear 0243 line. 0240 skips its rename when `mobile` is already there.
"""

revision: str = "0237_seeded_form_lists"
down_revision: str | None = "0234_txn_public_id"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — 0241 carries the UPDATE, including for databases that skipped this id."""


def downgrade() -> None:
    """Nothing to undo. The form-field rewrite belongs to 0241."""
