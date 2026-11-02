-- 0015_documents_crm_contacts: bind document collaboration + requests to the TRUE client model.
--
-- 0006/0008 shipped an INTERIM binding: documents.document_contacts.user_id and
-- documents.requests.user_id referenced auth.users (a login) as a stand-in for "client", with
-- client-visibility RLS keyed on auth.uid(). The real client entity is crm.contacts (0002), and
-- client-facing RLS scopes through the resolved contact_ids claim -> auth.contact_scope() (a login
-- maps to its parties via crm.contact_users). This migration reconciles the document tables onto
-- crm.contacts: the link is now the CONTACT, and a logged-in client sees a document/request when
-- its contact is in their contact_scope. The interim links held only fictitious dev data, so they
-- are dropped rather than backfilled.

-- ---- document_contacts: user_id (auth.users) -> contact_id (crm.contacts) -------------------
-- documents_read references document_contacts in a subquery, so drop that policy before the table.
DROP POLICY documents_read ON documents.documents;
DROP TABLE documents.document_contacts;  -- interim (fictitious) links; recreated on crm.contacts

CREATE TABLE documents.document_contacts (
    document_id uuid        NOT NULL REFERENCES documents.documents(id) ON DELETE CASCADE,
    tenant_id   int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    contact_id  int         NOT NULL REFERENCES crm.contacts(id) ON DELETE CASCADE,
    role        text        NOT NULL DEFAULT 'recipient',
    added_by    uuid        REFERENCES auth.users(id),
    added_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, contact_id, role)
);
CREATE INDEX document_contacts_contact_id_idx ON documents.document_contacts(contact_id);

-- Staff by tenant; the linked client reads their own via contact_scope. Scoped by the local
-- tenant_id/contact_id columns (no subquery into documents) to avoid RLS recursion.
ALTER TABLE documents.document_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.document_contacts FORCE ROW LEVEL SECURITY;
CREATE POLICY document_contacts_read ON documents.document_contacts FOR SELECT TO authenticated USING (
    tenant_id = ANY (auth.tenant_lineage()) OR contact_id = ANY (auth.contact_scope())
);
CREATE POLICY document_contacts_write ON documents.document_contacts FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT, INSERT, UPDATE, DELETE ON documents.document_contacts TO authenticated;
GRANT ALL ON documents.document_contacts TO service_role;

-- documents_read: client branch now matches on the contact (contact_scope), not the login.
CREATE POLICY documents_read ON documents.documents FOR SELECT TO authenticated USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR EXISTS (
        SELECT 1 FROM documents.document_contacts dc
        WHERE dc.document_id = documents.documents.id
          AND dc.contact_id = ANY (auth.contact_scope())
    )
);

-- ---- requests: user_id (auth.users) -> contact_id (crm.contacts) ----------------------------
DROP POLICY requests_read ON documents.requests;
DROP POLICY request_items_read ON documents.request_items;

TRUNCATE documents.requests CASCADE;  -- interim (fictitious) rows; cascades to request_items
ALTER TABLE documents.requests DROP COLUMN user_id;  -- also drops the requests_user_idx index
ALTER TABLE documents.requests
    ADD COLUMN contact_id int NOT NULL REFERENCES crm.contacts(id) ON DELETE CASCADE;
CREATE INDEX requests_contact_idx ON documents.requests(contact_id, status);

-- requested_by stays auth.users (the STAFF member who raised the request — an actor, not a client).
CREATE POLICY requests_read ON documents.requests FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()) OR contact_id = ANY (auth.contact_scope()));
CREATE POLICY request_items_read ON documents.request_items FOR SELECT TO authenticated
    USING (
        tenant_id = ANY (auth.tenant_lineage())
        OR EXISTS (
            SELECT 1 FROM documents.requests r
            WHERE r.id = request_id AND r.contact_id = ANY (auth.contact_scope())
        )
    );

-- ---- form_responses: user_id (auth.users) -> contact_id (crm.contacts) ----------------------
-- Already emptied by the `TRUNCATE documents.requests CASCADE` above (requests -> request_items ->
-- form_responses is an ON DELETE CASCADE chain), so this is a clean column swap. submitted_by stays
-- auth.users (the actor who submitted).
DROP POLICY form_responses_read ON documents.form_responses;
ALTER TABLE documents.form_responses DROP COLUMN user_id;
-- NULLABLE: a standalone form response (staff-submitted via the forms router) has NO client
-- contact; only request-driven client fills carry one. ON DELETE SET NULL keeps the submitted
-- record if the contact is later removed.
ALTER TABLE documents.form_responses
    ADD COLUMN contact_id int REFERENCES crm.contacts(id) ON DELETE SET NULL;
CREATE POLICY form_responses_read ON documents.form_responses FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()) OR contact_id = ANY (auth.contact_scope()));
