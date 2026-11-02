-- ============================================================================
-- 0222_partner_api_extras.sql — the firm API's identity document sides and its
-- webhook delivery record.
--
-- compliance.documents.side is which side of an identity document a firm
-- uploaded: 'front' or 'back'. Null is a front (a row from before the column,
-- a selfie, or a document the screening provider holds).
--
-- compliance.webhook_deliveries.sent_at is when a firm's endpoint accepted the
-- delivery (a 2xx). It stays null for a pending or failed row.
--
-- Both columns are nullable with no default, so neither rewrites its table.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE compliance.documents
  ADD COLUMN side text,
  ADD CONSTRAINT compliance_documents_side_check CHECK (side IN ('front', 'back'));

ALTER TABLE compliance.webhook_deliveries ADD COLUMN sent_at timestamptz;
