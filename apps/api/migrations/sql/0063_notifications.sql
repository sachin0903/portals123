-- 0063_notifications -- the in-app notification centre (event-driven), schema + RLS.
--
-- Notifications are DERIVED from events that already happened: a second cursor consumer tails
-- audit.actions (0041) the way flows.automation does, and a rule registry fans each action out into
-- per-recipient rows here. A handful of worker-side transitions the request-level action stream
-- cannot see (a journey blocking, an AML hit) call the same service primitive directly.
--
-- Three tables:
--   notifications.notifications  one row PER RECIPIENT (read/unread state is per person, so a role
--                                notice fans out to a row each active member holds).
--   notifications.preferences    per-recipient, per-category channel overrides. Absence = the
--                                registry default; only a deviation is stored (the l10n.overrides
--                                idiom), so a new category defaults sensibly with no backfill.
--   notifications.cursor         the durable consumer position over audit.actions -- a verbatim
--                                copy of flows.automation_cursor (0043), including the "seed at the
--                                current head on first run" rule (app-side, not here), so arming
--                                the consumer never replays the whole history of the stream.
--
-- Writer is service_role (the consumer / the notify() service). The ONLY thing a signed-in user may
-- do to their own rows is mark them read/seen -- hence GRANT SELECT, UPDATE and no INSERT/DELETE.
-- No audit.attach here on purpose: a notification is itself the echo of an already-audited action,
-- and attaching DML triggers would double the write volume of the busiest derived table for no
-- evidentiary gain (the mark-read PUT is captured by the action-events middleware regardless).

CREATE SCHEMA IF NOT EXISTS notifications;

-- ---- notifications.notifications -- the per-recipient feed row ------------------------------
CREATE TABLE notifications.notifications (
  id            bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  uid           text NOT NULL UNIQUE,                       -- 'ntf_...'
  tenant_id     int  NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,  -- denormalised for RLS

  -- Who must see it. Discriminated exactly like tasks.tasks' assignee: a staff USER (scoped by
  -- auth.uid()) or a client CONTACT (scoped by auth.contact_scope()). A role is fanned out to one
  -- user row per active member at creation time -- read-state cannot be shared.
  recipient_kind       text NOT NULL CHECK (recipient_kind IN ('user', 'contact')),
  recipient_user_id    uuid REFERENCES auth.users(id)   ON DELETE CASCADE,
  recipient_contact_id int  REFERENCES crm.contacts(id) ON DELETE CASCADE,
  CONSTRAINT notifications_one_recipient CHECK (
    (recipient_kind = 'user'    AND recipient_user_id IS NOT NULL AND recipient_contact_id IS NULL) OR
    (recipient_kind = 'contact' AND recipient_contact_id IS NOT NULL AND recipient_user_id IS NULL)),
  portal        text NOT NULL CHECK (portal IN ('client', 'platform', 'operations')),

  event_type    text NOT NULL,                              -- 'action.<op-id>' or a domain key
  category      text NOT NULL,                              -- registry category; drives preferences
  title         text NOT NULL,
  body          text,
  link          text,                                       -- an in-portal href for the item
  priority      text NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high')),

  -- What it is about (optional), mirroring tasks.tasks' polymorphic subject. No FK: subjects span
  -- namespaces (crm ints, flows uids, documents uuids). The service validates cheaply; a stale
  -- subject on a notification is harmless (it is a historical echo, not a live pointer).
  subject_type  text CHECK (subject_type IN ('contact', 'account', 'execution', 'document', 'transaction')),
  subject_id    text,
  CONSTRAINT notifications_subject_pair CHECK ((subject_type IS NULL) = (subject_id IS NULL)),

  -- Provenance + idempotency handle. source_action_id is the audit.actions.id a tailed row came
  -- from (no FK: audit.actions is a prunable stream, and a cursor/lineage pointer past a pruned row
  -- must stay valid -- the same rule automation_cursor.position lives by).
  source_kind      text NOT NULL CHECK (source_kind IN ('action', 'domain')),
  source_ref       text,
  source_action_id bigint,
  metadata         jsonb NOT NULL DEFAULT '{}',

  seen_at    timestamptz,                                   -- the badge was acknowledged
  read_at    timestamptz,                                   -- the item itself was opened
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX notifications_user_idx ON notifications.notifications (recipient_user_id, id DESC)
  WHERE recipient_user_id IS NOT NULL;
CREATE INDEX notifications_contact_idx ON notifications.notifications (recipient_contact_id, id DESC)
  WHERE recipient_contact_id IS NOT NULL;
-- The unread-count hot path (the bell badge). Partial so it stays tiny -- read rows leave it.
CREATE INDEX notifications_user_unread_idx ON notifications.notifications (recipient_user_id)
  WHERE recipient_user_id IS NOT NULL AND read_at IS NULL;
