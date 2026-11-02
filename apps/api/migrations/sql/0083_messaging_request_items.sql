-- ============================================================================
-- 0083_messaging_request_items -- link a chat message to the document-request
-- item it announces, so the client's thread can render a rich, clickable
-- "upload" card instead of plain text (and later tell a fulfilled item's
-- card apart from a still-pending one).
--
-- Nullable: almost every message carries no link at all (a plain text send,
-- or an attachment sent directly via the paperclip button). Cross-schema FK
-- to documents.request_items is an established pattern already
-- (flows.case_steps.request_item_id does the same).
--
-- No RLS change needed: messaging.messages' own policy already covers this
-- column like any other: it's read wherever the message row itself is
-- readable. The request_item's OWN current status/title is read via a JOIN
-- at query time (app/messaging.py::list_messages), never denormalised here,
-- so it can never go stale relative to documents.request_items itself.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE messaging.messages
  ADD COLUMN request_item_id integer REFERENCES documents.request_items(id) ON DELETE SET NULL;

CREATE INDEX messages_request_item_id_idx ON messaging.messages(request_item_id)
  WHERE request_item_id IS NOT NULL;
