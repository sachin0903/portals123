-- 0066_notification_rls_scope -- close a personal-feed leak in the 0063 notification policies.
--
-- 0063 scoped a row to a caller as `recipient_user_id = auth.uid() OR recipient_contact_id =
-- ANY(auth.contact_scope())`. But a STAFF adviser's contact_scope() includes their whole book, so
-- that second branch let an adviser read (and mark) notifications addressed to their CLIENTS — a
-- notification is personal, and a client's "a document was shared with you" is not their adviser's
-- to see. It only surfaced once a staff user actually advised the contact (tests/test_notifications
-- ::test_rls_scopes_to_the_recipient, which now seeds that link).
--
-- Fix: the contact branch is for CLIENT-PORTAL principals only. A client-portal session has an EMPTY
-- auth.tenant_lineage() (the 0004 trust boundary); a staff session never does. So gate the contact
-- branch on `auth.tenant_lineage() = '{}'` — staff then match by auth.uid() alone (their own rows),
-- clients by contact scope alone. Same fix on the preferences table (own-row writes).

DROP POLICY IF EXISTS notifications_read ON notifications.notifications;
CREATE POLICY notifications_read ON notifications.notifications FOR SELECT TO authenticated USING (
  recipient_user_id = auth.uid()
  OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope())));

DROP POLICY IF EXISTS notifications_mark ON notifications.notifications;
CREATE POLICY notifications_mark ON notifications.notifications FOR UPDATE TO authenticated
  USING (
    recipient_user_id = auth.uid()
    OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope())))
  WITH CHECK (
    recipient_user_id = auth.uid()
    OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope())));

DROP POLICY IF EXISTS preferences_own ON notifications.preferences;
CREATE POLICY preferences_own ON notifications.preferences FOR ALL TO authenticated
  USING (
    recipient_user_id = auth.uid()
    OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope())))
  WITH CHECK (
    recipient_user_id = auth.uid()
    OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope())));
