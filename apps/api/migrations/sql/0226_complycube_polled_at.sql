-- ============================================================================
-- 0226_complycube_polled_at.sql — when the worker last asked ComplyCube about a
-- pending check.
--
-- The webhook is the main way a result arrives. app/complycube/poller.py is the
-- backstop for a check that has waited too long: a local machine has no public
-- URL for ComplyCube to call, and a deployed one can miss a delivery.
--
-- Its own table, not a column on compliance.checks: an UPDATE of a check fires
-- the audit trigger and stamps updated_at, so a column would write an audit row
-- on every question and tell the firm a pending check was "updated today".
-- Worker state only: RLS forced, no policy, no grant to authenticated.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

CREATE TABLE compliance.check_polls (
  check_id  bigint PRIMARY KEY REFERENCES compliance.checks (id) ON DELETE CASCADE,
  polled_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE compliance.check_polls ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance.check_polls FORCE ROW LEVEL SECURITY;
REVOKE ALL ON compliance.check_polls FROM PUBLIC, authenticated;
GRANT ALL ON compliance.check_polls TO service_role;

CREATE INDEX compliance_checks_pending_idx
  ON compliance.checks (updated_at)
  WHERE status = 'pending' AND provider_check_id IS NOT NULL;
