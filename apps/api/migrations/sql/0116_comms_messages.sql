-- 0116_comms_messages -- the outbound-email ledger: one row per send, and the provider's own
-- delivery callbacks against it.
--
-- WHY THIS EXISTS. `app/email.py::send_email` is the single chokepoint every send funnels through,
-- and until now it recorded nothing: it returned a bare boolean, discarded the id Resend answers
-- with, and left the only durable evidence of a client email as a `flows.outbox` row reading
-- 'delivered' -- which means "Resend accepted it", not "it arrived". A firm asking "did my client
-- get the suitability report" had nothing to read. This is that record.
--
-- Two tables, and the split is the one flows.domain_events / flows.outbox already makes:
--   comms.messages        the FACT of a send, plus a denormalised current status for the feed.
--   comms.message_events  the provider's append-only callbacks, one row each.
--
-- The denormalised status could be derived from the events, and deliberately is not: the feed's hot
-- path is "the firm's last 50 messages with their state", and deriving that per row is a join and a
-- sort per message for a value that changes at most six times in its life. What keeps the two
-- honest is that the status only ever ADVANCES (see `status` below) -- so a re-delivered or late
-- callback can never walk a row backwards.
--
-- WRITER IS service_role ALONE (`app/comms/ledger.py`, and the Resend webhook). authenticated gets
-- SELECT and nothing else: a firm reads its own ledger and cannot edit the record of what was sent.
--
-- No audit.attach, for 0063's reason: this table IS a record plane, and DML triggers on it would
-- double the write volume of a busy derived table for no evidentiary gain.

CREATE SCHEMA IF NOT EXISTS comms;

