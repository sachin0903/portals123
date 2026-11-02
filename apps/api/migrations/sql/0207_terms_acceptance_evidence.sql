-- The signed copy behind an in-person terms acceptance.
--
-- An acceptance the client clicked in their own portal is its own evidence: the session, the
-- item and the audit row say who agreed and when. An acceptance an adviser records on the
-- client's behalf has no such trail, so it is only recorded against a scan or photograph of
-- the signed terms — and that file has to be findable FROM the acceptance, not only by
-- searching the client's documents for something that looks like it.
--
-- NULL for portal acceptances. SET NULL on delete rather than RESTRICT: the register row is
-- the legal fact and outlives a retention purge of the file.
--
-- The index is not CONCURRENTLY, for the reason 0024 records: this runs inside Alembic's
-- transaction, which CONCURRENTLY cannot, and the register is small.

ALTER TABLE terms.acceptances
    ADD COLUMN evidence_document_id uuid REFERENCES documents.documents(id) ON DELETE SET NULL;

CREATE INDEX acceptances_evidence_document_idx
    ON terms.acceptances (evidence_document_id)
    WHERE evidence_document_id IS NOT NULL;

COMMENT ON COLUMN terms.acceptances.evidence_document_id IS
    'The uploaded signed copy an in-person acceptance was recorded against (documents.documents). NULL when the client accepted in their own portal — that acceptance is its own evidence.';
