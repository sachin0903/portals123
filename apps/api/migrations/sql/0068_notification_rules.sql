-- 0068_notification_rules -- firm-configurable notification rules (the authorable builder).
--
-- A rule is one authored notification: a TRIGGER (a curated key over the audit.actions vocabulary,
-- app/notifications/catalogue.py) → one AUDIENCE (the single recipient kind; adviser+client = two
-- rules, the deliberately simpler model) → copy + channel defaults + guardrails. Built-in behaviour
-- stays in code (the catalogue defaults); these rows are a tenant's ADDITIONS/OVERRIDES.
--
-- Guardrails an author sets: default_in_app/default_email (opt-out vs opt-in), `mandatory` (the
-- recipient cannot disable it), `client_can_disable` (a client-audience rule may be turned off by
-- the client), `firm_editable` (Graphene ops let the firm edit this one). `authored_by` records
-- which tier created it.
--
-- Scoping mirrors documents: staff read their tenant lineage's rules; a CLIENT-portal principal
-- (empty auth.tenant_lineage(), 0004) reads only the CLIENT-audience rules of their own tenant (for
-- the client preferences page). Writes are service_role (the admin_session + firm_session paths,
-- both elevated), gated in the app layer by permission + firm_editable — the documents pattern.

CREATE TABLE notifications.rules (
  id          bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  uid         text NOT NULL UNIQUE,                          -- 'nrule_...'
  tenant_id   int  NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,

  trigger_key text NOT NULL,                                 -- catalogue key, e.g. 'document.shared'
  audience    text NOT NULL CHECK (audience IN ('client', 'adviser', 'role', 'actor', 'assignee')),
  role_slug   text,                                          -- required iff audience = 'role'
  CONSTRAINT rules_role_slug CHECK ((audience = 'role') = (role_slug IS NOT NULL)),

  label       text NOT NULL,
  title       text NOT NULL,
  body        text,
  link        text,
  priority    text NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high')),

  default_in_app     boolean NOT NULL DEFAULT true,
  default_email      boolean NOT NULL DEFAULT false,
  mandatory          boolean NOT NULL DEFAULT false,          -- recipient cannot disable
  client_can_disable boolean NOT NULL DEFAULT true,
  firm_editable      boolean NOT NULL DEFAULT true,
  authored_by        text NOT NULL DEFAULT 'graphene' CHECK (authored_by IN ('graphene', 'firm')),
  enabled            boolean NOT NULL DEFAULT true,

  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX rules_tenant_trigger_idx
  ON notifications.rules (tenant_id, trigger_key) WHERE enabled;

ALTER TABLE notifications.rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications.rules FORCE ROW LEVEL SECURITY;
-- Staff read their tenant lineage's rules; a client reads only their tenant's CLIENT-audience rules
-- (the client-preferences surface). Writes are service_role only (app-layer permission gating).
CREATE POLICY rules_read ON notifications.rules FOR SELECT TO authenticated USING (
  tenant_id = ANY (auth.tenant_lineage())
  OR (auth.tenant_lineage() = '{}'::int[] AND tenant_id = auth.tenant_id() AND audience = 'client'));
GRANT SELECT ON notifications.rules TO authenticated;
GRANT ALL ON notifications.rules TO service_role;
