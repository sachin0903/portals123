"""crm: a person's name is their first, middle and surname, whoever writes the row; a name
re-screens only when ComplyCube reads it

Revision ID: 0246_contact_name_from_parts
Revises: 0245_one_adviser_per_client
Create Date: 2026-10-08

``alembic_version.version_num`` is ``varchar(32)``; this id is 28 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0246_contact_name_from_parts"
down_revision: str | None = "0245_one_adviser_per_client"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0246_contact_name_from_parts.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    """Drop the name trigger and put 0223's re-screen trigger back as 0223 made it."""
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP TRIGGER IF EXISTS contact_name_from_parts ON crm.contacts;
        DROP FUNCTION IF EXISTS crm.contact_name_from_parts();
        DROP TRIGGER IF EXISTS mark_screen_stale ON crm.contacts;
        CREATE TRIGGER mark_screen_stale
          AFTER UPDATE OF first_name, surname, name, dob, nationality ON crm.contacts
          FOR EACH ROW
          WHEN (
            OLD.first_name IS DISTINCT FROM NEW.first_name
            OR OLD.surname IS DISTINCT FROM NEW.surname
            OR OLD.name IS DISTINCT FROM NEW.name
            OR OLD.dob IS DISTINCT FROM NEW.dob
            OR OLD.nationality IS DISTINCT FROM NEW.nationality
          )
          EXECUTE FUNCTION compliance.mark_screen_stale();
        """
    )
