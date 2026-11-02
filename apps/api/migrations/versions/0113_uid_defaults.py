"""every uid column defaults to nanoid(12), and the merge stops minting its own

Revision ID: 0113_uid_defaults
Revises: 0112_messaging_uids
Create Date: 2026-08-25

Sets `DEFAULT public.nanoid(12)` on all 41 uid columns -- every one but `auth.tenants.uid`, which
is a bucket name and a SQL schema name as well as a URL (see app/ids.py::tenant_uid) -- and
redefines `platform.merge_linked_entity` to omit the column so the database fills it, rather than
appending 16 hex characters of its own. A default is a catalogue change: no table is rewritten and
no existing row is touched.
"""

from pathlib import Path

from alembic import op

revision: str = "0113_uid_defaults"
down_revision: str | None = "0112_messaging_uids"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

#: Every uid column that gets the default -- the same list the SQL applies it to, repeated here
#: only so `downgrade` can lift it. `auth.tenants` is absent from both on purpose.
_TABLES: tuple[str, ...] = (
    "aml.hits",
    "aml.profiles",
    "aml.searches",
    "auth.api_keys",
    "auth.users",
    "crm.applications",
    "crm.contacts",
    "crm.kyc_checks",
    "documents.attachments",
    "documents.dataset_versions",
    "documents.datasets",
    "documents.documents",
    "documents.form_fields",
    "documents.form_responses",
    "documents.forms",
    "documents.request_items",
    "documents.requests",
    "documents.shares",
    "documents.template_versions",
    "documents.templates",
    "email.template_versions",
    "email.templates",
    "flows.automations",
    "flows.case_listeners",
    "flows.cases",
    "flows.executions",
    "flows.flow_versions",
    "flows.flows",
    "flows.timers",
    "messaging.attachments",
    "messaging.conversations",
    "messaging.groups",
    "notifications.notifications",
    "notifications.rule_versions",
    "notifications.rules",
    "platform.accounts",
    "platform.contributions",
    "platform.instructions",
    "platform.transaction_expectations",
    "platform.transfers",
    "tasks.tasks",
)


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0113_uid_defaults.sql")


def downgrade() -> None:
    # The defaults are lifted; the merge keeps the definition this migration gave it. Restoring the
    # hex-minting version would mean carrying a copy of 0070's body here to be un-run, and the
    # column it fills has a NOT NULL to satisfy either way -- so the honest downgrade is the one
    # that leaves the procedure alone and says so.
    for table in _TABLES:
        op.get_bind().exec_driver_sql(f"ALTER TABLE {table} ALTER COLUMN uid DROP DEFAULT")
