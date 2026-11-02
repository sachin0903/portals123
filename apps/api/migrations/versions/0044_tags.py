"""tags — contact/case labels, plus the AML hand-off's two never-legal CHECK values

Revision ID: 0044_tags
Revises: 0043_automations
Create Date: 2026-08-03

Flows v3 Phase A: the shared substrate automations compose through (apply_tag/remove_tag act on
it, conditions read it as contact.tags/case.tags, and the tag.applied trigger fires off it), plus
the widening that lets app/aml/screening.py's operator hand-off actually insert. Expand only. DDL
in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0044_tags"
down_revision: str | None = "0043_automations"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0044_tags.sql")


def downgrade() -> None:
    # The CHECKs narrow back to their 0043 state, which means deleting the rows the widening
    # allowed — safe only because a downgrade implies the features that wrote them are going too.
    op.get_bind().exec_driver_sql(
        "DROP TABLE IF EXISTS flows.case_tags; "
        "DROP TABLE IF EXISTS crm.contact_tags; "
        "DELETE FROM tasks.tasks WHERE source_kind = 'aml' OR category = 'aml'; "
        "ALTER TABLE tasks.tasks DROP CONSTRAINT IF EXISTS tasks_source_kind_check; "
        "ALTER TABLE tasks.tasks ADD CONSTRAINT tasks_source_kind_check "
        "  CHECK (source_kind IN ('manual', 'flow', 'document', 'system', 'automation')); "
        "ALTER TABLE tasks.tasks DROP CONSTRAINT IF EXISTS tasks_category_check; "
        "ALTER TABLE tasks.tasks ADD CONSTRAINT tasks_category_check "
        "  CHECK (category IN ('review', 'acknowledge', 'upload', 'follow_up', 'generic'))"
    )
