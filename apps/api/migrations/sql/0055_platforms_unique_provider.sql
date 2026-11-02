-- platform.platforms.provider is unique -- so the registry can be upserted, and so a provider
-- cannot come to mean two rows.
--
-- 0002 created platform.platforms and nothing has ever written to it. That was invisible until
-- the materialiser landed: `_sync_products` needs a platforms row for the
-- `platform.product_platforms` link (0042/0044 -- the per-platform reference for a shelf item),
-- and with none it rejects the whole entity ("no platform.platforms row for '<provider>'"). So
-- no product was ever created, so no account could resolve one. On this dev database that was
-- 0 of 373 Seccl accounts carrying a `product_id` -- and an account with no product has no
-- `product_type`, which is what `POST /crm/accounts/{id}/drawdown-session` gates on. The
-- drawdown journey answered "Drawdown applies to pension accounts only" for every account in
-- the book, including real SIPPs, because the wrapper was known to the custodian and to
-- nothing here.
--
-- (`platform.products.platform_id` is nullable and legacy -- a shelf item is one thing however
-- many custodians hold it, which is exactly why 0044 moved the per-platform reference out to
-- product_platforms. It is that child row, not the parent column, that needs this table.)
--
-- The seed itself is NOT in this migration. It lives in `app/providers.py::sync_platforms`,
-- upserted on startup beside `permissions.sync_permissions` -- the pattern this repo has
-- already settled on for a registry that is declared in code (`PROVIDERS`): a hand-written
-- INSERT here would drift from that tuple the day a fourth custodian is added. What the
-- migration owns is the constraint that makes the upsert well-defined.
--
-- Deduplicate first: nothing writes this table today, so on every real database this is a
-- no-op, but a UNIQUE on a table that could hold duplicates has to say what happens to them.
-- The lowest id wins, which is what `_platform_id`'s `ORDER BY id LIMIT 1` already assumed --
-- that ordering was the tell that duplicates were reachable at all.
DELETE FROM platform.platforms p
 USING platform.platforms keep
 WHERE p.provider = keep.provider
   AND p.id > keep.id;

-- CONCURRENTLY is not available inside a transaction and this table is empty in every
-- environment, so the plain form is correct here; `lock_timeout` still bounds the wait rather
-- than queueing behind a long reader.
SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.platforms
  ADD CONSTRAINT platforms_provider_key UNIQUE (provider);

COMMENT ON TABLE platform.platforms IS
  'The custodians/platforms we integrate with -- one row per provider, seeded from '
  'app/providers.py::PROVIDERS by sync_platforms() on startup. Distinct from '
  'platform.providers, which registers a named *connection* to one of these (a Morgan Lloyd '
  'Microsoft tenant, a Seccl firm), and from platform.tenant_platforms, which records that a '
  'tenant has one enabled.';
