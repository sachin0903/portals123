-- 0197_outbound_transactions — a Graphene-originated pending row in platform.transactions.
-- (First applied as 0179, then 0188; both ids remain no-op stubs.)
--
-- Decision 2 still holds for inbound credits: the materialiser upserts the custodian feed and we
-- do not invent a realised movement. The exception is the OTHER direction. An adviser raising a
-- withdrawal, payment, contribution or transfer is instructing money that has not landed yet, and
-- that instruction has to exist as a transaction — in a pending-style state — so it can be synced
-- to SEI / Seccl / Morgan Lloyd, have its status advanced when the vendor accepts it, and carry
-- the vendor's id on platform.links.
--
-- `source_system` is `platform.provider_code` (0143). Graphene is not a custodian; it is the
-- origin of an outbound row. Adding it to the enum is required so the unique key can hold these
-- rows without colliding with the feed. It is NOT added to `app/providers.py::PROVIDERS` — that
-- tuple is the admin dropdown / enablement list, and Graphene is not a platform a firm enables.
--
-- ADD VALUE is not used in any later statement of this migration (0168's trap: a new member is
-- unusable until the adding transaction commits, on a type that already existed). The CHECK and
-- index key off `source_transaction_id LIKE 'gxn:%'`, which is text, not the new enum member.
-- Python writes `source_system = graphene` after this revision has committed.
--
-- The Graphene key is never rewritten to a vendor id: the book-sink ON CONFLICT would then UPDATE
-- the pending row and could wipe origin metadata. The vendor id lives on platform.links
-- (entity_type = 'transaction') and in data.provider_references.
--
-- Status is constrained only for gxn: rows. Inbound status is the vendor's own string.
-- Mapping: docs/transaction-status.md.

SET LOCAL lock_timeout = '5s';

ALTER TYPE platform.provider_code ADD VALUE IF NOT EXISTS 'graphene';

ALTER TABLE platform.transactions
  DROP CONSTRAINT IF EXISTS transactions_outbound_status;
ALTER TABLE platform.transactions
  ADD CONSTRAINT transactions_outbound_status CHECK (
    source_transaction_id NOT LIKE 'gxn:%'
    OR (
      status IN ('pending', 'submitted', 'posted', 'failed', 'cancelled')
      AND (data ->> 'origin') IN ('instruction', 'contribution', 'transfer')
      AND NULLIF(data ->> 'origin_uid', '') IS NOT NULL
    )
  );

CREATE INDEX IF NOT EXISTS transactions_outbound_origin_idx
  ON platform.transactions ((data ->> 'origin_uid'))
  WHERE source_transaction_id LIKE 'gxn:%';

COMMENT ON CONSTRAINT transactions_outbound_status ON platform.transactions IS
  'Graphene-originated outbound rows (source_transaction_id gxn:{origin}:{uid}) only. Inbound '
  'feed status is unconstrained vendor text. See docs/transaction-status.md.';

COMMENT ON COLUMN platform.transactions.status IS
  'Inbound: the custodian''s own status string. Outbound (gxn: keys, source_system = graphene): '
  'pending (local, not yet accepted) | submitted (vendor API accepted) | posted (matched to a '
  'landed credit) | failed (vendor refused) | cancelled (local cancel before/without a posted '
  'credit).';
