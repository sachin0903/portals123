-- 0084_notification_rule_versions -- a full audit of every notification-rule change.
--
-- A rule is a single row in notifications.rules with one `authored_by` (who FIRST made it), which
-- could not answer "who changed this, and to what". This snapshots the rule on every create /
-- update / delete, so a firm can see exactly what changed and whether GRAPHENE or the firm made
-- each change — the notification-rule equivalent of email.template_versions.
--
-- `rule_uid` is NOT a foreign key: a version outlives the rule (a 'deleted' snapshot must survive
-- the row it describes). `authored_by_scope` records the TIER that made THAT change ('operations' =
-- Graphene via the admin portal, 'firm' = the firm's own staff) — distinct from the rule's original
-- `authored_by`, so an edit by the firm to a Graphene-authored rule is attributed to the firm.

CREATE TABLE notifications.rule_versions (
  id            bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  uid           text NOT NULL UNIQUE,                        -- 'nrulev_...'
  tenant_id     int  NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  rule_uid      text NOT NULL,                               -- the rule this version describes (no FK)
  version_number int NOT NULL,
  action        text NOT NULL CHECK (action IN ('created', 'updated', 'deleted')),

  -- Snapshot of the rule's authored fields at this version.
  trigger_key   text NOT NULL,
  audience      text NOT NULL,
  role_slug     text,
  label         text,
  title         text NOT NULL,
  body          text,
  link          text,
  priority      text NOT NULL,
  default_in_app boolean NOT NULL,
  default_email  boolean NOT NULL,
  mandatory      boolean NOT NULL,
  firm_editable  boolean NOT NULL,
  enabled        boolean NOT NULL,
  email_code     text,

  -- Who made this change, and when.
  authored_by_scope text NOT NULL CHECK (authored_by_scope IN ('operations', 'firm')),
  created_by        uuid,                                    -- the acting user (auth.uid()); nullable
  created_at        timestamptz NOT NULL DEFAULT now(),

  UNIQUE (rule_uid, version_number)
);

CREATE INDEX rule_versions_rule_idx
  ON notifications.rule_versions (rule_uid, version_number DESC);

-- RLS mirrors notifications.rules exactly (0068): staff read their tenant lineage's versions; a
-- CLIENT-portal principal (empty lineage) reads only their own tenant's CLIENT-audience versions —
-- the firm sees the history INCLUDING Graphene's changes, which is the whole point. Writes are
-- service_role only (the admin_session + firm_session paths, gated in the app layer).
ALTER TABLE notifications.rule_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications.rule_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY rule_versions_read ON notifications.rule_versions FOR SELECT TO authenticated USING (
  tenant_id = ANY (auth.tenant_lineage())
  OR (auth.tenant_lineage() = '{}'::int[] AND tenant_id = auth.tenant_id() AND audience = 'client'));
GRANT SELECT ON notifications.rule_versions TO authenticated;
GRANT ALL ON notifications.rule_versions TO service_role;
