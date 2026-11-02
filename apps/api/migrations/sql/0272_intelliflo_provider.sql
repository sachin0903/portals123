-- ============================================================================
-- 0272_intelliflo_provider -- `intelliflo` joins `platform.provider_code`.
--
-- "Link to IO" (the client record's Intelliflo Office card) records its match as an ordinary
-- `platform.links` row (entity_type='contact', provider='intelliflo', link_type='primary'), the
-- same shape every other provider link already uses. `platform.links.provider` is the enum (0143),
-- so a value not yet a member cannot be inserted.
--
-- NOT added to `app/providers.py::PROVIDERS`: that tuple is the admin dropdown / per-tenant
-- enablement list (slice config, lake schema, the works), and Intelliflo is neither a custodian
-- nor a scheme administrator a firm enables — it is Graphene-ops's own adviser-identity connection
-- (the `integrations` schema, 0231_integrations_schema), configured once via `INTELLIFLO_*` env,
-- never per tenant. Same reasoning 0197 used for `graphene`.
--
-- ADD VALUE is not used in any later statement of this migration (0168's trap: a new member is
-- unusable until the adding transaction commits, on a type that already existed) -- nothing else
-- here references 'intelliflo'.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TYPE platform.provider_code ADD VALUE IF NOT EXISTS 'intelliflo';
