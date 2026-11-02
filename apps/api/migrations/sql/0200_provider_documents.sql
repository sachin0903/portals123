-- 0200_provider_documents — file a custodian document into a tenant's library.
-- (First applied as 0182, then 0191; both ids remain no-op stubs.)
--
-- Seccl and SEI land the bytes in the core bucket first. The tenant's copy lives in
-- the tenant bucket, and the library row is what makes it visible. `book_writer` is
-- the pipeline's login and deliberately holds nothing on `documents.*` (0062): a
-- compromised sync credential must not be able to read a client's identity documents.
-- So the pipeline gets EXECUTE on this function and therefore exactly this insert,
-- the same shape as `auth.merge_provider_user`.
--
-- `p_contact_id` may be NULL. A SEI firm pack (the "Firm Generic Documents" group on
-- the end-client list) belongs to the firm, not to a person: the row is staff-visible
-- through the tenant policy and has no subject link. A client document passes the
-- contact. A second call for the same (tenant, provider, external id) with another
-- contact — a joint statement listed for both owners — adds that subject link and
-- does not insert a second document.
--
-- `source` gains 'provider'. It was 'generated' | 'uploaded', and 0122 refused to
-- overload that column with deletability — deletability stayed its own flag, and
-- this migration is what the flag was waiting for. A custodian document is neither
-- a render nor an upload, and saying so is what stops the retry path (which refuses
-- anything that is not 'generated') from trying to re-render a contract note.
-- `deletable` is false: the firm does not own the custodian's record.
--
-- Identity is (tenant, provider, external id) inside `metadata`, unique where the
-- external id is present. A second run returns the document already filed. The
-- index is not CONCURRENTLY, for the reason 0024 records: this runs inside
-- Alembic's transaction, which CONCURRENTLY cannot. The documents table is the
-- firm's own library, not a warehouse fact table.

SET LOCAL lock_timeout = '5s';

-- Named, not looked up. Postgres rewrites `IN (...)` to `= ANY (ARRAY[...])` in
-- pg_get_constraintdef, so a search for the text we wrote does not find the
-- constraint. 0005's inline check is `documents_source_check`.
ALTER TABLE documents.documents DROP CONSTRAINT IF EXISTS documents_source_check;
ALTER TABLE documents.documents
  ADD CONSTRAINT documents_source_check
  CHECK (source IN ('generated', 'uploaded', 'provider'));

CREATE UNIQUE INDEX IF NOT EXISTS documents_provider_external_key
  ON documents.documents (tenant_id, (metadata ->> 'provider'), (metadata ->> 'external_id'))
  WHERE (metadata ->> 'external_id') IS NOT NULL;

