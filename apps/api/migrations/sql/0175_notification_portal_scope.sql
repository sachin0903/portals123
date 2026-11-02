-- 0175_notification_portal_scope — a notification belongs to the PORTAL it was raised for.
--
-- 0066 and 0067 taught this policy to tell a staff principal from a client one. It still cannot
-- tell one STAFF portal from another: `auth.tenant_lineage() <> '{}'` is true for a platform
-- session AND for an operations session (0004 empties the lineage only for the client portal), so
-- the user branch reduced to "every row addressed to me, wherever I am signed in".
--
-- The visible effect: an internal ops user who also holds a platform-side recipient identity saw
-- their ADVISER notifications — "a task was assigned to you", a client's document — in the internal
-- ops portal's bell, links and all. Internal ops is not the audience for any of them.
--
-- The `portal` column has recorded the answer since 0063 (`CHECK (portal IN ('client', 'platform',
-- 'operations'))`) and nothing has ever read it: not this policy, not `list_notifications`, not
-- `unread_count`, and there is no index on it. The producers set it correctly — `catalogue.
-- audience_portal` returns 'client' or 'platform', and `consumer.recipient_portal` narrows to
-- 'client' when every recipient is a contact — so the data was already right and only the read was
-- wrong.
--
-- NOTHING IS TORN OUT. 'operations' stays a legal value, the fan-out, the catalogue and the bell in
-- the admin shell are all untouched. Internal ops reads nothing today because nothing writes an
-- 'operations' row — and the day something does, it arrives with no further change here. That is
-- the reason this is a portal PREDICATE rather than a rule that names the ops portal and denies it.
--
-- IN THE POLICY, NOT THE QUERY. The two endpoints that read the feed could each have grown a
-- `portal = :portal` clause, but every future reader would have had to remember it — and the last
-- two corrections to this same boundary (0066, 0067) were both made here for that reason. The mark
-- (UPDATE) policy moves with it: a row you may not read is not a row you may mark read.
--
-- `auth.portal() IS NULL` passes, deliberately. `tenant_session` refuses a session with no portal
-- (`db.py` → "This account has no access to this portal"), so a live request always carries one;
-- a NULL claim is the RLS unit tests and service_role paths, which 0004 documents as reading "as
-- before". Without the guard, `portal = NULL` is NULL — never true — and those suites would see an
-- empty table and report it as a scoping failure rather than a missing claim.

DROP POLICY IF EXISTS notifications_read ON notifications.notifications;
CREATE POLICY notifications_read ON notifications.notifications FOR SELECT TO authenticated USING (
  (auth.portal() IS NULL OR portal = auth.portal())
  AND (
    (auth.tenant_lineage() <> '{}'::int[] AND recipient_user_id = auth.uid())
    OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope()))));

DROP POLICY IF EXISTS notifications_mark ON notifications.notifications;
CREATE POLICY notifications_mark ON notifications.notifications FOR UPDATE TO authenticated
  USING (
    (auth.portal() IS NULL OR portal = auth.portal())
    AND (
      (auth.tenant_lineage() <> '{}'::int[] AND recipient_user_id = auth.uid())
      OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope()))))
  WITH CHECK (
    (auth.portal() IS NULL OR portal = auth.portal())
    AND (
      (auth.tenant_lineage() <> '{}'::int[] AND recipient_user_id = auth.uid())
      OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope()))));

-- The feed and the badge both filter on (recipient, read_at, dismissed_at) and now on `portal`
-- too. 0063's indexes are recipient-led and stay the useful ones; this adds the column to the
-- staff index so the ops portal's "no rows" answer is an index lookup rather than a scan of every
-- notification that user has ever been sent.
CREATE INDEX IF NOT EXISTS notifications_user_portal_idx
  ON notifications.notifications (recipient_user_id, portal, id DESC)
  WHERE recipient_user_id IS NOT NULL;
