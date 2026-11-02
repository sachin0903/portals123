-- 0071 -- drop the dead notifications.notifications.seen_at column.
--
-- 'seen' was the badge-acknowledged state from the original two-tier plan (open the bell → the badge
-- stops nagging; items stay unread until read). That was superseded by the decision that the badge
-- is purely an UNREAD count, clearing only on read/dismiss/mark-all — so nothing drives off 'seen',
-- nothing reads seen_at, and the mark-seen endpoint is gone. Contract phase; expand/contract.
ALTER TABLE notifications.notifications DROP COLUMN IF EXISTS seen_at;
