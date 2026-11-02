-- ============================================================================
-- 0164_tenant_data_tools.sql — non-production tenant reset job and pipeline pause.
--
-- A reset is not offboarding: the tenant, its identity/configuration, provider
-- enablements and audit trail survive.  The job ledger is deliberately outside
-- the reset manifest so an interrupted multi-store operation remains observable.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.tenant_platforms
  ADD COLUMN IF NOT EXISTS refresh_paused_at timestamptz,
  ADD COLUMN IF NOT EXISTS refresh_pause_reason text;

CREATE TABLE platform.tenant_data_jobs (
  id bigserial PRIMARY KEY,
  tenant_id bigint NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('reset', 'reset_and_seed')),
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
  phase text NOT NULL DEFAULT 'queued',
  requested_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text,
  attempts integer NOT NULL DEFAULT 0,
  claimed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);

CREATE UNIQUE INDEX tenant_data_jobs_one_active
  ON platform.tenant_data_jobs (tenant_id)
  WHERE status IN ('queued', 'processing');

CREATE INDEX tenant_data_jobs_tenant_created
  ON platform.tenant_data_jobs (tenant_id, created_at DESC);

ALTER TABLE platform.tenant_data_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.tenant_data_jobs FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_data_jobs_service ON platform.tenant_data_jobs;
CREATE POLICY tenant_data_jobs_service ON platform.tenant_data_jobs
  FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON platform.tenant_data_jobs FROM PUBLIC, authenticated, book_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.tenant_data_jobs TO service_role;
GRANT USAGE, SELECT ON SEQUENCE platform.tenant_data_jobs_id_seq TO service_role;

-- Reset preserves append-only operational evidence alongside audit history.
-- Restate the invariant defensively for databases that crossed a pre-release draft.
REVOKE UPDATE, DELETE ON platform.merge_rejects, platform.merge_runs FROM service_role;
REVOKE UPDATE, DELETE ON flows.ai_usage, flows.domain_events FROM service_role;

COMMENT ON COLUMN platform.tenant_platforms.refresh_paused_at IS
  'Operational gate for tenant reset/demo data. Configuration and enabled state are preserved.';
COMMENT ON TABLE platform.tenant_data_jobs IS
  'Operations-only ledger for non-production tenant data reset and deterministic demo seed jobs.';
