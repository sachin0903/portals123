-- ============================================================================
-- 0023_flows_hardening.sql — production hardening of the flow engine.
--
--   * flows.case_steps.data (jsonb) — per-step payload. Used to SNAPSHOT a fill_form step's form
--     definition at instantiate time, so a later edit/archive of the form can't break or change a
--     client's in-flight step (mirrors template_versions.input_form_snapshot). Also holds small
--     per-step metadata (e.g. why a step was skipped).
--   * one-active-case guard — a partial unique index so a subject contact can't accumulate multiple
--     live cases of the same flow (starting onboarding twice is a no-op, not a duplicate). Terminal
--     cases (completed/cancelled) drop out of the index, so re-onboarding later is allowed.
-- ============================================================================

ALTER TABLE flows.case_steps ADD COLUMN data jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Resolve any pre-existing duplicate active cases (created before this guard) so the unique index
-- can build: keep the most recent active case per (flow, subject) and cancel the older ones. Safe +
-- idempotent on any environment.
--
-- This DML must run as service_role: the migrator role is subject to FORCE ROW LEVEL SECURITY and
-- is NOT bypassrls, so with no request claims it would see zero rows and cancel nothing — while the
-- CREATE INDEX below (a physical build, not row-filtered) would still hit the real duplicates. The
-- migrator is a member of service_role (bootstrap_roles.sql), so it can assume it; RESET ROLE hands
-- the index build back to the migrator (the table owner — CREATE INDEX needs ownership, not RLS).
SET LOCAL ROLE service_role;
UPDATE flows.cases c SET status = 'cancelled', cancelled_at = now()
WHERE c.subject_contact_id IS NOT NULL
  AND c.status NOT IN ('completed', 'cancelled')
  AND EXISTS (
    SELECT 1 FROM flows.cases c2
    WHERE c2.flow_id = c.flow_id
      AND c2.subject_contact_id = c.subject_contact_id
      AND c2.status NOT IN ('completed', 'cancelled')
      AND (c2.created_at, c2.id) > (c.created_at, c.id)
  );
RESET ROLE;

CREATE UNIQUE INDEX cases_one_active_per_subject
  ON flows.cases (flow_id, subject_contact_id)
  WHERE subject_contact_id IS NOT NULL AND status NOT IN ('completed', 'cancelled');
