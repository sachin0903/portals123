-- The custodian organisation an adviser opens new clients under.
--
-- provider_refs is the person's own id at the custodian (an SEI employeeEntityId). This is the
-- other axis: which of the firm's Seccl nodes, or which SEI DSR team, their new clients and
-- accounts are opened on. A firm with more than one of either cannot keep taking the first id
-- in tenant_platforms.config.
--
-- Shape: {"seccl": "<platform.nodes.key>", "sei": "<dsr team key>"}. Chosen from that firm's
-- enabled node_ids / dsr_ids. Absent means the adviser has not chosen.

SET LOCAL lock_timeout = '5s';

ALTER TABLE auth.users
  ADD COLUMN provider_orgs jsonb;

ALTER TABLE auth.users
  ADD CONSTRAINT users_provider_orgs_object
  CHECK (provider_orgs IS NULL OR jsonb_typeof(provider_orgs) = 'object');

COMMENT ON COLUMN auth.users.provider_orgs IS
  'The custodian organisation this adviser opens new clients under. seccl is a node key, sei is a DSR team id, each chosen from the firm''s enabled list. Not the person''s own id at the custodian — that stays in provider_refs.';
