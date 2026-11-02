-- ============================================================================
-- 0047_flow_analytics.sql — the index the step funnel's cohort filter needs.
--
-- `GET /flows/{uid}/insights` scopes every aggregate to one flow VERSION over one date range
-- (app/flows/analytics.py explains why the version, not the flow, is the cohort). Before this
-- index that predicate had only 0024's single-column `cases_flow_version_id_idx`, so a firm with
-- a long-running journey read every case ever started on the version and filtered the date range
-- row by row — on the request path, under RLS, for a page whose whole point is being cheap enough
-- to leave open.
--
-- No new table, so no new RLS policy: `flows.cases` already carries its own (0021 — authenticated
-- read within the tenant lineage plus the subject's own contact scope, FORCE ROW LEVEL SECURITY),
-- and an index is invisible to it. Expand-only and reversible: dropping it costs a slower query
-- and nothing else.
--
-- CONCURRENTLY is deliberately NOT used, following the repo convention set out in 0024: these
-- migrations run inside Alembic's transaction, where CONCURRENTLY is illegal. `lock_timeout`
-- keeps the short exclusive build from queueing behind a long transaction — it fails fast and the
-- deploy retries instead of holding a lock on the table the engine writes to.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- (tenant_id, flow_version_id, created_at DESC): tenant first because RLS puts it in every plan,
-- then the cohort key, then the range column so the date filter is an index condition rather than
-- a filter. DESC matches the "most recent window" the surface defaults to.
CREATE INDEX IF NOT EXISTS cases_tenant_version_created_idx
  ON flows.cases (tenant_id, flow_version_id, created_at DESC);

-- The per-step roll-up joins `case_steps` back to the cohort's cases and groups by `step_id`,
-- reading `status`, `created_at` and `completed_at` off every row. `case_steps_case_id_idx` finds
-- the rows; this covering index lets the aggregate read the columns it needs without touching the
-- heap for the common case, which is what keeps a firm with a hundred thousand step rows off a
-- full scan.
CREATE INDEX IF NOT EXISTS case_steps_funnel_idx
  ON flows.case_steps (case_id, step_id) INCLUDE (status, created_at, completed_at);
