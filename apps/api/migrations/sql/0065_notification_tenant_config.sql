-- 0065_notification_tenant_config -- the FIRM-level layer of notification preference resolution.
--
-- Preference resolution has three layers, most-specific wins (docs/notifications.md):
--   registry default (code) -> THIS (tenant config, ops-editable) -> per-user override (0063).
-- Per (tenant, category): an `enabled` firm-wide off-switch (disabled => nothing for anyone in the
-- firm, whatever a user set), plus per-channel defaults (`in_app`/`email`, NULL = fall through to
-- the registry default). Configured by Graphene operations on the admin tenant page, the way
-- branding/emails/settings are; a firm may later self-serve the same rows.
--
-- Writer is service_role (the admin path writes elevated); the notifications/email subscribers read
-- it on their service_role sessions. A tenant-lineage SELECT policy lets a firm read its OWN config
-- (the platform surface + future self-service) but never another's.

CREATE TABLE notifications.tenant_config (
  id         bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  tenant_id  int NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  category   text NOT NULL,
  enabled    boolean NOT NULL DEFAULT true,   -- firm-wide off-switch for the category
  in_app     boolean,                         -- NULL = registry default
  email      boolean,                         -- NULL = registry default
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, category)
);

ALTER TABLE notifications.tenant_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications.tenant_config FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_config_read ON notifications.tenant_config FOR SELECT TO authenticated
  USING (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT ON notifications.tenant_config TO authenticated;   -- read own firm's config
GRANT ALL ON notifications.tenant_config TO service_role;       -- admin writes + subscribers read
