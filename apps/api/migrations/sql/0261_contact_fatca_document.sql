-- ============================================================================
-- 0261_contact_fatca_document.sql — the signed W-9 or W-8 behind a client's
-- FATCA answer.
--
-- `crm.contacts.fatca_status` (0134) records WHICH certification the client
-- holds. A W-9 or W-8 is a signed self-certification, so the answer is only as
-- good as the form behind it, and that form has to be findable FROM the answer,
-- not by searching the client's documents for something that looks like one.
-- 0207 gives an in-person terms acceptance its signed copy the same way.
--
-- 1. `fatca_document_id` points at the uploaded form. SET NULL on delete: the
--    answer is the fact and outlives a retention purge of the file.
--
-- 2. The file belongs to the answer it was filed for. When `fatca_status`
--    changes, to another value or to NULL, the link is cleared and the document
--    stays in the client's documents. A BEFORE trigger, as 0246 does for a
--    person's name, so the rule holds for every writer: the client record's
--    PATCH, a flow's form write-back, and anything else that updates the row.
--    An UPDATE that sets a new status AND a new document in one statement keeps
--    the new document.
--
-- 3. `fatca_form` (label "W-9 / W-8") joins the seeded document types, for every
--    firm that exists now and every firm created later. ON CONFLICT leaves a
--    label a firm has already renamed alone. Sort order 10 puts it after Letter.
--
-- The index is not CONCURRENTLY, for the reason 0024 records: this runs inside
-- Alembic's transaction, which CONCURRENTLY cannot.
--
-- Every statement can run again: the migration test re-runs this file.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE crm.contacts
    ADD COLUMN IF NOT EXISTS fatca_document_id uuid
        REFERENCES documents.documents(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS contacts_fatca_document_idx
    ON crm.contacts (fatca_document_id)
    WHERE fatca_document_id IS NOT NULL;

COMMENT ON COLUMN crm.contacts.fatca_document_id IS
    'The signed W-9 or W-8 behind fatca_status (documents.documents). Cleared when fatca_status '
    'changes (trigger contacts_fatca_document_follows_status); the document itself is kept.';

CREATE OR REPLACE FUNCTION crm.unlink_fatca_document() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, public AS $fn$
BEGIN
  IF NEW.fatca_document_id IS NOT DISTINCT FROM OLD.fatca_document_id THEN
    NEW.fatca_document_id := NULL;
  END IF;
  RETURN NEW;
END;
$fn$;

CREATE OR REPLACE TRIGGER contacts_fatca_document_follows_status
  BEFORE UPDATE OF fatca_status ON crm.contacts
  FOR EACH ROW
  WHEN (OLD.fatca_status IS DISTINCT FROM NEW.fatca_status)
  EXECUTE FUNCTION crm.unlink_fatca_document();

-- auth.tenants and documents.document_types are FORCE RLS and the migrator is
-- not BYPASSRLS, so the backfill runs as service_role (0039 says why).
SET LOCAL ROLE service_role;
INSERT INTO documents.document_types (tenant_id, kind, slug, label, sort_order)
SELECT t.id, 'document', 'fatca_form', 'W-9 / W-8', 10
  FROM auth.tenants t
ON CONFLICT (tenant_id, kind, slug) DO NOTHING;
RESET ROLE;

-- 0040's new-tenant seed, with `fatca_form` added.
CREATE OR REPLACE FUNCTION documents.seed_default_document_types()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO documents.document_types (tenant_id, kind, slug, label, sort_order)
    VALUES
        (NEW.id, 'document', 'report', 'Report', 1),
        (NEW.id, 'document', 'statement', 'Statement', 2),
        (NEW.id, 'document', 'agreement', 'Agreement', 3),
        (NEW.id, 'document', 'identity', 'Identity', 4),
        (NEW.id, 'document', 'tax', 'Tax', 5),
        (NEW.id, 'document', 'illustration', 'Illustration', 7),
        (NEW.id, 'document', 'application', 'Application', 8),
        (NEW.id, 'document', 'letter', 'Letter', 9),
        (NEW.id, 'document', 'fatca_form', 'W-9 / W-8', 10),
        (NEW.id, 'file', 'passport', 'Passport', 1),
        (NEW.id, 'file', 'national_id', 'National ID', 2),
        (NEW.id, 'file', 'proof_of_address', 'Proof of address', 3),
        (NEW.id, 'file', 'other', 'Other file', 9)
    ON CONFLICT (tenant_id, kind, slug) DO NOTHING;
    RETURN NEW;
END;
$$;
