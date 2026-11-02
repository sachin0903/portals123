"""aml rescreen on change: any change to the screened details screens the client again

Revision ID: 0253_aml_rescreen_on_change
Revises: 0252_identity_photos_by_check
Create Date: 2026-10-13

``alembic_version.version_num`` is ``varchar(32)``; this id is 27 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0253_aml_rescreen_on_change"
down_revision: str | None = "0252_identity_photos_by_check"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0253_aml_rescreen_on_change.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # 0223's function: only a clear screen goes stale, and its pass is left as it is.
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        CREATE OR REPLACE FUNCTION compliance.mark_screen_stale() RETURNS trigger
          LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $fn$
        BEGIN
          WITH marked AS (
            UPDATE compliance.checks ch
               SET stale_at = now()
              FROM compliance.clients cl
             WHERE cl.id = ch.client_id
               AND cl.contact_id = NEW.id
               AND cl.tenant_id = NEW.tenant_id
               AND ch.kind = 'aml'
               AND ch.origin = 'portal'
               AND ch.status = 'clear'
               AND ch.stale_at IS NULL
               AND NOT EXISTS (
                 SELECT 1 FROM compliance.checks later
                  WHERE later.client_id = ch.client_id
                    AND later.kind = 'aml'
                    AND later.id > ch.id
               )
            RETURNING ch.uid
          )
          INSERT INTO flows.outbox (tenant_id, event_type, payload)
          SELECT NEW.tenant_id,
                 'aml.rescreen.requested',
                 jsonb_build_object('contact_id', NEW.id, 'check_uid', marked.uid)
            FROM marked;
          RETURN NULL;
        END;
        $fn$;
        """
    )
