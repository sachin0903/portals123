"""contain auth.resolve_claims; make every client/account link tenant-local by construction

Revision ID: 0035_link_tenant_integrity
Revises: 0034_is_system_permission_only
Create Date: 2026-07-28

Part A. auth.resolve_claims(text, text) is the largest SECURITY DEFINER / BYPASSRLS accessor in the
schema 0030 hardened, and it was the one left unguarded: given a WorkOS user id + org id it returns
another principal's whole pre-resolved scope (tenant lineage, contact_ids, account_ids), and
`authenticated` holds EXECUTE because db.py mints through it. A client-portal session of one firm
could read back another firm's adviser's entire book — and two narrower spellings (a NULL user id
with a foreign org, or one's own id with a foreign org) reached the org->tenant resolution 0030
guarded elsewhere. The function now guards both arguments: no published claims means the request
layer is minting (db.py always calls it before set_config), published claims mean you may only ask
about yourself, and auth.is_system() keeps the ops view. Its scope lookups are also ANDed with the
resolved tenant so the claim set can never leave the login's own firm.

Part B. A row's client/account link could point outside the row's own tenant: documents.attachments /
platform.instructions WITH CHECK leaves contact_id unconstrained on the can_read_all() branch while
the read policies' client branch has no tenant conjunct, so a read-all staffer at Firm A could file a
Firm A row stamped with Firm B's contact and Firm B's client would read it. The same shape sits on
documents.document_contacts (a cross-firm DOCUMENT read), documents.requests,
documents.form_responses and tasks.tasks, and none of it binds service_role at all. Fixed once as the
invariant it is: composite (tenant_id, link_id) foreign keys onto crm.contacts (tenant_id, id) /
platform.accounts (tenant_id, id). RI checks bypass RLS and run as the table owner, so the constraint
is the same for every role and there is no spelling to get round.

DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035_link_tenant_integrity"
down_revision: str | None = "0034_is_system_permission_only"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Drops the composite FKs (the single-column originals were never removed, so referential integrity
# is unchanged) and restores 0027_tasks.sql's resolve_claims body verbatim -- i.e. deliberately
# reinstating the unguarded oracle, which is what "downgrade" means here.
_DOWN = """
ALTER TABLE flows.case_steps        DROP CONSTRAINT case_steps_account_tenant_fkey;
ALTER TABLE platform.instructions   DROP CONSTRAINT instructions_account_tenant_fkey;
ALTER TABLE platform.transactions   DROP CONSTRAINT transactions_account_tenant_fkey;
ALTER TABLE crm.account_contacts    DROP CONSTRAINT account_contacts_account_tenant_fkey;

ALTER TABLE tasks.tasks             DROP CONSTRAINT tasks_assignee_contact_tenant_fkey;
ALTER TABLE flows.cases             DROP CONSTRAINT cases_subject_contact_tenant_fkey;
ALTER TABLE documents.form_responses DROP CONSTRAINT form_responses_contact_tenant_fkey;
ALTER TABLE documents.requests      DROP CONSTRAINT requests_contact_tenant_fkey;
ALTER TABLE documents.documents     DROP CONSTRAINT documents_intended_contact_tenant_fkey;
ALTER TABLE documents.document_contacts DROP CONSTRAINT document_contacts_contact_tenant_fkey;
ALTER TABLE platform.instructions   DROP CONSTRAINT instructions_contact_tenant_fkey;
ALTER TABLE documents.attachments   DROP CONSTRAINT attachments_contact_tenant_fkey;
ALTER TABLE platform.transactions   DROP CONSTRAINT transactions_contact_tenant_fkey;
ALTER TABLE platform.accounts       DROP CONSTRAINT accounts_contact_tenant_fkey;
ALTER TABLE crm.account_contacts    DROP CONSTRAINT account_contacts_contact_tenant_fkey;
ALTER TABLE crm.kyc_checks          DROP CONSTRAINT kyc_checks_contact_tenant_fkey;
ALTER TABLE crm.relationships       DROP CONSTRAINT relationships_to_contact_tenant_fkey;
ALTER TABLE crm.relationships       DROP CONSTRAINT relationships_from_contact_tenant_fkey;
ALTER TABLE crm.contact_notes       DROP CONSTRAINT contact_notes_contact_tenant_fkey;
ALTER TABLE crm.client_advisers     DROP CONSTRAINT client_advisers_contact_tenant_fkey;
ALTER TABLE crm.contact_users       DROP CONSTRAINT contact_users_contact_tenant_fkey;

