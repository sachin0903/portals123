-- ============================================================================
-- 0262_drop_legacy_actions.sql -- retire the legacy actions table-queue engine.
--
-- baton (schema `baton`, migration 0261) is the sole domain-actions engine now: every action and
-- door runs on it, and the legacy engine code (app/actions/runs.py's engine, operations.py,
-- plan.py) is gone. These tables held the old runs/steps/attempts/events/subjects/approvers. The
-- firm policy is NOT here -- `actions.policies` and `actions.policy_roles` stay, and baton reads
-- them. CASCADE because `http_requests`→`steps`→`runs` and the rest FK into `runs`.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

DROP TABLE IF EXISTS actions.http_requests CASCADE;
DROP TABLE IF EXISTS actions.steps CASCADE;
DROP TABLE IF EXISTS actions.run_approvers CASCADE;
DROP TABLE IF EXISTS actions.events CASCADE;
DROP TABLE IF EXISTS actions.subjects CASCADE;
DROP TABLE IF EXISTS actions.runs CASCADE;
