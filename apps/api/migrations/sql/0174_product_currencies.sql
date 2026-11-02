-- A product narrows the firm's currency policy. Accounts may choose only the intersection, and
-- ISA wrappers are GBP-only. Existing non-ISA products retain every currency their firm already
-- allowed; newly-created rows inherit the firm's current list in the trigger.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.products
  ADD COLUMN allowed_currencies text[];

SET LOCAL ROLE service_role;
UPDATE auth.tenants tenant
   SET allowed_currencies = tenant.allowed_currencies || ARRAY['GBP']::text[]
 WHERE NOT 'GBP' = ANY(tenant.allowed_currencies)
   AND EXISTS (
     SELECT 1 FROM platform.products product
      WHERE product.tenant_id = tenant.id
        AND product.product_type IN ('ISA', 'JISA')
   );

UPDATE platform.products p
   SET allowed_currencies = CASE
     WHEN p.product_type IN ('ISA', 'JISA') THEN ARRAY['GBP']::text[]
     ELSE t.allowed_currencies
   END
  FROM auth.tenants t
 WHERE p.tenant_id = t.id;

UPDATE platform.products
   SET allowed_currencies = ARRAY['GBP']::text[]
 WHERE allowed_currencies IS NULL;
RESET ROLE;

ALTER TABLE platform.products
  ALTER COLUMN allowed_currencies SET NOT NULL,
  ADD CONSTRAINT products_allowed_currencies_nonempty
    CHECK (cardinality(allowed_currencies) > 0),
  ADD CONSTRAINT products_allowed_currencies_no_null
    CHECK (array_position(allowed_currencies, NULL) IS NULL);

CREATE FUNCTION auth.validate_product_currencies() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform, auth, ref
AS $$
DECLARE
  tenant_currencies text[];
BEGIN
  IF NEW.allowed_currencies IS NULL THEN
    SELECT allowed_currencies
      INTO tenant_currencies
      FROM auth.tenants
     WHERE id = NEW.tenant_id;
    NEW.allowed_currencies := COALESCE(tenant_currencies, ARRAY['GBP']::text[]);
  END IF;

  IF NEW.product_type IN ('ISA', 'JISA')
     AND NEW.allowed_currencies IS DISTINCT FROM ARRAY['GBP']::text[] THEN
    RAISE EXCEPTION 'ISA products must allow GBP only' USING ERRCODE = '23514';
  END IF;

  IF cardinality(NEW.allowed_currencies) <> (
    SELECT count(DISTINCT code) FROM unnest(NEW.allowed_currencies) configured(code)
  ) THEN
    RAISE EXCEPTION 'allowed_currencies contains duplicate currency codes'
      USING ERRCODE = '23514';
  END IF;

  IF EXISTS (
    SELECT 1
      FROM unnest(NEW.allowed_currencies) configured(code)
      LEFT JOIN ref.currencies known ON known.code = configured.code
     WHERE known.code IS NULL
  ) THEN
    RAISE EXCEPTION 'allowed_currencies contains an unknown currency code'
      USING ERRCODE = '23514';
  END IF;

  IF NEW.tenant_id IS NOT NULL THEN
    SELECT allowed_currencies
      INTO tenant_currencies
      FROM auth.tenants
     WHERE id = NEW.tenant_id;
    IF EXISTS (
      SELECT 1
        FROM unnest(NEW.allowed_currencies) configured(code)
       WHERE NOT configured.code = ANY(tenant_currencies)
    ) THEN
      RAISE EXCEPTION 'product currencies must be allowed by its firm'
        USING ERRCODE = '23514';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;
ALTER FUNCTION auth.validate_product_currencies() OWNER TO service_role;

CREATE TRIGGER products_validate_currencies
BEFORE INSERT OR UPDATE OF tenant_id, product_type, allowed_currencies ON platform.products
FOR EACH ROW EXECUTE FUNCTION auth.validate_product_currencies();

CREATE OR REPLACE FUNCTION auth.validate_tenant_currencies() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
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

  IF TG_OP = 'UPDATE' AND NEW.allowed_currencies IS DISTINCT FROM OLD.allowed_currencies THEN
    IF EXISTS (
      SELECT 1
        FROM platform.products product
        CROSS JOIN LATERAL unnest(product.allowed_currencies) configured(code)
       WHERE product.tenant_id = NEW.id
         AND NOT configured.code = ANY(NEW.allowed_currencies)
    ) THEN
      RAISE EXCEPTION 'a product still uses a currency being removed from the firm'
        USING ERRCODE = '23514';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;
ALTER FUNCTION auth.validate_tenant_currencies() OWNER TO service_role;

COMMENT ON COLUMN platform.products.allowed_currencies IS
  'Non-empty ISO currency codes permitted for accounts of this product; a subset of the firm policy.';