ALTER TABLE platform.accounts       DROP CONSTRAINT accounts_tenant_id_key;
ALTER TABLE crm.contacts            DROP CONSTRAINT contacts_tenant_id_key;

CREATE OR REPLACE FUNCTION auth.resolve_claims(p_workos_user_id text, p_workos_org_id text)
  RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = 'auth' AS $fn$
DECLARE
  v_user_uuid   uuid;
  v_tenant_id   int;
  v_tenant_ids  int[];
  v_portal      text;
  v_contact_ids int[];
  v_account_ids int[];
  v_role_ids    int[];
BEGIN
  SELECT id, tenant_id INTO v_user_uuid, v_tenant_id FROM auth.users
    WHERE workos_user_id = p_workos_user_id AND deleted_at IS NULL;

  IF v_tenant_id IS NULL THEN
    SELECT id INTO v_tenant_id FROM auth.tenants
      WHERE workos_organisation_id = p_workos_org_id;
  END IF;

  IF v_tenant_id IS NOT NULL THEN
    SELECT COALESCE(array_agg(id), '{}'::int[]) INTO v_tenant_ids
      FROM auth.tenants WHERE v_tenant_id = ANY(lineage);
  ELSE
    v_tenant_ids := '{}'::int[];
  END IF;

  SELECT CASE
           WHEN p_workos_org_id = te.client_portal_org_id THEN 'client'
           WHEN p_workos_org_id = te.operations_org_id     THEN 'operations'
           ELSE 'platform'
         END
    INTO v_portal
    FROM auth.tenant_environments te
    WHERE te.tenant_id = v_tenant_id
    ORDER BY te.id
    LIMIT 1;
  v_portal := COALESCE(v_portal, 'platform');

  IF v_user_uuid IS NOT NULL THEN
    IF v_portal = 'client' THEN
      SELECT COALESCE(array_agg(DISTINCT contact_id), '{}'::int[]) INTO v_contact_ids
        FROM crm.contact_users WHERE user_id = v_user_uuid;
    ELSE
      SELECT COALESCE(array_agg(DISTINCT cid), '{}'::int[]) INTO v_contact_ids FROM (
        SELECT contact_id AS cid FROM crm.contact_users   WHERE user_id = v_user_uuid
        UNION
        SELECT contact_id AS cid FROM crm.client_advisers WHERE adviser_id = v_user_uuid
      ) s;
    END IF;
  ELSE
    v_contact_ids := '{}'::int[];
  END IF;

  IF array_length(v_contact_ids, 1) IS NOT NULL THEN
    SELECT COALESCE(array_agg(DISTINCT aid), '{}'::int[]) INTO v_account_ids FROM (
      SELECT id AS aid FROM platform.accounts WHERE contact_id = ANY(v_contact_ids)
      UNION
      SELECT account_id AS aid FROM crm.account_contacts
        WHERE contact_id = ANY(v_contact_ids) AND account_id IS NOT NULL
    ) s;
  ELSE
    v_account_ids := '{}'::int[];
  END IF;

  IF v_user_uuid IS NOT NULL AND v_tenant_id IS NOT NULL THEN
    SELECT COALESCE(array_agg(DISTINCT role_id), '{}'::int[]) INTO v_role_ids
      FROM auth.organisation_memberships
      WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id AND portal = v_portal
        AND status = 'active' AND role_id IS NOT NULL;
  ELSE
    v_role_ids := '{}'::int[];
  END IF;

  RETURN jsonb_build_object(
    'user_uuid',   v_user_uuid,
    'tenant_id',   v_tenant_id,
    'tenant_ids',  to_jsonb(v_tenant_ids),
    'portal',      v_portal,
    'contact_ids', to_jsonb(v_contact_ids),
    'account_ids', to_jsonb(v_account_ids),
    'role_ids',    to_jsonb(v_role_ids)
  );
END;
$fn$;

ALTER FUNCTION auth.resolve_claims(text, text) OWNER TO service_role;
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0035_link_tenant_integrity.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
