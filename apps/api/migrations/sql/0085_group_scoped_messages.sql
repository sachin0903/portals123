-- ============================================================================
-- 0085_group_scoped_messages -- tag a message with the group it was sent
-- THROUGH, so a group's own feed can be scoped to messages actually sent
-- via that group rather than merging in a member's entire, unrelated 1:1
-- history with their adviser.
--
-- Nullable, and set ONLY by messaging_groups.send_group_message: a plain
-- direct reply (paperclip, "New", or a client's own message) never carries
-- one. The adviser's Direct-message views (app/messaging.py::list_messages
-- with include_group_messages=False, and list_conversations_for_tenant's
-- last_message) filter group_id IS NOT NULL rows OUT, so a group broadcast
-- never bleeds into what looks like an ordinary 1:1 exchange on the staff
-- side. The CLIENT side is deliberately untouched: to a client this is still
-- just a message in their own thread, exactly as before this migration.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE messaging.messages
  ADD COLUMN group_id bigint REFERENCES messaging.groups(id) ON DELETE SET NULL;

CREATE INDEX messages_group_id_idx ON messaging.messages(group_id)
  WHERE group_id IS NOT NULL;
