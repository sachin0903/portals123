-- 0072 -- notifications.preferences gains a per-notification grain (trigger_key).
--
-- Clients set preferences per NOTIFICATION now, not per category — "mute document requests but keep
-- document shared". `trigger_key` NULL = a category-level preference (staff, unchanged); a set
-- trigger_key = a per-notification override (clients). The unique key folds trigger_key in, NULLS
-- NOT DISTINCT so the category-level (NULL) rows still upsert. Resolution reads the per-trigger row
-- for a client recipient and the NULL row for staff. Expand phase.
ALTER TABLE notifications.preferences ADD COLUMN IF NOT EXISTS trigger_key text;

ALTER TABLE notifications.preferences DROP CONSTRAINT IF EXISTS preferences_key;

ALTER TABLE notifications.preferences ADD CONSTRAINT preferences_key UNIQUE NULLS NOT DISTINCT
  (recipient_kind, recipient_user_id, recipient_contact_id, category, trigger_key);
