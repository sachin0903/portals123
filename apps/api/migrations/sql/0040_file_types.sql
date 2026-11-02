-- 0040_file_types: extend the document-type registry to also cover SUPPORTING FILE types.
--
-- documents.document_types (0039) held only DOCUMENT types (report, statement, identity…).
-- Supporting files (documents.attachments.attachment_type — passport, national_id…) were a
-- hardcoded frontend list with no per-tenant management. This adds a `kind` discriminator so the
-- SAME per-tenant registry manages both vocabularies, and seeds the default supporting-file types
-- for existing + future tenants.
--
-- `kind='document'` is the pre-existing behaviour: the column DEFAULT keeps every existing row a
-- document type, and a bare INSERT (the register helper's) still lands as a document type.

ALTER TABLE documents.document_types
    ADD COLUMN kind text NOT NULL DEFAULT 'document'
        CHECK (kind IN ('document', 'file'));

-- A slug is now unique PER KIND, not globally per tenant: 'identity' can be both a document type and
-- a supporting-file type. Swap the (tenant, slug) key for (tenant, kind, slug).
ALTER TABLE documents.document_types DROP CONSTRAINT document_types_tenant_slug_key;
ALTER TABLE documents.document_types
    ADD CONSTRAINT document_types_tenant_kind_slug_key UNIQUE (tenant_id, kind, slug);

-- Seed the default supporting-file types for every EXISTING tenant. As in 0039, run as service_role
-- so its BYPASSRLS lets the set-difference against auth.tenants see all tenants (migrator is subject
-- to FORCE RLS on auth.tenants and is not bypassrls).
SET LOCAL ROLE service_role;
INSERT INTO documents.document_types (tenant_id, kind, slug, label, sort_order)
SELECT t.id, 'file', v.slug, v.label, v.sort_order
FROM auth.tenants t
CROSS JOIN (VALUES
    ('passport', 'Passport', 1),
    ('national_id', 'National ID', 2),
    ('proof_of_address', 'Proof of address', 3),
    ('other', 'Other file', 9)
) AS v(slug, label, sort_order)
ON CONFLICT (tenant_id, kind, slug) DO NOTHING;
RESET ROLE;

-- Reinstall the new-tenant seed trigger to seed BOTH vocabularies (kind is now explicit for every
-- seeded row). Idempotent via the new (tenant, kind, slug) key.
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
        (NEW.id, 'file', 'passport', 'Passport', 1),
        (NEW.id, 'file', 'national_id', 'National ID', 2),
        (NEW.id, 'file', 'proof_of_address', 'Proof of address', 3),
        (NEW.id, 'file', 'other', 'Other file', 9)
    ON CONFLICT (tenant_id, kind, slug) DO NOTHING;
    RETURN NEW;
END;
$$;
