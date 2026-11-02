-- 0122_documents_deletable: a document can be marked as one the firm may NOT delete.
--
-- WHY A COLUMN AND NOT `source`. `documents.documents.source` already carries a meaning and a
-- guard -- CHECK (source IN ('generated','uploaded')), and `retry_document` refuses anything that
-- is not 'generated' -- so widening it to carry provenance AND deletability would overload one
-- column with two questions and break that guard. Deletability is its own fact.
--
-- WHY NOT RLS. This is a CAPABILITY flag, not a visibility one. A policy that hid an undeletable
-- row from the UPDATE would make the archive endpoint answer "not found" for a document the reader
-- can plainly see listed -- a wrong answer that reads as data loss. The refusal belongs in the API,
-- where it can name the reason.
--
-- DEFAULT TRUE, so nothing that exists today changes: every document stays deletable until a
-- writer says otherwise. The intended first writer is provider ingest (a document synced from
-- SEI/Seccl is the custodian's record, not the firm's to remove) -- nothing outside the API writes
-- documents.documents yet, so this lands ahead of that with no backfill to do.
--
-- Additive and defaulted: PG18 stores the default in the catalogue rather than rewriting the
-- table, so this is a metadata-only change and needs no CONCURRENTLY dance.
ALTER TABLE documents.documents
    ADD COLUMN deletable boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN documents.documents.deletable IS
    'False for a document the firm may not archive (e.g. synced from a custodian). Enforced by the '
    'API''s archive endpoint, deliberately not by RLS -- see 0122_documents_deletable.sql.';