CREATE OR REPLACE FUNCTION documents.ingest_provider_document(
  p_tenant_id     int,
  p_contact_id    int,
  p_bucket        text,
  p_storage_key   text,
  p_content_type  text,
  p_size_bytes    bigint,
  p_sha256        text,
  p_document_type text,
  p_type_label    text,
  p_name          text,
  p_provider      text,
  p_external_id   text,
  p_metadata      jsonb
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
DECLARE
  v_uid  text;
  v_file uuid;
  v_doc  uuid;
  v_meta jsonb;
BEGIN
  IF p_tenant_id IS NULL THEN
    RAISE EXCEPTION 'provider document needs a tenant';
  END IF;
  IF nullif(btrim(p_provider), '') IS NULL OR nullif(btrim(p_external_id), '') IS NULL THEN
    RAISE EXCEPTION 'provider document needs a provider and an external id';
  END IF;
  IF nullif(btrim(p_bucket), '') IS NULL OR nullif(btrim(p_storage_key), '') IS NULL THEN
    RAISE EXCEPTION 'provider document needs a bucket and a key';
  END IF;

  SELECT d.id, d.uid INTO v_doc, v_uid
    FROM documents.documents d
   WHERE d.tenant_id = p_tenant_id
     AND d.metadata ->> 'provider' = p_provider
     AND d.metadata ->> 'external_id' = p_external_id;
  IF v_uid IS NOT NULL THEN
    -- Already filed. A joint owner listed on a later run still needs the subject
    -- link; the document row stays the one row.
    IF p_contact_id IS NOT NULL THEN
      UPDATE documents.documents
         SET intended_contact_id = p_contact_id
       WHERE id = v_doc AND intended_contact_id IS NULL;
      INSERT INTO documents.document_contacts (document_id, tenant_id, contact_id, role)
      VALUES (v_doc, p_tenant_id, p_contact_id, 'subject')
      ON CONFLICT (document_id, contact_id, role) DO NOTHING;
    END IF;
    RETURN v_uid;
  END IF;

  -- The picker reads documents.document_types. Registering the slug here means a
  -- contract note is a known tag without a human having to add it. The unique key
  -- is (tenant, kind, slug) since 0040 — ON CONFLICT (tenant, slug) matches no
  -- constraint and the insert fails. ON CONFLICT leaves a label the firm has
  -- already renamed alone.
  INSERT INTO documents.document_types (tenant_id, kind, slug, label, sort_order)
  VALUES (p_tenant_id, 'document', p_document_type, p_type_label, 100)
  ON CONFLICT (tenant_id, kind, slug) DO NOTHING;

  v_meta := coalesce(p_metadata, '{}'::jsonb)
            || jsonb_build_object('provider', p_provider, 'external_id', p_external_id);

  INSERT INTO documents.files
    (tenant_id, bucket, storage_key, content_type, size_bytes, sha256)
  VALUES
    (p_tenant_id, p_bucket, p_storage_key, p_content_type, p_size_bytes, p_sha256)
  RETURNING id INTO v_file;

  INSERT INTO documents.documents
    (tenant_id, document_type, source, name, status,
     original_file_id, pdf_file_id, intended_contact_id, deletable, metadata, generated_at)
  VALUES
    (p_tenant_id, p_document_type, 'provider', p_name, 'generated',
     v_file,
     CASE WHEN p_content_type = 'application/pdf' THEN v_file END,
     p_contact_id, false, v_meta, now())
  RETURNING id, uid INTO v_doc, v_uid;

  -- 'subject' is visibility without the claim of a send (0105). A contract note is
  -- the client's document; it is not something an adviser has posted to them. A firm
  -- pack has no contact, so it stays staff-visible and is not filed under anyone.
  IF p_contact_id IS NOT NULL THEN
    INSERT INTO documents.document_contacts (document_id, tenant_id, contact_id, role)
    VALUES (v_doc, p_tenant_id, p_contact_id, 'subject');
  END IF;

  RETURN v_uid;
EXCEPTION
  WHEN unique_violation THEN
    -- A concurrent run filed it between the lookup and the insert. The file row
    -- from THIS attempt rolls back with the savepoint; return the winner.
    SELECT d.id, d.uid INTO v_doc, v_uid
      FROM documents.documents d
     WHERE d.tenant_id = p_tenant_id
       AND d.metadata ->> 'provider' = p_provider
       AND d.metadata ->> 'external_id' = p_external_id;
    IF v_uid IS NULL THEN
      RAISE;
    END IF;
    IF p_contact_id IS NOT NULL THEN
      UPDATE documents.documents
         SET intended_contact_id = p_contact_id
       WHERE id = v_doc AND intended_contact_id IS NULL;
      INSERT INTO documents.document_contacts (document_id, tenant_id, contact_id, role)
      VALUES (v_doc, p_tenant_id, p_contact_id, 'subject')
      ON CONFLICT (document_id, contact_id, role) DO NOTHING;
    END IF;
    RETURN v_uid;
END;
$fn$;

-- Owned by service_role: a SECURITY DEFINER function runs as its owner, and this
-- one must reach documents.*. Left owned by migrator it would run with the
-- migration role's rights, which FORCE RLS hides every row from.
--
-- The new owner must hold CREATE on the schema or ALTER OWNER is refused
-- ("permission denied for schema documents"). USAGE alone, which 0005 granted,
-- is not enough.
GRANT CREATE ON SCHEMA documents TO service_role;
ALTER FUNCTION documents.ingest_provider_document(
  int, int, text, text, text, bigint, text, text, text, text, text, text, jsonb
) OWNER TO service_role;

-- USAGE on the schema is what makes the function callable. It grants nothing on
-- the tables: book_writer still cannot SELECT a document.
GRANT USAGE ON SCHEMA documents TO book_writer;

REVOKE ALL ON FUNCTION documents.ingest_provider_document(
  int, int, text, text, text, bigint, text, text, text, text, text, text, jsonb
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION documents.ingest_provider_document(
  int, int, text, text, text, bigint, text, text, text, text, text, text, jsonb
) TO book_writer, service_role;

COMMENT ON FUNCTION documents.ingest_provider_document(
  int, int, text, text, text, bigint, text, text, text, text, text, text, jsonb
) IS
  'File one custodian document into a tenant library: documents.files, a '
  'source=provider documents.documents row (not deletable), and a subject link when a '
  'contact is given. A NULL contact is a firm-level document. A repeat call adds a '
  'further subject link and does not insert again. Idempotent on (tenant, provider, '
  'external id). The only documents privilege book_writer holds.';
