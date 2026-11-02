-- 0201_provider_document_names — a repeat filing can move the object key.
--
-- Seccl's download name is already the file id. The first tenant copies prefixed
-- it again, so a contract note landed as `0000FLMHS-0000FLMHS.pdf`. The next run
-- files the single name and this function points the library row at it. The
-- caller deletes the doubled object after the row has moved.
--
-- CREATE OR REPLACE keeps the owner and the grants (0191). Replacing it requires
-- owning the function, which membership of service_role satisfies.

SET LOCAL ROLE service_role;

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
    UPDATE documents.files f
       SET bucket = p_bucket,
           storage_key = p_storage_key,
           content_type = p_content_type,
           size_bytes = p_size_bytes,
           sha256 = p_sha256
      FROM documents.documents d
     WHERE d.id = v_doc
       AND f.id IN (d.original_file_id, d.pdf_file_id)
       AND (f.bucket, f.storage_key) IS DISTINCT FROM (p_bucket, p_storage_key);
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

  IF p_contact_id IS NOT NULL THEN
    INSERT INTO documents.document_contacts (document_id, tenant_id, contact_id, role)
    VALUES (v_doc, p_tenant_id, p_contact_id, 'subject');
  END IF;

  RETURN v_uid;
EXCEPTION
  WHEN unique_violation THEN
    SELECT d.id, d.uid INTO v_doc, v_uid
      FROM documents.documents d
     WHERE d.tenant_id = p_tenant_id
       AND d.metadata ->> 'provider' = p_provider
       AND d.metadata ->> 'external_id' = p_external_id;
    IF v_uid IS NULL THEN
      RAISE;
    END IF;
    UPDATE documents.files f
       SET bucket = p_bucket,
           storage_key = p_storage_key,
           content_type = p_content_type,
           size_bytes = p_size_bytes,
           sha256 = p_sha256
      FROM documents.documents d
     WHERE d.id = v_doc
       AND f.id IN (d.original_file_id, d.pdf_file_id)
       AND (f.bucket, f.storage_key) IS DISTINCT FROM (p_bucket, p_storage_key);
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

RESET ROLE;