CREATE INDEX notifications_contact_unread_idx ON notifications.notifications (recipient_contact_id)
  WHERE recipient_contact_id IS NOT NULL AND read_at IS NULL;

-- Idempotency: an automated source raises at most one notification per recipient, so at-least-once
-- redelivery and consumer replay collapse here (the service's ON CONFLICT ... DO NOTHING).
-- NULLS NOT DISTINCT is load-bearing: for a user recipient recipient_contact_id is always NULL, and
-- Postgres' default (NULLs distinct) would treat every such row as unique and defeat the collapse.
CREATE UNIQUE INDEX notifications_source_ref_key ON notifications.notifications
  (recipient_kind, recipient_user_id, recipient_contact_id, source_kind, source_ref)
  NULLS NOT DISTINCT
  WHERE source_ref IS NOT NULL;

-- ---- notifications.preferences -- per-recipient, per-category channel overrides -------------
CREATE TABLE notifications.preferences (
  id            bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  tenant_id     int  NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  recipient_kind       text NOT NULL CHECK (recipient_kind IN ('user', 'contact')),
  recipient_user_id    uuid REFERENCES auth.users(id)   ON DELETE CASCADE,
  recipient_contact_id int  REFERENCES crm.contacts(id) ON DELETE CASCADE,
  CONSTRAINT preferences_one_recipient CHECK (
    (recipient_kind = 'user'    AND recipient_user_id IS NOT NULL AND recipient_contact_id IS NULL) OR
    (recipient_kind = 'contact' AND recipient_contact_id IS NOT NULL AND recipient_user_id IS NULL)),
  category   text NOT NULL,
  in_app     boolean,                                       -- NULL = fall back to the registry default
  email      boolean,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT preferences_key UNIQUE NULLS NOT DISTINCT
    (recipient_kind, recipient_user_id, recipient_contact_id, category)
);

-- ---- notifications.cursor -- durable consumer position over audit.actions -------------------
-- Verbatim of flows.automation_cursor (0043). Keyed by consumer name, no FK to the stream (it may
-- be pruned; a cursor past a pruned row must stay valid). No row seeded here: the consumer creates
-- its own on first run at the CURRENT head of audit.actions (app/notifications/consumer.py), so a
-- fresh deployment notifies on what happens NEXT, never the entire backlog.
CREATE TABLE notifications.cursor (
  consumer   text PRIMARY KEY,
  position   bigint NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- ============================================================================================
-- RLS
-- ============================================================================================

-- notifications: a signed-in principal sees ONLY their own rows -- a staff user by auth.uid(), a
-- client by their contact scope. The contact branch deliberately omits auth.uid() (a client is
-- scoped by contact, exactly as documents/tasks are). The only write authenticated may make is the
-- mark-read/seen UPDATE; inserts are service_role's alone.
ALTER TABLE notifications.notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications.notifications FORCE ROW LEVEL SECURITY;
CREATE POLICY notifications_read ON notifications.notifications FOR SELECT TO authenticated USING (
  recipient_user_id = auth.uid() OR recipient_contact_id = ANY (auth.contact_scope()));
CREATE POLICY notifications_mark ON notifications.notifications FOR UPDATE TO authenticated
  USING     (recipient_user_id = auth.uid() OR recipient_contact_id = ANY (auth.contact_scope()))
  WITH CHECK (recipient_user_id = auth.uid() OR recipient_contact_id = ANY (auth.contact_scope()));
GRANT SELECT, UPDATE ON notifications.notifications TO authenticated;   -- mark read/seen only
GRANT ALL ON notifications.notifications TO service_role;

-- preferences: same own-row scope, and here authenticated genuinely owns the row (it sets its own
-- channels), so it may INSERT/UPDATE its own.
ALTER TABLE notifications.preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications.preferences FORCE ROW LEVEL SECURITY;
CREATE POLICY preferences_own ON notifications.preferences FOR ALL TO authenticated
  USING     (recipient_user_id = auth.uid() OR recipient_contact_id = ANY (auth.contact_scope()))
  WITH CHECK (recipient_user_id = auth.uid() OR recipient_contact_id = ANY (auth.contact_scope()));
GRANT SELECT, INSERT, UPDATE ON notifications.preferences TO authenticated;
GRANT ALL ON notifications.preferences TO service_role;

-- cursor: service_role only -- no authenticated grant and no policy (withholding the privilege
-- beats an always-false policy), FORCE RLS. Mirrors flows.automation_cursor (0043) exactly.
ALTER TABLE notifications.cursor ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications.cursor FORCE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON notifications.cursor TO service_role;

GRANT USAGE ON SCHEMA notifications TO authenticated, service_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA notifications TO authenticated, service_role;
