-- 0067_notification_user_scope -- the SYMMETRIC half of 0066: the user-recipient branch is
-- STAFF-only, so a staff user's own notifications never show on the CLIENT portal.
--
-- 0066 gated the CONTACT branch to client-portal principals (empty auth.tenant_lineage()), but left
-- the USER branch `recipient_user_id = auth.uid()` ungated. So a person who is BOTH staff and signed
-- into the client portal (or any client session whose auth.uid() coincides with a staff user id) saw
-- their STAFF notifications — e.g. "a task was assigned to you" — inside the client portal. A
-- user-recipient notification is a STAFF notification; it belongs only to a staff-portal session.
--
-- Fix: each branch is gated to its principal kind. Staff (non-empty lineage) match by auth.uid();
-- clients (empty lineage, the 0004 trust boundary) match by contact scope; neither sees the other.
-- Same recreation on the mark (UPDATE) policy.

DROP POLICY IF EXISTS notifications_read ON notifications.notifications;
CREATE POLICY notifications_read ON notifications.notifications FOR SELECT TO authenticated USING (
  (auth.tenant_lineage() <> '{}'::int[] AND recipient_user_id = auth.uid())
  OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope())));

DROP POLICY IF EXISTS notifications_mark ON notifications.notifications;
CREATE POLICY notifications_mark ON notifications.notifications FOR UPDATE TO authenticated
  USING (
    (auth.tenant_lineage() <> '{}'::int[] AND recipient_user_id = auth.uid())
    OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope())))
  WITH CHECK (
    (auth.tenant_lineage() <> '{}'::int[] AND recipient_user_id = auth.uid())
    OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope())));
