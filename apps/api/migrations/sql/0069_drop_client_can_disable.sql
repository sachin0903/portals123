-- 0069 -- drop the dead notifications.rules.client_can_disable column.
--
-- It was a second guardrail beside `mandatory` that NOTHING read in resolution — only `mandatory`
-- forces a recipient's default (docs/notifications.md). Two switches for one idea confused the
-- builder ("Mandatory" vs "Client can turn off"), so the redundant one goes. `mandatory` stays as
-- the single control (and force-enables even over a prior opt-out). Contract phase; expand/contract.
ALTER TABLE notifications.rules DROP COLUMN IF EXISTS client_can_disable;
