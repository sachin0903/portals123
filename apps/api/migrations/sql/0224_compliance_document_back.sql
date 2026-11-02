-- ============================================================================
-- 0224_compliance_document_back.sql — our copy of an identity document's back.
--
-- compliance.documents.back_object_key is the stored file of the document's
-- back, beside object_key (the front). An identity check writes it when it
-- sends a back: a portal client's driving licence or national ID card, or a
-- firm's two-sided upload through the firm API. It is null for a passport, a
-- selfie, a firm's own upload row, and every row from before the column.
--
-- Nullable with no default, so the ADD COLUMN does not rewrite the table.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE compliance.documents ADD COLUMN back_object_key text;
