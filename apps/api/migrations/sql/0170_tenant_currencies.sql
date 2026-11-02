-- A firm's currency policy is two facts: the currencies it supports and the one it treats as
-- its base/default. They live on the tenant rather than on a portal or a product: every account
-- picker and every monetary default must answer the same question.
--
-- Existing firms keep the behaviour they had before this setting existed (every reference
-- currency was offered). Newly-created firms start GBP-only and can be widened deliberately in
-- Operations. This avoids silently narrowing a live firm's onboarding choices during migration.

SET LOCAL lock_timeout = '5s';

ALTER TABLE auth.tenants
  ADD COLUMN base_currency text NOT NULL DEFAULT 'GBP'
    REFERENCES ref.currencies(code),
  ADD COLUMN allowed_currencies text[];

-- auth.tenants is FORCE-RLS, including for its owner. The backfill must see every firm.
SET LOCAL ROLE service_role;
UPDATE auth.tenants
   SET allowed_currencies = ARRAY(
     SELECT code FROM ref.currencies ORDER BY CASE WHEN code = 'GBP' THEN 0 ELSE 1 END, code
   );
RESET ROLE;

ALTER TABLE auth.tenants
  ALTER COLUMN allowed_currencies SET NOT NULL,
  ALTER COLUMN allowed_currencies SET DEFAULT ARRAY['GBP']::text[],
  ADD CONSTRAINT tenants_allowed_currencies_nonempty
    CHECK (cardinality(allowed_currencies) > 0),
  ADD CONSTRAINT tenants_base_currency_allowed
    CHECK (base_currency = ANY(allowed_currencies)),
  ADD CONSTRAINT tenants_allowed_currencies_no_null
    CHECK (array_position(allowed_currencies, NULL) IS NULL);

-- An array cannot carry a foreign key per member. Keep the database boundary honest for every
-- writer, not only the admin route: an unknown ISO code must fail before it can become a choice.
CREATE FUNCTION auth.validate_tenant_currencies() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, auth, ref
AS $$
BEGIN
  IF EXISTS (
    SELECT 1
      FROM unnest(NEW.allowed_currencies) AS configured(code)
      LEFT JOIN ref.currencies known ON known.code = configured.code
     WHERE known.code IS NULL
  ) THEN
    RAISE EXCEPTION 'allowed_currencies contains an unknown currency code'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER tenants_validate_currencies
BEFORE INSERT OR UPDATE OF base_currency, allowed_currencies ON auth.tenants
FOR EACH ROW EXECUTE FUNCTION auth.validate_tenant_currencies();

COMMENT ON COLUMN auth.tenants.base_currency IS
  'The firm base currency and default for new monetary records.';
COMMENT ON COLUMN auth.tenants.allowed_currencies IS
  'ISO currency codes the firm permits in account and monetary-entry choices.';
