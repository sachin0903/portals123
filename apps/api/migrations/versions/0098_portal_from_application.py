"""portal-via-application (P3): classify the portal claim from the WorkOS application

Revision ID: 0098_portal_from_application
Revises: 0093_portal_applications
Create Date: 2026-08-16

Step P3 of moving the portal boundary from WorkOS organisations to WorkOS applications
(docs/auth-applications-migration.md, path A1). auth.resolve_claims gains an optional third argument
p_client_id (the token's application client id) and classifies the `portal` claim from it, falling
back to the existing org-based classification. Behaviour-preserving until a firm's per-application
client-id columns (0093) are populated. DDL in the sibling SQL file; applied as the elevated migrator
role. The downgrade restores the 0035 two-argument form verbatim.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0098_portal_from_application"
down_revision: str | None = "0097_portal_applications"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0098_portal_from_application.sql")


# The 0035 two-argument resolve_claims, inlined so the downgrade restores ONLY the function (re-running
# 0035_link_tenant_integrity.sql would also re-execute its PART B ADD CONSTRAINTs, which already exist).
_DOWN = """
DROP FUNCTION IF EXISTS auth.resolve_claims(text, text, text);

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
  v_claims      jsonb := auth.jwt();
BEGIN
  IF v_claims <> '{}'::jsonb AND NOT auth.is_system() AND NOT (
       p_workos_user_id IS NOT NULL
       AND p_workos_user_id = v_claims ->> 'sub'
       AND (p_workos_org_id IS NULL OR p_workos_org_id = v_claims ->> 'org_id')
     ) THEN
    RAISE EXCEPTION
      'auth.resolve_claims: a session with published claims may only resolve its own identity'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

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

  IF v_user_uuid IS NOT NULL AND v_tenant_id IS NOT NULL THEN
    IF v_portal = 'client' THEN
      SELECT COALESCE(array_agg(DISTINCT contact_id), '{}'::int[]) INTO v_contact_ids
        FROM crm.contact_users WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id;
    ELSE
      SELECT COALESCE(array_agg(DISTINCT cid), '{}'::int[]) INTO v_contact_ids FROM (
        SELECT contact_id AS cid FROM crm.contact_users
          WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id
        UNION
        SELECT contact_id AS cid FROM crm.client_advisers
          WHERE adviser_id = v_user_uuid AND tenant_id = v_tenant_id
      ) s;
    END IF;
  ELSE
    v_contact_ids := '{}'::int[];
  END IF;

  IF array_length(v_contact_ids, 1) IS NOT NULL THEN
    SELECT COALESCE(array_agg(DISTINCT aid), '{}'::int[]) INTO v_account_ids FROM (
      SELECT id AS aid FROM platform.accounts
        WHERE contact_id = ANY(v_contact_ids) AND tenant_id = v_tenant_id
      UNION
      SELECT account_id AS aid FROM crm.account_contacts
        WHERE contact_id = ANY(v_contact_ids) AND account_id IS NOT NULL
          AND tenant_id = v_tenant_id
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


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
