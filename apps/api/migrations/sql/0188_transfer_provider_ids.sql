-- ============================================================================
-- 0188_transfer_provider_ids.sql — the Seccl and SEI ids a refresh or cancel
-- addresses.
--
-- `ml_transfer_id` is a column because a transfer is one-to-one with its Morgan
-- Lloyd record. Seccl's product-transfer transaction id and SEI's activity id
-- are the same kind of fact: the workbench loads the row and then calls the
-- vendor back, and a jsonb scratchpad is how that id gets lost between the two.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.transfers
  ADD COLUMN IF NOT EXISTS seccl_transfer_id text,
  ADD COLUMN IF NOT EXISTS sei_activity_id text;

COMMENT ON COLUMN platform.transfers.seccl_transfer_id IS
  'The Seccl product-transfer transaction id (or its group linkId when that is all the create returned). Refresh and cancel address this.';
COMMENT ON COLUMN platform.transfers.sei_activity_id IS
  'The SEI transfer or cash-receipt activity id. A pending-status poll addresses this; without it a poll by account number is refused when SEI returns more than one.';
