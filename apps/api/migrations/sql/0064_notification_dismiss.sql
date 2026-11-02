-- 0064_notification_dismiss -- let a recipient DISMISS a notification (remove it from their feed).
--
-- Soft, not a delete: a hard delete would free the (recipient, source_kind, source_ref) idempotency
-- key, so the next consumer replay of the same audit.actions row would re-create the notification
-- the user just dismissed. A dismissed_at stamp keeps the row (and its key) while hiding it from the
-- feed and the unread count. It rides the existing authenticated UPDATE grant + the own-row policy,
-- so no new privilege is needed -- dismissing is the same "mark my own row" write as read/seen.

ALTER TABLE notifications.notifications ADD COLUMN dismissed_at timestamptz;
