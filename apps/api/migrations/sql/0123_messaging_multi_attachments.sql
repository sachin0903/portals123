-- 0123: a message may carry SEVERAL attachments.
--
-- 0081 made `message_id` UNIQUE, so one message held at most one file. That is the wrong grain for
-- how people actually send things: attaching three documents produced three separate messages, and
-- any note typed alongside them landed on the first with the other two arriving bare underneath.
-- The reader then has to infer that the four rows are one act.
--
-- Dropping the constraint is the whole change. The table already models one ROW per file — uid,
-- filename, content type, size, storage key — so several rows sharing a `message_id` need no new
-- columns; only the promise that there is at most one of them goes.
--
-- The storage KEY changes with it (app/storage.py::attachment_key): it was
-- `attachments/{contact_uid}/{message_id}.{ext}`, which two files on one message would collide on.
-- It is keyed by the attachment's own uid now. Existing objects keep their old keys and stay
-- readable — the key is stored on the row (`storage_key`) and read back from there, never
-- recomputed — so this is additive in the object store too and needs no data migration.
--
-- Expand/contract: dropping a constraint is instant and takes no table rewrite. The replacement
-- index is what keeps the read fast (every message projection joins on `message_id`), and the
-- UNIQUE it replaces was serving that purpose as a side effect.
ALTER TABLE messaging.attachments DROP CONSTRAINT IF EXISTS attachments_message_id_key;

CREATE INDEX IF NOT EXISTS attachments_message_idx ON messaging.attachments (message_id);
