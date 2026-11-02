"""Keep the integrations-tenant revision id locatable

Revision ID: 0246_integrations_tenant
Revises: 0244_merge_account_party
Create Date: 2026-10-11

NO DDL. The ``integrations`` changes this id was applied under live in the
``sei_reports`` repo's own scripts, not here; this file exists only so Alembic
can still resolve the id the dev database was stamped with.

That database was migrated on 9 Oct from a copy of this revision that was never
committed. ``0245`` and ``0246_contact_name_from_parts`` have not run on it.
``0247_merge_integrations`` joins this stub to that line. Do not re-point
0245's parent onto this id: 0245 has already been applied on the linear path.

``alembic_version.version_num`` is ``varchar(32)``; this id is 24 characters.
"""

revision: str = "0246_integrations_tenant"
down_revision: str | None = "0244_merge_account_party"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — the objects this id created are already on the database that has it."""


def downgrade() -> None:
    """Nothing to undo. The integrations objects do not belong to this revision."""
