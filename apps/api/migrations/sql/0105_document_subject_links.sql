-- documents: backfill the 'subject' link for every document already FILED UNDER a client.
--
-- RETROACTIVE AND DELIBERATE. This migration changes what existing clients can see: every
-- document with a non-null `intended_contact_id` becomes visible to that client, without anyone
-- having sent it. That is the point — a client should see everything filed against them, and
-- `intended_contact_id` has been recording exactly that since 0021 while granting no visibility at
-- all. Filing under a client is already gated on the staffer being able to SEE that client
-- (`_assert_visible_contact` on the upload / generate / assign paths), so these rows disclose
-- documents to the very client each one was recorded as being for.
--
-- Why a link row and not a policy change: the client branch of `documents_read` (last written by
-- 0021_documents_scope) reads
--
--     OR EXISTS (SELECT 1 FROM documents.document_contacts dc
--                 WHERE dc.document_id = documents.documents.id
--                   AND dc.contact_id = ANY (auth.contact_scope()))
--
-- It filters on `contact_id` ALONE, with no role predicate, so a link is sufficient and no policy
-- needs touching. Nothing about the RLS surface changes here — only rows.
--
-- Why role='subject' and NOT 'recipient': 'recipient' is read as evidence of a SEND.
-- `routers/documents._SELECT` computes `sent_to_client`, `sent_at` and the `recipients` string by
-- filtering `role = 'recipient'`, and `worker._client_context` picks the first 'recipient' as the
-- person to render for. Backfilling as a recipient would report every one of these documents as
-- already sent and list the client among its own recipients, destroying the one signal that
-- answers "has this client actually received it". 'subject' means "filed under this client":
-- visibility without the claim of delivery. The value is not new — `illustrations._store_pdf` has
-- filed its PDFs this way from the start.
--
-- `added_by` is left NULL on purpose: no human performed this link, and naming one would be a lie
-- (the same rule `documents/service.py::audit_event` follows for the engine's own writes).
--
-- Idempotent (ON CONFLICT DO NOTHING against the (document_id, contact_id, role) primary key), so
-- a re-run is a no-op and a document the new code has already linked is simply skipped.
--
-- As service_role: documents.documents and documents.document_contacts both have FORCE ROW LEVEL
-- SECURITY and the migrator role is not BYPASSRLS, so without this the INSERT ... SELECT would
-- read zero rows and silently backfill nothing -- the trap 0042's seed and 0059's backfill both
-- document.
SET LOCAL ROLE service_role;

-- `status` matters, and excluding it was a hole. A `queued` document has not rendered and a
-- `failed` one never will, and the client branch of `documents_read` carries NO status predicate
-- (nor does `list_documents`' default `status=active`, which is only `status <> 'archived'`) -- so
-- backfilling either would put a document no adviser has seen into a client's portal, permanently
-- in the failed case. A queued one needs no help here: `worker._persist` files its link when the
-- render succeeds, which is where every filing path now does it.
INSERT INTO documents.document_contacts (document_id, tenant_id, contact_id, role, added_by)
SELECT d.id, d.tenant_id, d.intended_contact_id, 'subject', NULL
  FROM documents.documents d
 WHERE d.intended_contact_id IS NOT NULL
   AND d.status NOT IN ('queued', 'failed')
ON CONFLICT (document_id, contact_id, role) DO NOTHING;

-- Retract the links this backfill's first form created for queued/failed documents where it has
-- already run (this migration was in flight, so that is a dev database, but the statement is cheap
-- and makes a re-run of the file correct rather than merely idempotent). Scoped to `role='subject'`
-- and to a NULL `added_by`, so a 'recipient' row from a real send -- or a subject link a human
-- filed -- is never touched.
DELETE FROM documents.document_contacts dc
 USING documents.documents d
 WHERE d.id = dc.document_id
   AND dc.role = 'subject'
   AND dc.added_by IS NULL
   AND d.status IN ('queued', 'failed');

RESET ROLE;