-- ---- comms.messages -- one row per outbound email, whatever its outcome --------------------
CREATE TABLE comms.messages (
  id   bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,   -- the feed's keyset cursor
  uid  text NOT NULL UNIQUE,                              -- bare 12-char new_id(), no prefix

  -- NULLABLE on purpose. A Graphene-side send has no firm -- the AML monitoring alert to
  -- COMPLIANCE_EMAIL is the live example -- and 0038 records what happens when a provider-shaped
  -- mirror makes its tenant NOT NULL: the unattributable row is DROPPED, and the gap it exists to
  -- show becomes invisible. Such a row is readable by Graphene operations alone (see the policy).
  tenant_id int REFERENCES auth.tenants(id) ON DELETE CASCADE,

  provider            text NOT NULL DEFAULT 'resend',
  -- The webhook's lookup key, read off Resend's own response body. NULL whenever nothing was
  -- posted (unconfigured / suppressed) or the post never returned an id (failed).
  provider_message_id text,

  -- Who it went to, discriminated as tasks.tasks' assignee and notifications' recipient are.
  -- 'external' carries neither id: an invite goes to an address with no crm.contacts row yet,
  -- which is the entire point of an invite.
  recipient_kind       text NOT NULL CHECK (recipient_kind IN ('contact', 'user', 'external')),
  recipient_contact_id int  REFERENCES crm.contacts(id) ON DELETE SET NULL,
  recipient_user_id    uuid REFERENCES auth.users(id)   ON DELETE SET NULL,
  -- The CHECK forbids the WRONG id being set; it deliberately does NOT require the right one to be
  -- present. Both FKs are ON DELETE SET NULL because this is a historical record that must outlive
  -- its subject -- and a CHECK demanding `recipient_contact_id IS NOT NULL` for kind 'contact'
  -- would fight exactly that, making the SET NULL violate the constraint and the contact delete
  -- fail. The ledger keeps the address it mailed regardless, which is what the row is FOR.
  CONSTRAINT messages_recipient_ref CHECK (
    (recipient_kind = 'contact'  AND recipient_user_id IS NULL) OR
    (recipient_kind = 'user'     AND recipient_contact_id IS NULL) OR
    (recipient_kind = 'external' AND recipient_contact_id IS NULL AND recipient_user_id IS NULL)),

  -- The normalised single address `_single_address` returned and `send_email` actually POSTed --
  -- never the caller's raw `to`. The domain sits denormalised beside it because it is the one part
  -- of a recipient that may be printed in a log line, and the feed's filters read it constantly.
  to_address text NOT NULL,
  to_domain  text NOT NULL,
  subject    text NOT NULL,

  -- Why it was sent. All nullable: a transactional send (invite, password reset) has a
  -- template_code and no event, and a flow's ad-hoc email action has neither.
  template_code text,                                     -- email_catalog.KNOWN_CODES.code
  event_type    text,                                     -- the audit.actions event that caused it
  -- notifications.rules.uid. No FK on purpose: a rule may be deleted and this history must survive,
  -- exactly as notifications.rule_versions.rule_uid carries none for the same reason.
  rule_uid      text,
  category      text,                                     -- the rule's category, for filtering
  source        text NOT NULL DEFAULT 'app' CHECK (source IN ('app', 'worker')),

  -- STATUS IS MONOTONIC BY RANK -- the contributions.ml_status rule. Ranked
  -- sent < delayed < delivered; bounced/complained win from ANY rank (a bounce after a delivery is
  -- real: a deferred address that later hard-bounces); and unconfigured/suppressed/failed are set
  -- at INSERT and never advance, because nothing was posted for a provider to have an opinion
  -- about. The rank lives once, in app/comms/ledger.py::STATUS_RANK -- a CHECK cannot express
  -- "only forwards" without a trigger, and a trigger here would be a second writer.
  --
  -- Edited IN PLACE when read receipts were dropped, rather than reverted by a later migration:
  -- 0111/0112 are branch-local and have never reached an environment, so there is nothing to
  -- expand/contract against. The one database that had run them was aligned by hand. Had this
  -- shipped anywhere, the drop would have to be its own migration.
  --
  -- NO 'opened' OR 'clicked', and no stamp columns for them: this product does not do read
  -- receipts. An open is a tracking pixel loading, which Apple Mail Privacy Protection fires for
  -- every Apple Mail user whether or not a human looked, and which a client blocking images never
  -- fires however carefully it was read -- evidence in neither direction, bought with a tracking
  -- pixel in a client's email. app/comms/ledger.py::NOT_TRACKED drops those callbacks at the door.
  status text NOT NULL CHECK (status IN (
    'unconfigured', 'suppressed', 'failed', 'sent', 'delivered', 'bounced', 'complained')),
  -- The suppression reason (which names the SETTING, never the recipient) or the failure. It is
  -- what stops a dev box's wall of 'suppressed' reading as "every email broke".
  error text,

  -- The rendered body, as sent. Capped app-side (ledger.MAX_HTML_BYTES); NULL above the cap rather
  -- than truncated, because half an email renders as a broken one and reads as corruption.
  html text,

  created_at    timestamptz NOT NULL DEFAULT now(),
  sent_at       timestamptz,
  delivered_at  timestamptz,
  bounced_at    timestamptz,
  complained_at timestamptz
);

-- The feed: the firm's own messages, newest first.
CREATE INDEX messages_tenant_idx ON comms.messages (tenant_id, id DESC);
-- THE SAFETY NET'S SWEEP (app/comms/poll.py): the messages still waiting on a delivery state.
-- Partial on the PENDING set, not on "has a provider id" -- the latter covers every successful
-- send for ever, so a poll looking for the handful that are unresolved would scan more of it each
-- month. This index holds only what is actually pending (near-empty wherever the webhook works),
-- and its `id` order serves the sweep's cursor and ORDER BY directly, so a pass costs the same
-- whether the table holds a thousand rows or a million. That is what makes a 60-second cadence
-- free rather than something to budget for.
CREATE INDEX messages_pending_idx ON comms.messages (id)
  WHERE provider_message_id IS NOT NULL AND status IN ('sent', 'delayed');
-- The webhook's only lookup. Partial: a suppressed/unconfigured row never has one.
CREATE INDEX messages_provider_message_id_idx ON comms.messages (provider_message_id)
  WHERE provider_message_id IS NOT NULL;
