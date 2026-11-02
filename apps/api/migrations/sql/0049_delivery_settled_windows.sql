-- ============================================================================
-- 0049_delivery_settled_windows.sql — window the automation rate limits on when a firing RAN.
--
-- 0043 windowed both throttles on `flows.automation_deliveries.created_at`:
--
--   * the per-tenant rate limit  (`automation._dispatched_in_window`, `automation_deliveries_rate_idx`)
--   * the per-subject re-arm     (`automation._rearmed`,             `automation_deliveries_rearm_idx`)
--
-- A delivery row is written when a firing is first CONSIDERED and re-settled in place when it
-- later runs (`_record`'s `ON CONFLICT … DO UPDATE`, which is what makes `(automation_id,
-- event_key)` a genuine idempotency key across the deferral path). So `created_at` is when the
-- event arrived, not when the automation fired — and a firing deferred for ten minutes and then
-- dispatched carried a ten-minute-old `created_at` that had already fallen out of a one-minute
-- window. It never counted against the limit.
--
-- That leaked hardest under exactly the condition the guard exists for: `run_deferred_once` drains
-- `DEFER_BATCH` rows every couple of seconds, and under sustained overflow none of them counted,
-- so the real dispatch rate ran clear of `executions_per_minute`. The re-arm window leaked the
-- same way, letting a deferred chaser re-fire at the same client too soon.
--
-- The fix is in the two queries, which now read `updated_at` — maintained by the table's
-- `set_updated_at` trigger, and (because `dispatched` is terminal for a delivery: nothing
-- re-settles a row that ran) exactly the moment the automation fired. This migration is the index
-- half: without it both queries fall off their partial indexes and re-check every dispatched row.
--
-- Expand/contract: the new indexes are created first and the superseded ones dropped after, so a
-- rolling deploy never has a window with neither. No table, column or policy changes — RLS on
-- `flows.automation_deliveries` is untouched (0043), and an index is invisible to it. Reversible:
-- the downgrade puts the `created_at` pair back.
--
-- CONCURRENTLY is deliberately NOT used, following the convention set out in 0024 and restated in
-- 0047: these run inside Alembic's transaction, where CONCURRENTLY is illegal. `lock_timeout`
-- keeps the short exclusive build from queueing behind a long transaction — it fails fast and the
-- deploy retries rather than holding a lock on a table the consumer writes to on every event.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- The re-arm lookup: "has THIS automation already fired for THIS subject recently?" — one
-- index-only backwards scan per candidate. Same shape as 0043's, on the settle time.
CREATE INDEX IF NOT EXISTS automation_deliveries_rearm_settled_idx
  ON flows.automation_deliveries (automation_id, subject_kind, subject_id, updated_at DESC)
  WHERE outcome = 'dispatched';

-- The per-tenant rate window.
CREATE INDEX IF NOT EXISTS automation_deliveries_rate_settled_idx
  ON flows.automation_deliveries (tenant_id, updated_at DESC) WHERE outcome = 'dispatched';

DROP INDEX IF EXISTS flows.automation_deliveries_rearm_idx;
DROP INDEX IF EXISTS flows.automation_deliveries_rate_idx;
