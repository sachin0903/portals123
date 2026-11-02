-- ============================================================================
-- 0086_group_batches -- tag the N per-member rows one `send_group_message`
-- call creates with a shared `batch_id`, so a group's feed can collapse them
-- back into ONE entry (tagged with every recipient) instead of rendering the
-- same broadcast as N separate, identical-looking bubbles.
--
-- Nullable: a plain direct message is never batched. `created_at` alone
-- can't stand in for this (0056's own comment already notes it's frozen for
-- the whole transaction, so it's a correct-by-coincidence signal, not an
-- honest one) — `batch_id` says explicitly "these rows are one send".
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE messaging.messages
  ADD COLUMN batch_id uuid;

CREATE INDEX messages_batch_id_idx ON messaging.messages(batch_id)
  WHERE batch_id IS NOT NULL;
