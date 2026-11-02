-- ============================================================================
-- 0002_tenant_storage -- the per-tenant object-storage registry (docs/storage.md).
--
-- Creates the `storage` schema -- the object-storage plane. (The `integrations` schema
-- stays connectivity-only per data-architecture Decision 7 and arrives with the
-- platform/crm migration; the physical file registry -- storage.files -- lands here
-- later with the documents work.) Holding:
--
--   * storage.tenants -- one row per tenant: WHERE that tenant's bucket
--     is (kind/endpoint/bucket), its public branding base URL (set only once the
--     files/tenant/* anonymous-read policy is applied), the tenant-scoped service
--     user's access key id (nullable -- minting it is best-effort, docs/storage.md §7),
--     and the provisioning status.
--   * storage.tenant_secrets -- the service user's secret access key,
--     Fernet-encrypted (app/crypto.py, SECRETS_MASTER_KEY). service_role-ONLY, exactly
--     like auth.environment_secrets: no authenticated grant or policy, so the request
--     role cannot read even the ciphertext.
--
-- Buckets themselves are minted by the API at tenant creation (app/tenant_storage.py);
-- this registry is the source of truth the request path resolves buckets from.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS storage;
GRANT USAGE ON SCHEMA storage TO anon, authenticated, service_role;

-- ============================================================================
-- storage.tenants -- non-secret registry row. Keys stored elsewhere
-- (e.g. the future documents.files.storage_key) are RELATIVE to this bucket.
-- ============================================================================
CREATE TABLE storage.tenants (
  tenant_id int PRIMARY KEY REFERENCES auth.tenants(id) ON DELETE CASCADE,
  kind text NOT NULL DEFAULT 'rustfs' CHECK (kind IN ('rustfs', 's3', 'minio')),
  endpoint text NOT NULL,             -- S3 API endpoint ('' = plain AWS S3)
  region text,
  bucket text NOT NULL UNIQUE,        -- {S3_TENANT_BUCKET_PREFIX}{uid hex}, e.g. t-ab12cd34ef567890
  -- Public base for branding URLs (files/tenant/*). Set ONLY after the anonymous-read
  -- prefix policy has been applied successfully; NULL => branding falls back to the
  -- legacy shared bucket (docs/storage.md §8).
  public_base_url text,
  -- Tenant-scoped service user (bucket-scoped policy). NULL when the backend cannot
  -- mint one programmatically (best-effort -- RustFS IAM is young; docs/storage.md §7).
  access_key_id text,
  status text NOT NULL DEFAULT 'provisioning'
    CHECK (status IN ('provisioning', 'active', 'suspended')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER set_updated_at BEFORE UPDATE ON storage.tenants
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- A firm may read WHERE its data lives (its own row); all writes are service_role only
-- (provisioning runs in the admin/service context).
ALTER TABLE storage.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE storage.tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY storage_tenants_read ON storage.tenants FOR SELECT TO authenticated
  USING (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT ON storage.tenants TO authenticated;
GRANT ALL ON storage.tenants TO service_role;

-- ============================================================================
-- storage.tenant_secrets -- service_role ONLY (mirrors
-- auth.environment_secrets): no authenticated grant or policy exists, so even the
-- ciphertext is unreadable on the request path.
-- ============================================================================
CREATE TABLE storage.tenant_secrets (
  tenant_id int PRIMARY KEY
    REFERENCES storage.tenants(tenant_id) ON DELETE CASCADE,
  secret_access_key_ct text,          -- Fernet ciphertext of the service user's secret key
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER set_updated_at BEFORE UPDATE ON storage.tenant_secrets
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE storage.tenant_secrets ENABLE ROW LEVEL SECURITY;
ALTER TABLE storage.tenant_secrets FORCE ROW LEVEL SECURITY;
GRANT ALL ON storage.tenant_secrets TO service_role;

-- ============================================================================
-- Registry changes are auditable (bucket/status/credential rotation). The secrets
-- table is audited too (out-of-band writes must be visible), so audit.record()'s
-- redaction list gains `secret_access_key_ct` -- ciphertext stays out of the trail
-- on principle, like the WorkOS *_ct columns. This re-issues the 0001 function
-- verbatim with the one extra array element (migrations evolve shared functions;
-- the SQL file is the reviewable artifact).
-- ============================================================================
CREATE OR REPLACE FUNCTION audit.record() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = 'audit', 'auth', 'pg_catalog' AS $fn$
DECLARE
  v_new     jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END;
  v_old     jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END;
  v_subject jsonb := COALESCE(v_new, v_old);
  -- never copy secrets / opaque pointers into the trail (key_hash is a hash, not the
  -- secret, but it stays out of logs on principle; credentials_ref points at a vault).
  v_redact  text[] := ARRAY['key_hash', 'credentials_ref', 'workos_api_key_ct', 'cookie_password_ct', 'secret_access_key_ct'];
  v_entity  bigint;
  v_tenant  int;
  v_detail  jsonb;
  k         text;
BEGIN
  -- entity_id only when the PK is integer-shaped; uuid/composite PKs live in detail.
  BEGIN v_entity := (v_subject ->> 'id')::bigint; EXCEPTION WHEN others THEN v_entity := NULL; END;

  -- Stamp the AFFECTED row's tenant (correct for cross-tenant admin writes); fall back
  -- to the actor's tenant for tables that carry no tenant_id (e.g. auth.*).
  BEGIN
    v_tenant := COALESCE(NULLIF(v_subject ->> 'tenant_id', '')::int, auth.tenant_id());
  EXCEPTION WHEN others THEN v_tenant := auth.tenant_id();
  END;

  v_new := v_new - v_redact;
  v_old := v_old - v_redact;

  IF TG_OP = 'UPDATE' THEN
    v_detail := '{}'::jsonb;   -- only the columns that actually changed (redacted ones excluded)
    FOR k IN SELECT jsonb_object_keys(v_new) LOOP
      IF v_new -> k IS DISTINCT FROM v_old -> k THEN
        v_detail := v_detail || jsonb_build_object(k, jsonb_build_object('old', v_old -> k, 'new', v_new -> k));
      END IF;
    END LOOP;
    v_detail := jsonb_build_object('id', v_subject -> 'id', 'changed', v_detail);
  ELSIF TG_OP = 'INSERT' THEN
    v_detail := jsonb_build_object('id', v_subject -> 'id', 'row', v_new);
  ELSE
    v_detail := jsonb_build_object('id', v_subject -> 'id', 'row', v_old);
  END IF;

  INSERT INTO audit.log (tenant_id, user_id, action, entity_type, entity_id, detail)
  VALUES (
    v_tenant,
    auth.uid(),
    TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME || '.' || lower(TG_OP),
    TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
    v_entity,
    v_detail
  );
  RETURN NULL;  -- AFTER trigger: return value is ignored
END;
$fn$;

SELECT audit.attach(ARRAY[
  'storage.tenants',
  'storage.tenant_secrets'
]);
