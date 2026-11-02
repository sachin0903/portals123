-- 0266_action_policy_manual — a per-action "manual" switch.
--
-- Some catalogue actions have no provider API yet (the placeholders: invest/sell, drawdown,
-- beneficiaries, illustration). Rather than let them fail when run, a firm can mark the action
-- MANUAL: the run HALTS awaiting a person, who does the work by hand and "accepts" it to complete
-- the run without a provider call. Modelled like the approval hold so it rides the same gate /
-- decide machinery — the difference is that accepting completes the run instead of sending it.

SET LOCAL lock_timeout = '5s';

ALTER TABLE actions.policies
  ADD COLUMN manual boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN actions.policies.manual IS
  'When true the action halts as a manual step: the run waits for a person to accept it, then '
  'completes without a provider call. For actions whose provider API is not built yet.';
