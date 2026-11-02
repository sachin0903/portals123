-- 0268_policy_notify_channels — which channels an approval notice uses, per action policy.
--
-- When a run is held for approval, the approver roles are notified. Until now that fanned out to
-- BOTH an in-app notification and an email (the `review.requested` trigger defaulted email on). A
-- firm can now choose, per action policy, which channels an approval notice uses — defaulting to
-- in-app only. The channels are stamped on the review.requested event and honoured by the in-app
-- and email consumers.

SET LOCAL lock_timeout = '5s';

ALTER TABLE actions.policies
  ADD COLUMN notify_in_app boolean NOT NULL DEFAULT true,
  ADD COLUMN notify_email boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN actions.policies.notify_in_app IS
  'Approvers of a held run get an in-app notification. On by default.';
COMMENT ON COLUMN actions.policies.notify_email IS
  'Approvers of a held run also get an email. Off by default — in-app only unless switched on.';
