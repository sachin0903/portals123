-- ============================================================================
-- 0024_flows_p0_hardening.sql — Phase-0 flow-engine hardening (D2/D3 + public throttle).
--
--   * D2 — re-key the one-active-case guard on the terminal TIMESTAMPS (completed_at/cancelled_at),
--     not the mutable, free-form `status` string. Engine status is authorable/derived, so a firm
--     terminal label other than 'completed'/'cancelled' would otherwise keep a finished case inside
--     the unique set and wrongly block a legitimate re-onboarding.
--   * D3 — covering indexes for FK columns that lacked them (seq-scan on delete/RESTRICT checks).
--   * H1 — a per-tenant fixed-window throttle table for the UNAUTHENTICATED public onboarding entry,
--     so email-rotation can't drive unbounded contact/login/email creation. service_role-only.
--
-- lock_timeout keeps the (non-CONCURRENT) index rebuild from hanging behind a long txn — it fails
-- fast instead. CONCURRENTLY isn't used: these migrations run inside Alembic's transaction and the
-- flows.* tables are small/new, so a short exclusive build is acceptable (repo convention).
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- Normalise terminal cases missing their timestamp so they drop out of the re-keyed index (defensive
-- — any status='completed'/'cancelled' row must have the matching *_at set, or it would re-enter the
-- active set). Runs as service_role (migrator is FORCE-RLS, not bypassrls; see 0023 for the idiom).
SET LOCAL ROLE service_role;
UPDATE flows.cases SET cancelled_at = COALESCE(cancelled_at, updated_at, now())
  WHERE status = 'cancelled' AND cancelled_at IS NULL;
UPDATE flows.cases SET completed_at = COALESCE(completed_at, updated_at, now())
  WHERE status = 'completed' AND completed_at IS NULL;
RESET ROLE;

-- D2 — re-key the one-active-case guard on timestamps, not the status string.
DROP INDEX IF EXISTS flows.cases_one_active_per_subject;
CREATE UNIQUE INDEX cases_one_active_per_subject
  ON flows.cases (flow_id, subject_contact_id)
  WHERE subject_contact_id IS NOT NULL AND completed_at IS NULL AND cancelled_at IS NULL;

-- D3 — covering indexes for the three unindexed FKs.
CREATE INDEX IF NOT EXISTS cases_flow_version_id_idx ON flows.cases(flow_version_id);
CREATE INDEX IF NOT EXISTS flows_current_published_version_idx
  ON flows.flows(current_published_version_id) WHERE current_published_version_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS case_steps_account_id_idx
  ON flows.case_steps(account_id) WHERE account_id IS NOT NULL;

-- H1 — per-tenant fixed-window throttle for the public onboarding entry. The public path runs
-- elevated (system_transaction / service_role) and resolves the tenant by request host; a firm
-- rarely self-starts many onboardings an hour, so a per-tenant volume cap bounds the abuse of an
-- unauthenticated endpoint that creates contacts + WorkOS users + emails. No authenticated access:
-- RLS enabled + forced with NO policy = deny-all for the app role; service_role bypasses by role.
CREATE TABLE flows.public_start_throttle (
  tenant_id    int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  window_start timestamptz NOT NULL,
  count        int         NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, window_start)
);
ALTER TABLE flows.public_start_throttle ENABLE ROW LEVEL SECURITY;
ALTER TABLE flows.public_start_throttle FORCE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON flows.public_start_throttle TO service_role;
