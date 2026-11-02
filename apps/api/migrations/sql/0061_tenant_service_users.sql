-- ============================================================================
-- 0061_tenant_service_users -- four scoped service users per tenant bucket.
--
-- 0027 minted ONE credential per tenant with `s3:*` on the whole bucket, and every
-- consumer shared it: the ETL writing parquet, the API writing documents, an export
-- reading them. One key, one blast radius, and no way to revoke the reader without
-- stopping the writer.
--
-- Four roles instead, each with the narrowest policy that lets its consumer work
-- (the literal documents live in app/object_store.py::service_user_policy):
--
--   base       s3:* on the bucket            -- provisioning/administrative use only
--   datalake   rw  datalake/*, files/platforms/*   -- the interlace pipeline
--   documents  rw  files/*                   -- the API's document + branding writes
--   readonly   ro  files/*                   -- exports, per-user read credentials
--
-- Keyed (tenant_id, role), so `storage.tenants.access_key_id` stays as it was and keeps
-- pointing at `base`: nothing that reads the old column has to change to land this, and
-- the backfill below moves the credential 0027 already minted into role 'base' rather
-- than re-minting it (re-minting would invalidate a key the lake is actively using).
--
-- The secrets table mirrors storage.tenant_secrets: service_role only, NO policy and NO
-- grant to `authenticated` -- withholding the privilege beats an always-false policy.
-- `secret_access_key_ct` keeps the column name 0027 added to audit.record()'s redaction
-- list, so the ciphertext stays out of the audit trail without re-issuing that function.
-- ============================================================================

CREATE TABLE storage.tenant_service_users (
  tenant_id int NOT NULL REFERENCES storage.tenants(tenant_id) ON DELETE CASCADE,
  -- A CHECK rather than an enum: psycopg has no loader for a custom enum ARRAY, and a
  -- `role text` reads back as text everywhere (0002's product_type[] cost us a whole
  -- listing page learning that). Widening the set is a one-line migration either way.
  role text NOT NULL CHECK (role IN ('base', 'datalake', 'documents', 'readonly')),
  -- Deterministic (`svc-{bucket}-{role}`), so a repair can ask the store whether THIS
  -- principal exists instead of trusting the row -- the gap that made provisioning
  -- report `service_user: ok` for a credential the store had long since lost.
  access_key_id text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, role)
);

CREATE TRIGGER set_updated_at BEFORE UPDATE ON storage.tenant_service_users
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Same posture as storage.tenants: a firm may see WHICH credentials exist for it (an
-- access key id is an identifier, not a secret), and every write is service_role.
ALTER TABLE storage.tenant_service_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE storage.tenant_service_users FORCE ROW LEVEL SECURITY;
CREATE POLICY storage_tenant_service_users_read ON storage.tenant_service_users
  FOR SELECT TO authenticated USING (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT ON storage.tenant_service_users TO authenticated;
GRANT ALL ON storage.tenant_service_users TO service_role;

CREATE TABLE storage.tenant_service_user_secrets (
  tenant_id int NOT NULL,
  role text NOT NULL,
  secret_access_key_ct text NOT NULL,   -- Fernet ciphertext, like storage.tenant_secrets
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, role),
  FOREIGN KEY (tenant_id, role)
    REFERENCES storage.tenant_service_users (tenant_id, role) ON DELETE CASCADE
);

CREATE TRIGGER set_updated_at BEFORE UPDATE ON storage.tenant_service_user_secrets
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE storage.tenant_service_user_secrets ENABLE ROW LEVEL SECURITY;
ALTER TABLE storage.tenant_service_user_secrets FORCE ROW LEVEL SECURITY;
GRANT ALL ON storage.tenant_service_user_secrets TO service_role;

-- ---------------------------------------------------------------------------
-- Backfill: adopt the credential 0027 minted as role 'base'. Expand-phase, so the old
-- columns keep their values and both readers work until the code moves. A tenant whose
-- key the store has since forgotten is adopted too -- provisioning now verifies against
-- the store, so it will be re-minted on the next repair rather than trusted forever.
-- ---------------------------------------------------------------------------
INSERT INTO storage.tenant_service_users (tenant_id, role, access_key_id)
SELECT tenant_id, 'base', access_key_id
  FROM storage.tenants
 WHERE access_key_id IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO storage.tenant_service_user_secrets (tenant_id, role, secret_access_key_ct)
SELECT s.tenant_id, 'base', s.secret_access_key_ct
  FROM storage.tenant_secrets s
  JOIN storage.tenant_service_users u
    ON u.tenant_id = s.tenant_id AND u.role = 'base'
 WHERE s.secret_access_key_ct IS NOT NULL
ON CONFLICT DO NOTHING;