-- The event registry's "N emails in the last 30 days" count, per event.
CREATE INDEX messages_tenant_event_idx ON comms.messages (tenant_id, event_type, id DESC)
  WHERE event_type IS NOT NULL;
-- A future per-client rail on the client record.
CREATE INDEX messages_tenant_contact_idx ON comms.messages (tenant_id, recipient_contact_id, id DESC)
  WHERE recipient_contact_id IS NOT NULL;

-- ---- comms.message_events -- the provider's callbacks, append-only ------------------------
CREATE TABLE comms.message_events (
  id         bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  message_id bigint NOT NULL REFERENCES comms.messages(id) ON DELETE CASCADE,
  -- Denormalised for RLS, the notifications.notifications idiom: scoping through the parent would
  -- put an EXISTS subquery on every read of the detail pane, and the value cannot drift (a
  -- message's tenant never changes).
  tenant_id  int REFERENCES auth.tenants(id) ON DELETE CASCADE,

  -- Resend's own vocabulary, stored verbatim, and NOT constrained to a list: a provider may add an
  -- event type at any time, and refusing to record one we do not recognise would lose the very
  -- callback that explains a delivery failure. The app decides which types MEAN something.
  event_type text NOT NULL,
  -- The provider's id for this callback (Svix's `svix-id`) -- the idempotency handle, because
  -- Resend delivers at least once and the same callback arrives repeatedly.
  provider_event_id text,
  occurred_at timestamptz,                                -- as the provider timed it, not as we read it
  payload     jsonb NOT NULL DEFAULT '{}',
  created_at  timestamptz NOT NULL DEFAULT now(),

  -- What makes redelivery a no-op. NULLS NOT DISTINCT for 0063's reason: a callback carrying no id
  -- would otherwise be unique every time and defeat the collapse entirely.
  CONSTRAINT message_events_provider_key UNIQUE NULLS NOT DISTINCT (message_id, provider_event_id)
);

CREATE INDEX message_events_message_idx ON comms.message_events (message_id, id);

-- ============================================================================================
-- RLS
-- ============================================================================================
-- A firm reads its own ledger. The scope is auth.tenant_lineage() and DELIBERATELY OMITS
-- auth.contact_scope() -- the same omission 0066/0067 had to make for notifications, and for a
-- sharper reason here: an adviser's contact scope is their whole book, so including it would let a
-- CLIENT principal read the ledger of mail sent about them and, through `html`, its contents.
-- A client has their own inbox; this is the firm's operational record. Do not add contact_scope
-- for symmetry with 0026 -- docs/aml.md makes the same argument about screening.
--
-- A tenant_id IS NULL row (a Graphene-side send) is readable by Graphene operations alone, keyed on
-- auth.is_system() -- the 0038 posture, where is_system() is the `system` PERMISSION and not
-- something a firm reaches by naming one of its roles "admin".
ALTER TABLE comms.messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE comms.messages FORCE ROW LEVEL SECURITY;
CREATE POLICY messages_read ON comms.messages FOR SELECT TO authenticated USING (
  (tenant_id IS NOT NULL AND tenant_id = ANY (auth.tenant_lineage()))
  OR (tenant_id IS NULL AND auth.is_system()));
-- SELECT only. The ledger is the record of what was sent; nobody signed in may edit it.
GRANT SELECT ON comms.messages TO authenticated;
GRANT ALL ON comms.messages TO service_role;

ALTER TABLE comms.message_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE comms.message_events FORCE ROW LEVEL SECURITY;
CREATE POLICY message_events_read ON comms.message_events FOR SELECT TO authenticated USING (
  (tenant_id IS NOT NULL AND tenant_id = ANY (auth.tenant_lineage()))
  OR (tenant_id IS NULL AND auth.is_system()));
GRANT SELECT ON comms.message_events TO authenticated;
GRANT ALL ON comms.message_events TO service_role;

GRANT USAGE ON SCHEMA comms TO authenticated, service_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA comms TO authenticated, service_role;
