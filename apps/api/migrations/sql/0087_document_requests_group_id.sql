-- ============================================================================
-- 0087_document_requests_group_id -- tag a request with the group it was
-- raised through, so a client's fulfilment of it can be tagged the same way
-- (app/documents/service.py::create_request, mark_request_item_fulfilled_via_chat's
-- caller in app/routers/messaging.py) and surface in that group's own feed,
-- not just the member's 1:1 thread.
--
-- Nullable: a request raised against one client directly (the Requests page,
-- a client's Documents tab) never carries one. Cross-schema FK to
-- messaging.groups, the same direction-agnostic pattern
-- messaging.messages.request_item_id -> documents.request_items already
-- established the other way.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE documents.requests
  ADD COLUMN group_id bigint REFERENCES messaging.groups(id) ON DELETE SET NULL;

CREATE INDEX requests_group_id_idx ON documents.requests(group_id)
  WHERE group_id IS NOT NULL;
