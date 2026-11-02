-- 0070 -- notifications.tenant_config gains an AUDIENCE split + a MANDATORY flag.
--
-- The admin defaults matrix (docs/notifications.md) now sets a firm's defaults SEPARATELY for the
-- advisers and the clients of a category, and can mark a category MANDATORY per audience — which
-- forces the default even over a recipient's prior opt-out (the resolver already honours a
-- mandatory rule; tenant_config.mandatory folds into it). Existing rows default to the 'client'
-- audience; the 'adviser' row falls back to the registry default until configured. Expand phase.
ALTER TABLE notifications.tenant_config
  ADD COLUMN IF NOT EXISTS audience  text    NOT NULL DEFAULT 'client',
  ADD COLUMN IF NOT EXISTS mandatory boolean NOT NULL DEFAULT false;

ALTER TABLE notifications.tenant_config
  DROP CONSTRAINT IF EXISTS tenant_config_tenant_id_category_key;

ALTER TABLE notifications.tenant_config
  ADD CONSTRAINT tenant_config_key UNIQUE (tenant_id, category, audience);

ALTER TABLE notifications.tenant_config
  ADD CONSTRAINT tenant_config_audience_valid CHECK (audience IN ('client', 'adviser'));
